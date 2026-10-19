"""The upload pipeline's Celery tasks: PDF text extraction, then AI analysis.

1. :func:`extract_resume` (``extract_queue``, step ``extract_text``): download the PDF from object
   storage, extract its text (pdfplumber, then PyMuPDF, then OCR through the AI service for
   scanned PDFs), store the text on the resume (``resumes.raw_text``), and queue
   :func:`process_candidate_ai`.
2. :func:`process_candidate_ai` (``ai_queue``): extract a profile and score it against the
   campaign with the AI service, resolve the Person, and write the Application in one
   transaction (EPIC-00 spec §5.1); mark the resume ``done``. Since the read switch (E0-19,
   spec §6.1 phase 4) no legacy candidate is written: the dual write of phases 1–3 is over.

Progress goes to the SSE stream through :mod:`app.modules.ingestion.events`. Applications
(and the legacy candidates still read) go only through ``app.modules.hiring.service``, People only
through ``app.modules.talent.service``, and the language model only through
``app.modules.ai.service``.

**Steps.** ``resumes.processing_step`` records the step in progress, and the step that failed
after a failure (spec §4, §5.5): ``extract_text`` → ``extract_profile`` → ``score`` →
``resolve_person`` → ``save``; it is cleared when the resume is ``done``. Every step is safe to
re-run, so a Celery retry, or the retry-failed endpoint, simply runs the task again and the
completed steps are skipped or repeated without side effects (see :func:`process_candidate_ai`).

**Task names are a contract.** Queued messages, the routes in ``app.platform.celery_app`` and
Flower's history refer to tasks by name, so the tasks keep the names they had when they lived
in the old ``workers`` package (:data:`EXTRACT_TASK_NAME`, :data:`AI_TASK_NAME`). Renaming one
needs a release that registers both names.

The tasks run in Celery worker processes, which have no event loop, so they use a synchronous
engine and ``Session`` (created here, used by nothing else).
"""

import uuid
from typing import TYPE_CHECKING, Any

import structlog
from billiard.einfo import ExceptionInfo
from celery import Task
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.modules.ai.schemas import ExtractionResult, JobRequirements
from app.modules.ai.service import AIQuotaExceeded, get_ai_service
from app.modules.hiring import service as hiring_service
from app.modules.ingestion import pdf, repository
from app.modules.ingestion.events import publish_resume_event
from app.modules.ingestion.models import Resume
from app.modules.talent import service as talent_service
from app.modules.talent.schemas import PersonProfile, ResolveResult
from app.platform import storage
from app.platform.celery_app import celery_app
from app.platform.config import settings

logger = structlog.get_logger()

EXTRACT_TASK_NAME = "workers.extract_task.extract_resume"
AI_TASK_NAME = "workers.ai_task.process_candidate_ai"

# Sync engine for Celery workers
sync_engine = create_engine(settings.SYNC_DATABASE_URL, pool_pre_ping=True)
SyncSession = sessionmaker(bind=sync_engine)

QUOTA_EXCEEDED_MESSAGE = (
    "Gemini API daily quota reached (20 req/day on free tier). "
    "This resume was not processed. Quota resets at midnight PT. "
    "Upgrade to a paid Gemini API key to remove this limit."
)

# resumes.processing_step values (spec §4).
STEP_EXTRACT_TEXT = "extract_text"
STEP_EXTRACT_PROFILE = "extract_profile"
STEP_RESOLVE_PERSON = "resolve_person"
STEP_SCORE = "score"
STEP_SAVE = "save"
# Steps run by process_candidate_ai: a resume that failed at one of these already has its text,
# so a retry starts at the AI task (ingestion.service.retry_failed).
AI_STEPS = (STEP_EXTRACT_PROFILE, STEP_SCORE, STEP_RESOLVE_PERSON, STEP_SAVE)

# Retry delays (seconds) of the AI task: any failure, and a quota error whose provider gave no
# retry delay (the Gemini adapter used to wait 65 s between quota rounds itself).
AI_RETRY_DELAY = 60
QUOTA_RETRY_FALLBACK_DELAY = 65.0


