"""Contract: v2's problem details for framework errors, its OpenAPI document, and the root
application around it (the health checks; ``/api/v1`` answers 404 since E2-12).

The document is the source of the generated TypeScript client (E1-03), so it is snapshotted as a
whole; the conventions the client relies on are also asserted directly.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from syrupy.assertion import SnapshotAssertion

from app.modules.hiring.models import Campaign
from tests.contract_v2.data import UNKNOWN_ID, V2
from tests.snapshot_utils import response_data

CAMEL_CASE = re.compile(r"^[a-z]+(?:[A-Z][a-z0-9]*)*$")


# ── Problem details for errors the framework raises ───────────
async def test_unknown_route(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.get(f"{V2}/does-not-exist")
    assert response_data(resp) == snapshot


async def test_method_not_allowed(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.put(f"{V2}/campaigns", json={})
    assert response_data(resp, "allow") == snapshot


async def test_malformed_json_body(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.post(
        f"{V2}/campaigns", content=b"{not json", headers={"content-type": "application/json"}
    )
    data = response_data(resp)
    # The JSON decoder's message and position are pydantic's wording, not the contract.
    for error in data["body"]["errors"]:
        error["msg"] = "<json decode error>"
    assert data == snapshot


async def test_json_body_without_content_type(
    client: AsyncClient, snapshot: SnapshotAssertion
) -> None:
    # v2 keeps FastAPI's strict_content_type default (v1 turns it off): a body without a JSON
    # Content-Type is not parsed as JSON, so the campaign body is invalid.
    resp = await client.post(f"{V2}/campaigns", content=b'{"title": "T", "role": "Dev"}')
    assert "content-type" not in resp.request.headers
    assert response_data(resp) == snapshot


async def test_unhandled_error_is_a_problem_without_internals(
    raw_client: AsyncClient, monkeypatch: pytest.MonkeyPatch, snapshot: SnapshotAssertion
) -> None:
    from app.modules.talent import service as talent

    async def boom(*_: Any) -> int:
        raise RuntimeError("secret database detail")

    monkeypatch.setattr(talent, "count_people", boom)
    resp = await raw_client.get(f"{V2}/stats")
    assert response_data(resp) == snapshot
    assert "secret" not in resp.text and "/api/v2" not in resp.text  # v1 echoed the URL (B10)


async def test_an_unhandled_error_is_logged_without_the_query_string(
    raw_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The root application's handler logged it until /api/v1 was removed (E2-12).
    from app.modules.talent import service as talent
    from app.platform import problems

    async def boom(*_: Any) -> int:
        raise RuntimeError("database detail")

    logged: list[tuple[str, dict[str, Any]]] = []

    class Logger:
        def error(self, event: str, **fields: Any) -> None:
            logged.append((event, fields))

    monkeypatch.setattr(talent, "count_people", boom)
    monkeypatch.setattr(problems, "logger", Logger())
    assert (await raw_client.get(f"{V2}/stats?client_id={UNKNOWN_ID}")).status_code == 500
    assert logged == [
        (
            "unhandled_exception",
            {"method": "GET", "path": "/api/v2/stats", "error": "database detail"},
        )
    ]


# ── /api/v1 is gone (E2-12) ───────────────────────────────────
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/campaigns"),
        ("POST", "/api/v1/campaigns"),
        ("POST", "/api/v1/campaigns/parse-jd"),
        ("GET", f"/api/v1/campaigns/{UNKNOWN_ID}"),
        ("PUT", f"/api/v1/campaigns/{UNKNOWN_ID}"),
        ("DELETE", f"/api/v1/campaigns/{UNKNOWN_ID}"),
        ("GET", f"/api/v1/campaigns/{UNKNOWN_ID}/stats"),
        ("GET", f"/api/v1/campaigns/{UNKNOWN_ID}/candidates"),
        ("GET", f"/api/v1/campaigns/{UNKNOWN_ID}/candidates/export"),
        ("PATCH", f"/api/v1/campaigns/{UNKNOWN_ID}/candidates/bulk-pipeline"),
        ("DELETE", f"/api/v1/campaigns/{UNKNOWN_ID}/candidates/bulk"),
        ("POST", f"/api/v1/campaigns/{UNKNOWN_ID}/upload"),
        ("GET", f"/api/v1/campaigns/{UNKNOWN_ID}/resumes"),
        ("POST", f"/api/v1/campaigns/{UNKNOWN_ID}/resumes/retry-failed"),
        ("GET", f"/api/v1/campaigns/{UNKNOWN_ID}/stream"),
        ("GET", f"/api/v1/candidates/{UNKNOWN_ID}"),
        ("PUT", f"/api/v1/candidates/{UNKNOWN_ID}/category"),
        ("PATCH", f"/api/v1/candidates/{UNKNOWN_ID}/notes"),
        ("PATCH", f"/api/v1/candidates/{UNKNOWN_ID}/pipeline"),
        ("DELETE", f"/api/v1/candidates/{UNKNOWN_ID}"),
        ("GET", f"/api/v1/resumes/{UNKNOWN_ID}"),
        ("GET", f"/api/v1/resumes/{UNKNOWN_ID}/candidate"),
        ("GET", "/api/v1/stats"),
        ("GET", "/api/v1/health"),
    ],
)
async def test_every_former_v1_endpoint_is_not_found(
    client: AsyncClient, db_session: AsyncSession, method: str, path: str
) -> None:
    """Each of v1's 23 operations (and the ``/api/v1/health`` its document listed, B10) answers
    404 and writes nothing."""
    resp = await client.request(method, path, json={"title": "T", "role": "R"})
    assert (resp.status_code, resp.json()) == (404, {"detail": "Not Found"})
    assert (await db_session.scalar(select(func.count()).select_from(Campaign))) == 0


async def test_the_health_checks_stay(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert (resp.status_code, resp.json()["status"]) == (200, "ok")


# ── The OpenAPI document ──────────────────────────────────────
async def test_openapi_document(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.get(f"{V2}/openapi.json")
    assert resp.status_code == 200
    assert resp.json() == snapshot


async def test_openapi_document_is_the_offline_export(client: AsyncClient) -> None:
    # E1-03 generates the client from v2_app.openapi() without a server: same document.
    from app.v2 import v2_app

    served = (await client.get(f"{V2}/openapi.json")).json()
    assert served == v2_app.openapi()
    assert served["servers"] == [{"url": "/api/v2"}]


async def test_the_root_document_lists_only_the_health_checks(client: AsyncClient) -> None:
    # /api/v2 has its own document (a mounted app's routes are not in the parent's), and /api/v1
    # is gone (E2-12).
    paths = (await client.get("/openapi.json")).json()["paths"]
    assert sorted(paths) == ["/health", "/health/ready"]


def _operations(document: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method, path, op) for path, ops in document["paths"].items() for method, op in ops.items()
    ]


def test_every_operation_follows_the_client_conventions() -> None:
    from app.v2 import v2_app

    document = v2_app.openapi()
    operations = _operations(document)
    ids = [op["operationId"] for _, _, op in operations]
    # 21 + 9 identity (E2-05) + 4 clients (E2-06) + 4 auth and 8 team operations (E2-07)
    # + 3 for Google and Microsoft sign-in (E2-08)
    assert len(ids) == len(set(ids)) == 49
    for method, path, op in operations:
        assert CAMEL_CASE.match(op["operationId"]), (method, path)
        assert len(op["tags"]) == 1, (method, path)
        for status, response in op["responses"].items():
            if status.startswith(("4", "5")):
                assert response["content"] == {
                    "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}
                }, (method, path, status)
        has_input = bool(op.get("parameters") or op.get("requestBody"))
        assert ("422" in op["responses"]) == has_input, (method, path)
    schemas = document["components"]["schemas"]
    assert "HTTPValidationError" not in schemas  # FastAPI's default 422 shape is never used
    for enum_name in (
        "ApplicationCategory",
        "PipelineStage",
        "ResumeStatus",
        "CampaignStatus",
        "ClientStatus",
        "Permission",
        "OidcProvider",
        "OidcLoginError",
    ):
        assert schemas[enum_name]["enum"], enum_name
    assert schemas["Problem"]["required"] == ["title", "status"]


def test_a_v2_route_without_an_operation_id_is_refused() -> None:
    from app.v2 import _check_operation_ids, _operation_id

    router = APIRouter()

    @router.get("/x")
    async def x() -> None: ...

    app = FastAPI(generate_unique_id_function=_operation_id)
    app.include_router(router)
    # FastAPI >= 0.137 resolves included routes lazily: create_v2_app checks them explicitly.
    with pytest.raises(RuntimeError, match="no explicit operation_id"):
        _check_operation_ids(app)
