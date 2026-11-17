"""Contract: /api/v2 uploads, resume list, retry-failed and the per-campaign SSE stream."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from syrupy.assertion import SnapshotAssertion

from app.modules.ingestion.events import campaign_channel, publish_resume_event
from tests.characterization.snapshot_utils import celery_calls_data, response_data
from tests.characterization.sse import read_sse
from tests.contract_v2.data import UNKNOWN_ID, V2, applicant, files, pdf
from tests.factories import at, make_campaign, make_resume
from tests.fakes import CeleryRecorder, FakeRedis


# ── POST /campaigns/{id}/uploads ──────────────────────────────
async def test_upload(
    client: AsyncClient,
    db_session: AsyncSession,
    celery_calls: CeleryRecorder,
    snapshot: SnapshotAssertion,
) -> None:
    campaign = await make_campaign(db_session)
    done = await make_resume(db_session, campaign, original_filename="done.pdf")
    done.file_hash = hashlib.sha256(pdf("dup")).hexdigest()
    await db_session.flush()
    upload = files(
        ("alice.pdf", pdf("alice")),
        ("notes.txt", b"hello"),
        ("fake.pdf", b"not a pdf"),
        ("again.pdf", pdf("dup")),
    )
    resp = await client.post(f"{V2}/campaigns/{campaign.id}/uploads", files=upload)
    assert {
        "response": response_data(resp),
        "celery_calls": celery_calls_data(celery_calls),
    } == snapshot


async def test_upload_unknown_campaign(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.post(
        f"{V2}/campaigns/{UNKNOWN_ID}/uploads", files=files(("a.pdf", pdf("a")))
    )
    assert response_data(resp) == snapshot


async def test_upload_too_many_files(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    snapshot: SnapshotAssertion,
) -> None:
    from app.platform.config import settings

    monkeypatch.setattr(settings, "MAX_FILES_PER_BATCH", 2)
    campaign = await make_campaign(db_session)
    upload = files(*((f"{i}.pdf", pdf(str(i))) for i in range(3)))
    resp = await client.post(f"{V2}/campaigns/{campaign.id}/uploads", files=upload)
    assert response_data(resp) == snapshot


async def test_upload_without_files(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    campaign = await make_campaign(db_session)
    resp = await client.post(
        f"{V2}/campaigns/{campaign.id}/uploads",
        files=[("resume", ("a.pdf", pdf("a"), "application/pdf"))],
    )
    assert response_data(resp) == snapshot


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param(
            {"data": {"note": "x" * (16 * 1024 + 1)}, "files": files(("a.pdf", pdf("a")))},
            id="oversized-non-file-field",
        ),
        pytest.param(
            {"data": {f"f{i}": "x" for i in range(11)}, "files": files(("a.pdf", pdf("a")))},
            id="too-many-non-file-fields",
        ),
        pytest.param(
            {"files": files(*((f"{i:02d}.pdf", pdf(str(i))) for i in range(52)))},
            id="files-past-the-parser-limit",
        ),
    ],
)
async def test_upload_body_over_parsing_limits(
    client: AsyncClient,
    db_session: AsyncSession,
    celery_calls: CeleryRecorder,
    snapshot: SnapshotAssertion,
    kwargs: dict[str, Any],
) -> None:
    """A body over a parsing limit (app.platform.uploads) is a 400 problem, before the
    endpoint runs; 52 files is one past what the parser accepts with MAX_FILES_PER_BATCH=50."""
    campaign = await make_campaign(db_session)
    resp = await client.post(f"{V2}/campaigns/{campaign.id}/uploads", **kwargs)
    assert {
        "response": response_data(resp),
        "celery_calls": celery_calls_data(celery_calls),
    } == snapshot


# ── GET /campaigns/{id}/resumes ───────────────────────────────
async def _campaign_with_resumes(db: AsyncSession) -> Any:
    campaign = await make_campaign(db)
    for i, status in enumerate(["pending", "extracting", "processing", "failed"]):
        resume = await make_resume(
            db, campaign, original_filename=f"{status}.pdf", status=status, uploaded_at=at(i)
        )
        if status == "failed":
            resume.processing_step = "score"
            resume.error_message = "Gemini returned invalid JSON"
            resume.retry_count = 3
    await applicant(db, campaign, 10)  # a done resume, with its application
    await db.flush()
    return campaign


async def test_list_resumes(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    campaign = await _campaign_with_resumes(db_session)
    resp = await client.get(f"{V2}/campaigns/{campaign.id}/resumes")
    assert response_data(resp) == snapshot


async def test_list_resumes_by_status(client: AsyncClient, db_session: AsyncSession) -> None:
    campaign = await _campaign_with_resumes(db_session)
    resp = await client.get(
        f"{V2}/campaigns/{campaign.id}/resumes?status=failed&status=done&page_size=1"
    )
    body = resp.json()
    assert (body["total"], [r["filename"] for r in body["items"]]) == (
        2,
        ["cv_Backend Engineer - Q1 Hiring_10.pdf"],
    )


async def test_list_resumes_unknown_campaign(
    client: AsyncClient, snapshot: SnapshotAssertion
) -> None:
    resp = await client.get(f"{V2}/campaigns/{UNKNOWN_ID}/resumes")
    assert response_data(resp) == snapshot


# ── POST /campaigns/{id}/resumes/retry-failed ─────────────────
async def test_retry_failed(
    client: AsyncClient,
    db_session: AsyncSession,
    celery_calls: CeleryRecorder,
    snapshot: SnapshotAssertion,
) -> None:
    campaign = await make_campaign(db_session)
    first = await make_resume(db_session, campaign, original_filename="a.pdf", status="failed",
                              processing_step="score", uploaded_at=at(1))  # fmt: skip
    second = await make_resume(db_session, campaign, original_filename="b.pdf",
                               status="quota_exceeded", uploaded_at=at(2))  # fmt: skip
    await make_resume(db_session, campaign, original_filename="c.pdf", status="done")
    resp = await client.post(f"{V2}/campaigns/{campaign.id}/resumes/retry-failed")
    assert {
        "response": response_data(resp),
        "order": [first.id, second.id],
        "tasks": sorted(c.task for c in celery_calls.calls),
    } == snapshot


async def test_retry_failed_unknown_campaign(
    client: AsyncClient, snapshot: SnapshotAssertion
) -> None:
    resp = await client.post(f"{V2}/campaigns/{UNKNOWN_ID}/resumes/retry-failed")
    assert response_data(resp) == snapshot


# ── GET /campaigns/{id}/events (SSE) ──────────────────────────
def publish_once_connected(
    publishers: list[Callable[[], None]],
) -> Callable[[dict[str, Any]], None]:
    def on_event(event: dict[str, Any]) -> None:
        if event.get("event") == "connected":
            for publish in publishers:
                publish()

    return on_event


async def test_events_are_typed_and_campaign_filtered(
    app: FastAPI,
    client: AsyncClient,
    db_session: AsyncSession,
    fake_redis: FakeRedis,
    snapshot: SnapshotAssertion,
) -> None:
    campaign = await make_campaign(db_session)
    other = await make_campaign(db_session, title="Other")
    rid = str(uuid.uuid4())
    cid = campaign.id
    publishers: list[Callable[[], None]] = [
        # Another campaign's event: never delivered.
        lambda: publish_resume_event(
            str(uuid.uuid4()), "extracting", {"filename": "x.pdf"}, campaign_id=other.id
        ),  # fmt: skip
        lambda: publish_resume_event(rid, "extracting", {"filename": "jane.pdf"}, campaign_id=cid),
        # Not a valid ResumeEvent (unknown status): skipped, not sent malformed.
        lambda: publish_resume_event(rid, "exploded", {}, campaign_id=cid),
        lambda: publish_resume_event(
            rid,
            "done",
            {
                "candidate_name": "Jane Doe",
                "score": 82.0,
                "category": "strong_match",
                "filename": "jane.pdf",
                "person_id": str(uuid.uuid4()),
                "matched_existing": False,
            },
            campaign_id=cid,
        ),  # fmt: skip
        lambda: publish_resume_event(rid, "quota_exceeded", {"error": "quota"}, campaign_id=cid),
    ]
    result = await read_sse(
        app,
        f"{V2}/campaigns/{campaign.id}/events",
        count=4,
        on_event=publish_once_connected(publishers),
    )
    assert {
        "status_code": result.status_code,
        "content_type": result.headers.get("content-type"),
        "events": result.events,
    } == snapshot
    channel = campaign_channel(campaign.id)
    assert fake_redis.sync_client().pubsub_numsub(channel) == [(channel.encode(), 0)]


async def test_events_stream_holds_no_database_transaction(
    app: FastAPI, client: AsyncClient, db_session: AsyncSession
) -> None:
    # The existence check's read transaction ends before the stream starts, so an open stream
    # does not keep a database connection checked out (whenever the framework closes sessions).
    campaign = await make_campaign(db_session)
    seen: list[bool] = []

    def on_event(event: dict[str, Any]) -> None:
        seen.append(db_session.in_transaction())

    await read_sse(app, f"{V2}/campaigns/{campaign.id}/events", count=1, on_event=on_event)
    assert seen == [False]


async def test_events_unknown_campaign(app: FastAPI, client: AsyncClient) -> None:
    # B7: v1's stream connects for any id; v2 answers 404 problem details.
    result = await read_sse(app, f"{V2}/campaigns/{UNKNOWN_ID}/events", count=0)
    assert result.status_code == 404
    assert result.headers["content-type"] == "application/problem+json"


async def test_events_invalid_campaign_id(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.get(f"{V2}/campaigns/not-a-uuid/events")
    assert response_data(resp) == snapshot


def test_resume_event_payload_keeps_every_field() -> None:
    from app.modules.ingestion.schemas_v2 import ResumeEvent

    event = ResumeEvent.model_validate_json(
        json.dumps({"resume_id": str(uuid.uuid4()), "status": "failed", "error": "x"})
    )
    assert set(json.loads(event.model_dump_json())) == set(ResumeEvent.model_fields)
