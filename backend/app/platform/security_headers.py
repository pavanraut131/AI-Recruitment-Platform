"""Security headers on every ``/api/v2`` response (EPIC-02 spec §10, ASVS V14.4).

- ``X-Content-Type-Options: nosniff``: a JSON or CSV body is never sniffed into HTML.
- ``Referrer-Policy: no-referrer``: on every response, not only the auth routes: nothing the API
  returns links anywhere, and a token page (E2-07) must never leak its URL.
- ``Cache-Control: no-store``: responses are per user and per agency and must not be kept by a
  browser or proxy cache. A response that sets its own ``Cache-Control`` (the SSE stream says
  ``no-cache``) keeps it.

Pure ASGI, so streaming responses pass through unbuffered.
"""

from __future__ import annotations

from typing import Final

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HEADERS: Final = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in HEADERS.items():
                    if name not in headers:
                        headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)
