"""Celery tasks carry their agency (EPIC-02 spec §6.3): the message names it, the task's sessions
(the failure handlers' too) are bound to it, its events go to that agency's channel, and its log
lines name it. A message without ``agency_id`` (queued before E2-03) runs for the default agency
and is logged, until E2-04 rejects it. ``reset_stuck`` re-queues each resume with its own
agency."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import structlog
from billiard.einfo import ExceptionInfo
from sqlalchemy.orm import Session

from app.modules.ai.fake import FakeAIService
from app.modules.hiring.models import Application, Campaign, Client
from app.modules.identity.models import Agency
from app.modules.ingestion import reset_stuck, tasks
from app.modules.ingestion.events import campaign_channel
from app.modules.ingestion.models import Resume
from app.platform.celery_app import TenantTask
from app.platform.tenancy import DEFAULT_AGENCY_ID, DEFAULT_CLIENT_ID, agency_id_of
from tests.fakes import CeleryRecorder, FakeRedis, InMemoryStorage
from tests.ingestion.test_tasks import RESUME_TEXT, make_pdf


def _agency(db: Session) -> tuple[uuid.UUID, uuid.UUID]:
    agency = Agency(id=uuid.uuid4(), name="Other agency", slug=f"other-{uuid.uuid4().hex[:8]}")
    db.add(agency)
    db.flush()
    client = Client(id=uuid.uuid4(), agency_id=agency.id, name="Other client")
    db.add(client)
    db.flush()
    return agency.id, client.id


def _resume(
    db: Session,
    storage: InMemoryStorage,
    agency_id: uuid.UUID | None,
    client_id: uuid.UUID | None,
    **fields: Any,
) -> Resume:
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
    fake_storage: InMemoryStorage,
    fake_redis: FakeRedis,
    celery_calls: CeleryRecorder,
    fake_ai: FakeAIService,
) -> None:
    agency_id, client_id = _agency(sync_db)
    resume = _resume(sync_db, fake_storage, agency_id, client_id)
    drain = _channels(fake_redis)
    kwargs = {"agency_id": str(agency_id)}

    assert tasks.extract_resume.apply(args=(str(resume.id),), kwargs=kwargs).successful()
    [queued] = celery_calls.for_task(tasks.AI_TASK_NAME)
    assert queued.kwargs == kwargs  # the next step carries it on
    assert tasks.process_candidate_ai.apply(args=(str(resume.id),), kwargs=kwargs).successful()

    sync_db.expire_all()
    stored = sync_db.get_one(Resume, resume.id)
    application = sync_db.query(Application).filter(Application.resume_id == resume.id).one()
    assert (stored.status, application.agency_id) == ("done", agency_id)
    channel = f"agency:{agency_id}:campaign:{resume.campaign_id}:events"
    assert drain() == [channel, channel]  # extracting, done


def test_a_task_of_another_agency_finds_nothing_and_changes_nothing(
    sync_db: Session,
    fake_storage: InMemoryStorage,
    fake_redis: FakeRedis,
    celery_calls: CeleryRecorder,
    fake_ai: FakeAIService,
) -> None:
    other_agency, _ = _agency(sync_db)
    resume = _resume(sync_db, fake_storage, DEFAULT_AGENCY_ID, DEFAULT_CLIENT_ID)
    drain = _channels(fake_redis)
    kwargs = {"agency_id": str(other_agency)}

    tasks.extract_resume.apply(args=(str(resume.id),), kwargs=kwargs)
    tasks.process_candidate_ai.apply(args=(str(resume.id),), kwargs=kwargs)

    sync_db.expire_all()
    assert sync_db.get_one(Resume, resume.id).status == "pending"
    assert sync_db.query(Application).count() == 0
    assert (drain(), celery_calls.calls) == ([], [])


def test_a_message_without_an_agency_runs_for_the_default_agency_and_is_logged(
    sync_db: Session,
    fake_storage: InMemoryStorage,
    celery_calls: CeleryRecorder,
) -> None:
    """Queued before E2-03 (until E2-04, which rejects such messages)."""
    resume = _resume(sync_db, fake_storage, DEFAULT_AGENCY_ID, DEFAULT_CLIENT_ID)

    with structlog.testing.capture_logs() as logs:
        assert tasks.extract_resume.apply(args=(str(resume.id),)).successful()

    assert {
        "event": "task_without_agency_id",
        "fallback_agency_id": str(DEFAULT_AGENCY_ID),
        "task": tasks.EXTRACT_TASK_NAME,
    }.items() <= next(e for e in logs if e["event"] == "task_without_agency_id").items()
    [queued] = celery_calls.for_task(tasks.AI_TASK_NAME)
    assert queued.kwargs == {"agency_id": str(DEFAULT_AGENCY_ID)}  # from now on it carries it


def test_an_invalid_agency_fails_the_task_before_it_touches_anything(
    sync_db: Session, fake_storage: InMemoryStorage
) -> None:
    resume = _resume(sync_db, fake_storage, DEFAULT_AGENCY_ID, DEFAULT_CLIENT_ID)

    result = tasks.extract_resume.apply(args=(str(resume.id),), kwargs={"agency_id": "nope"})

    assert result.failed()
    sync_db.expire_all()
    assert sync_db.get_one(Resume, resume.id).status == "pending"


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
    assert TenantTask.agency_from(None) == DEFAULT_AGENCY_ID
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
    sync_db: Session, fake_storage: InMemoryStorage, fake_redis: FakeRedis, task: Any
) -> None:
    agency_id, client_id = _agency(sync_db)
    resume = _resume(sync_db, fake_storage, agency_id, client_id, retry_count=2)
    drain = _channels(fake_redis)
    args = (str(resume.id),)

    # A message naming another agency (the default) finds nothing to mark.
    task.on_failure(
        RuntimeError("boom"), "t", args, {"agency_id": str(DEFAULT_AGENCY_ID)}, _einfo()
    )
    sync_db.expire_all()
    assert sync_db.get_one(Resume, resume.id).status == "pending"
    assert drain() == []

    task.on_failure(RuntimeError("boom"), "t", args, {"agency_id": str(agency_id)}, _einfo())
    sync_db.expire_all()
    assert sync_db.get_one(Resume, resume.id).status == "failed"
    assert drain() == [f"agency:{agency_id}:campaign:{resume.campaign_id}:events"]


def test_on_failure_with_an_invalid_agency_writes_nothing(
    sync_db: Session, fake_storage: InMemoryStorage
) -> None:
    resume = _resume(sync_db, fake_storage, DEFAULT_AGENCY_ID, DEFAULT_CLIENT_ID, retry_count=2)
    tasks.extract_resume.on_failure(
        RuntimeError("boom"), "t", (str(resume.id),), {"agency_id": "x"}, _einfo()
    )
    sync_db.expire_all()
    assert sync_db.get_one(Resume, resume.id).status == "pending"


def test_a_quota_error_is_recorded_under_the_tasks_agency(
    sync_db: Session,
    fake_storage: InMemoryStorage,
    fake_redis: FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
    fake_ai: FakeAIService,
) -> None:
    from app.modules.ai.service import AIQuotaExceeded

    agency_id, client_id = _agency(sync_db)
    resume = _resume(
        sync_db, fake_storage, agency_id, client_id, status="processing", raw_text=RESUME_TEXT
    )
    drain = _channels(fake_redis)

    def quota(*_: Any, **__: Any) -> Any:
        raise AIQuotaExceeded("quota", retry_after=1.0)

    monkeypatch.setattr(FakeAIService, "extract_profile", quota)
    tasks.process_candidate_ai.apply(args=(str(resume.id),), kwargs={"agency_id": str(agency_id)})

    sync_db.expire_all()
    assert sync_db.get_one(Resume, resume.id).status == "quota_exceeded"
    assert set(drain()) == {f"agency:{agency_id}:campaign:{resume.campaign_id}:events"}


# ── reset_stuck spans agencies, explicitly ────────────────────
def test_reset_stuck_requeues_each_resume_with_its_own_agency(
    sync_db: Session, fake_storage: InMemoryStorage, celery_calls: CeleryRecorder
) -> None:
    agency_id, client_id = _agency(sync_db)
    mine = _resume(sync_db, fake_storage, agency_id, client_id, status="failed")
    default = _resume(sync_db, fake_storage, DEFAULT_AGENCY_ID, DEFAULT_CLIENT_ID, status="failed")
    legacy = _resume(sync_db, fake_storage, None, None, status="processing")  # before E2-02

    reset_stuck.main()

    queued = {c.args[0]: c.kwargs for c in celery_calls.for_task(tasks.EXTRACT_TASK_NAME)}
    assert queued == {
        str(mine.id): {"agency_id": str(agency_id)},
        str(default.id): {"agency_id": str(DEFAULT_AGENCY_ID)},
        str(legacy.id): {"agency_id": str(DEFAULT_AGENCY_ID)},
    }
