"""``/me``, ``/me/password``, ``/me/active-agency`` and ``/agency`` (spec §4.3, §8.2) with real
sessions, their audit events, and the tenant every v2 request is bound to."""

from __future__ import annotations

import hashlib
import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models import AuditEvent, UserSession
from app.modules.identity.schemas import Role
from tests.default_agency import DEFAULT_AGENCY_ID
from tests.factories import make_campaign
from tests.identity.conftest import (
    PASSWORD,
    SESSION_COOKIE,
    V2,
    Users,
    csrf,
    problem,
    set_cookie_headers,
)

NEW_PASSWORD = "a much better passphrase"


async def _events(db: AsyncSession, action: str) -> list[AuditEvent]:
    db.expire_all()
    result = await db.execute(select(AuditEvent).where(AuditEvent.action == action))
    return list(result.scalars())


# ── /me ───────────────────────────────────────────────────────
async def test_me_needs_a_session(auth_client: AsyncClient) -> None:
    for method, path in (("GET", "/me"), ("PATCH", "/me"), ("POST", "/me/password")):
        resp = await auth_client.request(method, V2 + path, json={})
        assert resp.status_code == 401, path
        assert problem(resp)["detail"] == "Not signed in"


async def test_update_me(users: Users, auth_client: AsyncClient) -> None:
    await users.user("ada@example.com")
    await users.login("ada@example.com")
    resp = await auth_client.patch(
        f"{V2}/me", json={"display_name": "  Ada Lovelace  "}, headers=csrf(auth_client)
    )
    assert resp.status_code == 200 and resp.json()["user"]["display_name"] == "Ada Lovelace"
    assert (await auth_client.get(f"{V2}/me")).json()["user"]["display_name"] == "Ada Lovelace"
    blank = await auth_client.patch(
        f"{V2}/me", json={"display_name": " "}, headers=csrf(auth_client)
    )
    assert blank.status_code == 422


async def test_change_password_rotates_and_signs_out_everywhere(
    users: Users, auth_client: AsyncClient, db_session: AsyncSession
) -> None:
    user_id = await users.user("ada@example.com")
    async with AsyncClient(transport=auth_client._transport, base_url="http://test") as laptop:
        assert (await users.login("ada@example.com", client=laptop)).status_code == 200
        await users.login("ada@example.com")
        old = auth_client.cookies.get(SESSION_COOKIE)

        resp = await auth_client.post(
            f"{V2}/me/password",
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
            headers=csrf(auth_client),
        )
        assert resp.status_code == 204, resp.text
        assert SESSION_COOKIE in set_cookie_headers(resp)
        new = auth_client.cookies.get(SESSION_COOKIE)
        assert new and new != old
        # This browser continues in the new session; the other one is signed out.
        assert (await auth_client.get(f"{V2}/me")).status_code == 200
        assert (await laptop.get(f"{V2}/me")).status_code == 401

    db_session.expire_all()
    sessions = (
        (await db_session.execute(select(UserSession).where(UserSession.user_id == user_id)))
        .scalars()
        .all()
    )
    open_sessions = [s for s in sessions if s.revoked_at is None]
    assert [s.id_hash for s in open_sessions] == [hashlib.sha256(new.encode()).digest()]
    [event] = await _events(db_session, "auth.password_changed")
    assert event.actor_user_id == user_id
    # The new password signs in, the old one does not.
    assert (await users.login("ada@example.com", PASSWORD)).status_code == 401
    assert (await users.login("ada@example.com", NEW_PASSWORD)).status_code == 200


async def test_change_password_checks_the_current_one_and_the_policy(
    users: Users, auth_client: AsyncClient
) -> None:
    await users.user("ada@example.com")
    await users.login("ada@example.com")
    cases = [
        (
            {"current_password": "not it at all", "new_password": NEW_PASSWORD},
            "current_password",
            "Your current password is not correct",
        ),
        (
            {"current_password": PASSWORD, "new_password": "too short"},
            "new_password",
            "Use at least 12 characters",
        ),
        (
            {"current_password": PASSWORD, "new_password": "x" * 129},
            "new_password",
            "Use at most 128 characters",
        ),
        (
            {"current_password": PASSWORD, "new_password": " ADA@example.com "},
            "new_password",
            "Don't use your email address as your password",
        ),
    ]
    for body, field, message in cases:
        resp = await auth_client.post(f"{V2}/me/password", json=body, headers=csrf(auth_client))
        assert resp.status_code == 422, body
        assert problem(resp)["errors"] == [{"loc": ["body", field], "msg": message}]
    # Nothing changed: still signed in with the old password.
    assert (await auth_client.get(f"{V2}/me")).status_code == 200


