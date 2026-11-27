"""Team management with real cookies (spec §5.2, §8.2, §11.3; E2-07): the member list, role and
client changes, deactivate and reactivate, and the guard rails: only owners manage owners and
admins, nobody changes or deactivates themselves, the agency keeps an active owner; another
agency's members are 404; every change is audited."""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.hiring.models import Client
from app.modules.identity import service
from app.modules.identity.dependencies import DEV_FALLBACK
from app.modules.identity.models import AuditEvent, Membership
from app.modules.identity.schemas import Role
from app.platform.config import settings
from tests.default_agency import DEFAULT_AGENCY_ID, DEFAULT_CLIENT_ID
from tests.identity.conftest import V2, Users, csrf, problem

OWNER = "owner@example.com"


async def _events(db: AsyncSession, action: str) -> list[AuditEvent]:
    db.expire_all()
    result = await db.execute(
        select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.id)
    )
    return list(result.scalars())


async def _membership(db: AsyncSession, user_id: uuid.UUID) -> Membership:
    db.expire_all()
    result = await db.execute(
        select(Membership).where(
            Membership.user_id == user_id, Membership.agency_id == DEFAULT_AGENCY_ID
        )
    )
    return result.scalar_one()


async def _team(users: Users, db: AsyncSession) -> dict[str, uuid.UUID]:
    """An owner, an admin, a recruiter and a viewer of the default agency, by role: their
    membership ids."""
    ids: dict[str, uuid.UUID] = {}
    for role in Role:
        user = await users.user(f"{role.value}@example.com", {DEFAULT_AGENCY_ID: role})
        ids[role.value] = (await _membership(db, user)).id
    return ids


async def _patch(client: AsyncClient, membership_id: uuid.UUID, **body: Any) -> Any:
    return await client.patch(f"{V2}/team/members/{membership_id}", json=body, headers=csrf(client))


async def _post(client: AsyncClient, membership_id: uuid.UUID, action: str) -> Any:
    return await client.post(f"{V2}/team/members/{membership_id}/{action}", headers=csrf(client))


# ── The list ──────────────────────────────────────────────────
async def test_owners_and_admins_list_the_members(
    users: Users, auth_client: AsyncClient, db_session: AsyncSession
) -> None:
    team = await _team(users, db_session)
    other = await users.agency()
    await users.user("elsewhere@example.com", {other: Role.owner})  # another agency's member

    for role in ("owner", "admin"):
        await users.login(f"{role}@example.com")
        resp = await auth_client.get(f"{V2}/team/members")
        assert resp.status_code == 200, resp.text
        page = resp.json()
        assert page["total"] == 4
        assert [m["user"]["email"] for m in page["items"]] == [
            "admin@example.com",
            "owner@example.com",
            "recruiter@example.com",
            "viewer@example.com",
        ]
        first = page["items"][0]
        assert first["id"] == str(team["admin"]) and first["role"] == "admin"
        assert first["status"] == "active" and first["all_clients"] is True
        assert first["clients"] == [] and first["deactivated_at"] is None
    for role in ("recruiter", "viewer"):
        await users.login(f"{role}@example.com")
        resp = await auth_client.get(f"{V2}/team/members")
        assert resp.status_code == 403
        assert problem(resp)["detail"] == "You don't have permission to see the team"


