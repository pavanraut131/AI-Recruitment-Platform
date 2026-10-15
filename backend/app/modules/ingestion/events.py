"""Resume progress events over Redis pub/sub (the SSE stream's source).

The Celery tasks :func:`publish_resume_event` on two channels: ``resume:status:<resume id>`` (no
subscriber today) and ``resume:all_updates``, which :func:`campaign_event_stream` forwards to
SSE clients. The payload is ``{"resume_id": ..., "status": ..., **extra}``; its shape per status
is pinned by ``tests/characterization/test_sse.py``.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Mapping
from typing import Any

import redis
import redis.asyncio as aioredis

from app.platform.config import settings

ALL_UPDATES_CHANNEL = "resume:all_updates"


def publish_resume_event(
    resume_id: str, status: str, extra: Mapping[str, Any] | None = None
) -> None:
    """Publish a resume's status change (synchronous: called from the Celery tasks)."""
    r = redis.Redis.from_url(settings.REDIS_URL)
    payload = {"resume_id": resume_id, "status": status, **(extra or {})}
    r.publish(f"resume:status:{resume_id}", json.dumps(payload))
    # Also publish to campaign-level channel
    r.publish(ALL_UPDATES_CHANNEL, json.dumps(payload))


async def campaign_event_stream(campaign_id: uuid.UUID) -> AsyncIterator[dict[str, str]]:
    """SSE events for a campaign's page: ``connected`` first, then a ``resume_update`` per
    published event.

    BUG (recorded): events are not filtered by campaign; every resume's updates are forwarded.
    """
    r = aioredis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    pubsub = r.pubsub()
    await pubsub.subscribe(ALL_UPDATES_CHANNEL)

    try:
        # Send initial connection event
        yield {"event": "connected", "data": json.dumps({"campaign_id": str(campaign_id)})}

        async for message in pubsub.listen():
            if message["type"] == "message":
                data = json.loads(message["data"])
                yield {
                    "event": "resume_update",
                    "data": json.dumps(data),
                }

    except asyncio.CancelledError:
        await pubsub.unsubscribe(ALL_UPDATES_CHANNEL)
        await r.aclose()
