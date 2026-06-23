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
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from celery import Task
from google.api_core.exceptions import ResourceExhausted

from workers.celery_app import celery_app
from app.core.config import settings
from app.models.resume import Resume
from app.models.candidate import Candidate
from app.models.campaign import Campaign
from app.services.gemini import extract_candidate_info, score_candidate, is_resume_content

logger = structlog.get_logger()

sync_engine = create_engine(settings.SYNC_DATABASE_URL, pool_pre_ping=True)
SyncSession = sessionmaker(bind=sync_engine)


def _publish_sse_event(resume_id: str, status: str, extra: dict = None):
    import redis
    import json
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

            candidate = db.query(Candidate).filter(
                Candidate.resume_id == uuid.UUID(resume_id)
            ).first()
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
        extraction = extract_candidate_info(raw_text)
        log.info("extraction_complete", name=extraction.name, skills_count=len(extraction.skills or []))

        # ── Step 1b: Validate it's actually a resume ──────────────
        if not is_resume_content(extraction):
            log.warning("not_a_resume", name=extraction.name, skills=extraction.skills)
            with SyncSession() as db:
                resume = db.query(Resume).filter(Resume.id == uuid.UUID(resume_id)).first()
                if resume:
                    resume.status = "failed"
                    resume.error_message = "File does not appear to be a resume (no candidate info found)"
                    db.commit()
            _publish_sse_event(
                resume_id,
                "failed",
                {"error": "Not a resume — no candidate information could be extracted"},
            )
            return  # Stop here — do NOT retry, this is a data quality issue

        # ── Step 2: Score candidate ───────────────────────────────
        log.info("calling_gemini_scoring")
        candidate_profile = extraction.model_dump()
        scoring = score_candidate(candidate_profile, role, required_skills)
        log.info(
            "scoring_complete",
            score=scoring.score,
            category=scoring.category,
            name=extraction.name,
        )

        # ── Step 3: Persist results ───────────────────────────────
        with SyncSession() as db:
            candidate = db.query(Candidate).filter(
                Candidate.resume_id == uuid.UUID(resume_id)
            ).first()

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

    except ResourceExhausted:
        # Daily quota hit — stop immediately, do NOT retry
        _mark_quota_exceeded(resume_id, log)

    except Exception as exc:
        log.error("ai_processing_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=60)
