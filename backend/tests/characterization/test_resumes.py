"""Characterization: resume upload, listing, detail and retry-failed.

For uploads the observable behaviour is more than the HTTP response: also recorded are the Celery
dispatches (``celery_calls``), the objects written to storage (``fake_storage``) and the resume
rows created.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from syrupy.assertion import SnapshotAssertion

from app.core.config import settings
from app.models.campaign import Campaign
from app.models.resume import Resume
from tests.characterization.snapshot_utils import (
    celery_calls_data,
    response_data,
    stored_objects_data,
)
from tests.factories import at, make_campaign, make_resume
from tests.fakes import CeleryRecorder, InMemoryStorage

API = "/api/v1"
UNKNOWN_ID = "00000000-0000-0000-0000-000000000000"
LIMIT_BYTES = settings.MAX_FILE_SIZE_MB * 1024 * 1024


def pdf(label: str, size: int | None = None) -> bytes:
    """A minimal byte string that passes the upload's PDF checks (``%PDF`` magic bytes)."""
    body = f"%PDF-1.4\n% {label}\n".encode()
    if size is not None:
        # Pad with a byte that cannot occur in httpx's hex multipart boundary: python-multipart
        # scans data made of boundary characters byte by byte (~100x slower).
        body = body.ljust(size, b"x")
    return body


Files = list[tuple[str, tuple[str, bytes, str]]]


def files(*items: tuple[str, bytes]) -> Files:
    return [("files", (name, content, "application/pdf")) for name, content in items]


async def _resume_rows(db: AsyncSession) -> list[dict[str, Any]]:
    db.expire_all()
    result = await db.execute(
        select(Resume).order_by(Resume.original_filename, Resume.status, Resume.storage_path)
    )
    return [
        {
            "id": r.id,
            "campaign_id": r.campaign_id,
            "original_filename": r.original_filename,
            "storage_path": r.storage_path,
            "file_hash": r.file_hash,
            "file_size_bytes": r.file_size_bytes,
            "status": r.status,
            "retry_count": r.retry_count,
            "error_message": r.error_message,
            "celery_task_id": r.celery_task_id,
        }
        for r in result.scalars().all()
    ]


async def _upload(
    client: AsyncClient,
    db: AsyncSession,
    celery_calls: CeleryRecorder,
    storage: InMemoryStorage,
    campaign: Campaign,
    upload: Files,
) -> dict[str, Any]:
    resp = await client.post(f"{API}/campaigns/{campaign.id}/upload", files=upload)
    return {
        "response": response_data(resp),
        "celery_calls": celery_calls_data(celery_calls),
        "stored_objects": stored_objects_data(storage),
        "resume_rows": await _resume_rows(db),
    }


# ── POST /campaigns/{id}/upload ───────────────────────────────
async def test_upload_accepted(
    client: AsyncClient,
    db_session: AsyncSession,
    celery_calls: CeleryRecorder,
    fake_storage: InMemoryStorage,
    snapshot: SnapshotAssertion,
) -> None:
    campaign = await make_campaign(db_session)
    upload = files(
        ("alice.pdf", pdf("alice")),
        ("BOB_UPPERCASE.PDF", pdf("bob")),  # extension check is case-insensitive
        ("name with spaces.pdf", pdf("carol")),
    )
    result = await _upload(client, db_session, celery_calls, fake_storage, campaign, upload)
    assert result == snapshot


async def test_upload_rejects_invalid_files(
    client: AsyncClient,
    db_session: AsyncSession,
    celery_calls: CeleryRecorder,
    fake_storage: InMemoryStorage,
    snapshot: SnapshotAssertion,
) -> None:
    campaign = await make_campaign(db_session)
    upload = files(
        ("resume.docx", b"PK\x03\x04 not a pdf"),
        ("notes.txt", pdf("pdf bytes, wrong extension")),
        ("fake.pdf", b"<html>not a pdf</html>"),
        ("empty.pdf", b""),
        ("valid.pdf", pdf("valid")),
    )
    result = await _upload(client, db_session, celery_calls, fake_storage, campaign, upload)
    assert result == snapshot


async def test_upload_only_invalid_files(
    client: AsyncClient,
    db_session: AsyncSession,
    celery_calls: CeleryRecorder,
    fake_storage: InMemoryStorage,
    snapshot: SnapshotAssertion,
) -> None:
    # Still 202, with nothing accepted.
    campaign = await make_campaign(db_session)
    upload = files(("resume.docx", b"PK\x03\x04"))
    result = await _upload(client, db_session, celery_calls, fake_storage, campaign, upload)
    assert result == snapshot


