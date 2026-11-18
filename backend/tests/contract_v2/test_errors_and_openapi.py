"""Contract: v2's problem details for framework errors, and its OpenAPI document.

The document is the source of the generated TypeScript client (E1-03), so it is snapshotted as a
whole, like v1's; the conventions the client relies on are also asserted directly.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from httpx import AsyncClient
from syrupy.assertion import SnapshotAssertion

from tests.characterization.snapshot_utils import response_data
from tests.contract_v2.data import V2

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
    assert "secret" not in resp.text and "/api/v2" not in resp.text  # v1 echoes the URL (B10)


async def test_v1_errors_keep_their_shape(client: AsyncClient) -> None:
    # The problem handlers belong to the v2 app only (the characterization snapshots pin every
    # v1 response; this is the quick, readable check).
    v1 = await client.get("/api/v1/campaigns/not-a-uuid")
    assert v1.headers["content-type"] == "application/json"
    assert set(v1.json()) == {"detail"}
    missing = await client.get("/api/v1/does-not-exist")
    assert missing.json() == {"detail": "Not Found"}


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


async def test_v1_document_has_no_v2_operations(client: AsyncClient) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]
    assert paths and all(p.startswith("/api/v1/") or p.startswith("/health") for p in paths)


def _operations(document: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method, path, op) for path, ops in document["paths"].items() for method, op in ops.items()
    ]


def test_every_operation_follows_the_client_conventions() -> None:
    from app.v2 import v2_app

    document = v2_app.openapi()
    operations = _operations(document)
    ids = [op["operationId"] for _, _, op in operations]
    assert len(ids) == len(set(ids)) == 21
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
    for enum_name in ("ApplicationCategory", "PipelineStage", "ResumeStatus", "CampaignStatus"):
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
