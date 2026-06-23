"""
Worker 1: PDF Extraction
- Downloads PDF from MinIO
- Extracts text using pdfplumber → PyMuPDF fallback
- Saves raw_text to Candidate record
- Enqueues AI processing task
"""
import uuid
import structlog
from celery import Task
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from workers.celery_app import celery_app
from app.core.config import settings
from app.core import storage
from app.services.pdf import extract_text_from_pdf, clean_text, is_valid_pdf
from app.models.resume import Resume
from app.models.candidate import Candidate

logger = structlog.get_logger()

# Sync engine for Celery workers
sync_engine = create_engine(settings.SYNC_DATABASE_URL, pool_pre_ping=True)
SyncSession = sessionmaker(bind=sync_engine)


def _publish_sse_event(resume_id: str, status: str, extra: dict = None):
    """Publish status update to Redis pub/sub for SSE streaming."""
    import redis
    import json

    r = redis.Redis.from_url(settings.REDIS_URL)
    payload = {"resume_id": resume_id, "status": status, **(extra or {})}
    r.publish(f"resume:status:{resume_id}", json.dumps(payload))
    # Also publish to campaign-level channel
    r.publish("resume:all_updates", json.dumps(payload))


class BaseTask(Task):
    abstract = True

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        resume_id = args[0] if args else kwargs.get("resume_id", "unknown")
        logger.error("task_failed", task=self.name, resume_id=resume_id, error=str(exc))

        with SyncSession() as db:
            resume = db.query(Resume).filter(Resume.id == uuid.UUID(resume_id)).first()
            if resume:
                resume.retry_count += 1
                if resume.retry_count >= 3:
                    resume.status = "failed"
                    resume.error_message = str(exc)
                    _publish_sse_event(resume_id, "failed", {"error": str(exc)})
                db.commit()


@celery_app.task(
    bind=True,
    base=BaseTask,
    name="workers.extract_task.extract_resume",
    max_retries=3,
    default_retry_delay=30,
    queue="extract_queue",
)
def extract_resume(self, resume_id: str):
    """Download PDF and extract text. Then enqueue AI processing."""
    log = logger.bind(task="extract_resume", resume_id=resume_id)
    log.info("task_started")

    # Capture scalar values inside session scope to avoid DetachedInstanceError
    with SyncSession() as db:
        resume = db.query(Resume).filter(Resume.id == uuid.UUID(resume_id)).first()
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
        _publish_sse_event(resume_id, "extracting", {"filename": original_filename})

        # Download from MinIO
        log.info("downloading_pdf", path=storage_path)
        pdf_bytes = storage.download_file(storage_path)

        if not is_valid_pdf(pdf_bytes):
            raise ValueError("Downloaded file is not a valid PDF")

        # Extract text
        raw_text = extract_text_from_pdf(pdf_bytes)

        if raw_text:
            raw_text = clean_text(raw_text)
        else:
            # Scanned PDF: use Gemini Vision as OCR fallback
            log.warning("scanned_pdf_detected", note="using gemini vision fallback")
            raw_text = _gemini_vision_ocr(pdf_bytes)

        if not raw_text or len(raw_text.strip()) < 50:
            raise ValueError("Could not extract meaningful text from PDF")

        log.info("text_extracted", chars=len(raw_text))

        # Create placeholder Candidate record
        with SyncSession() as db:
            resume = db.query(Resume).filter(Resume.id == uuid.UUID(resume_id)).first()
            resume.status = "processing"

            existing = db.query(Candidate).filter(Candidate.resume_id == uuid.UUID(resume_id)).first()
            if not existing:
                candidate = Candidate(
                    resume_id=uuid.UUID(resume_id),
                    campaign_id=resume.campaign_id,
                    raw_text=raw_text,
                )
                db.add(candidate)
            else:
                existing.raw_text = raw_text

            db.commit()

        # Enqueue AI task
        from workers.ai_task import process_candidate_ai
        process_candidate_ai.apply_async(args=[resume_id], queue="ai_queue")
        log.info("ai_task_enqueued")

    except Exception as exc:
        log.error("extraction_failed", error=str(exc))
        if self.request.retries >= self.max_retries:
            # Max retries hit — mark as failed so it doesn't stay stuck in "extracting"
            with SyncSession() as db:
                resume = db.query(Resume).filter(Resume.id == uuid.UUID(resume_id)).first()
                if resume:
                    resume.status = "failed"
                    resume.error_message = str(exc)
                    db.commit()
            _publish_sse_event(resume_id, "failed", {"error": str(exc)})
            return
        raise self.retry(exc=exc, countdown=30)


def _gemini_vision_ocr(pdf_bytes: bytes) -> str:
    """Use Gemini Vision to OCR a scanned PDF (fallback path)."""
    import google.generativeai as genai
    import fitz

    genai.configure(api_key=settings.GEMINI_API_KEY)
    model = genai.GenerativeModel(settings.GEMINI_MODEL)

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    all_text = []

    for page_num, page in enumerate(doc):
        pix = page.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("png")

        response = model.generate_content([
            "Extract all text from this resume page. Return only the extracted text, no formatting.",
            {"mime_type": "image/png", "data": img_bytes},
        ])
        all_text.append(response.text)

    doc.close()
    return "\n".join(all_text)
