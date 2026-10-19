"""``app.modules.ingestion.service`` against real Postgres (the ``db_session`` transaction).

The HTTP behaviour is pinned by the characterization snapshots; these call the service directly,
including the functions only ``app.api`` uses (status counts, deleting resumes and PDFs).
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ingestion import service, tasks
from app.modules.ingestion.models import Resume
from app.platform.config import settings
from tests.factories import at, make_campaign, make_resume
from tests.fakes import CeleryRecorder, InMemoryStorage

PDF = b"%PDF-1.4\n% test resume\n"


@dataclass
class FakeUpload:
    """The two things the service needs from FastAPI's ``UploadFile``."""

    filename: str | None
    content: bytes

    async def read(self, size: int = -1) -> bytes:
        return self.content


async def _count(db: AsyncSession) -> int:
    return (await db.execute(select(func.count()).select_from(Resume))).scalar_one()


# ── Status counts ─────────────────────────────────────────────
async def test_resume_status_counts_per_campaign_and_overall(db_session: AsyncSession) -> None:
    campaign = await make_campaign(db_session)
    other = await make_campaign(db_session, title="Other")
    for status in ("done", "done", "failed", "pending"):
        await make_resume(db_session, campaign, status=status)
    await make_resume(db_session, other, status="quota_exceeded")

    assert await service.resume_status_counts(db_session, campaign.id) == {
        "done": 2,
        "failed": 1,
        "pending": 1,
    }
    assert await service.resume_status_counts(db_session) == {
        "done": 2,
        "failed": 1,
        "pending": 1,
        "quota_exceeded": 1,
    }
    assert await service.resume_status_counts(db_session, uuid.uuid4()) == {}


# ── Delete (E0-20: rows in the caller's transaction, PDFs after its commit) ──
async def test_delete_campaign_resumes_deletes_only_that_campaigns_and_returns_their_keys(
    db_session: AsyncSession,
) -> None:
    campaign = await make_campaign(db_session)
    other = await make_campaign(db_session, title="Other")
    doomed = [await make_resume(db_session, campaign, original_filename=f"{n}.pdf") for n in "ab"]
    kept = await make_resume(db_session, other)

    files = await service.delete_campaign_resumes(db_session, campaign.id)

    assert sorted(files.keys) == sorted(r.storage_path for r in doomed)
    remaining = (await db_session.execute(select(Resume.id))).scalars().all()
    assert remaining == [kept.id]


async def test_resume_deletes_do_not_commit(db_session: AsyncSession) -> None:
    resume = await make_resume(db_session)
    await db_session.commit()  # (releases the test's savepoint; the outer transaction stays)

    await service.delete_campaign_resumes(db_session, resume.campaign_id)
    await db_session.rollback()

    assert await _count(db_session) == 1


async def test_delete_stored_files_deletes_the_objects(fake_storage: InMemoryStorage) -> None:
    for key in ("campaigns/c/resumes/1/a.pdf", "campaigns/c/resumes/2/b.pdf"):
        fake_storage.upload_file(PDF, key)

    failed = await service.delete_stored_files(
        service.StoredFiles(("campaigns/c/resumes/1/a.pdf", "campaigns/c/resumes/9/gone.pdf"))
    )

    assert failed == []  # a missing object is not an error (S3 semantics)
    assert list(fake_storage.objects) == ["campaigns/c/resumes/2/b.pdf"]


