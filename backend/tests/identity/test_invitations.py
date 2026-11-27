"""Invitations end to end with real cookies (spec §4.3, §11.3; E2-07): create, the email and its
link, preview, accept as a new user and as a signed-in existing user; expired, revoked,
replaced, resent and used links (410); the guard rails on who may invite whom; validation; the
per-agency and per-IP limits; and the audit events."""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.hiring.models import Client
from app.modules.identity.models import AuditEvent, Invitation, Membership, User, UserSession
from app.modules.identity.schemas import Role
from app.platform.config import settings
from tests.default_agency import DEFAULT_AGENCY_ID, DEFAULT_CLIENT_ID
from tests.identity.conftest import (
    PASSWORD,
    SESSION_COOKIE,
    V2,
    Mailbox,
    Users,
    anonymous_csrf,
    csrf,
    problem,
)
from tests.tenancy.conftest import OtherAgency, other_agency, session_for  # noqa: F401

OWNER = "owner@example.com"
NEW = "nina@example.com"
GONE = "This invitation has expired or is no longer valid. Ask for a new invitation."


async def _events(db: AsyncSession, action: str) -> list[AuditEvent]:
    db.expire_all()
    result = await db.execute(
        select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.id)
    )
    return list(result.scalars())


async def _owner(users: Users) -> uuid.UUID:
    owner = await users.user(OWNER)
    assert (await users.login(OWNER)).status_code == 200
    return owner


async def _invite(client: AsyncClient, email: str = NEW, **body: object) -> dict[str, object]:
    resp = await client.post(
        f"{V2}/team/invitations",
        json={"email": email, "role": "recruiter", **body},
        headers=csrf(client),
    )
    assert resp.status_code == 201, resp.text
    data: dict[str, object] = resp.json()
    return data


async def _preview(client: AsyncClient, token: str) -> tuple[int, dict[str, object]]:
    resp = await client.post(
        f"{V2}/auth/invitations/preview",
        json={"token": token},
        headers=await anonymous_csrf(client),
    )
    return resp.status_code, resp.json()


async def _accept(client: AsyncClient, token: str, **body: object) -> tuple[int, dict[str, object]]:
    headers = csrf(client) if client.cookies.get(SESSION_COOKIE) else await anonymous_csrf(client)
    resp = await client.post(
        f"{V2}/auth/invitations/accept", json={"token": token, **body}, headers=headers
    )
    return resp.status_code, resp.json()


NEW_ACCOUNT = {"display_name": "Nina Shah", "password": "nina's long passphrase"}


# ── The whole flow, new user ──────────────────────────────────
async def test_invite_preview_and_accept_as_a_new_user(
    users: Users,
    auth_client: AsyncClient,
    browser: AsyncClient,
    mailbox: Mailbox,
    db_session: AsyncSession,
) -> None:
    owner = await _owner(users)
    created = await _invite(
        auth_client, " Nina@Example.com ", all_clients=False, client_ids=[str(DEFAULT_CLIENT_ID)]
    )
    assert created["email"] == NEW and created["role"] == "recruiter"
    assert created["status"] == "pending" and created["all_clients"] is False
    assert created["clients"] == [{"id": str(DEFAULT_CLIENT_ID), "name": "General"}]
    assert created["invited_by"] == {"id": str(owner), "email": OWNER, "display_name": "owner"}

    # The email: the link carries the token in its fragment, never in a query string.
    message = mailbox.last(NEW)
    token = mailbox.token(NEW)
    link = f"http://localhost:3000/accept-invite#token={token}"
    assert len(token) >= 43
    assert link in message.text and link in message.html
    assert message.subject == "owner invited you to join Default agency on HireFlow AI"
    assert "as a recruiter" in message.text and "?token=" not in message.text
    # Only the token's SHA-256 is stored.
    [row] = (await db_session.execute(select(Invitation))).scalars().all()
    assert row.token_hash == hashlib.sha256(token.encode()).digest()
    assert row.expires_at - row.created_at == timedelta(days=7)
    raw = (await db_session.execute(text("SELECT token_hash FROM invitations"))).scalars().all()
    assert token.encode() not in raw

    status, preview = await _preview(browser, token)
    assert status == 200
    assert preview == {
        "agency": {"id": str(DEFAULT_AGENCY_ID), "name": "Default agency"},
        "invited_by": "owner",
        "email": NEW,
        "role": "recruiter",
        "account_exists": False,
        "expires_at": preview["expires_at"],
    }

    status, me = await _accept(browser, token, **NEW_ACCOUNT)
    assert status == 200, me
    assert me["user"]["email"] == NEW and me["user"]["display_name"] == "Nina Shah"
    assert me["active_agency"] == {"id": str(DEFAULT_AGENCY_ID), "name": "Default agency"}
    assert me["role"] == "recruiter"
    assert me["client_scope"] == {"all_clients": False, "client_ids": [str(DEFAULT_CLIENT_ID)]}
    # Signed in (a new session) in the agency.
    assert browser.cookies.get(SESSION_COOKIE)
    assert (await browser.get(f"{V2}/me")).json()["user"]["email"] == NEW

    db_session.expire_all()
    user = (await db_session.execute(select(User).where(User.email == NEW))).scalar_one()
    user_id = user.id
    assert user.email_verified_at is not None  # the token proved the inbox
    membership = (
        await db_session.execute(select(Membership).where(Membership.user_id == user.id))
    ).scalar_one()
    assert (membership.role, membership.all_clients, membership.invited_by_user_id) == (
        "recruiter",
        False,
        owner,
    )
    [accepted] = await _events(db_session, "team.invitation_accepted")
    assert (accepted.agency_id, accepted.actor_user_id) == (DEFAULT_AGENCY_ID, user_id)
    assert accepted.details == {"role": "recruiter", "new_account": True}
    [event] = await _events(db_session, "team.invitation_created")
    assert (event.actor_user_id, event.target_id) == (owner, created["id"])
    assert event.details["email"] == NEW and event.details["role"] == "recruiter"

    # Used: the link is gone, and the invitation leaves the pending list.
    assert (await _preview(browser, token))[0] == 410
    status, body = await _accept(browser, token, **NEW_ACCOUNT)
    assert status == 410 and body["detail"] == GONE
    listed = (await auth_client.get(f"{V2}/team/invitations")).json()
    assert listed["items"] == [] and listed["total"] == 0


