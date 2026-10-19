"""Characterization: global stats, health endpoints and the published OpenAPI schema."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from syrupy.assertion import SnapshotAssertion

from tests.characterization.snapshot_utils import response_data
from tests.factories import make_campaign, make_candidate, make_resume
from tests.fakes import FakeRedis

API = "/api/v1"


# ── GET /api/v1/stats ─────────────────────────────────────────
async def test_global_stats_empty(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.get(f"{API}/stats")
    assert response_data(resp) == snapshot


async def test_global_stats(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    active = await make_campaign(db_session, title="Active")
    await make_campaign(db_session, title="Closed", status="closed")
    await make_campaign(db_session, title="Archived", status="archived")
    # A status outside the known three counts towards the total only.
    await make_campaign(db_session, title="Odd", status="paused")
    for status in ("pending", "extracting", "processing", "done", "failed", "quota_exceeded"):
        await make_resume(db_session, active, original_filename=f"{status}.pdf", status=status)
    await make_candidate(db_session, active)
    # Being analysed: no candidate (application) until scored, so not in total_candidates
    # (E0-19; before the read switch its placeholder candidate was counted).
    await make_resume(
        db_session, active, original_filename="in_progress.pdf", status="processing", raw_text="t"
    )
    resp = await client.get(f"{API}/stats")
    assert response_data(resp) == snapshot


# ── Health ────────────────────────────────────────────────────
async def test_health(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.get("/health")
    assert response_data(resp) == snapshot


async def test_api_v1_health_does_not_exist(
    client: AsyncClient, snapshot: SnapshotAssertion
) -> None:
    # BUG (recorded): /api/v1/health is listed as an endpoint but is not routed. The only
    # health routes are /health and /health/ready (app/api/__init__.py used to define an unused
    # /api/v1 router; E0-09 removed it).
    resp = await client.get(f"{API}/health")
    assert response_data(resp) == snapshot


@pytest.mark.parametrize(
    "redis_up", [pytest.param(True, id="all-ok"), pytest.param(False, id="redis-down")]
)
async def test_health_ready(
    client: AsyncClient,
    db_engine: AsyncEngine,
    fake_redis: FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
    snapshot: SnapshotAssertion,
    redis_up: bool,
) -> None:
    from app.platform import database

    # The readiness check opens its own connection with the app's engine; use the test engine
    # (NullPool), since a pooled asyncpg connection cannot be reused across test event loops.
    monkeypatch.setattr(database, "engine", db_engine)
    fake_redis.server.connected = redis_up
    resp = await client.get("/health/ready")
    data = response_data(resp)
    # The text after "error: " is the Redis client's exception message; with fakeredis it is not
    # what redis-py says in production, so only its presence is recorded.
    checks = data["body"]["checks"]
    if checks["redis"].startswith("error: "):
        checks["redis"] = "error: <redis exception message>"
    assert data == snapshot


async def test_unknown_route(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.get(f"{API}/does-not-exist")
    assert response_data(resp) == snapshot


# ── OpenAPI ───────────────────────────────────────────────────
async def test_openapi_schema(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    """The full published schema: every path, method, parameter and response model."""
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    assert resp.json() == snapshot
