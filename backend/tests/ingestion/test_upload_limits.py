"""The upload endpoints' body-parsing limits (S1, ``app.platform.uploads``), v1 and v2.

Edges of each limit: at the limit the body is parsed and the endpoint answers as it always has;
one over, the parser refuses it with a 400 before the endpoint runs (nothing stored or queued).
The response bodies are recorded by the snapshot tests (``characterization/test_resumes.py``,
``contract_v2/test_uploads.py``); this file checks where each limit falls.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.config import settings
from app.platform.uploads import MAX_FIELD_BYTES, MAX_FORM_FIELDS, UploadRoute
from tests.factories import make_campaign
from tests.fakes import CeleryRecorder, InMemoryStorage

PDF = b"%PDF-1.4\n% limits\n"
UPLOAD_URLS = {
    "v1": "/api/v1/campaigns/{id}/upload",
    "v2": "/api/v2/campaigns/{id}/uploads",
}
# The endpoint's own "too many files" answer (unchanged by S1): v1 400, v2 422.
TOO_MANY_FILES_STATUS = {"v1": 400, "v2": 422}

pytestmark = pytest.mark.parametrize("api", ["v1", "v2"])


def pdfs(count: int) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("files", (f"{i}.pdf", PDF + str(i).encode(), "application/pdf")) for i in range(count)]


def detail(resp: Response) -> Any:
    return resp.json()["detail"]


@pytest.fixture
def batch_limit(monkeypatch: pytest.MonkeyPatch) -> int:
    """A small MAX_FILES_PER_BATCH, so the file-count edges need few files (read per request)."""
    monkeypatch.setattr(settings, "MAX_FILES_PER_BATCH", 2)
    return 2


async def _post(client: AsyncClient, db: AsyncSession, api: str, **kwargs: Any) -> Response:
    campaign = await make_campaign(db)
    return await client.post(UPLOAD_URLS[api].format(id=campaign.id), **kwargs)


# ── Non-file fields ───────────────────────────────────────────
async def test_non_file_field_at_the_size_limit_is_parsed(
    api: str, client: AsyncClient, db_session: AsyncSession
) -> None:
    resp = await _post(client, db_session, api, data={"note": "x" * MAX_FIELD_BYTES}, files=pdfs(1))
    assert resp.status_code == 202, resp.text


async def test_oversized_non_file_field_is_rejected(
    api: str,
    client: AsyncClient,
    db_session: AsyncSession,
    celery_calls: CeleryRecorder,
    fake_storage: InMemoryStorage,
) -> None:
    resp = await _post(
        client, db_session, api, data={"note": "x" * (MAX_FIELD_BYTES + 1)}, files=pdfs(1)
    )
    assert (resp.status_code, detail(resp)) == (400, "Part exceeded maximum size of 16KB.")
    assert celery_calls.calls == []
    assert fake_storage.objects == {}


async def test_oversized_urlencoded_field_is_rejected(
    api: str, client: AsyncClient, db_session: AsyncSession
) -> None:
    # Starlette < 1.3.1 ignored the limits for urlencoded bodies (GHSA-82w8-qh3p-5jfq).
    resp = await _post(client, db_session, api, data={"files": "x" * MAX_FIELD_BYTES})
    assert (resp.status_code, detail(resp)) == (400, "Field exceeded maximum size of 16KB.")


async def test_non_file_fields_at_the_count_limit_are_parsed(
    api: str, client: AsyncClient, db_session: AsyncSession
) -> None:
    fields = {f"f{i}": "x" for i in range(MAX_FORM_FIELDS)}
    resp = await _post(client, db_session, api, data=fields, files=pdfs(1))
    assert resp.status_code == 202, resp.text


async def test_too_many_non_file_fields_are_rejected(
    api: str, client: AsyncClient, db_session: AsyncSession
) -> None:
    fields = {f"f{i}": "x" for i in range(MAX_FORM_FIELDS + 1)}
    resp = await _post(client, db_session, api, data=fields, files=pdfs(1))
    assert (resp.status_code, detail(resp)) == (
        400,
        f"Too many fields. Maximum number of fields is {MAX_FORM_FIELDS}.",
    )


# ── Files ─────────────────────────────────────────────────────
async def test_one_file_over_the_batch_limit_reaches_the_endpoint(
    api: str, client: AsyncClient, db_session: AsyncSession, batch_limit: int
) -> None:
    resp = await _post(client, db_session, api, files=pdfs(batch_limit + 1))
    assert (resp.status_code, detail(resp)) == (
        TOO_MANY_FILES_STATUS[api],
        f"Maximum {batch_limit} files per upload batch",
    )


async def test_more_files_are_rejected_while_parsing(
    api: str,
    client: AsyncClient,
    db_session: AsyncSession,
    batch_limit: int,
    celery_calls: CeleryRecorder,
    fake_storage: InMemoryStorage,
) -> None:
    resp = await _post(client, db_session, api, files=pdfs(batch_limit + 2))
    assert (resp.status_code, detail(resp)) == (
        400,
        f"Too many files. Maximum number of files is {batch_limit + 1}.",
    )
    assert celery_calls.calls == []
    assert fake_storage.objects == {}


def test_only_form_routes_parse_with_the_limits(api: str) -> None:
    """``UploadRoute`` wraps the routes with a form body (the upload) and no others."""
    from app.modules.ingestion import router, router_v2

    module_router = router.router if api == "v1" else router_v2.router
    routes = [r for r in module_router.routes if isinstance(r, UploadRoute)]
    wrapped = {r.path for r in routes if r.get_route_handler().__name__ == "parse_form_with_limits"}
    assert wrapped == {
        UPLOAD_URLS[api].removeprefix(f"/api/{api}").replace("{id}", "{campaign_id}")
    }
    assert len(routes) == len(module_router.routes) > 1  # the others use the class, unwrapped