async def test_upload_size_limit(
    client: AsyncClient,
    db_session: AsyncSession,
    celery_calls: CeleryRecorder,
    fake_storage: InMemoryStorage,
    snapshot: SnapshotAssertion,
) -> None:
    # Exactly MAX_FILE_SIZE_MB is accepted; one byte more is rejected.
    campaign = await make_campaign(db_session)
    upload = files(
        ("at_limit.pdf", pdf("at limit", LIMIT_BYTES)),
        ("too_large.pdf", pdf("too large", LIMIT_BYTES + 1)),
    )
    result = await _upload(client, db_session, celery_calls, fake_storage, campaign, upload)
    assert result == snapshot


async def test_upload_too_many_files(
    client: AsyncClient,
    db_session: AsyncSession,
    celery_calls: CeleryRecorder,
    fake_storage: InMemoryStorage,
    snapshot: SnapshotAssertion,
) -> None:
    campaign = await make_campaign(db_session)
    count = settings.MAX_FILES_PER_BATCH + 1
    upload = files(*[(f"r{i:02d}.pdf", pdf(str(i))) for i in range(count)])
    result = await _upload(client, db_session, celery_calls, fake_storage, campaign, upload)
    assert result == snapshot


async def test_upload_duplicates(
    client: AsyncClient,
    db_session: AsyncSession,
    celery_calls: CeleryRecorder,
    fake_storage: InMemoryStorage,
    snapshot: SnapshotAssertion,
) -> None:
    """Duplicate detection is by SHA-256 of the content, per campaign, against the newest row."""
    campaign = await make_campaign(db_session)
    other = await make_campaign(db_session, title="Other campaign")
    contents = {status: pdf(status) for status in ("done", "pending", "processing", "failed")}
    contents["quota_exceeded"] = pdf("quota_exceeded")
    contents["failed_then_done"] = pdf("failed then done")
    contents["other_campaign"] = pdf("other campaign")
    for status in ("done", "pending", "processing", "failed", "quota_exceeded"):
        await make_resume(
            db_session,
            campaign,
            original_filename=f"existing_{status}.pdf",
            file_hash=hashlib.sha256(contents[status]).hexdigest(),
            status=status,
        )
    # Older failed attempt, newer successful one: the newest row decides.
    for minute, status in ((0, "failed"), (1, "done")):
        await make_resume(
            db_session,
            campaign,
            original_filename=f"existing_failed_then_done_{status}.pdf",
            file_hash=hashlib.sha256(contents["failed_then_done"]).hexdigest(),
            status=status,
            uploaded_at=at(minute),
        )
    await make_resume(
        db_session,
        other,
        original_filename="existing_other_campaign.pdf",
        file_hash=hashlib.sha256(contents["other_campaign"]).hexdigest(),
        status="done",
    )
    upload = files(
        ("again_done.pdf", contents["done"]),
        ("again_pending.pdf", contents["pending"]),
        ("again_processing.pdf", contents["processing"]),
        ("again_failed.pdf", contents["failed"]),
        ("again_quota_exceeded.pdf", contents["quota_exceeded"]),
        ("again_failed_then_done.pdf", contents["failed_then_done"]),
        ("again_other_campaign.pdf", contents["other_campaign"]),
        ("twice_in_batch_1.pdf", pdf("twice")),
        ("twice_in_batch_2.pdf", pdf("twice")),
    )
    result = await _upload(client, db_session, celery_calls, fake_storage, campaign, upload)
    assert result == snapshot


async def test_upload_unknown_campaign(
    client: AsyncClient,
    celery_calls: CeleryRecorder,
    fake_storage: InMemoryStorage,
    snapshot: SnapshotAssertion,
) -> None:
    resp = await client.post(
        f"{API}/campaigns/{UNKNOWN_ID}/upload", files=files(("a.pdf", pdf("a")))
    )
    assert {
        "response": response_data(resp),
        "celery_calls": celery_calls_data(celery_calls),
        "stored_objects": stored_objects_data(fake_storage),
    } == snapshot


async def test_upload_unknown_campaign_is_checked_before_file_count(
    client: AsyncClient, snapshot: SnapshotAssertion
) -> None:
    upload = files(*[(f"r{i}.pdf", pdf(str(i))) for i in range(settings.MAX_FILES_PER_BATCH + 1)])
    resp = await client.post(f"{API}/campaigns/{UNKNOWN_ID}/upload", files=upload)
    assert response_data(resp) == snapshot


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({}, id="no-body"),
        pytest.param({"data": {"files": "not a file"}}, id="files-as-form-field"),
        pytest.param(
            {"files": [("resume", ("a.pdf", b"%PDF", "application/pdf"))]}, id="wrong-field-name"
        ),
    ],
)
async def test_upload_validation_error(
    client: AsyncClient,
    db_session: AsyncSession,
    snapshot: SnapshotAssertion,
    kwargs: dict[str, Any],
) -> None:
    campaign = await make_campaign(db_session)
    resp = await client.post(f"{API}/campaigns/{campaign.id}/upload", **kwargs)
    assert response_data(resp) == snapshot


