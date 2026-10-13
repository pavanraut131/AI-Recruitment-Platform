"""Drive an SSE endpoint over raw ASGI and collect a bounded number of events.

httpx's ``ASGITransport`` buffers the whole response body before returning, so it cannot read an
endless event stream. This helper calls the ASGI app directly, parses events as the body chunks
arrive, and once ``count`` events have been read sends ``http.disconnect`` (what a browser closing
the ``EventSource`` does). Everything runs under a timeout, so a broken stream fails the test
instead of hanging it.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

SEPARATOR = "\r\n\r\n"  # sse-starlette's default line separator is "\r\n"


@dataclass
class SSEResult:
    status_code: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)


def _parse_event(frame: str) -> dict[str, Any]:
    event: dict[str, Any] = {}
    for line in frame.split("\r\n"):
        name, _, value = line.partition(":")
        event[name] = value[1:] if value.startswith(" ") else value
    if "data" in event:
        event["data"] = json.loads(event["data"])
    return event


async def read_sse(
    app: Any,
    path: str,
    count: int,
    *,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    max_seconds: float = 5.0,
) -> SSEResult:
    """GET ``path`` on ``app`` and return the first ``count`` events.

    ``on_event`` is called with each parsed event as it arrives, e.g. to publish the next Redis
    message once the stream reports it is ``connected`` (subscribed).
    """
    result = SSEResult()
    disconnected = asyncio.Event()
    request_sent = False
    buffer = ""

    async def receive() -> dict[str, Any]:
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        nonlocal buffer
        if message["type"] == "http.response.start":
            result.status_code = message["status"]
            result.headers = {k.decode(): v.decode() for k, v in message.get("headers", [])}
        elif message["type"] == "http.response.body":
            buffer += message.get("body", b"").decode()
            while SEPARATOR in buffer and not disconnected.is_set():
                frame, buffer = buffer.split(SEPARATOR, 1)
                event = _parse_event(frame)
                result.events.append(event)
                if on_event is not None:
                    on_event(event)
                if len(result.events) >= count:
                    disconnected.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"test"), (b"accept", b"text/event-stream")],
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
    }
    async with asyncio.timeout(max_seconds):
        await app(scope, receive, send)
    return result
