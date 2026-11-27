"""Contract: the team endpoints of /api/v2 (E2-07): members (list, role and client changes,
deactivate, reactivate) and invitations (list, create, resend, revoke), with their problems
(403, 404, 409, 422, 429). Real sessions and cookies (``tests/identity/conftest.py``); the usual
UUID and datetime masking, plus ``Retry-After``."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from syrupy.assertion import SnapshotAssertion

from app.modules.hiring.models import Client
from app.modules.identity.models import Membership
from app.modules.identity.schemas import Role
from app.platform.config import settings
from tests.contract_v2.test_identity import _data
from tests.default_agency import DEFAULT_AGENCY_ID, DEFAULT_CLIENT_ID
from tests.identity.conftest import V2, Users, browser, csrf  # noqa: F401 (fixture)

ACME = uuid.UUID("0000000a-0000-4000-8000-00000000acde")


async def _team(users: Users, db: AsyncSession) -> dict[str, uuid.UUID]:
    db.add(Client(id=ACME, agency_id=DEFAULT_AGENCY_ID, name="Acme"))
    await db.flush()
    ids: dict[str, uuid.UUID] = {}
    for role in Role:
        user = await users.user(f"{role.value}@example.com", {DEFAULT_AGENCY_ID: role})
        ids[role.value] = (
            await db.execute(select(Membership.id).where(Membership.user_id == user))
        ).scalar_one()
    return ids


async def test_members(
    users: Users,
    auth_client: AsyncClient,
    browser: AsyncClient,  # noqa: F811
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    snapshot: SnapshotAssertion,
) -> None:
    team = await _team(users, db_session)
    responses: dict[str, Any] = {}

    async def patch(name: str, membership: uuid.UUID, **body: Any) -> None:
        responses[name] = _data(
            await auth_client.patch(
                f"{V2}/team/members/{membership}", json=body, headers=csrf(auth_client)
            )
        )

    async def post(name: str, membership: uuid.UUID, action: str) -> None:
        responses[name] = _data(
            await auth_client.post(
                f"{V2}/team/members/{membership}/{action}", headers=csrf(auth_client)
            )
        )

    await users.login("recruiter@example.com")
    responses["list_as_recruiter"] = _data(await auth_client.get(f"{V2}/team/members"))
    await patch("update_as_recruiter", team["viewer"], role="recruiter")

    await users.login("admin@example.com")
    await patch("update_owner_as_admin", team["owner"], role="viewer")

    await users.login("owner@example.com")
    await patch(
        "limit_clients",
        team["recruiter"],
        all_clients=False,
        client_ids=[str(ACME), str(DEFAULT_CLIENT_ID)],
    )
    await patch("promote", team["viewer"], role="admin")
    await patch("update_self", team["owner"], role="admin")
    await patch("update_unknown", uuid.UUID(int=1), role="viewer")
    await patch("update_invalid", team["admin"], all_clients=False)
    await patch("update_unknown_client", team["recruiter"], client_ids=[str(uuid.UUID(int=2))])
    await post("deactivate", team["recruiter"], "deactivate")
    await post("deactivate_self", team["owner"], "deactivate")
    responses["list"] = _data(await auth_client.get(f"{V2}/team/members"))
    await post("reactivate", team["recruiter"], "reactivate")
    # The last active owner, reachable only by the development fallback owner (a request
    # without a session cookie when AUTH_REQUIRED=false).
    monkeypatch.setattr(settings, "AUTH_REQUIRED", False)
    responses["demote_last_owner"] = _data(
        await browser.patch(f"{V2}/team/members/{team['owner']}", json={"role": "admin"})
    )
    assert responses == snapshot


async def test_invitations(
    users: Users,
    auth_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    snapshot: SnapshotAssertion,
) -> None:
    await _team(users, db_session)
    responses: dict[str, Any] = {}

    async def create(name: str, **body: Any) -> dict[str, Any]:
        resp = await auth_client.post(
            f"{V2}/team/invitations", json=body, headers=csrf(auth_client)
        )
        responses[name] = _data(resp, "retry-after")
        result: dict[str, Any] = resp.json()
        return result

    await users.login("viewer@example.com")
    responses["list_as_viewer"] = _data(await auth_client.get(f"{V2}/team/invitations"))
    await users.login("admin@example.com")
    await create("create_admin_as_admin", email="boss@example.com", role="admin")

    await users.login("owner@example.com")
    first = await create(
        "create",
        email="Nina@Example.com",
        role="recruiter",
        all_clients=False,
        client_ids=[str(ACME)],
    )
    await create("create_admin", email="boss@example.com", role="admin")
    await create("create_member", email="viewer@example.com", role="viewer")
    await create("create_invalid_email", email="nina", role="viewer")
    await create("create_owner_limited", email="x@example.com", role="owner", all_clients=False)
    await create("create_without_clients", email="x@example.com", role="viewer", all_clients=False)
    responses["list"] = _data(await auth_client.get(f"{V2}/team/invitations"))
    responses["resend"] = _data(
        await auth_client.post(
            f"{V2}/team/invitations/{first['id']}/resend", headers=csrf(auth_client)
        )
    )
    responses["revoke"] = _data(
        await auth_client.delete(f"{V2}/team/invitations/{first['id']}", headers=csrf(auth_client))
    )
    responses["revoke_again"] = _data(
        await auth_client.delete(f"{V2}/team/invitations/{first['id']}", headers=csrf(auth_client))
    )
    responses["resend_revoked"] = _data(
        await auth_client.post(
            f"{V2}/team/invitations/{first['id']}/resend", headers=csrf(auth_client)
        )
    )
    monkeypatch.setattr(settings, "INVITATIONS_MAX_PER_AGENCY_PER_DAY", 3)
    await create("create_over_the_daily_limit", email="late@example.com", role="viewer")
    assert responses == snapshot
