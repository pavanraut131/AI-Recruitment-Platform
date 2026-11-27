"""Celery tasks carry their agency (EPIC-02 spec §6.3, §11.2 ``test_celery_tenant``): the message
names it, the task's sessions (the failure handlers' too) are bound to it, its events go to that
agency's channel, and its log lines name it. Under row-level security (E2-04) a task run with
another agency's id for a resume finds nothing and changes nothing, and a message without
``agency_id`` is rejected before it touches anything.

Another agency's rows are arranged through a session bound to it (``session_of``) on the test's
rolled-back connection: the default agency's ``sync_db`` may not write them (``WITH CHECK``),
and does not see them (``USING``). ``reset_stuck``, which spans agencies as the owner role, is
``test_operator_commands.py``'s.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import structlog
from billiard.einfo import ExceptionInfo
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.ai.fake import FakeAIService
from app.modules.hiring.models import Application, Campaign, Client
from app.modules.identity.models import Agency
from app.modules.ingestion import tasks
from app.modules.ingestion.events import campaign_channel
from app.modules.ingestion.models import Resume
from app.platform.celery_app import MissingAgency, TenantTask
from app.platform.tenancy import agency_id_of, bind
from tests.default_agency import DEFAULT_AGENCY_ID, DEFAULT_CLIENT_ID
from tests.fakes import CeleryRecorder, FakeRedis, InMemoryStorage
from tests.ingestion.test_tasks import RESUME_TEXT, make_pdf


@pytest.fixture
def session_of(sync_db: Session) -> Iterator[Any]:
    """``session_of(agency_id)``: a session on the test's connection (``tasks.SyncSession``,
    patched by ``sync_db``), bound to that agency."""
    opened: list[Session] = []

    def open_session(agency_id: uuid.UUID) -> Session:
        session = tasks.SyncSession(expire_on_commit=False)
        bind(session, agency_id)
        opened.append(session)
        return session

    yield open_session
    for session in opened:
        session.close()


def _agency(sync_db: Session, session_of: Any) -> tuple[uuid.UUID, uuid.UUID]:
    """A new agency (``agencies`` is global) and its client (written as that agency)."""
    agency = Agency(id=uuid.uuid4(), name="Other agency", slug=f"other-{uuid.uuid4().hex[:8]}")
    sync_db.add(agency)
    sync_db.commit()
    db = session_of(agency.id)
    client = Client(id=uuid.uuid4(), agency_id=agency.id, name="Other client")
    db.add(client)
    db.commit()
    return agency.id, client.id


def _resume(
    db: Session,
    storage: InMemoryStorage,
    client_id: uuid.UUID,
    **fields: Any,
) -> Resume:
    """A campaign and a resume (with its PDF) of ``db``'s agency."""
    agency_id = agency_id_of(db)
    campaign = Campaign(
        title="Backend",
        role="Backend Engineer",
        required_skills=["Python"],
        agency_id=agency_id,
        client_id=client_id,
    )
    db.add(campaign)
    db.flush()
    resume_id = uuid.uuid4()
    key = f"agencies/{agency_id}/campaigns/{campaign.id}/resumes/{resume_id}.pdf"
    resume = Resume(
        id=resume_id,
        agency_id=agency_id,
        campaign_id=campaign.id,
        original_filename="jane.pdf",
        storage_path=key,
        file_size_bytes=100,
        status=fields.pop("status", "pending"),
        **fields,
    )
    db.add(resume)
    db.commit()
    storage.upload_file(make_pdf(RESUME_TEXT), key)
    return resume


def _status(db: Session, resume_id: uuid.UUID) -> str | None:
    db.expire_all()
    status: str | None = db.scalar(select(Resume.status).where(Resume.id == resume_id))
    return status


