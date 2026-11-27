"""Contract: /api/v2/clients (list, create, detail, rename, archive) and their audit events
(EPIC-02 spec §4.7, §8.2). As the default agency's owner (the ``client`` fixture), whose agency
has its "General" client from the E2-01 migration."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from syrupy.assertion import SnapshotAssertion

from app.modules.hiring.models import Client
from app.modules.identity.models import AuditEvent
from app.platform.tenancy import DEFAULT_AGENCY_ID, DEFAULT_CLIENT_ID
from tests.characterization.snapshot_utils import response_data
from tests.contract_v2.data import UNKNOWN_ID, V2
from tests.factories import at, make_client


async def _clients(db: AsyncSession) -> None:
    """General (the migration's), Acme, beta labs (lower-case) and an archived Zeta."""
    await make_client(db, "Acme", created_at=at(1))
    await make_client(db, "beta labs", created_at=at(2))
    await make_client(db, "Zeta", status="archived", created_at=at(3))


async def _events(db: AsyncSession, action: str) -> list[dict[str, Any]]:
    rows = await db.scalars(select(AuditEvent).where(AuditEvent.action == action))
    return [
        {
            "agency_id": e.agency_id,
            "actor_user_id": e.actor_user_id,
            "target": (e.target_type, e.target_id),
            "details": e.details,
        }
        for e in rows
    ]


async def _count(db: AsyncSession) -> int:
    return await db.scalar(select(func.count(Client.id))) or 0


# ── GET /clients ──────────────────────────────────────────────
async def test_list_clients(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    await _clients(db_session)
    resp = await client.get(f"{V2}/clients")
    assert response_data(resp) == snapshot


@pytest.mark.parametrize(
    "params",
    [
        pytest.param("status=active", id="active"),
        pytest.param("status=archived&status=active", id="status-any-of"),
        pytest.param("q=A", id="q-ignores-case"),
        pytest.param("q=%25", id="q-wildcard-literal"),
        pytest.param("page=2&page_size=2", id="page-2"),
    ],
)
async def test_list_clients_filters(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion, params: str
) -> None:
    await _clients(db_session)
    resp = await client.get(f"{V2}/clients?{params}")
    body = resp.json()
    assert resp.status_code == 200
    assert {"names": [c["name"] for c in body["items"]], "total": body["total"],
            "page": body["page"], "page_size": body["page_size"]} == snapshot  # fmt: skip


@pytest.mark.parametrize(
    "params",
    [
        pytest.param("status=deleted", id="unknown-status"),
        pytest.param("page_size=101", id="page-size-over-100"),
        pytest.param(f"q={'x' * 201}", id="q-too-long"),
    ],
)
async def test_list_clients_invalid_query(
    client: AsyncClient, snapshot: SnapshotAssertion, params: str
) -> None:
    resp = await client.get(f"{V2}/clients?{params}")
    assert response_data(resp) == snapshot


# ── GET /clients/{id} ─────────────────────────────────────────
async def test_get_client(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.get(f"{V2}/clients/{DEFAULT_CLIENT_ID}")
    assert response_data(resp) == snapshot


@pytest.mark.parametrize("client_id", [UNKNOWN_ID, "not-a-uuid"], ids=["unknown", "invalid"])
async def test_get_client_not_found_or_invalid(
    client: AsyncClient, snapshot: SnapshotAssertion, client_id: str
) -> None:
    resp = await client.get(f"{V2}/clients/{client_id}")
    assert response_data(resp) == snapshot


# ── POST /clients ─────────────────────────────────────────────
async def test_create_client(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    resp = await client.post(f"{V2}/clients", json={"name": "  Northwind Traders "})
    assert response_data(resp) == snapshot
    created = resp.json()
    assert (await client.get(f"{V2}/clients/{created['id']}")).json() == created
    assert await _events(db_session, "client.created") == [
        {
            "agency_id": DEFAULT_AGENCY_ID,
            "actor_user_id": None,  # the client fixture's development fallback owner
            "target": ("client", created["id"]),
            "details": {"name": "Northwind Traders"},
        }
    ]


@pytest.mark.parametrize(
    "name", ["general", "ZETA"], ids=["same-name-other-case", "an-archived-clients-name"]
)
async def test_create_client_with_a_name_in_use(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion, name: str
) -> None:
    await _clients(db_session)
    before = await _count(db_session)
    resp = await client.post(f"{V2}/clients", json={"name": name})
    assert response_data(resp) == snapshot
    assert await _count(db_session) == before
    assert await _events(db_session, "client.created") == []


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="empty-object"),
        pytest.param({"name": "   "}, id="blank-name"),
        pytest.param({"name": "x" * 201}, id="name-too-long"),
        pytest.param({"name": None}, id="null-name"),
        pytest.param({"name": "Acme", "status": "archived"}, id="unknown-field"),
    ],
)
async def test_create_client_invalid(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion, payload: object
) -> None:
    resp = await client.post(f"{V2}/clients", json=payload)
    assert response_data(resp) == snapshot
    assert await _count(db_session) == 1  # General


# ── PATCH /clients/{id} ───────────────────────────────────────
async def test_rename_archive_and_restore_a_client(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    acme = await make_client(db_session, "Acme")
    url = f"{V2}/clients/{acme.id}"
    renamed = await client.patch(url, json={"name": "Acme Holdings"})
    archived = await client.patch(url, json={"status": "archived"})
    unchanged = await client.patch(url, json={"name": "Acme Holdings", "status": "archived"})
    restored = await client.patch(url, json={"status": "active", "name": "acme holdings"})
    assert [response_data(r) for r in (renamed, archived, unchanged, restored)] == snapshot
    events = await _events(db_session, "client.updated")
    assert [e["details"] for e in events] == [
        {"name": {"old": "Acme", "new": "Acme Holdings"}},
        {"status": {"old": "active", "new": "archived"}},
        # the unchanged request recorded nothing
        {"name": {"old": "Acme Holdings", "new": "acme holdings"},
         "status": {"old": "archived", "new": "active"}},
    ]  # fmt: skip
    assert {e["target"] for e in events} == {("client", str(acme.id))}


async def test_rename_a_client_to_a_name_in_use(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    await _clients(db_session)
    acme = await db_session.scalar(select(Client).where(Client.name == "Acme"))
    assert acme is not None
    resp = await client.patch(f"{V2}/clients/{acme.id}", json={"name": "BETA LABS"})
    assert response_data(resp) == snapshot
    await db_session.refresh(acme)
    assert acme.name == "Acme"
    assert await _events(db_session, "client.updated") == []


@pytest.mark.parametrize(
    ("client_id", "payload"),
    [
        pytest.param(UNKNOWN_ID, {"name": "X"}, id="unknown-client"),
        pytest.param(str(DEFAULT_CLIENT_ID), {"name": None}, id="null-name"),
        pytest.param(str(DEFAULT_CLIENT_ID), {"status": None}, id="null-status"),
        pytest.param(str(DEFAULT_CLIENT_ID), {"status": "deleted"}, id="unknown-status"),
        pytest.param(str(DEFAULT_CLIENT_ID), {"agency_id": UNKNOWN_ID}, id="unknown-field"),
    ],
)
async def test_update_client_not_found_or_invalid(
    client: AsyncClient, snapshot: SnapshotAssertion, client_id: str, payload: object
) -> None:
    resp = await client.patch(f"{V2}/clients/{client_id}", json=payload)
    assert response_data(resp) == snapshot
