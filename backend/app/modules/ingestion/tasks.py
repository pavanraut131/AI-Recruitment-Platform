"""The upload pipeline's Celery tasks: PDF text extraction, then AI analysis.

1. :func:`extract_resume` (``extract_queue``): download the PDF from object storage, extract its
   text (pdfplumber, then PyMuPDF, then OCR through the AI service for scanned PDFs), store the
   text on the resume's candidate (creating it), and queue :func:`process_candidate_ai`.
2. :func:`process_candidate_ai` (``ai_queue``): extract a profile and score it against the
   campaign with the AI service, store both on the candidate and mark the resume ``done``.

Progress goes to the SSE stream through :mod:`app.modules.ingestion.events`. Candidates are read
and written only through ``app.modules.hiring.service``, and the language model only through
``app.modules.ai.service``.

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
from sqlalchemy.orm import sessionmaker

from app.modules.ai.schemas import ExtractionResult, JobRequirements
from app.modules.ai.service import AIQuotaExceeded, get_ai_service
from app.modules.hiring import service as hiring_service
from app.modules.ingestion import pdf, repository
from app.modules.ingestion.events import publish_resume_event
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


def enqueue_analysis(resume_id: str) -> None:
    process_candidate_ai.apply_async(args=(resume_id,), queue="ai_queue")


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

        # Store the text on the (placeholder) candidate, in one transaction with the status
        with SyncSession() as db:
            resume = repository.get_sync(db, uuid.UUID(resume_id))
            if not resume:
                raise ValueError("Resume not found")
            resume.status = "processing"
            hiring_service.save_resume_text(
                db, resume_id=resume.id, campaign_id=resume.campaign_id, raw_text=raw_text
            )
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


# ── Step 2: AI extraction and scoring ─────────────────────────
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


def _mark_failed(resume_id: str, error_message: str) -> None:
    with SyncSession() as db:
        resume = repository.get_sync(db, uuid.UUID(resume_id))
        if resume:
            resume.status = "failed"
            resume.error_message = error_message
            db.commit()


def _mark_quota_exceeded(resume_id: str, log: Any) -> None:
    """Immediately mark a resume as quota_exceeded — no retry."""
    log.warning("quota_exceeded_stopping", resume_id=resume_id)
    with SyncSession() as db:
        resume = repository.get_sync(db, uuid.UUID(resume_id))
        if resume:
            resume.status = "quota_exceeded"
            resume.error_message = QUOTA_EXCEEDED_MESSAGE
            db.commit()
    publish_resume_event(resume_id, "quota_exceeded", {"error": QUOTA_EXCEEDED_MESSAGE})


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
    default_retry_delay=60,
    queue="ai_queue",
)
def process_candidate_ai(self: AITask, resume_id: str) -> None:
    """Run AI extraction + scoring for a resume."""
    log = logger.bind(task="process_candidate_ai", resume_id=resume_id)
    log.info("task_started")

    try:
        rid = uuid.UUID(resume_id)
        with SyncSession() as db:
            resume = repository.get_sync(db, rid)
            if not resume:
                log.error("resume_not_found")
                return

            raw_text = hiring_service.get_resume_text(db, rid)
            if not raw_text:
                raise ValueError("No raw text found for candidate")

            campaign = hiring_service.get_campaign_requirements(db, resume.campaign_id)
            if not campaign:
                raise ValueError("Campaign not found")

        ai = get_ai_service()

        # ── Step 1: Extract structured info ──────────────────────
        log.info("calling_gemini_extraction")
        extraction = ai.extract_profile(raw_text)
        log.info(
            "extraction_complete", name=extraction.name, skills_count=len(extraction.skills or [])
        )

        # ── Step 1b: Validate it's actually a resume ──────────────
        if not is_resume_content(extraction):
            log.warning("not_a_resume", name=extraction.name, skills=extraction.skills)
            _mark_failed(resume_id, "File does not appear to be a resume (no candidate info found)")
            publish_resume_event(
                resume_id,
                "failed",
                {"error": "Not a resume — no candidate information could be extracted"},
            )
            return  # Stop here — do NOT retry, this is a data quality issue

        # ── Step 2: Score candidate ───────────────────────────────
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

        # ── Step 3: Persist results ───────────────────────────────
        with SyncSession() as db:
            hiring_service.record_extraction(db, rid, extraction)
            hiring_service.record_scoring(db, rid, scoring)

            resume = repository.get_sync(db, rid)
            if not resume:
                raise ValueError("Resume not found")
            resume.status = "done"

            db.commit()
            original_filename = resume.original_filename

        log.info("candidate_saved", name=extraction.name, score=scoring.score)

        # ── Step 4: Publish SSE event ─────────────────────────────
        publish_resume_event(
            resume_id,
            "done",
            {
                "candidate_name": extraction.name,
                "score": scoring.score,
                "category": scoring.category,
                "filename": original_filename,
            },
        )

    except AIQuotaExceeded:
        # Daily quota hit — stop immediately, do NOT retry
        _mark_quota_exceeded(resume_id, log)

    except Exception as exc:
        log.error("ai_processing_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=60) from exc