def _channels(fake_redis: FakeRedis) -> Any:
    pubsub = fake_redis.sync_client().pubsub()
    pubsub.psubscribe(campaign_channel("*", "*"))

    def drain() -> list[str]:
        seen = []
        while (message := pubsub.get_message()) is not None:
            if message["type"] == "pmessage":
                seen.append(message["channel"].decode())
        return seen

    return drain


# ── The agency travels with the task ──────────────────────────
def test_the_pipeline_runs_in_the_messages_agency(
    sync_db: Session,
    session_of: Any,
    fake_storage: InMemoryStorage,
    fake_redis: FakeRedis,
    celery_calls: CeleryRecorder,
    fake_ai: FakeAIService,
) -> None:
    agency_id, client_id = _agency(sync_db, session_of)
    theirs = session_of(agency_id)
    resume = _resume(theirs, fake_storage, client_id)
    drain = _channels(fake_redis)
    kwargs = {"agency_id": str(agency_id)}

    assert tasks.extract_resume.apply(args=(str(resume.id),), kwargs=kwargs).successful()
    [queued] = celery_calls.for_task(tasks.AI_TASK_NAME)
    assert queued.kwargs == kwargs  # the next step carries it on
    assert tasks.process_candidate_ai.apply(args=(str(resume.id),), kwargs=kwargs).successful()

    assert _status(theirs, resume.id) == "done"
    application = theirs.query(Application).filter(Application.resume_id == resume.id).one()
    assert application.agency_id == agency_id
    channel = f"agency:{agency_id}:campaign:{resume.campaign_id}:events"
    assert drain() == [channel, channel]  # extracting, done
    # The default agency's session does not see any of it.
    assert _status(sync_db, resume.id) is None
    assert sync_db.scalar(select(func.count()).select_from(Application)) == 0


def test_a_task_of_another_agency_finds_nothing_and_changes_nothing(
    sync_db: Session,
    session_of: Any,
    fake_storage: InMemoryStorage,
    fake_redis: FakeRedis,
    celery_calls: CeleryRecorder,
    fake_ai: FakeAIService,
) -> None:
    other_agency, _ = _agency(sync_db, session_of)
    resume = _resume(sync_db, fake_storage, DEFAULT_CLIENT_ID)
    drain = _channels(fake_redis)
    kwargs = {"agency_id": str(other_agency)}

    tasks.extract_resume.apply(args=(str(resume.id),), kwargs=kwargs)
    tasks.process_candidate_ai.apply(args=(str(resume.id),), kwargs=kwargs)

    assert _status(sync_db, resume.id) == "pending"
    assert sync_db.query(Application).count() == 0
    assert (drain(), celery_calls.calls) == ([], [])


@pytest.mark.parametrize("task", [tasks.extract_resume, tasks.process_candidate_ai])
def test_a_message_without_an_agency_is_rejected_and_writes_nothing(
    sync_db: Session,
    fake_storage: InMemoryStorage,
    fake_redis: FakeRedis,
    celery_calls: CeleryRecorder,
    task: Any,
) -> None:
    """Queued by code older than E2-03: rejected (not run, not re-queued) since E2-04."""
    resume = _resume(sync_db, fake_storage, DEFAULT_CLIENT_ID, status="processing")
    drain = _channels(fake_redis)

    with structlog.testing.capture_logs() as logs:
        result = task.apply(args=(str(resume.id),))

    assert result.state == "REJECTED"
    [logged] = [e for e in logs if e["event"] == "task_without_agency_id"]
    assert (logged["log_level"], logged["task"]) == ("error", task.name)
    assert _status(sync_db, resume.id) == "processing"  # untouched, not even marked failed
    assert (drain(), celery_calls.calls) == ([], [])


def test_an_invalid_agency_fails_the_task_before_it_touches_anything(
    sync_db: Session, fake_storage: InMemoryStorage
) -> None:
    resume = _resume(sync_db, fake_storage, DEFAULT_CLIENT_ID)

    result = tasks.extract_resume.apply(args=(str(resume.id),), kwargs={"agency_id": "nope"})

    assert result.failed()
    assert _status(sync_db, resume.id) == "pending"