async def test_upload_invalid_campaign_id(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.post(f"{API}/campaigns/not-a-uuid/upload", files=files(("a.pdf", pdf("a"))))
    assert response_data(resp) == snapshot


# ── GET /campaigns/{id}/resumes ───────────────────────────────
async def test_list_campaign_resumes(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    # Newest upload first; every status; resumes of other campaigns excluded.
    campaign = await make_campaign(db_session)
    statuses = ["pending", "extracting", "processing", "done", "failed", "quota_exceeded"]
    for minute, status in enumerate(statuses):
        failed = status in ("failed", "quota_exceeded")
        await make_resume(
            db_session,
            campaign,
            original_filename=f"{status}.pdf",
            status=status,
            uploaded_at=at(minute),
            retry_count=3 if status == "failed" else 0,
            error_message=f"{status} error message" if failed else None,
            celery_task_id="11111111-2222-3333-4444-555555555555" if status != "pending" else None,
        )
    await make_resume(db_session, await make_campaign(db_session, title="Other"))
    resp = await client.get(f"{API}/campaigns/{campaign.id}/resumes")
    assert response_data(resp) == snapshot


async def test_list_campaign_resumes_unknown_campaign(
    client: AsyncClient, snapshot: SnapshotAssertion
) -> None:
    resp = await client.get(f"{API}/campaigns/{UNKNOWN_ID}/resumes")
    assert response_data(resp) == snapshot


async def test_list_campaign_resumes_invalid_id(
    client: AsyncClient, snapshot: SnapshotAssertion
) -> None:
    resp = await client.get(f"{API}/campaigns/not-a-uuid/resumes")
    assert response_data(resp) == snapshot


# ── GET /resumes/{id} ─────────────────────────────────────────
async def test_get_resume(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    resume = await make_resume(
        db_session, status="failed", retry_count=2, error_message="Could not extract text"
    )
    resp = await client.get(f"{API}/resumes/{resume.id}")
    assert response_data(resp) == snapshot


async def test_get_resume_not_found(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.get(f"{API}/resumes/{UNKNOWN_ID}")
    assert response_data(resp) == snapshot


async def test_get_resume_invalid_id(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.get(f"{API}/resumes/not-a-uuid")
    assert response_data(resp) == snapshot


# ── POST /campaigns/{id}/resumes/retry-failed ─────────────────
async def test_retry_failed_resumes(
    client: AsyncClient,
    db_session: AsyncSession,
    celery_calls: CeleryRecorder,
    snapshot: SnapshotAssertion,
) -> None:
    campaign = await make_campaign(db_session)
    other = await make_campaign(db_session, title="Other")
    for status in ("failed", "quota_exceeded", "done", "pending", "processing"):
        await make_resume(
            db_session,
            campaign,
            original_filename=f"{status}.pdf",
            status=status,
            retry_count=3 if status == "failed" else 0,
            error_message="boom" if status in ("failed", "quota_exceeded") else None,
        )
    await make_resume(
        db_session, other, original_filename="other_failed.pdf", status="failed", retry_count=3
    )
    resp = await client.post(f"{API}/campaigns/{campaign.id}/resumes/retry-failed")
    rows = await _resume_rows(db_session)
    # Dispatch order follows an unordered query; label each call with its resume's file name
    # and sort by it so the snapshot is stable.
    filenames = {str(r["id"]): r["original_filename"] for r in rows}
    calls = [
        {**call, "resume_filename": filenames[call["args"][0]]}
        for call in celery_calls_data(celery_calls)
    ]
    assert {
        "response": response_data(resp),
        "celery_calls": sorted(calls, key=lambda c: c["resume_filename"]),
        "resume_rows": rows,
    } == snapshot


async def test_retry_failed_resumes_nothing_to_retry(
    client: AsyncClient,
    db_session: AsyncSession,
    celery_calls: CeleryRecorder,
    snapshot: SnapshotAssertion,
) -> None:
    campaign = await make_campaign(db_session)
    await make_resume(db_session, campaign, status="done")
    resp = await client.post(f"{API}/campaigns/{campaign.id}/resumes/retry-failed")
    assert {"response": response_data(resp), "celery_calls": len(celery_calls)} == snapshot


async def test_retry_failed_resumes_unknown_campaign(
    client: AsyncClient, snapshot: SnapshotAssertion
) -> None:
    # Not a 404.
    resp = await client.post(f"{API}/campaigns/{UNKNOWN_ID}/resumes/retry-failed")
    assert response_data(resp) == snapshot


async def test_retry_failed_resumes_invalid_id(
    client: AsyncClient, snapshot: SnapshotAssertion
) -> None:
    resp = await client.post(f"{API}/campaigns/not-a-uuid/resumes/retry-failed")
    assert response_data(resp) == snapshot
