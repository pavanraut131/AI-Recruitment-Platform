"""Resume progress events over Redis pub/sub (the SSE stream's source).

The Celery tasks :func:`publish_resume_event` on **one channel per campaign**,
``campaign:<campaign id>:events`` (:func:`campaign_channel`), and :func:`campaign_event_stream`
subscribes to the channel of the campaign it streams. A client watching campaign A therefore
never receives, and the API process never even sees, an event of campaign B (B3, fixed in E0-21;
until then every event went to one global channel, ``resume:all_updates``, and every stream
forwarded all of them). Before E0-21 the tasks also published to ``resume:status:<resume id>``,
which nothing subscribed to; it was removed.

The payload is ``{"resume_id": ..., "status": ..., **extra}``; its shape per status is pinned by
``tests/characterization/test_sse.py`` and did not change with the channel. Since E0-16 the
``done`` event also carries ``person_id`` and ``matched_existing`` (additive; no other event
knows the Person yet).
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Mapping
from typing import Any

import redis
import redis.asyncio as aioredis
import structlog
from pydantic import ValidationError

from app.modules.ingestion.schemas_v2 import ResumeEvent
from app.platform.config import settings

logger = structlog.get_logger()


def campaign_channel(campaign_id: uuid.UUID | str) -> str:
    """The Redis channel carrying the progress events of one campaign's resumes."""
    return f"campaign:{campaign_id}:events"


def publish_resume_event(
    resume_id: str,
    status: str,
    extra: Mapping[str, Any] | None = None,
    *,
    campaign_id: uuid.UUID | str,
) -> None:
    """Publish a resume's status change to its campaign's channel (synchronous: called from the
    Celery tasks)."""
    r = redis.Redis.from_url(settings.REDIS_URL)
    payload = {"resume_id": resume_id, "status": status, **(extra or {})}
    r.publish(campaign_channel(campaign_id), json.dumps(payload))


async def campaign_event_stream(campaign_id: uuid.UUID) -> AsyncIterator[dict[str, str]]:
    """SSE events for a campaign's page: ``connected`` first, then a ``resume_update`` per event
    published for one of **this campaign's** resumes."""
    channel = campaign_channel(campaign_id)
    r = aioredis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    pubsub = r.pubsub()
    await pubsub.subscribe(channel)

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
        await pubsub.unsubscribe(channel)
        await r.aclose()


async def typed_campaign_event_stream(campaign_id: uuid.UUID) -> AsyncIterator[dict[str, str]]:
    """The ``/api/v2`` stream: the same channel, events and order as
    :func:`campaign_event_stream`, with each ``resume_update`` payload validated into
    :class:`~app.modules.ingestion.schemas_v2.ResumeEvent` and sent with **every** field (the
    ones its status does not carry as ``null``). A payload that does not validate (never
    published by the tasks) is logged and skipped rather than sent malformed. ``connected``
    carries ``{"campaign_id": ...}`` as before."""
    async for event in campaign_event_stream(campaign_id):
        if event["event"] != "resume_update":
            yield event
            continue
        try:
            typed = ResumeEvent.model_validate_json(event["data"])
        except ValidationError:
            logger.warning("sse_event_skipped", campaign_id=str(campaign_id), data=event["data"])
            continue
        yield {"event": "resume_update", "data": typed.model_dump_json()}