def test_tasks_log_their_agency() -> None:
    task = tasks.extract_resume
    agency_id = uuid.uuid4()
    task.before_start("task-id", ("resume",), {"agency_id": str(agency_id)})
    assert structlog.contextvars.get_contextvars()["agency_id"] == str(agency_id)
    task.after_return("SUCCESS", None, "task-id", ("resume",), {}, None)  # type: ignore[arg-type]
    assert "agency_id" not in structlog.contextvars.get_contextvars()


def test_tenant_sessions_are_bound_to_the_given_agency(sync_db: Session) -> None:
    agency_id = uuid.uuid4()
    with tasks.process_candidate_ai.tenant_session(agency_id) as session:
        assert agency_id_of(session) == agency_id
    assert TenantTask.agency_from(str(agency_id)) == agency_id
    with pytest.raises(MissingAgency):
        TenantTask.agency_from(None)
    with pytest.raises(ValueError):
        TenantTask.agency_from("not-a-uuid")


# ── Failure handlers use the task's agency ────────────────────
def _einfo() -> ExceptionInfo:
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        return ExceptionInfo()


@pytest.mark.parametrize("task", [tasks.extract_resume, tasks.process_candidate_ai])
def test_on_failure_writes_under_the_tasks_agency(
    sync_db: Session,
    session_of: Any,
    fake_storage: InMemoryStorage,
    fake_redis: FakeRedis,
    task: Any,
) -> None:
    agency_id, client_id = _agency(sync_db, session_of)
    theirs = session_of(agency_id)
    resume = _resume(theirs, fake_storage, client_id, retry_count=2)
    drain = _channels(fake_redis)
    args = (str(resume.id),)

    # A message naming another agency (the default) finds nothing to mark.
    task.on_failure(
        RuntimeError("boom"), "t", args, {"agency_id": str(DEFAULT_AGENCY_ID)}, _einfo()
    )
    assert _status(theirs, resume.id) == "pending"
    assert drain() == []

    task.on_failure(RuntimeError("boom"), "t", args, {"agency_id": str(agency_id)}, _einfo())
    assert _status(theirs, resume.id) == "failed"
    assert drain() == [f"agency:{agency_id}:campaign:{resume.campaign_id}:events"]


@pytest.mark.parametrize("agency", [{"agency_id": "x"}, {}])
def test_on_failure_without_a_valid_agency_writes_nothing(
    sync_db: Session, fake_storage: InMemoryStorage, agency: dict[str, str]
) -> None:
    resume = _resume(sync_db, fake_storage, DEFAULT_CLIENT_ID, retry_count=2)
    tasks.extract_resume.on_failure(RuntimeError("boom"), "t", (str(resume.id),), agency, _einfo())
    assert _status(sync_db, resume.id) == "pending"


def test_a_quota_error_is_recorded_under_the_tasks_agency(
    sync_db: Session,
    session_of: Any,
    fake_storage: InMemoryStorage,
    fake_redis: FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
    fake_ai: FakeAIService,
) -> None:
    from app.modules.ai.service import AIQuotaExceeded

    agency_id, client_id = _agency(sync_db, session_of)
    theirs = session_of(agency_id)
    resume = _resume(theirs, fake_storage, client_id, status="processing", raw_text=RESUME_TEXT)
    drain = _channels(fake_redis)

    def quota(*_: Any, **__: Any) -> Any:
        raise AIQuotaExceeded("quota", retry_after=1.0)

    monkeypatch.setattr(FakeAIService, "extract_profile", quota)
    tasks.process_candidate_ai.apply(args=(str(resume.id),), kwargs={"agency_id": str(agency_id)})

    assert _status(theirs, resume.id) == "quota_exceeded"
    assert set(drain()) == {f"agency:{agency_id}:campaign:{resume.campaign_id}:events"}
