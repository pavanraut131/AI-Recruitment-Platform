"""Fixed-window rate limits in Redis (EPIC-02 spec §4.6).

A limit is a counter per key and window: the first hit creates the key with the window as its
TTL, every hit increments it, and the key expires with the window. Requests over a limit are
answered ``429`` with ``Retry-After`` (the key's remaining TTL). The design **throttles but never
locks accounts**: nothing outlives a window, so an attacker cannot lock a victim out for longer
than the window by failing on purpose.

Keys never contain a raw identifier: callers pass :func:`hashed`, a keyed hash (HMAC-SHA256 with
a key derived from ``SECRET_KEY``), so neither an email address nor an IP ends up in Redis.

The store is a :class:`RateLimitStore` protocol (``hit``, ``peek``, ``reset``, ``mark_once``).
:class:`RedisRateLimitStore` implements it on any ``redis.asyncio`` client, which in tests is
the suite's fakeredis (``tests.fakes.FakeRedis``, through ``redis.asyncio.Redis.from_url``).
:func:`rate_limit_store` is the FastAPI dependency: one client per request, closed afterwards.

If Redis cannot be reached the limiter **fails open** (logs ``ratelimit_unavailable`` and lets
the request through): sign-in must not depend on a cache being up, and the password hash cost
still bounds guessing. The trade-off is in the E2-05 PR.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Final, Protocol

import redis.asyncio as aioredis
import structlog
from redis.exceptions import RedisError

from app.platform.config import settings

logger = structlog.get_logger()

KEY_PREFIX: Final = "ratelimit"
_KEY_LABEL: Final = b"hireflow/ratelimit/v1"


def hashed(identifier: str) -> str:
    """A keyed, stable hash of ``identifier`` for use in a key (never the identifier itself)."""
    key = hmac.new(settings.SECRET_KEY.encode(), _KEY_LABEL, hashlib.sha256).digest()
    return hmac.new(key, identifier.encode(), hashlib.sha256).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class Window:
    """A counter's state: hits so far in the current window and seconds until it resets."""

    count: int
    retry_after: int


@dataclass(frozen=True, slots=True)
class Limit:
    """``max_hits`` per ``window`` for the counters named ``name``."""

    name: str
    max_hits: int
    window: timedelta

    @property
    def seconds(self) -> int:
        return max(1, int(self.window.total_seconds()))

    def key(self, identifier_hash: str) -> str:
        return f"{KEY_PREFIX}:{self.name}:{identifier_hash}"


class RateLimitStore(Protocol):
    async def hit(self, key: str, window_seconds: int) -> Window:
        """Count one hit; the window starts with the first hit."""
        ...

    async def peek(self, key: str) -> Window:
        """The current window without counting a hit (``count`` 0 when there is none)."""
        ...

    async def reset(self, key: str) -> None:
        """Forget the counter (e.g. failures after a successful sign-in)."""
        ...

    async def mark_once(self, key: str, window_seconds: int) -> bool:
        """True the first time in a window (to audit an over-limit once per window)."""
        ...


class RedisRateLimitStore:
    """:class:`RateLimitStore` on a ``redis.asyncio`` client. ``SET NX EX`` then ``INCR`` in one
    transaction, so a key never exists without its TTL (``INCR`` keeps the TTL)."""

    def __init__(self, client: aioredis.Redis) -> None:
        self._client = client

    async def hit(self, key: str, window_seconds: int) -> Window:
        try:
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.set(key, 0, ex=window_seconds, nx=True)
                pipe.incr(key)
                pipe.ttl(key)
                _, count, ttl = await pipe.execute()
        except RedisError as exc:
            logger.warning("ratelimit_unavailable", error=type(exc).__name__)
            return Window(0, 0)
        return Window(int(count), _ttl(ttl, window_seconds))

    async def peek(self, key: str) -> Window:
        try:
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.get(key)
                pipe.ttl(key)
                value, ttl = await pipe.execute()
        except RedisError as exc:
            logger.warning("ratelimit_unavailable", error=type(exc).__name__)
            return Window(0, 0)
        return Window(int(value or 0), _ttl(ttl, 0))

    async def reset(self, key: str) -> None:
        try:
            await self._client.delete(key)
        except RedisError as exc:
            logger.warning("ratelimit_unavailable", error=type(exc).__name__)

    async def mark_once(self, key: str, window_seconds: int) -> bool:
        try:
            return bool(await self._client.set(f"{key}:marked", 1, ex=window_seconds, nx=True))
        except RedisError as exc:
            logger.warning("ratelimit_unavailable", error=type(exc).__name__)
            return False


def _ttl(ttl: int | None, default: int) -> int:
    # TTL is -1 (no expiry) or -2 (no key) in edge cases; never report less than a second.
    return int(ttl) if ttl is not None and ttl > 0 else max(default, 1)


async def rate_limit_store() -> AsyncIterator[RateLimitStore]:
    """FastAPI dependency: a :class:`RedisRateLimitStore` on ``REDIS_URL`` for one request."""
    client = aioredis.Redis.from_url(settings.REDIS_URL, socket_timeout=2, socket_connect_timeout=2)
    try:
        yield RedisRateLimitStore(client)
    finally:
        await client.aclose()