# ── Existing users ────────────────────────────────────────────
async def test_an_existing_user_signs_in_then_accepts(
    users: Users,
    auth_client: AsyncClient,
    browser: AsyncClient,
    mailbox: Mailbox,
    db_session: AsyncSession,
) -> None:
    northbank = await users.agency()
    await users.user(NEW, {northbank: Role.viewer})
    await _owner(users)
    await _invite(auth_client, role="viewer")
    token = mailbox.token(NEW)

    assert (await _preview(browser, token))[1]["account_exists"] is True
    status, body = await _accept(browser, token, **NEW_ACCOUNT)  # not signed in
    assert status == 409
    assert body["detail"] == "An account with this email exists. Sign in, then accept."

    assert (await users.login(NEW, client=browser)).status_code == 200
    old_cookie = browser.cookies.get(SESSION_COOKIE)
    status, me = await _accept(browser, token)
    assert status == 200, me
    assert me["active_agency"]["id"] == str(DEFAULT_AGENCY_ID)  # the new agency is active
    assert {m["agency"]["id"] for m in me["memberships"]} == {
        str(DEFAULT_AGENCY_ID),
        str(northbank),
    }
    # Rotated: the session the request came with is revoked.
    new_cookie = browser.cookies.get(SESSION_COOKIE)
    assert new_cookie and new_cookie != old_cookie
    assert old_cookie is not None
    db_session.expire_all()
    revoked = await db_session.get(UserSession, hashlib.sha256(old_cookie.encode()).digest())
    assert revoked is not None and revoked.revoked_at is not None
    [event] = await _events(db_session, "team.invitation_accepted")
    assert event.details == {"role": "viewer", "new_account": False}


async def test_signed_in_as_someone_else_is_refused(
    users: Users, auth_client: AsyncClient, browser: AsyncClient, mailbox: Mailbox
) -> None:
    await users.user("someone@example.com")
    await _owner(users)
    await _invite(auth_client)
    token = mailbox.token(NEW)
    assert (await users.login("someone@example.com", client=browser)).status_code == 200
    status, body = await _accept(browser, token)
    assert status == 403
    assert body["detail"].startswith("This invitation is for another email address.")
    assert (await _preview(browser, token))[0] == 200  # still usable by the invitee


