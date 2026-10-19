"""The upload pipeline's Celery tasks, run in-process with ``AI_PROVIDER=fake``.

Database, SSE and eager-retry fixtures: ``tests/ingestion/conftest.py``. Object storage, Redis
and Celery dispatch are the usual in-memory fakes (``tests/conftest.py``). The Person /
Application writes are covered in ``test_pipeline.py``.
"""

from __future__ import annotations

import uuid
from typing import Any

import fitz
import pytest
from sqlalchemy.orm import Session

from app.modules.ai.fake import FakeAIService
from app.modules.ai.schemas import ExtractionResult
from app.modules.ai.service import AIQuotaExceeded
from app.modules.hiring.models import Application, Campaign, Candidate
from app.modules.ingestion import tasks
from app.modules.ingestion.models import Resume
from tests.fakes import CeleryRecorder, InMemoryStorage
from tests.ingestion.conftest import Events

RESUME_TEXT = """Jane Doe
jane.doe@example.com | +1 555 0100
Senior Backend Engineer at Acme, 6 years of experience
Skills: Python, FastAPI, PostgreSQL, Docker
B.Tech in Computer Science
Project: Resume parser built with Python and Redis
"""


def make_pdf(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=11)
    data: bytes = doc.tobytes()
    doc.close()
    return data


def _arrange(db: Session, storage: InMemoryStorage, pdf_bytes: bytes) -> Resume:
    campaign = Campaign(
        title="Backend Q1", role="Backend Engineer", required_skills=["Python", "Go"]
    )
    db.add(campaign)
    db.flush()
    resume_id = uuid.uuid4()
    key = f"campaigns/{campaign.id}/resumes/{resume_id}/jane.pdf"
    resume = Resume(
        id=resume_id,
        campaign_id=campaign.id,
        original_filename="jane.pdf",
        storage_path=key,
        file_size_bytes=len(pdf_bytes),
        status="pending",
    )
    db.add(resume)
    db.commit()
    storage.upload_file(pdf_bytes, key)
    return resume


def _application(db: Session, resume_id: uuid.UUID) -> Application | None:
    db.expire_all()
    return db.query(Application).filter(Application.resume_id == resume_id).first()


def _candidates(db: Session) -> int:
    return db.query(Candidate).count()


def test_pipeline_with_the_fake_ai_creates_a_scored_application(
    sync_db: Session,
    fake_storage: InMemoryStorage,
    celery_calls: CeleryRecorder,
    fake_ai: FakeAIService,
    events: Events,
) -> None:
    resume = _arrange(sync_db, fake_storage, make_pdf(RESUME_TEXT))
    rid = str(resume.id)

    tasks.extract_resume.apply(args=(rid,))

    sync_db.refresh(resume)
    assert resume.raw_text is not None and "Jane Doe" in resume.raw_text
    assert _application(sync_db, resume.id) is None  # not analysed yet
    assert resume.status == "processing"
    assert resume.celery_task_id is not None
    assert [(c.task, c.args, c.options) for c in celery_calls.calls] == [
        (tasks.AI_TASK_NAME, (rid,), {"queue": "ai_queue"})
    ]

    tasks.process_candidate_ai.apply(args=(rid,))

    application = _application(sync_db, resume.id)
    assert application is not None
    sync_db.refresh(resume)
    expected = fake_ai.extract_profile(resume.raw_text or "")
    assert resume.extracted == expected.model_dump(mode="json")
    assert (expected.name, expected.email) == ("Jane Doe", "jane.doe@example.com")
    assert application.missing_skills == ["Go"]
    assert application.score is not None and application.category is not None
    assert _candidates(sync_db) == 0  # no legacy candidate since the read switch (E0-19)
    assert resume.status == "done"
    assert resume.processing_step is None
    assert resume.person_id is not None
    assert events() == [
        {"resume_id": rid, "status": "extracting", "filename": "jane.pdf"},
        {
            "resume_id": rid,
            "status": "done",
            "candidate_name": "Jane Doe",
            "score": application.score,
            "category": application.category,
            "filename": "jane.pdf",
            "person_id": str(resume.person_id),
            "matched_existing": False,
        },
    ]


def test_scanned_pdf_falls_back_to_ocr_through_the_ai_service(
    sync_db: Session, fake_storage: InMemoryStorage, fake_ai: FakeAIService
) -> None:
    resume = _arrange(sync_db, fake_storage, make_pdf(""))  # a page without a text layer

    tasks.extract_resume.apply(args=(str(resume.id),))

    sync_db.refresh(resume)
    assert resume.raw_text is not None and "Scanned resume (1 page(s)" in resume.raw_text
    assert _candidates(sync_db) == 0


class _QuotaExhaustedAI(FakeAIService):
    def extract_profile(self, text: str) -> ExtractionResult:
        raise AIQuotaExceeded("429 quota")


def test_quota_exceeded_on_every_retry_leaves_the_resume_quota_exceeded(
    sync_db: Session,
    fake_storage: InMemoryStorage,
    monkeypatch: pytest.MonkeyPatch,
    events: Events,
) -> None:
    resume = _arrange(sync_db, fake_storage, make_pdf(RESUME_TEXT))
    tasks.extract_resume.apply(args=(str(resume.id),))
    monkeypatch.setattr(tasks, "get_ai_service", _QuotaExhaustedAI)

    result = tasks.process_candidate_ai.apply(args=(str(resume.id),))

    # The first run and its 3 retries all hit the quota; then it stops, without failing.
    assert result.successful()
    sync_db.refresh(resume)
    assert (resume.status, resume.error_message, resume.processing_step) == (
        "quota_exceeded",
        tasks.QUOTA_EXCEEDED_MESSAGE,
        "extract_profile",
    )
    quota_event = {
        "resume_id": str(resume.id),
        "status": "quota_exceeded",
        "error": tasks.QUOTA_EXCEEDED_MESSAGE,
    }
    assert [e for e in events() if e["status"] != "extracting"] == [quota_event] * 4


def test_text_that_is_not_a_resume_fails_without_retry(
    sync_db: Session, fake_storage: InMemoryStorage, fake_ai: FakeAIService, events: Events
) -> None:
    text = "Quarterly report. Revenue grew in every region this quarter. " * 5
    resume = _arrange(sync_db, fake_storage, make_pdf(text))
    tasks.extract_resume.apply(args=(str(resume.id),))

    result = tasks.process_candidate_ai.apply(args=(str(resume.id),))

    assert result.successful()
    sync_db.refresh(resume)
    assert resume.status == "failed"
    assert resume.error_message == "File does not appear to be a resume (no candidate info found)"
    assert events()[-1]["error"] == "Not a resume — no candidate information could be extracted"


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"name": "Jane Doe", "email": "j@example.com"}, True),
        ({"skills": ["Go"], "education": [{"degree": "BSc"}]}, True),
        ({"name": "Jane Doe"}, False),
        ({"name": " J ", "skills": []}, False),
        ({}, False),
    ],
)
def test_is_resume_content_needs_two_signals(fields: dict[str, Any], expected: bool) -> None:
    assert tasks.is_resume_content(ExtractionResult(**fields)) is expected
