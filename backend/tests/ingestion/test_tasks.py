"""The upload pipeline's Celery tasks, run in-process with ``AI_PROVIDER=fake``.

The tasks open their own synchronous sessions (``tasks.SyncSession``). Here that factory is
replaced by one bound to a single connection inside an outer transaction that is rolled back
after the test (``join_transaction_mode="create_savepoint"``, like ``db_session``), so the
tasks' commits never persist. Test data is arranged through the same connection.

Object storage, Redis and Celery dispatch are the usual in-memory fakes (``tests/conftest.py``).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import fitz
import pytest
from sqlalchemy import Connection, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.modules.ai.fake import FakeAIService
from app.modules.ai.schemas import ExtractionResult
from app.modules.ai.service import AIQuotaExceeded
from app.modules.hiring.models import Campaign, Candidate
from app.modules.ingestion import tasks
from app.modules.ingestion.events import ALL_UPDATES_CHANNEL
from app.modules.ingestion.models import Resume
from app.platform.config import settings
from tests.fakes import CeleryRecorder, FakeRedis, InMemoryStorage

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


@pytest.fixture
def sync_connection() -> Iterator[Connection]:
    engine = create_engine(settings.SYNC_DATABASE_URL, poolclass=NullPool)
    with engine.connect() as conn:
        outer = conn.begin()
        try:
            yield conn
        finally:
            outer.rollback()
    engine.dispose()


@pytest.fixture
def sync_db(sync_connection: Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    factory = sessionmaker(bind=sync_connection, join_transaction_mode="create_savepoint")
    monkeypatch.setattr(tasks, "SyncSession", factory)
    with factory() as session:
        yield session


Events = Callable[[], list[dict[str, Any]]]


@pytest.fixture
def events(fake_redis: FakeRedis) -> Iterator[Events]:
    """Call it to get every payload published on the SSE channel so far in the test."""
    pubsub = fake_redis.sync_client().pubsub()
    pubsub.subscribe(ALL_UPDATES_CHANNEL)
    received: list[dict[str, Any]] = []

    def drain() -> list[dict[str, Any]]:
        # (Not ignore_subscribe_messages: that returns None for the subscribe confirmation.)
        while (message := pubsub.get_message()) is not None:
            if message["type"] == "message":
                received.append(json.loads(message["data"]))
        return received

    yield drain
    pubsub.close()


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


def _candidate(db: Session, resume_id: uuid.UUID) -> Candidate | None:
    db.expire_all()
    return db.query(Candidate).filter(Candidate.resume_id == resume_id).first()


def test_pipeline_with_the_fake_ai_creates_a_scored_candidate(
    sync_db: Session,
    fake_storage: InMemoryStorage,
    celery_calls: CeleryRecorder,
    fake_ai: FakeAIService,
    events: Events,
) -> None:
    resume = _arrange(sync_db, fake_storage, make_pdf(RESUME_TEXT))
    rid = str(resume.id)

    tasks.extract_resume.apply(args=(rid,))

    candidate = _candidate(sync_db, resume.id)
    assert candidate is not None and candidate.raw_text is not None
    assert "Jane Doe" in candidate.raw_text
    assert candidate.score is None  # not analysed yet
    sync_db.refresh(resume)
    assert resume.status == "processing"
    assert resume.celery_task_id is not None
    assert [(c.task, c.args, c.options) for c in celery_calls.calls] == [
        (tasks.AI_TASK_NAME, (rid,), {"queue": "ai_queue"})
    ]

    tasks.process_candidate_ai.apply(args=(rid,))

    candidate = _candidate(sync_db, resume.id)
    assert candidate is not None
    expected = fake_ai.extract_profile(candidate.raw_text or "")
    assert (candidate.name, candidate.email) == ("Jane Doe", "jane.doe@example.com")
    assert candidate.skills == expected.skills
    assert candidate.missing_skills == ["Go"]
    assert candidate.score is not None and candidate.category is not None
    sync_db.refresh(resume)
    assert resume.status == "done"
    assert events() == [
        {"resume_id": rid, "status": "extracting", "filename": "jane.pdf"},
        {
            "resume_id": rid,
            "status": "done",
            "candidate_name": "Jane Doe",
            "score": candidate.score,
            "category": candidate.category,
            "filename": "jane.pdf",
        },
    ]


def test_scanned_pdf_falls_back_to_ocr_through_the_ai_service(
    sync_db: Session, fake_storage: InMemoryStorage, fake_ai: FakeAIService
) -> None:
    resume = _arrange(sync_db, fake_storage, make_pdf(""))  # a page without a text layer

    tasks.extract_resume.apply(args=(str(resume.id),))

    candidate = _candidate(sync_db, resume.id)
    assert candidate is not None
    assert candidate.raw_text is not None and "Scanned resume (1 page(s)" in candidate.raw_text


class _QuotaExhaustedAI(FakeAIService):
    def extract_profile(self, text: str) -> ExtractionResult:
        raise AIQuotaExceeded("429 quota")


def test_quota_exceeded_marks_the_resume_immediately(
    sync_db: Session,
    fake_storage: InMemoryStorage,
    monkeypatch: pytest.MonkeyPatch,
    events: Events,
) -> None:
    resume = _arrange(sync_db, fake_storage, make_pdf(RESUME_TEXT))
    tasks.extract_resume.apply(args=(str(resume.id),))
    monkeypatch.setattr(tasks, "get_ai_service", _QuotaExhaustedAI)

    tasks.process_candidate_ai.apply(args=(str(resume.id),))

    sync_db.refresh(resume)
    assert (resume.status, resume.error_message) == (
        "quota_exceeded",
        tasks.QUOTA_EXCEEDED_MESSAGE,
    )
    assert events()[-1] == {
        "resume_id": str(resume.id),
        "status": "quota_exceeded",
        "error": tasks.QUOTA_EXCEEDED_MESSAGE,
    }


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