async def test_a_deactivated_member_is_reactivated_by_a_new_invitation(
    users: Users,
    auth_client: AsyncClient,
    browser: AsyncClient,
    mailbox: Mailbox,
    db_session: AsyncSession,
) -> None:
    """Nina was deactivated in the default agency; she still works for Northbank, signs in
    there, and accepts a new invitation: her old membership comes back with the new role."""
    northbank = await users.agency()
    nina = await users.user(NEW, {DEFAULT_AGENCY_ID: Role.viewer, northbank: Role.viewer})
    await db_session.execute(
        update(Membership)
        .where(Membership.user_id == nina, Membership.agency_id == DEFAULT_AGENCY_ID)
        .values(status="deactivated", deactivated_at=text("now()"))
    )
    await db_session.flush()
    await _owner(users)
    await _invite(auth_client, role="recruiter")
    assert (await users.login(NEW, client=browser)).status_code == 200
    status, me = await _accept(browser, mailbox.token(NEW))
    assert status == 200, me
    assert me["role"] == "recruiter" and me["active_agency"]["id"] == str(DEFAULT_AGENCY_ID)
    db_session.expire_all()
    memberships = (
        (await db_session.execute(select(Membership).where(Membership.user_id == nina)))
        .scalars()
        .all()
    )
    assert len(memberships) == 2 and all(m.status == "active" for m in memberships)


async def test_an_active_member_cannot_be_invited_or_accept_twice(
    users: Users, auth_client: AsyncClient
) -> None:
    await _owner(users)
    await users.user(NEW, {DEFAULT_AGENCY_ID: Role.viewer})
    resp = await auth_client.post(
        f"{V2}/team/invitations", json={"email": NEW, "role": "admin"}, headers=csrf(auth_client)
    )
    assert resp.status_code == 409
    assert problem(resp)["detail"] == f"{NEW} is already a member of this agency"


