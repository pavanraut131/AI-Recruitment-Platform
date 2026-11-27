"""Login throttling (spec §4.6): 429 with ``Retry-After`` before any password work, per email
(failures) and per IP (attempts), audited once per window, never a lockout; and the limiter's
store on fakeredis."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity import passwords
from app.modules.identity.models import AuditEvent
from app.platform import ratelimit
from app.platform.config import settings
from tests.fakes import FakeRedis
from tests.identity.conftest import Users, problem


async def _rate_limited_events(db: AsyncSession) -> int:
    db.expire_all()
    query = select(func.count()).where(AuditEvent.action == "auth.rate_limited")
    return int((await db.execute(query)).scalar_one())


async def test_five_failures_per_email_then_429_even_with_the_right_password(
    users: Users, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await users.user("ada@example.com")
    for _ in range(5):
        assert (await users.login("ada@example.com", "wrong password!!")).status_code == 401

    calls: list[str] = []
    real = passwords.verify_password
    monkeypatch.setattr(passwords, "verify_password", lambda *a: calls.append("x") or real(*a))
    for _ in range(2):
        resp = await users.login("ADA@example.com")  # right password, same (normalised) email
        assert resp.status_code == 429
        assert problem(resp)["detail"] == "Too many attempts. Try again later."
        retry_after = int(resp.headers["retry-after"])
        assert 0 < retry_after <= 15 * 60
    assert calls == []  # refused before any password work
    assert await _rate_limited_events(db_session) == 1  # once per window

    # Another address from the same browser is not affected (no lockout of others).
    await users.user("bob@example.com")
    assert (await users.login("bob@example.com")).status_code == 200


async def test_a_success_forgets_earlier_failures(users: Users) -> None:
    await users.user("ada@example.com")
    for _ in range(4):
        assert (await users.login("ada@example.com", "wrong password!!")).status_code == 401
    assert (await users.login("ada@example.com")).status_code == 200
    for _ in range(4):
        assert (await users.login("ada@example.com", "wrong password!!")).status_code == 401
    assert (await users.login("ada@example.com")).status_code == 200


async def test_attempts_per_ip(users: Users, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LOGIN_MAX_ATTEMPTS_PER_IP", 3)
    await users.user("ada@example.com")
    for i in range(3):  # different emails: only the IP counter can stop them
        assert (await users.login(f"nobody{i}@example.com")).status_code == 401
    resp = await users.login("ada@example.com")
    assert resp.status_code == 429 and int(resp.headers["retry-after"]) > 0


async def test_the_window_expires_no_lockout(
    users: Users, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    await users.user("ada@example.com")
    for _ in range(5):
        await users.login("ada@example.com", "wrong password!!")
    assert (await users.login("ada@example.com")).status_code == 429
    # The window ends (simulated: the counters expire with their TTL).
    client = fake_redis.sync_client()
    for key in client.scan_iter("ratelimit:*"):
        client.delete(key)
    assert (await users.login("ada@example.com")).status_code == 200


async def test_redis_down_fails_open(users: Users, fake_redis: FakeRedis) -> None:
    await users.user("ada@example.com")
    fake_redis.server.connected = False
    assert (await users.login("ada@example.com", "wrong password!!")).status_code == 401
    assert (await users.login("ada@example.com")).status_code == 200


# ── The store ─────────────────────────────────────────────────
async def test_fixed_window_store(fake_redis: FakeRedis) -> None:
    store = ratelimit.RedisRateLimitStore(fake_redis.async_client())
    limit = ratelimit.Limit("t", 2, timedelta(minutes=1))
    key = limit.key(ratelimit.hashed("someone@example.com"))
    assert "someone" not in key
    assert (await store.peek(key)).count == 0
    first = await store.hit(key, limit.seconds)
    assert first.count == 1 and 0 < first.retry_after <= 60
    assert (await store.hit(key, limit.seconds)).count == 2
    assert (await store.peek(key)).count == 2
    assert fake_redis.sync_client().ttl(key) > 0  # the key never lives without its TTL
    assert await store.mark_once(key, limit.seconds) is True
    assert await store.mark_once(key, limit.seconds) is False
    await store.reset(key)
    assert (await store.peek(key)).count == 0


def test_hashed_identifiers_are_keyed_and_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    a = ratelimit.hashed("ada@example.com")
    assert a == ratelimit.hashed("ada@example.com") and "ada" not in a
    monkeypatch.setattr(settings, "SECRET_KEY", "a different deployment secret value!")
    assert ratelimit.hashed("ada@example.com") != a