# ── Role and client changes ───────────────────────────────────
async def test_an_owner_changes_roles_and_client_access(
    users: Users, auth_client: AsyncClient, db_session: AsyncSession
) -> None:
    team = await _team(users, db_session)
    acme = Client(id=uuid.uuid4(), agency_id=DEFAULT_AGENCY_ID, name="Acme")
    db_session.add(acme)
    await db_session.flush()
    acme_id = acme.id
    await users.login(OWNER)

    resp = await _patch(
        auth_client,
        team["recruiter"],
        all_clients=False,
        client_ids=[str(DEFAULT_CLIENT_ID), str(acme_id)],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["all_clients"] is False
    assert body["clients"] == [
        {"id": str(acme_id), "name": "Acme"},
        {"id": str(DEFAULT_CLIENT_ID), "name": "General"},
    ]
    listed = {m["id"]: m for m in (await auth_client.get(f"{V2}/team/members")).json()["items"]}
    assert listed[str(team["recruiter"])]["clients"] == body["clients"]

    # Promoting to admin gives every client.
    resp = await _patch(auth_client, team["recruiter"], role="admin")
    assert resp.status_code == 200, resp.text
    assert (resp.json()["role"], resp.json()["all_clients"], resp.json()["clients"]) == (
        "admin",
        True,
        [],
    )
    events = await _events(db_session, "team.member_role_changed")
    first, second = events[-2:]
    assert first.details["role"] == {"old": "recruiter", "new": "recruiter"}
    assert first.details["all_clients"] == {"old": True, "new": False}
    assert sorted(first.details["client_ids"]["new"]) == sorted(
        [str(DEFAULT_CLIENT_ID), str(acme_id)]
    )
    assert second.details["role"] == {"old": "recruiter", "new": "admin"}
    assert second.details["client_ids"]["new"] == []
    assert (second.agency_id, second.target_id) == (DEFAULT_AGENCY_ID, str(team["recruiter"]))

    # No change, no event.
    resp = await _patch(auth_client, team["recruiter"], role="admin")
    assert resp.status_code == 200
    assert len(await _events(db_session, "team.member_role_changed")) == len(events)


async def test_admins_manage_recruiters_and_viewers_only(
    users: Users, auth_client: AsyncClient, db_session: AsyncSession
) -> None:
    team = await _team(users, db_session)
    second_admin = await users.user("admin2@example.com", {DEFAULT_AGENCY_ID: Role.admin})
    await users.login("admin@example.com")
    privileged = "You don't have permission to manage owners and admins"

    assert (await _patch(auth_client, team["viewer"], role="recruiter")).status_code == 200
    for membership, body in (
        (team["recruiter"], {"role": "admin"}),  # granting admin
        (team["recruiter"], {"role": "owner"}),
        (team["owner"], {"role": "viewer"}),  # changing an owner
        ((await _membership(db_session, second_admin)).id, {"role": "viewer"}),
    ):
        resp = await _patch(auth_client, membership, **body)
        assert resp.status_code == 403, body
        assert problem(resp)["detail"] == privileged
    for action in ("deactivate", "reactivate"):
        resp = await _post(auth_client, team["owner"], action)
        assert resp.status_code == 403
        assert problem(resp)["detail"] == privileged

    # Recruiters and viewers manage nobody.
    await users.login("recruiter@example.com")
    resp = await _patch(auth_client, team["viewer"], role="viewer")
    assert resp.status_code == 403
    assert problem(resp)["detail"] == "You don't have permission to manage the team"
    assert (await _post(auth_client, team["viewer"], "deactivate")).status_code == 403


async def test_nobody_changes_or_deactivates_themselves(
    users: Users, auth_client: AsyncClient, db_session: AsyncSession
) -> None:
    team = await _team(users, db_session)
    await users.user("owner2@example.com", {DEFAULT_AGENCY_ID: Role.owner})
    await users.login(OWNER)
    resp = await _patch(auth_client, team["owner"], role="admin")
    assert resp.status_code == 403
    assert problem(resp)["detail"] == "You can't change the role or client access of yourself"
    resp = await _post(auth_client, team["owner"], "deactivate")
    assert resp.status_code == 403
    assert problem(resp)["detail"] == "You can't deactivate yourself"
    await users.login("admin@example.com")
    assert (await _patch(auth_client, team["admin"], role="viewer")).status_code == 403
    assert (await _membership(db_session, (await _user_of(db_session, team["owner"])))).role == (
        "owner"
    )


async def _user_of(db: AsyncSession, membership_id: uuid.UUID) -> uuid.UUID:
    membership = await db.get(Membership, membership_id)
    assert membership is not None
    return membership.user_id


async def test_the_last_active_owner_stays(
    users: Users,
    auth_client: AsyncClient,
    browser: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Self-changes are refused anyway, so only someone who is not an owner of the agency can
    reach the last owner: the development fallback owner (``AUTH_REQUIRED=false``, a request
    without a session cookie: ``browser``)."""
    team = await _team(users, db_session)
    second = await users.user("owner2@example.com", {DEFAULT_AGENCY_ID: Role.owner})
    second_id = (await _membership(db_session, second)).id
    owner_user = await _user_of(db_session, team["owner"])

    # Two owners: one may demote the other.
    await users.login(OWNER)
    assert (await _patch(auth_client, second_id, role="admin")).status_code == 200
    # Now one: neither demoted nor deactivated.
    monkeypatch.setattr(settings, "AUTH_REQUIRED", False)
    fallback = browser
    for resp in (
        await fallback.patch(f"{V2}/team/members/{team['owner']}", json={"role": "admin"}),
        await fallback.post(f"{V2}/team/members/{team['owner']}/deactivate"),
    ):
        assert resp.status_code == 409, resp.text
        assert problem(resp)["detail"] == "The agency needs at least one active owner"
    assert (await _membership(db_session, owner_user)).status == "active"
    # Promote someone else, then the first owner may go.
    resp = await fallback.patch(f"{V2}/team/members/{second_id}", json={"role": "owner"})
    assert resp.status_code == 200
    resp = await fallback.post(f"{V2}/team/members/{team['owner']}/deactivate")
    assert resp.status_code == 200
    # A deactivated owner is no longer counted.
    resp = await fallback.patch(f"{V2}/team/members/{second_id}", json={"role": "viewer"})
    assert resp.status_code == 409


async def test_client_access_is_validated(
    users: Users, auth_client: AsyncClient, db_session: AsyncSession
) -> None:
    team = await _team(users, db_session)
    await users.login(OWNER)
    cases: list[tuple[uuid.UUID, dict[str, Any], str]] = [
        (team["admin"], {"all_clients": False}, "all_clients"),
        (team["admin"], {"client_ids": [str(DEFAULT_CLIENT_ID)]}, "client_ids"),
        (team["recruiter"], {"all_clients": False}, "client_ids"),  # needs a client
        (
            team["recruiter"],
            {"all_clients": False, "client_ids": [str(uuid.uuid4())]},
            "client_ids",
        ),
        (team["recruiter"], {"role": "manager"}, "role"),
    ]
    for membership, body, field in cases:
        resp = await _patch(auth_client, membership, **body)
        assert resp.status_code == 422, (body, resp.text)
        assert [e["loc"] for e in problem(resp)["errors"]] == [["body", field]], body


# ── Deactivate and reactivate ─────────────────────────────────
async def test_deactivation_ends_access_at_once_and_reactivation_restores_it(
    users: Users, auth_client: AsyncClient, browser: AsyncClient, db_session: AsyncSession
) -> None:
    team = await _team(users, db_session)
    assert (await users.login("recruiter@example.com", client=browser)).status_code == 200
    assert (await browser.get(f"{V2}/campaigns")).status_code == 200
    await users.login(OWNER)

    resp = await _post(auth_client, team["recruiter"], "deactivate")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "deactivated" and resp.json()["deactivated_at"]
    # Their very next request: no access to the agency (the session itself is still valid).
    resp = await browser.get(f"{V2}/campaigns")
    assert resp.status_code == 403 and problem(resp)["detail"] == "Choose an agency"
    # Twice changes nothing (one event).
    assert (await _post(auth_client, team["recruiter"], "deactivate")).status_code == 200
    assert len(await _events(db_session, "team.member_deactivated")) == 1
    listed = (await auth_client.get(f"{V2}/team/members")).json()["items"]
    assert {m["id"]: m["status"] for m in listed}[str(team["recruiter"])] == "deactivated"

    resp = await _post(auth_client, team["recruiter"], "reactivate")
    assert resp.status_code == 200
    assert (resp.json()["status"], resp.json()["deactivated_at"]) == ("active", None)
    assert (await browser.get(f"{V2}/campaigns")).status_code == 200
    for action in ("team.member_deactivated", "team.member_reactivated"):
        [event] = await _events(db_session, action)
        assert event.target_id == str(team["recruiter"])
        assert event.details["role"] == "recruiter"


async def test_another_agencys_members_are_not_found(
    users: Users, auth_client: AsyncClient, db_session: AsyncSession
) -> None:
    await users.user(OWNER)
    other = await users.agency()
    theirs = await users.user("theirs@example.com", {other: Role.viewer})
    db_session.expire_all()
    their_membership = (
        await db_session.execute(select(Membership).where(Membership.user_id == theirs))
    ).scalar_one()
    their_id = their_membership.id
    await users.login(OWNER)
    for membership_id in (their_id, uuid.uuid4()):
        assert (await _patch(auth_client, membership_id, role="viewer")).status_code == 404
        for action in ("deactivate", "reactivate"):
            resp = await _post(auth_client, membership_id, action)
            assert resp.status_code == 404
            assert problem(resp)["detail"] == "Member not found"
    db_session.expire_all()
    unchanged = await db_session.get(Membership, their_id)
    assert unchanged is not None and (unchanged.role, unchanged.status) == ("viewer", "active")


# ── The decision functions (the permission matrix, E2-06) ─────
@pytest.mark.parametrize("role", list(Role))
def test_the_team_rows_of_the_matrix(role: Role) -> None:
    """Spec §5.2: team:read and team:manage for owners and admins, team:manage_privileged for
    owners; recruiters and viewers have none."""
    from app.modules.identity.permissions import Permission, has_permission

    team = {Permission.team_read, Permission.team_manage, Permission.team_manage_privileged}
    expected = {
        Role.owner: team,
        Role.admin: {Permission.team_read, Permission.team_manage},
        Role.recruiter: set(),
        Role.viewer: set(),
    }[role]
    assert {p for p in team if has_permission(role, p)} == expected

    principal = dataclasses.replace(DEV_FALLBACK, role=role)
    manages = {target for target in Role if service.can_manage(principal, target)}
    assert (
        manages
        == {
            Role.owner: set(Role),
            Role.admin: {Role.recruiter, Role.viewer},
            Role.recruiter: set(),
            Role.viewer: set(),
        }[role]
    )