# ── Links that no longer work (410) ───────────────────────────
async def test_an_expired_invitation_is_gone_until_resent(
    users: Users,
    auth_client: AsyncClient,
    browser: AsyncClient,
    mailbox: Mailbox,
    db_session: AsyncSession,
) -> None:
    await _owner(users)
    created = await _invite(auth_client)
    token = mailbox.token(NEW)
    await db_session.execute(
        update(Invitation).values(expires_at=text("now() - interval '1 second'"))
    )
    await db_session.flush()
    assert (await _preview(browser, token))[0] == 410
    assert (await _accept(browser, token, **NEW_ACCOUNT))[0] == 410
    listed = (await auth_client.get(f"{V2}/team/invitations")).json()["items"]
    assert [(i["id"], i["status"]) for i in listed] == [(created["id"], "expired")]

    # Resending renews it with a new link; the old one stays gone.
    resp = await auth_client.post(
        f"{V2}/team/invitations/{created['id']}/resend", headers=csrf(auth_client)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "pending"
    fresh = mailbox.token(NEW)
    assert fresh != token and mailbox.count(NEW) == 2
    assert (await _preview(browser, token))[0] == 410
    assert (await _preview(browser, fresh))[0] == 200
    [event] = await _events(db_session, "team.invitation_resent")
    assert event.target_id == created["id"]


async def test_a_revoked_invitation_is_gone(
    users: Users,
    auth_client: AsyncClient,
    browser: AsyncClient,
    mailbox: Mailbox,
    db_session: AsyncSession,
) -> None:
    await _owner(users)
    created = await _invite(auth_client)
    token = mailbox.token(NEW)
    resp = await auth_client.delete(
        f"{V2}/team/invitations/{created['id']}", headers=csrf(auth_client)
    )
    assert resp.status_code == 204
    assert (await _preview(browser, token))[0] == 410
    assert (await _accept(browser, token, **NEW_ACCOUNT))[0] == 410
    again = await auth_client.delete(
        f"{V2}/team/invitations/{created['id']}", headers=csrf(auth_client)
    )
    assert again.status_code == 404
    [event] = await _events(db_session, "team.invitation_revoked")
    assert event.target_id == created["id"]


async def test_a_new_invitation_replaces_the_open_one(
    users: Users,
    auth_client: AsyncClient,
    browser: AsyncClient,
    mailbox: Mailbox,
    db_session: AsyncSession,
) -> None:
    await _owner(users)
    first = await _invite(auth_client, role="viewer")
    old = mailbox.token(NEW)
    second = await _invite(auth_client, role="recruiter")
    new = mailbox.token(NEW)
    assert (await _preview(browser, old))[0] == 410
    status, preview = await _preview(browser, new)
    assert status == 200 and preview["role"] == "recruiter"
    listed = (await auth_client.get(f"{V2}/team/invitations")).json()
    assert [i["id"] for i in listed["items"]] == [second["id"]]
    events = await _events(db_session, "team.invitation_created")
    assert events[-1].details["replaced_invitation_id"] == first["id"]


async def test_unknown_tokens_are_gone(browser: AsyncClient) -> None:
    for token in ("x" * 43, "not-a-token"):
        status, body = await _preview(browser, token)
        assert (status, body["detail"]) == (410, GONE)
    assert (await _accept(browser, "x" * 43, **NEW_ACCOUNT))[0] == 410


# ── New accounts: validation ──────────────────────────────────
async def test_a_new_account_needs_a_name_and_a_good_password(
    users: Users, auth_client: AsyncClient, browser: AsyncClient, mailbox: Mailbox
) -> None:
    await _owner(users)
    await _invite(auth_client)
    token = mailbox.token(NEW)
    cases = [
        ({"password": NEW_ACCOUNT["password"]}, "display_name", "Enter your name"),
        ({"display_name": "Nina"}, "password", "Choose a password"),
        ({"display_name": "Nina", "password": "short"}, "password", None),
        ({"display_name": "Nina", "password": NEW}, "password", None),  # the email itself
    ]
    for body, field, message in cases:
        status, problem_body = await _accept(browser, token, **body)
        assert status == 422, body
        [error] = problem_body["errors"]
        assert error["loc"] == ["body", field]
        if message:
            assert error["msg"] == message
    status, _ = await _accept(browser, token, **NEW_ACCOUNT)  # the link still works
    assert status == 200


# ── Who may invite whom ───────────────────────────────────────
async def test_who_may_invite_whom(
    users: Users, auth_client: AsyncClient, db_session: AsyncSession
) -> None:
    await users.user(OWNER)
    for role in (Role.admin, Role.recruiter, Role.viewer):
        await users.user(f"{role.value}@example.com", {DEFAULT_AGENCY_ID: role})

    async def invite(as_: str, role: str) -> int:
        await users.login(as_)
        resp = await auth_client.post(
            f"{V2}/team/invitations",
            json={"email": f"new-{uuid.uuid4().hex[:6]}@example.com", "role": role},
            headers=csrf(auth_client),
        )
        return resp.status_code

    for as_ in ("recruiter@example.com", "viewer@example.com"):
        assert await invite(as_, "viewer") == 403
        await users.login(as_)
        assert (await auth_client.get(f"{V2}/team/invitations")).status_code == 403
        assert (await auth_client.get(f"{V2}/team/members")).status_code == 403
    assert await invite("admin@example.com", "recruiter") == 201
    assert await invite("admin@example.com", "viewer") == 201
    assert await invite("admin@example.com", "admin") == 403
    assert await invite("admin@example.com", "owner") == 403
    assert await invite(OWNER, "admin") == 201
    assert await invite(OWNER, "owner") == 201

    # An admin cannot resend, revoke or replace an invitation for an owner or admin.
    await users.login(OWNER)
    privileged = await _invite(auth_client, "boss@example.com", role="admin")
    await users.login("admin@example.com")
    for method, path in (
        ("POST", f"/team/invitations/{privileged['id']}/resend"),
        ("DELETE", f"/team/invitations/{privileged['id']}"),
    ):
        resp = await auth_client.request(method, V2 + path, headers=csrf(auth_client))
        assert resp.status_code == 403, path
        assert problem(resp)["detail"] == "You don't have permission to manage owners and admins"
    replace = await auth_client.post(
        f"{V2}/team/invitations",
        json={"email": "boss@example.com", "role": "viewer"},
        headers=csrf(auth_client),
    )
    assert replace.status_code == 403
    listed = (await auth_client.get(f"{V2}/team/invitations")).json()["items"]
    assert privileged["id"] in {i["id"] for i in listed}


async def test_the_request_is_validated(
    users: Users, auth_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _owner(users)
    archived = Client(id=uuid.uuid4(), agency_id=DEFAULT_AGENCY_ID, name="Old", status="archived")
    db_session.add(archived)
    await db_session.flush()

    async def errors(body: dict[str, object]) -> list[dict[str, object]]:
        resp = await auth_client.post(
            f"{V2}/team/invitations",
            json={"email": NEW, "role": "recruiter", **body},
            headers=csrf(auth_client),
        )
        assert resp.status_code == 422, (body, resp.text)
        found: list[dict[str, object]] = problem(resp)["errors"]
        return found

    def loc(found: list[dict[str, object]]) -> list[object]:
        return [error["loc"] for error in found]

    assert loc(await errors({"email": "not an email"})) == [["body", "email"]]
    assert loc(await errors({"email": "a,b@example.com"})) == [["body", "email"]]
    assert loc(await errors({"role": "admin", "all_clients": False})) == [["body", "all_clients"]]
    assert loc(await errors({"all_clients": False})) == [["body", "client_ids"]]
    assert loc(await errors({"client_ids": [str(DEFAULT_CLIENT_ID)]})) == [["body", "client_ids"]]
    for client_id in (archived.id, uuid.uuid4()):  # archived, unknown
        found = await errors({"all_clients": False, "client_ids": [str(client_id)]})
        assert found == [
            {
                "loc": ["body", "client_ids"],
                "msg": "Choose clients of this agency that are not archived",
            }
        ]
    assert loc(await errors({"role": "superuser"})) == [["body", "role"]]


async def test_another_agencys_client_cannot_be_granted(
    users: Users,
    auth_client: AsyncClient,
    other_agency: OtherAgency,  # noqa: F811
) -> None:
    await _owner(users)
    resp = await auth_client.post(
        f"{V2}/team/invitations",
        json={
            "email": NEW,
            "role": "viewer",
            "all_clients": False,
            "client_ids": [str(other_agency.client_id)],
        },
        headers=csrf(auth_client),
    )
    assert resp.status_code == 422
    assert str(other_agency.client_id) not in resp.text


# ── Limits ────────────────────────────────────────────────────
async def test_an_agency_sends_at_most_so_many_invitations_a_day(
    users: Users,
    auth_client: AsyncClient,
    mailbox: Mailbox,
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> None:
    monkeypatch.setattr(settings, "INVITATIONS_MAX_PER_AGENCY_PER_DAY", 2)
    await _owner(users)
    first = await _invite(auth_client, "a@example.com")
    await auth_client.post(  # a resend is an email too
        f"{V2}/team/invitations/{first['id']}/resend", headers=csrf(auth_client)
    )
    for path, body in (
        ("/team/invitations", {"email": "b@example.com", "role": "viewer"}),
        (f"/team/invitations/{first['id']}/resend", None),
    ):
        resp = await auth_client.post(V2 + path, json=body, headers=csrf(auth_client))
        assert resp.status_code == 429, path
        assert 0 < int(resp.headers["retry-after"]) <= 24 * 3600
    assert len(mailbox.messages()) == 2
    assert len(await _events(db_session, "auth.rate_limited")) == 1  # once per window

    # Another agency is not affected.
    northbank = await users.agency()
    await users.user("owner@northbank.example", {northbank: Role.owner})
    await users.login("owner@northbank.example")
    await _invite(auth_client, "c@example.com")


async def test_preview_and_accept_are_limited_per_ip(
    users: Users, browser: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "TOKEN_MAX_ATTEMPTS_PER_IP", 3)
    for _ in range(2):
        assert (await _preview(browser, "x" * 43))[0] == 410
    assert (await _accept(browser, "x" * 43, **NEW_ACCOUNT))[0] == 410
    status, body = await _preview(browser, "x" * 43)
    assert status == 429 and body["detail"] == "Too many attempts. Try again later."
    assert (await _accept(browser, "x" * 43, **NEW_ACCOUNT))[0] == 429


async def test_anonymous_requests_need_the_csrf_token(browser: AsyncClient) -> None:
    for path in ("/auth/invitations/preview", "/auth/invitations/accept"):
        resp = await browser.post(V2 + path, json={"token": "x" * 43})
        assert resp.status_code == 403
        assert problem(resp)["detail"] == "CSRF token missing or invalid"


async def test_invitations_list_only_open_ones_of_the_agency(
    users: Users, auth_client: AsyncClient, mailbox: Mailbox
) -> None:
    await _owner(users)
    a = await _invite(auth_client, "a@example.com", role="viewer")
    b = await _invite(
        auth_client,
        "b@example.com",
        role="recruiter",
        all_clients=False,
        client_ids=[str(DEFAULT_CLIENT_ID)],
    )
    c = await _invite(auth_client, "c@example.com")
    await auth_client.delete(f"{V2}/team/invitations/{c['id']}", headers=csrf(auth_client))
    listed = (await auth_client.get(f"{V2}/team/invitations", params={"page_size": 1})).json()
    assert (listed["total"], listed["page_size"]) == (2, 1)
    page = (await auth_client.get(f"{V2}/team/invitations")).json()["items"]
    assert [i["id"] for i in page] == [b["id"], a["id"]]  # newest first
    assert page[0]["clients"] == [{"id": str(DEFAULT_CLIENT_ID), "name": "General"}]
    assert page[1]["clients"] == [] and page[1]["all_clients"] is True
    assert PASSWORD not in str(page)