async def test_delete_stored_files_logs_a_failure_and_carries_on(
    fake_storage: InMemoryStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in ("k/fails.pdf", "k/ok.pdf"):
        fake_storage.upload_file(PDF, key)

    def delete_file(key: str) -> None:
        if key == "k/fails.pdf":
            raise ConnectionError("storage is down")
        fake_storage.delete_file(key)

    errors: list[tuple[str, dict[str, Any]]] = []

    class Logger:
        def error(self, event: str, **fields: Any) -> None:
            errors.append((event, fields))

    monkeypatch.setattr(service.storage, "delete_file", delete_file)
    monkeypatch.setattr(service, "logger", Logger())
    failed = await service.delete_stored_files(service.StoredFiles(("k/fails.pdf", "k/ok.pdf")))

    assert failed == ["k/fails.pdf"]
    assert list(fake_storage.objects) == ["k/fails.pdf"]  # left as an orphan
    assert errors == [
        ("stored_file_delete_failed", {"key": "k/fails.pdf", "error": "storage is down"})
    ]


# ── Upload ────────────────────────────────────────────────────
async def test_upload_stores_accepted_files_and_queues_extraction(
    db_session: AsyncSession, celery_calls: CeleryRecorder, fake_storage: InMemoryStorage
) -> None:
    campaign = await make_campaign(db_session)

    result = await service.upload_resumes(
        db_session,
        campaign.id,
        [FakeUpload("cv.pdf", PDF), FakeUpload("notes.txt", PDF), FakeUpload("bad.pdf", b"x")],
    )

    assert (result.total_files, result.accepted_files) == (3, 1)
    assert result.rejected_files == [
        {"filename": "notes.txt", "reason": "Only PDF files are accepted"},
        {"filename": "bad.pdf", "reason": "Invalid PDF file"},
    ]
    [resume_id] = result.resume_ids
    resume = await db_session.get(Resume, resume_id)
    assert resume is not None
    assert (resume.status, resume.file_hash) == ("pending", hashlib.sha256(PDF).hexdigest())
    assert fake_storage.objects[resume.storage_path].body == PDF
    assert [(c.task, c.args, c.options) for c in celery_calls.calls] == [
        (tasks.EXTRACT_TASK_NAME, (str(resume_id),), {"queue": "extract_queue"})
    ]


async def test_upload_unknown_campaign(db_session: AsyncSession) -> None:
    with pytest.raises(service.NotFoundError, match="^Campaign not found$"):
        await service.upload_resumes(db_session, uuid.uuid4(), [FakeUpload("cv.pdf", PDF)])


async def test_upload_too_many_files(db_session: AsyncSession) -> None:
    campaign = await make_campaign(db_session)
    files = [FakeUpload(f"{i}.pdf", PDF) for i in range(settings.MAX_FILES_PER_BATCH + 1)]

    with pytest.raises(service.InvalidInputError, match="files per upload batch"):
        await service.upload_resumes(db_session, campaign.id, files)

    assert await _count(db_session) == 0


@pytest.mark.parametrize(
    ("existing_status", "accepted"),
    [("done", False), ("processing", False), ("failed", True), ("quota_exceeded", True)],
)
async def test_upload_duplicate_of_existing_resume(
    db_session: AsyncSession, existing_status: str, accepted: bool
) -> None:
    campaign = await make_campaign(db_session)
    await make_resume(
        db_session, campaign, file_hash=hashlib.sha256(PDF).hexdigest(), status=existing_status
    )

    result = await service.upload_resumes(db_session, campaign.id, [FakeUpload("cv.pdf", PDF)])

    assert result.accepted_files == int(accepted)


async def test_upload_file_without_a_name_is_an_error(db_session: AsyncSession) -> None:
    campaign = await make_campaign(db_session)

    with pytest.raises(ValueError, match="no file name"):
        await service.upload_resumes(db_session, campaign.id, [FakeUpload(None, PDF)])


# ── Resumes ───────────────────────────────────────────────────
async def test_list_and_get_resumes(db_session: AsyncSession) -> None:
    campaign = await make_campaign(db_session)
    older = await make_resume(db_session, campaign, uploaded_at=at(0))
    newer = await make_resume(db_session, campaign, uploaded_at=at(1))

    assert [r.id for r in await service.list_resumes(db_session, campaign.id)] == [
        newer.id,
        older.id,
    ]
    assert (await service.get_resume(db_session, older.id)).id == older.id
    with pytest.raises(service.NotFoundError, match="^Resume not found$"):
        await service.get_resume(db_session, uuid.uuid4())


async def test_retry_failed_resets_and_requeues_failed_and_quota_exceeded(
    db_session: AsyncSession, celery_calls: CeleryRecorder
) -> None:
    campaign = await make_campaign(db_session)
    failed = await make_resume(db_session, campaign, status="failed", retry_count=3)
    quota = await make_resume(db_session, campaign, status="quota_exceeded", error_message="q")
    await make_resume(db_session, campaign, status="done")

    assert await service.retry_failed(db_session, campaign.id) == 2

    for resume in (failed, quota):
        await db_session.refresh(resume)
        assert (resume.status, resume.retry_count, resume.error_message) == ("pending", 0, None)
    assert sorted(c.args[0] for c in celery_calls.for_task(tasks.EXTRACT_TASK_NAME)) == sorted(
        [str(failed.id), str(quota.id)]
    )
    assert await service.retry_failed(db_session, uuid.uuid4()) == 0


@pytest.mark.parametrize(
    ("processing_step", "task", "queue"),
    [
        (None, tasks.EXTRACT_TASK_NAME, "extract_queue"),  # from before processing_step existed
        ("extract_text", tasks.EXTRACT_TASK_NAME, "extract_queue"),
        ("extract_profile", tasks.AI_TASK_NAME, "ai_queue"),
        ("score", tasks.AI_TASK_NAME, "ai_queue"),
        ("resolve_person", tasks.AI_TASK_NAME, "ai_queue"),
        ("save", tasks.AI_TASK_NAME, "ai_queue"),
    ],
)
async def test_retry_failed_resumes_from_the_failed_step(
    db_session: AsyncSession,
    celery_calls: CeleryRecorder,
    processing_step: str | None,
    task: str,
    queue: str,
) -> None:
    # Failed during AI analysis: the text is stored, so it goes straight back to the AI task,
    # which skips the steps already done (spec §5.5).
    campaign = await make_campaign(db_session)
    resume = await make_resume(
        db_session, campaign, status="failed", processing_step=processing_step
    )

    assert await service.retry_failed(db_session, campaign.id) == 1

    assert [(c.task, c.args, c.options) for c in celery_calls.calls] == [
        (task, (str(resume.id),), {"queue": queue})
    ]