# ── Agency switch ─────────────────────────────────────────────
async def test_agency_switch(
    users: Users, auth_client: AsyncClient, db_session: AsyncSession
) -> None:
    northbank = await users.agency("Northbank Talent")
    user_id = await users.user(
        "ada@example.com", {DEFAULT_AGENCY_ID: Role.owner, northbank: Role.recruiter}
    )
    elsewhere = await users.agency("Elsewhere Ltd")  # not a member
    me = (await users.login("ada@example.com")).json()
    assert me["active_agency"] is None
    assert [m["agency"]["name"] for m in me["memberships"]] == [
        "Default agency",
        "Northbank Talent",
    ]
    # Agency-scoped endpoints need a chosen agency.
    for path in ("/campaigns", "/agency"):
        resp = await auth_client.get(V2 + path)
        assert resp.status_code == 403 and problem(resp)["detail"] == "Choose an agency"

    # An agency you are not a member of, or that does not exist: the same 404.
    for agency in (elsewhere, uuid.uuid4()):
        resp = await auth_client.put(
            f"{V2}/me/active-agency", json={"agency_id": str(agency)}, headers=csrf(auth_client)
        )
        assert resp.status_code == 404 and problem(resp)["detail"] == "Agency not found"

    token = auth_client.cookies.get(SESSION_COOKIE)
    resp = await auth_client.put(
        f"{V2}/me/active-agency", json={"agency_id": str(northbank)}, headers=csrf(auth_client)
    )
    assert resp.status_code == 200, resp.text
    me = resp.json()
    assert me["active_agency"] == {"id": str(northbank), "name": "Northbank Talent"}
    assert me["role"] == "recruiter"
    assert auth_client.cookies.get(SESSION_COOKIE) == token  # no rotation on a switch
    assert (await auth_client.get(f"{V2}/agency")).json()["name"] == "Northbank Talent"

    await auth_client.put(
        f"{V2}/me/active-agency",
        json={"agency_id": str(DEFAULT_AGENCY_ID)},
        headers=csrf(auth_client),
    )
    assert (await auth_client.get(f"{V2}/agency")).json()["id"] == str(DEFAULT_AGENCY_ID)
    events = await _events(db_session, "auth.agency_switched")
    assert [(e.agency_id, e.actor_user_id) for e in events] == [
        (northbank, user_id),
        (DEFAULT_AGENCY_ID, user_id),
    ]
    assert events[1].details == {"from_agency_id": str(northbank)}


async def test_v2_requests_run_in_the_sessions_agency(
    users: Users, auth_client: AsyncClient, db_session: AsyncSession
) -> None:
    """``tenant_db`` binds the request to the signed-in user's active agency: a Northbank user
    sees none of the default agency's campaigns, and the default agency's owner sees them."""
    northbank = await users.agency()
    await users.user("nina@example.com", {northbank: Role.owner})
    await users.user("ada@example.com")
    campaign = await make_campaign(db_session, title="Default agency campaign")

    await users.login("nina@example.com")
    page = (await auth_client.get(f"{V2}/campaigns")).json()
    assert page["total"] == 0
    assert (await auth_client.get(f"{V2}/campaigns/{campaign.id}")).status_code == 404

    await users.login("ada@example.com")
    page = (await auth_client.get(f"{V2}/campaigns")).json()
    assert [c["id"] for c in page["items"]] == [str(campaign.id)]


# ── /agency ───────────────────────────────────────────────────
async def test_get_and_rename_the_agency(
    users: Users, auth_client: AsyncClient, db_session: AsyncSession
) -> None:
    owner = await users.user("ada@example.com")
    await users.user("bob@example.com", {DEFAULT_AGENCY_ID: Role.admin})

    await users.login("bob@example.com")
    assert (await auth_client.get(f"{V2}/agency")).json() == {
        "id": str(DEFAULT_AGENCY_ID),
        "name": "Default agency",
        "slug": "default",
    }
    resp = await auth_client.patch(
        f"{V2}/agency", json={"name": "Bob's"}, headers=csrf(auth_client)
    )
    assert resp.status_code == 403
    assert problem(resp)["detail"] == "You don't have permission to manage the agency"

    await users.login("ada@example.com")
    resp = await auth_client.patch(
        f"{V2}/agency",
        json={"name": "  Rao Recruiting  ", "slug": "ignored"},
        headers=csrf(auth_client),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "id": str(DEFAULT_AGENCY_ID),
        "name": "Rao Recruiting",
        "slug": "default",
    }
    [event] = await _events(db_session, "agency.updated")
    assert (event.agency_id, event.actor_user_id) == (DEFAULT_AGENCY_ID, owner)
    assert event.details == {"name": {"old": "Default agency", "new": "Rao Recruiting"}}
    for name in ("", "x" * 201):
        resp = await auth_client.patch(
            f"{V2}/agency", json={"name": name}, headers=csrf(auth_client)
        )
        assert resp.status_code == 422
