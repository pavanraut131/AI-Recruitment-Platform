"""Characterization: GET /campaigns/{id}/stream (Server-Sent Events).

How the endpoint works today: it subscribes to the Redis pub/sub channel ``resume:all_updates``,
sends a ``connected`` event, then forwards every message on that channel as a ``resume_update``
event (data = the published JSON, unchanged). The Celery tasks publish those messages with
``publish_resume_event`` (``app/modules/ingestion/events.py``, called from
``app/modules/ingestion/tasks.py``; before E0-10 ``_publish_sse_event`` in ``workers/``).

The tests publish through that real function (Redis is fakeredis, see ``fake_redis``),
using the same arguments as each call site, so the recorded payload shapes are the ones a client
receives in production. Statuses with an event: ``extracting``, ``done``, ``failed`` (four
variants) and ``quota_exceeded``. ``pending`` and ``processing`` have none: nothing is published
when a resume is uploaded or when extraction hands over to AI processing.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import AppStatus
from syrupy.assertion import SnapshotAssertion

from app.modules.ingestion.events import publish_resume_event
from tests.characterization.snapshot_utils import response_data
from tests.characterization.sse import read_sse
from tests.factories import make_campaign, make_resume
from tests.fakes import FakeRedis

API = "/api/v1"
UNKNOWN_ID = "00000000-0000-0000-0000-000000000000"
QUOTA_MESSAGE = (
    "Gemini API daily quota reached (20 req/day on free tier). "
    "This resume was not processed. Quota resets at midnight PT. "
    "Upgrade to a paid Gemini API key to remove this limit."
)


@pytest.fixture(autouse=True)
def _reset_sse_app_status() -> Iterator[None]:
    # sse-starlette keeps a process-wide anyio.Event bound to the first event loop that used it;
    # each test has its own loop.
    AppStatus.should_exit_event = None
    yield
    AppStatus.should_exit_event = None


def publish_once_connected(
    publishers: list[Callable[[], None]],
) -> Callable[[dict[str, Any]], None]:
    """``on_event`` callback: when the stream says ``connected`` (it is subscribed by then),
    run every publisher."""

    def on_event(event: dict[str, Any]) -> None:
        if event.get("event") == "connected":
            for publish in publishers:
                publish()

    return on_event


async def test_stream_event_for_each_resume_status(
    app: FastAPI,
    db_session: AsyncSession,
    fake_redis: FakeRedis,
    snapshot: SnapshotAssertion,
) -> None:
    campaign = await make_campaign(db_session)
    resume = await make_resume(db_session, campaign, original_filename="jane_doe_resume.pdf")
    rid = str(resume.id)
    person_id = str(uuid.uuid4())
    publishers: list[Callable[[], None]] = [
        # tasks.extract_resume: task started
        lambda: publish_resume_event(rid, "extracting", {"filename": "jane_doe_resume.pdf"}),
        # tasks.extract_resume: retries exhausted
        lambda: publish_resume_event(
            rid, "failed", {"error": "Could not extract meaningful text from PDF"}
        ),
        # tasks.process_candidate_ai: success. person_id and matched_existing were added in E0-16
        # (additive: the Person the resume was attached to, and whether it existed before).
        lambda: publish_resume_event(
            rid,
            "done",
            {
                "candidate_name": "Jane Doe",
                "score": 82.0,
                "category": "strong_match",
                "filename": "jane_doe_resume.pdf",
                "person_id": person_id,
                "matched_existing": True,
            },
        ),
        # tasks.process_candidate_ai: extraction found no candidate data
        lambda: publish_resume_event(
            rid, "failed", {"error": "Not a resume — no candidate information could be extracted"}
        ),
        # tasks.AITask.on_failure (retries exhausted) / tasks.ExtractTask.on_failure
        lambda: publish_resume_event(rid, "failed", {"error": "Gemini returned invalid JSON"}),
        # tasks._mark_quota_exceeded
        lambda: publish_resume_event(rid, "quota_exceeded", {"error": QUOTA_MESSAGE}),
    ]
    result = await read_sse(
        app,
        f"{API}/campaigns/{campaign.id}/stream",
        count=1 + len(publishers),
        on_event=publish_once_connected(publishers),
    )
    assert {
        "status_code": result.status_code,
        "headers": result.headers,
        "events": result.events,
    } == snapshot

    redis = fake_redis.sync_client()
    # The task also publishes to a per-resume channel nobody subscribes to.
    assert redis.pubsub_numsub(f"resume:status:{rid}") == [(f"resume:status:{rid}".encode(), 0)]
    # After the client disconnects the stream unsubscribes.
    assert redis.pubsub_numsub("resume:all_updates") == [(b"resume:all_updates", 0)]


async def test_stream_forwards_events_of_other_campaigns(
    app: FastAPI, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    # BUG (recorded): the stream does not filter by campaign; a client watching campaign A also
    # receives every update for campaign B (and for unknown resume ids).
    watched = await make_campaign(db_session, title="Watched")
    other = await make_campaign(db_session, title="Other")
    other_resume = await make_resume(db_session, other, original_filename="other.pdf")
    publishers: list[Callable[[], None]] = [
        lambda: publish_resume_event(str(other_resume.id), "extracting", {"filename": "other.pdf"}),
        lambda: publish_resume_event("not-a-resume-id", "failed", {"error": "x"}),
    ]
    result = await read_sse(
        app,
        f"{API}/campaigns/{watched.id}/stream",
        count=3,
        on_event=publish_once_connected(publishers),
    )
    assert {"watched_campaign": watched.id, "events": result.events} == snapshot


async def test_stream_unknown_campaign_still_connects(
    app: FastAPI, snapshot: SnapshotAssertion
) -> None:
    result = await read_sse(app, f"{API}/campaigns/{UNKNOWN_ID}/stream", count=1)
    assert {"status_code": result.status_code, "events": result.events} == snapshot


async def test_stream_invalid_campaign_id(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.get(f"{API}/campaigns/not-a-uuid/stream")
    assert response_data(resp) == snapshot


async def test_reader_times_out_instead_of_hanging(app: FastAPI) -> None:
    # Guard for the helper itself: waiting for an event that never comes fails fast.
    with pytest.raises(TimeoutError):
        await read_sse(app, f"{API}/campaigns/{UNKNOWN_ID}/stream", count=2, max_seconds=0.3)
