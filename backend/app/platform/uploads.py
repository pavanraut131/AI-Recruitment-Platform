"""Explicit request-body limits for the upload endpoints (S1).

FastAPI parses a form body with ``request.form()`` and Starlette's defaults: up to 1000 files,
1000 other fields and 1 MiB per non-file field. The upload endpoints (v1 and v2) take one thing,
the ``files`` list, so they get tighter limits, enforced by Starlette's parser while it reads the
body, before anything reaches the endpoint:

- **Files**: one more than ``MAX_FILES_PER_BATCH``. A batch over the limit by one still reaches
  the endpoint and gets its documented "too many files" answer (v1 400, v2 422, after the
  campaign check, as the snapshots record); a body with even more files is cut off while it is
  parsed, so it never has more than ``MAX_FILES_PER_BATCH + 1`` files spooled to temporary
  storage.
- **Other fields**: :data:`MAX_FORM_FIELDS`. The endpoints read none, but a stray field (a
  client's extra metadata, or ``files`` sent as text) is still validated as before, a 422.
- **Size of a non-file field**: :data:`MAX_FIELD_BYTES`. Non-file parts are buffered in memory
  (GHSA-f96h-pmfr-66vw), so a large one is refused instead of being read.

A body over a limit is answered with **400** and Starlette's message as the detail (v1:
``{"detail": ...}``, v2: problem details), the status Starlette uses for every multipart parse
error. File sizes are not limited here: files are spooled to disk by the parser and checked
against ``MAX_FILE_SIZE_MB`` by the ingestion service.

Use :class:`UploadRoute` as an ``APIRouter``'s ``route_class``; it only changes the routes that
take a form body.
"""

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from fastapi import Request, Response, params
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.platform.config import settings

MAX_FORM_FIELDS = 10
"""Non-file fields accepted in an upload body (the endpoints read none)."""

MAX_FIELD_BYTES = 16 * 1024
"""Largest non-file field accepted in an upload body, in bytes."""


@dataclass(frozen=True)
class FormLimits:
    max_files: int
    max_fields: int
    max_part_size: int


def upload_form_limits() -> FormLimits:
    """The limits for an upload body, read from the settings at request time."""
    return FormLimits(
        max_files=settings.MAX_FILES_PER_BATCH + 1,
        max_fields=MAX_FORM_FIELDS,
        max_part_size=MAX_FIELD_BYTES,
    )


class UploadRoute(APIRoute):
    """An ``APIRoute`` that parses a form body with :func:`upload_form_limits`.

    It parses the body itself before FastAPI does; Starlette caches the parsed form on the
    request, so FastAPI's own ``request.form()`` returns it (and closes its files as usual).
    Routes without a form body are left unchanged.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()
        if self.body_field is None or not isinstance(self.body_field.field_info, params.Form):
            return handler

        async def parse_form_with_limits(request: Request) -> Response:
            limits = upload_form_limits()
            try:
                await request.form(
                    max_files=limits.max_files,
                    max_fields=limits.max_fields,
                    max_part_size=limits.max_part_size,
                )
            except StarletteHTTPException:
                raise  # over a limit, or malformed: Starlette's 400
            except Exception:
                # Anything else (e.g. the client disconnecting) is reported by FastAPI's own
                # parse of the body, which then fails too: a 400, as before.
                pass
            return await handler(request)

        return parse_form_with_limits
