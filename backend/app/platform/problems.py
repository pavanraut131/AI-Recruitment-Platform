"""RFC 9457 problem details (``application/problem+json``) for the ``/api/v2`` application.

Every error the v2 API returns has one shape, :class:`Problem`::

    {"type": "about:blank", "title": "Not Found", "status": 404, "detail": "Campaign not found"}

Request validation errors (422) add ``errors``, one entry per invalid field, each with the field's
path (``loc``, e.g. ``["query", "page_size"]`` or ``["body", "title"]``) and a message.

:func:`install_problem_handlers` registers the handlers on **one** FastAPI application (the v2
sub-application, ``app.v2``). The v1 application never gets them, so v1 responses stay exactly as
the characterization snapshots record them.

Routers raise ``HTTPException`` (or :class:`ProblemError` for a custom ``type``/``title``) as
usual; :func:`errors_as_http` turns a module service's error classes into ``HTTPException``\\ s
with the status codes a router chooses. ``type`` is ``about:blank`` unless a more specific
problem type is given; with ``about:blank`` the ``title`` is the HTTP status phrase (RFC 9457
§4.2.1). A 500 never echoes the request URL or the exception (unlike v1's handler, B10).
"""

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_MEDIA_TYPE = "application/problem+json"
ABOUT_BLANK = "about:blank"


# ── Shapes ────────────────────────────────────────────────────
class FieldError(BaseModel):
    """One invalid request field."""

    loc: list[str | int] = Field(
        description="Path of the field: where it is (body, query, path, header) then its keys."
    )
    msg: str = Field(description="What is wrong with it.")


class Problem(BaseModel):
    """An error, as RFC 9457 problem details (``application/problem+json``)."""

    type: str = Field(
        default=ABOUT_BLANK,
        description="A URI reference identifying the problem type; about:blank for a plain "
        "HTTP error.",
    )
    title: str = Field(description="Short summary of the problem type (the HTTP status phrase).")
    status: int = Field(description="The HTTP status code.")
    detail: str | None = Field(default=None, description="Explanation specific to this occurrence.")
    errors: list[FieldError] | None = Field(
        default=None, description="Validation errors (422 only), one per invalid field."
    )


class ProblemResponse(JSONResponse):
    media_type = PROBLEM_MEDIA_TYPE


class ProblemError(Exception):
    """An error with a specific problem ``type`` (and optionally ``title``), field ``errors``
    (a 422 raised by a service rather than by request validation) or response ``headers``."""

    def __init__(
        self,
        status: int,
        detail: str | None = None,
        *,
        type: str = ABOUT_BLANK,
        title: str | None = None,
        errors: Sequence[FieldError] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.type = type
        self.title = title
        self.errors = errors
        self.headers = headers


def problem_response(
    status: int,
    detail: str | None = None,
    *,
    type: str = ABOUT_BLANK,
    title: str | None = None,
    errors: Sequence[FieldError] | None = None,
    headers: Mapping[str, str] | None = None,
) -> ProblemResponse:
    problem = Problem(
        type=type,
        title=title or _phrase(status),
        status=status,
        detail=detail,
        errors=list(errors) if errors is not None else None,
    )
    return ProblemResponse(
        problem.model_dump(exclude_none=True), status_code=status, headers=headers
    )


def _phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"


# ── Handlers ──────────────────────────────────────────────────
async def _http_exception(request: Request, exc: Exception) -> ProblemResponse:
    assert isinstance(exc, StarletteHTTPException)
    detail = exc.detail if isinstance(exc.detail, str) else None
    return problem_response(exc.status_code, detail, headers=exc.headers)


async def _problem_error(request: Request, exc: Exception) -> ProblemResponse:
    assert isinstance(exc, ProblemError)
    return problem_response(
        exc.status,
        exc.detail,
        type=exc.type,
        title=exc.title,
        errors=exc.errors,
        headers=exc.headers,
    )


async def _validation_error(request: Request, exc: Exception) -> ProblemResponse:
    assert isinstance(exc, RequestValidationError)
    errors = [_field_error(error) for error in exc.errors()]
    count = len(errors)
    detail = f"{count} invalid field{'s' if count != 1 else ''}"
    return problem_response(422, detail, errors=errors)


def _field_error(error: Mapping[str, Any]) -> FieldError:
    loc = [part if isinstance(part, int) else str(part) for part in error.get("loc", ())]
    return FieldError(loc=loc, msg=str(error.get("msg", "Invalid value")))


async def _unhandled(request: Request, exc: Exception) -> ProblemResponse:
    # Not logged here: Starlette re-raises the exception after this response, and the root
    # application's handler (app.main) logs it.
    return problem_response(500, "Internal server error")


def install_problem_handlers(app: FastAPI) -> None:
    """Answer every error of ``app`` (and only of ``app``) with problem details."""
    app.add_exception_handler(StarletteHTTPException, _http_exception)
    app.add_exception_handler(ProblemError, _problem_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unhandled)


# ── Router helpers ────────────────────────────────────────────
@contextmanager
def errors_as_http(status_codes: Mapping[type[Exception], int]) -> Iterator[None]:
    """Turn a service error into an ``HTTPException`` (then a problem) with its status code.

    ``status_codes`` maps error classes (the first matching entry wins, subclasses included) to
    status codes; the error's ``detail`` attribute (or its message) becomes the problem's
    ``detail``. An error with a ``field`` attribute (a request body field the service rejected)
    becomes a problem with one :class:`FieldError` at ``["body", field]``, like a validation
    error. Other exceptions pass through.
    """
    try:
        yield
    except Exception as exc:
        for error_type, status in status_codes.items():
            if isinstance(exc, error_type):
                detail = getattr(exc, "detail", None) or str(exc)
                field = getattr(exc, "field", None)
                if isinstance(field, str):
                    raise ProblemError(
                        status,
                        "1 invalid field",
                        errors=[FieldError(loc=["body", field], msg=detail)],
                    ) from exc
                raise HTTPException(status_code=status, detail=detail) from exc
        raise


def problem_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI ``responses`` entries documenting problem details for these status codes."""
    return {
        status: {
            "description": _phrase(status),
            "content": {PROBLEM_MEDIA_TYPE: {"schema": {"$ref": "#/components/schemas/Problem"}}},
        }
        for status in statuses
    }


def problem_schemas() -> dict[str, Any]:
    """The JSON schemas of :class:`Problem` and :class:`FieldError`, keyed by component name, for
    the OpenAPI document of an application that uses :func:`problem_responses`."""
    schema = Problem.model_json_schema(ref_template="#/components/schemas/{model}")
    definitions: dict[str, Any] = schema.pop("$defs", {})
    return {**definitions, "Problem": schema}