# celery-types makes Task generic over (params, return); the runtime class is not subscriptable.
if TYPE_CHECKING:
    _ResumeTask = Task[[str], None]
else:
    _ResumeTask = Task


def _resume_id_arg(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    return str(args[0] if args else kwargs.get("resume_id", "unknown"))


# ── Dispatch ──────────────────────────────────────────────────
def enqueue_extraction(resume_id: uuid.UUID | str) -> None:
    extract_resume.apply_async(args=(str(resume_id),), queue="extract_queue")


def enqueue_analysis(resume_id: uuid.UUID | str) -> None:
    process_candidate_ai.apply_async(args=(str(resume_id),), queue="ai_queue")


# ── Step 1: PDF text extraction ───────────────────────────────
class ExtractTask(_ResumeTask):
    abstract = True

    def on_failure(
        self,
        exc: Exception,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        einfo: ExceptionInfo,
    ) -> None:
        resume_id = _resume_id_arg(args, kwargs)
        logger.error("task_failed", task=self.name, resume_id=resume_id, error=str(exc))

        with SyncSession() as db:
            resume = repository.get_sync(db, uuid.UUID(resume_id))
            if resume:
                resume.retry_count += 1
                if resume.retry_count >= 3:
                    resume.status = "failed"
                    resume.error_message = str(exc)
                    publish_resume_event(resume_id, "failed", {"error": str(exc)})
                db.commit()


@celery_app.task(
    bind=True,
    base=ExtractTask,
    name=EXTRACT_TASK_NAME,
    max_retries=3,
    default_retry_delay=30,
    queue="extract_queue",
)
def extract_resume(self: ExtractTask, resume_id: str) -> None:
    """Download PDF and extract text. Then enqueue AI processing."""
    log = logger.bind(task="extract_resume", resume_id=resume_id)
    log.info("task_started")

    # Capture scalar values inside session scope to avoid DetachedInstanceError
    with SyncSession() as db:
        resume = repository.get_sync(db, uuid.UUID(resume_id))
        if not resume:
            log.error("resume_not_found")
            return

        resume.status = "extracting"
        resume.processing_step = STEP_EXTRACT_TEXT
        resume.celery_task_id = self.request.id
        db.commit()

        # Read all needed values NOW, inside the session, before it closes
        original_filename = resume.original_filename
        storage_path = resume.storage_path

    try:
        publish_resume_event(resume_id, "extracting", {"filename": original_filename})

        # Download from MinIO
        log.info("downloading_pdf", path=storage_path)
        pdf_bytes = storage.download_file(storage_path)

        if not pdf.is_valid_pdf(pdf_bytes):
            raise ValueError("Downloaded file is not a valid PDF")

        # Extract text
        raw_text = pdf.extract_text_from_pdf(pdf_bytes)

        if raw_text:
            raw_text = pdf.clean_text(raw_text)
        else:
            # Scanned PDF: OCR the rendered pages with the AI service
            log.warning("scanned_pdf_detected", note="using gemini vision fallback")
            raw_text = get_ai_service().transcribe_page_images(pdf.render_pages_png(pdf_bytes))

        if not raw_text or len(raw_text.strip()) < 50:
            raise ValueError("Could not extract meaningful text from PDF")

        log.info("text_extracted", chars=len(raw_text))

        # Store the text on the resume, in one transaction with the status
        with SyncSession() as db:
            resume = repository.get_sync(db, uuid.UUID(resume_id))
            if not resume:
                raise ValueError("Resume not found")
            resume.status = "processing"
            resume.raw_text = raw_text
            db.commit()

        enqueue_analysis(resume_id)
        log.info("ai_task_enqueued")

    except Exception as exc:
        log.error("extraction_failed", error=str(exc))
        if self.request.retries >= (self.max_retries or 0):
            # Max retries hit — mark as failed so it doesn't stay stuck in "extracting"
            with SyncSession() as db:
                failed = repository.get_sync(db, uuid.UUID(resume_id))
                if failed:
                    failed.status = "failed"
                    failed.error_message = str(exc)
                    db.commit()
            publish_resume_event(resume_id, "failed", {"error": str(exc)})
            return
        raise self.retry(exc=exc, countdown=30) from exc


# ── Step 2: AI extraction, scoring, Person resolution, dual write ──
def is_resume_content(extraction: ExtractionResult) -> bool:
    """
    Heuristic check: does the extracted data look like a real resume?
    Rejects documents that yield no identifiable candidate info.
    """
    has_name = bool(extraction.name and len(extraction.name.strip()) > 1)
    has_skills = bool(extraction.skills and len(extraction.skills) > 0)
    has_experience = bool(extraction.work_experience and len(extraction.work_experience) > 0)
    has_education = bool(extraction.education and len(extraction.education) > 0)
    has_email = bool(extraction.email)

    # Must have at least 2 of these signals to be considered a real resume
    signals = sum([has_name, has_skills, has_experience, has_education, has_email])
    return signals >= 2


def person_profile(extraction: ExtractionResult) -> PersonProfile:
    """The talent ``PersonProfile`` part of an AI extraction (the contact details are passed to
    ``resolve_person`` separately, as identifiers)."""
    return PersonProfile(
        full_name=extraction.name,
        github_url=extraction.github_url,
        linkedin_url=extraction.linkedin_url,
        skills=extraction.skills,
        education=extraction.education,
        work_experience=extraction.work_experience,
        certifications=extraction.certifications,
        projects=extraction.projects,
        years_of_experience=extraction.years_of_experience,
    )


def _require_resume(db: Session, rid: uuid.UUID) -> Resume:
    resume = repository.get_sync(db, rid)
    if not resume:
        raise ValueError("Resume not found")
    return resume


def _mark_failed(resume_id: str, error_message: str) -> None:
    with SyncSession() as db:
        resume = repository.get_sync(db, uuid.UUID(resume_id))
        if resume:
            resume.status = "failed"
            resume.error_message = error_message
            db.commit()


def _record_failed_step(resume_id: str, step: str, log: Any) -> None:
    """Persist the step that just failed (its own transaction: the step's was rolled back).

    The status is left alone: the task retries, and ``AITask.on_failure`` sets ``failed`` once
    the retries are used up.
    """
    try:
        with SyncSession() as db:
            resume = repository.get_sync(db, uuid.UUID(resume_id))
            if resume:
                resume.processing_step = step
                db.commit()
    except Exception as exc:  # never hide the original failure behind this one
        log.error("record_failed_step_failed", step=step, error=str(exc))


def _mark_quota_exceeded(resume_id: str, step: str, log: Any) -> None:
    """Set ``quota_exceeded`` (and the step it stopped at) now, and tell the UI."""
    log.warning("quota_exceeded", resume_id=resume_id, step=step)
    with SyncSession() as db:
        resume = repository.get_sync(db, uuid.UUID(resume_id))
        if resume:
            resume.status = "quota_exceeded"
            resume.error_message = QUOTA_EXCEEDED_MESSAGE
            resume.processing_step = step
            db.commit()
    publish_resume_event(resume_id, "quota_exceeded", {"error": QUOTA_EXCEEDED_MESSAGE})


def quota_retry_delay(exc: AIQuotaExceeded) -> float:
    """Seconds to wait before retrying after a quota error: the provider's retry delay when it
    gave one, else :data:`QUOTA_RETRY_FALLBACK_DELAY`."""
    if exc.retry_after is not None and exc.retry_after > 0:
        return exc.retry_after
    return QUOTA_RETRY_FALLBACK_DELAY


class AITask(_ResumeTask):
    abstract = True

    def on_failure(
        self,
        exc: Exception,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        einfo: ExceptionInfo,
    ) -> None:
        resume_id = _resume_id_arg(args, kwargs)
        logger.error("ai_task_failed", resume_id=resume_id, error=str(exc))
        _mark_failed(resume_id, f"AI processing failed: {str(exc)}")
        publish_resume_event(resume_id, "failed", {"error": str(exc)})


@celery_app.task(
    bind=True,
    base=AITask,
    name=AI_TASK_NAME,
    max_retries=3,
    default_retry_delay=AI_RETRY_DELAY,
    queue="ai_queue",
)
def process_candidate_ai(self: AITask, resume_id: str) -> None:
    """Analyse a resume and write its Application (spec §5.1).

    Steps, each safe to re-run (a retry runs the whole task again):

    1. ``extract_profile``: ``ai.extract_profile`` on the resume text. **Skipped** when
       ``resumes.extracted`` is set: the snapshot is reused. A new extraction is committed to
       ``resumes.extracted`` straight away, so a later failure never pays for it twice.
    2. ``score``: ``ai.score`` against the campaign. Not stored on its own; a retry rescores.
    3. ``resolve_person``: ``talent.resolve_person``, storing ``resumes.person_id``. **Skipped**
       when ``resumes.person_id`` is set (the stored result is read instead). ``resolve_person``
       is itself idempotent per resume.
    4. ``save``: ``hiring.upsert_application`` (a new application gets a fresh id) and the
       resume's ``done`` status. A resume extracted before the read switch still has a legacy
       candidate: its id is reused by a new application, or redirected by a
       ``legacy_candidate_map`` row to an existing one (E0-17), so it keeps leading to exactly
       one application. No legacy candidate is written (E0-19).

    Steps 3 and 4 are **one transaction**: the Person, its identifiers or conflict, the
    ``person_resumes`` row, ``resumes.person_id`` and the Application are committed together or
    not at all, so a failure at either step leaves nothing behind for the retry to duplicate.

    The text comes from ``resumes.raw_text``; a resume extracted before the switch has it on its
    legacy candidate instead (``hiring.legacy_resume_text``, read-only).

    **Why score before resolving** (spec §5.1 lists resolve first). ``resolve_person`` takes
    transaction-level advisory locks on the resume's email and phone, held until the commit.
    Scoring does not depend on the Person, so the two AI calls (seconds each, and the quota
    errors) happen before that transaction opens; it then holds the locks only for a few
    milliseconds of SQL. Resolving first would hold them across the Gemini scoring call, and
    every other upload of the same person (same email or phone) would wait behind it.

    **Failures.** ``processing_step`` is left at the failed step. A quota error sets
    ``quota_exceeded`` at once, publishes it and schedules the retry after the provider's retry
    delay (spec §5.5). Any other error is retried after :data:`AI_RETRY_DELAY` seconds; when the
    retries are used up, ``AITask.on_failure`` marks the resume ``failed``.
    """
    log = logger.bind(task="process_candidate_ai", resume_id=resume_id)
    log.info("task_started")
    rid = uuid.UUID(resume_id)
    step = STEP_EXTRACT_PROFILE

    try:
        # ── Load the inputs and mark the step (short transaction, no AI call inside) ──
        with SyncSession() as db:
            resume = repository.get_sync(db, rid)
            if not resume:
                log.error("resume_not_found")
                return

            # A resume extracted before the read switch has its text on its legacy candidate.
            raw_text = resume.raw_text or hiring_service.legacy_resume_text(db, rid)
            if not raw_text:
                raise ValueError("No raw text found for candidate")

            campaign = hiring_service.get_campaign_requirements(db, resume.campaign_id)
            if not campaign:
                raise ValueError("Campaign not found")

            snapshot = resume.extracted
            resume.status = "processing"  # back from quota_exceeded / pending on a retry
            resume.error_message = None
            resume.processing_step = step
            db.commit()

        ai = get_ai_service()

        # ── extract_profile ───────────────────────────────────────
        if snapshot is None:
            log.info("calling_gemini_extraction")
            extraction = ai.extract_profile(raw_text)
        else:
            log.info("extraction_snapshot_reused")
            extraction = ExtractionResult.model_validate(snapshot)
        log.info(
            "extraction_complete", name=extraction.name, skills_count=len(extraction.skills or [])
        )

        # Validate it's actually a resume
        if not is_resume_content(extraction):
            log.warning("not_a_resume", name=extraction.name, skills=extraction.skills)
            _mark_failed(resume_id, "File does not appear to be a resume (no candidate info found)")
            publish_resume_event(
                resume_id,
                "failed",
                {"error": "Not a resume — no candidate information could be extracted"},
            )
            return  # Stop here — do NOT retry, this is a data quality issue

        step = STEP_SCORE
        with SyncSession() as db:
            resume = _require_resume(db, rid)
            if snapshot is None:
                resume.extracted = extraction.model_dump(mode="json")
            resume.processing_step = step
            db.commit()

        # ── score ─────────────────────────────────────────────────
        log.info("calling_gemini_scoring")
        scoring = ai.score(
            extraction,
            JobRequirements(role=campaign.role, required_skills=campaign.required_skills),
        )
        log.info(
            "scoring_complete",
            score=scoring.score,
            category=scoring.category,
            name=extraction.name,
        )

        # ── resolve_person + save: one transaction, committed once ──
        step = STEP_RESOLVE_PERSON
        with SyncSession() as db:
            resume = _require_resume(db, rid)
            resolution = _resolve_person(db, resume, extraction)

            step = STEP_SAVE
            # No legacy candidate is written any more (E0-19): a new application gets a fresh
            # id. Only a resume extracted before the switch has a legacy candidate (a
            # placeholder with its text); it must still lead to exactly one application (spec
            # §6.3), so a new application reuses its id, and when the Person already had one
            # for this campaign its id is redirected there (E0-17, the backfill's rule). The
            # application's recruiter fields are its own now and are left alone.
            candidate_id = hiring_service.legacy_candidate_id_for_resume(db, rid)
            application = hiring_service.upsert_application(
                db,
                campaign_id=resume.campaign_id,
                person_id=resolution.person_id,
                resume_id=rid,
                resume_uploaded_at=resume.uploaded_at,
                scoring=scoring,
                application_id=candidate_id,
            )
            if candidate_id is not None:
                hiring_service.link_legacy_candidate(
                    db, candidate_id=candidate_id, application_id=application.id
                )
            resume.status = "done"
            resume.processing_step = None
            db.commit()  # also releases resolve_person's advisory locks
            original_filename = resume.original_filename

        log.info(
            "candidate_saved",
            name=extraction.name,
            score=scoring.score,
            person_id=str(resolution.person_id),
            matched_existing=resolution.matched_existing,
        )

        # ── Publish SSE event ─────────────────────────────────────
        publish_resume_event(
            resume_id,
            "done",
            {
                "candidate_name": extraction.name,
                "score": scoring.score,
                "category": scoring.category,
                "filename": original_filename,
                # Added in E0-16 (additive): the Person the resume was attached to.
                "person_id": str(resolution.person_id),
                "matched_existing": resolution.matched_existing,
            },
        )

    except AIQuotaExceeded as exc:
        # Show it now (the UI's "Quota Limit" state), then try again when the provider says so.
        _mark_quota_exceeded(resume_id, step, log)
        if self.request.retries >= (self.max_retries or 0):
            log.warning("quota_retries_exhausted", step=step)
            return  # stays quota_exceeded; the retry-failed endpoint can re-queue it
        countdown = quota_retry_delay(exc)
        log.info("quota_retry_scheduled", countdown=countdown, step=step)
        raise self.retry(countdown=countdown) from exc

    except Exception as exc:
        log.error("ai_processing_failed", error=str(exc), step=step)
        _record_failed_step(resume_id, step, log)
        raise self.retry(exc=exc, countdown=AI_RETRY_DELAY) from exc


def _resolve_person(db: Session, resume: Resume, extraction: ExtractionResult) -> ResolveResult:
    """Step ``resolve_person``: which Person the resume belongs to, stored as
    ``resumes.person_id``. Skipped when that is already set (the stored result is returned)."""
    if resume.person_id is not None:
        stored = talent_service.get_resolution(db, resume.id)
        if stored is not None:
            return stored
        # person_id was set without talent's record of it (not done by any current writer):
        # trust it rather than resolve again, and report the Person as already known.
        return ResolveResult(person_id=resume.person_id, matched_existing=True)

    result = talent_service.resolve_person(
        db,
        email=extraction.email,
        phone=extraction.phone,
        profile=person_profile(extraction),
        resume_id=resume.id,
        uploaded_at=resume.uploaded_at,
        default_region=settings.PHONE_DEFAULT_REGION,
    )
    resume.person_id = result.person_id
    return result
