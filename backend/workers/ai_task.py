"""
Worker 2: AI Processing
- Loads raw resume text and campaign requirements
- Calls Gemini for structured extraction
- Calls Gemini for candidate scoring
- Saves full Candidate record
- Publishes SSE update
"""

import uuid

import structlog
from celery import Task
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.resume import Resume
from app.modules.ai.schemas import ExtractionResult, JobRequirements
from app.modules.ai.service import AIQuotaExceeded, get_ai_service
from app.modules.hiring.models import Campaign, Candidate
from app.platform.celery_app import celery_app
from app.platform.config import settings

logger = structlog.get_logger()

sync_engine = create_engine(settings.SYNC_DATABASE_URL, pool_pre_ping=True)
SyncSession = sessionmaker(bind=sync_engine)


def _publish_sse_event(resume_id: str, status: str, extra: dict = None):
    import json

    import redis

    r = redis.Redis.from_url(settings.REDIS_URL)
    payload = {"resume_id": resume_id, "status": status, **(extra or {})}
    r.publish(f"resume:status:{resume_id}", json.dumps(payload))
    r.publish("resume:all_updates", json.dumps(payload))


def _mark_quota_exceeded(resume_id: str, log):
    """Immediately mark a resume as quota_exceeded — no retry."""
    error_msg = (
        "Gemini API daily quota reached (20 req/day on free tier). "
        "This resume was not processed. Quota resets at midnight PT. "
        "Upgrade to a paid Gemini API key to remove this limit."
    )
    log.warning("quota_exceeded_stopping", resume_id=resume_id)
    with SyncSession() as db:
        resume = db.query(Resume).filter(Resume.id == uuid.UUID(resume_id)).first()
        if resume:
            resume.status = "quota_exceeded"
            resume.error_message = error_msg
            db.commit()
    _publish_sse_event(resume_id, "quota_exceeded", {"error": error_msg})


class BaseTask(Task):
    abstract = True

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        resume_id = args[0] if args else kwargs.get("resume_id", "unknown")
        logger.error("ai_task_failed", resume_id=resume_id, error=str(exc))
        with SyncSession() as db:
            resume = db.query(Resume).filter(Resume.id == uuid.UUID(resume_id)).first()
            if resume:
                resume.status = "failed"
                resume.error_message = f"AI processing failed: {str(exc)}"
                db.commit()
        _publish_sse_event(resume_id, "failed", {"error": str(exc)})


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="workers.ai_task.process_candidate_ai",
    max_retries=3,
    default_retry_delay=60,
    queue="ai_queue",
)
def process_candidate_ai(self, resume_id: str):
    """Run Gemini extraction + scoring for a resume."""
    log = logger.bind(task="process_candidate_ai", resume_id=resume_id)
    log.info("task_started")

    try:
        with SyncSession() as db:
            resume = db.query(Resume).filter(Resume.id == uuid.UUID(resume_id)).first()
            if not resume:
                log.error("resume_not_found")
                return

            candidate = (
                db.query(Candidate).filter(Candidate.resume_id == uuid.UUID(resume_id)).first()
            )
            if not candidate or not candidate.raw_text:
                raise ValueError("No raw text found for candidate")

            campaign = db.query(Campaign).filter(Campaign.id == resume.campaign_id).first()
            if not campaign:
                raise ValueError("Campaign not found")

            raw_text = candidate.raw_text
            role = campaign.role
            required_skills = campaign.required_skills or []

        # ── Step 1: Extract structured info ──────────────────────
        log.info("calling_gemini_extraction")
        extraction = get_ai_service().extract_profile(raw_text)
        log.info(
            "extraction_complete", name=extraction.name, skills_count=len(extraction.skills or [])
        )

        # ── Step 1b: Validate it's actually a resume ──────────────
        if not is_resume_content(extraction):
            log.warning("not_a_resume", name=extraction.name, skills=extraction.skills)
            with SyncSession() as db:
                resume = db.query(Resume).filter(Resume.id == uuid.UUID(resume_id)).first()
                if resume:
                    resume.status = "failed"
                    resume.error_message = (
                        "File does not appear to be a resume (no candidate info found)"
                    )
                    db.commit()
            _publish_sse_event(
                resume_id,
                "failed",
                {"error": "Not a resume — no candidate information could be extracted"},
            )
            return  # Stop here — do NOT retry, this is a data quality issue

        # ── Step 2: Score candidate ───────────────────────────────
        log.info("calling_gemini_scoring")
        scoring = get_ai_service().score(
            extraction, JobRequirements(role=role, required_skills=required_skills)
        )
        log.info(
            "scoring_complete",
            score=scoring.score,
            category=scoring.category,
            name=extraction.name,
        )

        # ── Step 3: Persist results ───────────────────────────────
        with SyncSession() as db:
            candidate = (
                db.query(Candidate).filter(Candidate.resume_id == uuid.UUID(resume_id)).first()
            )

            # Populate extraction fields
            candidate.name = extraction.name
            candidate.email = extraction.email
            candidate.phone = extraction.phone
            candidate.github_url = extraction.github_url
            candidate.linkedin_url = extraction.linkedin_url
            candidate.skills = extraction.skills
            candidate.education = extraction.education
            candidate.work_experience = extraction.work_experience
            candidate.certifications = extraction.certifications
            candidate.projects = extraction.projects
            candidate.years_of_experience = extraction.years_of_experience

            # Populate AI scoring fields
            candidate.score = scoring.score
            candidate.score_breakdown = scoring.score_breakdown
            candidate.category = scoring.category
            candidate.summary = scoring.summary
            candidate.strengths = scoring.strengths
            candidate.weaknesses = scoring.weaknesses
            candidate.missing_skills = scoring.missing_skills
            candidate.recommendation = scoring.recommendation

            resume = db.query(Resume).filter(Resume.id == uuid.UUID(resume_id)).first()
            resume.status = "done"

            db.commit()
            original_filename = resume.original_filename

        log.info("candidate_saved", name=extraction.name, score=scoring.score)

        # ── Step 4: Publish SSE event ─────────────────────────────
        _publish_sse_event(
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
