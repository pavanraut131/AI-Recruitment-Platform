"""Sign in with Google and Microsoft, end to end with real cookies against fake providers (spec
§4.3, §11.3; E2-08): ``GET /auth/providers``, ``POST /auth/oidc/{provider}/start`` and the
callback, with the linking rules 1-4 of §4.3, invitations, rotation, ``next``, rate limits,
the state cookie, provider errors and every way the ID token can be wrong, and the audit events.

The fake providers (``tests/identity/fake_oidc.py``) answer at Google's and Microsoft's real
URLs through ``httpx.MockTransport``; ``sign_in`` plays the browser: start, sign in at the
provider with chosen ID-token claims, come back to the callback.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import AsyncClient
from joserfc import jwt
from joserfc.jwk import OctKey
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity import oidc
from app.modules.identity.models import (
    AuditEvent,
    Invitation,
    Membership,
    OidcAccount,
    User,
    UserSession,
)
from app.modules.identity.schemas import OidcProvider, Role
from app.platform.config import settings
from tests.default_agency import DEFAULT_AGENCY_ID
from tests.fakes import FakeRedis
from tests.identity.conftest import Mailbox, Users, csrf, problem, set_cookie_headers
from tests.identity.fake_oidc import (
    CSRF_COOKIE,
    OIDC_COOKIE,
    OTHER_TENANT_ID,
    SESSION_COOKIE,
    TENANT_ID,
    V2,
    FakeProviders,
    callback,
    location,
    login_error,
    other_key,
    s256,
    sign_in,
    start,
)

ADA = "ada@example.com"
FRONTEND = "http://localhost:3000"
GOOGLE_SUB = "108123456789012345678"


async def _events(db: AsyncSession, action: str) -> list[AuditEvent]:
    db.expire_all()
    result = await db.execute(
        select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.id)
    )
    return list(result.scalars())


async def _links(db: AsyncSession) -> list[OidcAccount]:
    db.expire_all()
    return list((await db.execute(select(OidcAccount).order_by(OidcAccount.created_at))).scalars())


async def _me(client: AsyncClient) -> dict[str, Any]:
    resp = await client.get(f"{V2}/me")
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def _signed_out(client: AsyncClient) -> bool:
    return not client.cookies.get(SESSION_COOKIE)


# ── GET /auth/providers ───────────────────────────────────────
async def test_providers_lists_only_the_configured_ones(
    auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    resp = await auth_client.get(f"{V2}/auth/providers")
    assert resp.status_code == 200
    assert resp.json() == {"providers": []}  # the default: both hidden

    monkeypatch.setattr(settings, "OIDC_MICROSOFT_CLIENT_ID", "ms-id")
    monkeypatch.setattr(settings, "OIDC_MICROSOFT_CLIENT_SECRET", "ms-secret")
    assert (await auth_client.get(f"{V2}/auth/providers")).json() == {
        "providers": [{"id": "microsoft", "name": "Microsoft"}]
    }
    monkeypatch.setattr(settings, "OIDC_GOOGLE_CLIENT_ID", "g-id")
    monkeypatch.setattr(settings, "OIDC_GOOGLE_CLIENT_SECRET", "g-secret")
    assert (await auth_client.get(f"{V2}/auth/providers")).json() == {
        "providers": [{"id": "google", "name": "Google"}, {"id": "microsoft", "name": "Microsoft"}]
    }


async def test_a_provider_without_credentials_is_hidden(
    auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "OIDC_GOOGLE_CLIENT_ID", "g-id")  # Google only
    monkeypatch.setattr(settings, "OIDC_GOOGLE_CLIENT_SECRET", "g-secret")
    resp = await start(auth_client, "microsoft")
    assert resp.status_code == 404 and problem(resp)["detail"] == "Not Found"
    resp = await callback(auth_client, "microsoft", code="c", state="s")
    assert resp.status_code == 404 and problem(resp)["detail"] == "Not Found"
    assert OIDC_COOKIE not in set_cookie_headers(resp)
    # A provider HireFlow does not know is not a provider at all.
    token = (await auth_client.get(f"{V2}/auth/csrf")).json()["csrf_token"]
    unknown = await auth_client.post(
        f"{V2}/auth/oidc/github/start", json={}, headers={"X-CSRF-Token": token}
    )
    assert unknown.status_code == 422
    assert problem(unknown)["errors"][0]["loc"] == ["path", "provider"]


# ── Start ─────────────────────────────────────────────────────
async def test_start_builds_the_authorization_url_and_seals_the_flow(
    auth_client: AsyncClient, fake_oidc: FakeProviders
) -> None:
    resp = await start(auth_client, "google", next="/campaigns?status=active")
    assert resp.status_code == 200, resp.text
    url = resp.json()["authorization_url"]
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == fake_oidc.google.authorization_endpoint
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert query == {
        "response_type": "code",
        "client_id": fake_oidc.google.client_id,
        "redirect_uri": "http://localhost:8000/api/v2/auth/oidc/google/callback",
        "scope": "openid email profile",
        "state": query["state"],
        "nonce": query["nonce"],
        "code_challenge": query["code_challenge"],
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    assert query["state"] != query["nonce"]

    # The flow is in an HttpOnly, Lax, 10-minute cookie, encrypted: nothing readable in it.
    cookie = set_cookie_headers(resp)[OIDC_COOKIE]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    assert "Max-Age=600" in cookie
    value = auth_client.cookies.get(OIDC_COOKIE)
    assert value and value.count(".") == 4  # a JWE
    assert query["state"] not in value and query["nonce"] not in value
    flow = oidc.unseal(value)
    assert flow is not None
    assert (flow.provider, flow.state, flow.nonce) == (
        OidcProvider.google,
        query["state"],
        query["nonce"],
    )
    assert s256(flow.code_verifier) == query["code_challenge"]  # PKCE S256
    assert 43 <= len(flow.code_verifier) <= 128
    assert flow.next == "/campaigns?status=active" and flow.invitation_hash is None
    # Nothing is written to the database by a start.
    assert resp.headers["cache-control"] == "no-store"


async def test_start_needs_the_csrf_token(
    auth_client: AsyncClient, fake_oidc: FakeProviders
) -> None:
    resp = await auth_client.post(f"{V2}/auth/oidc/google/start", json={})
    assert resp.status_code == 403
    assert problem(resp)["detail"] == "CSRF token missing or invalid"


async def test_start_answers_502_when_the_provider_is_down(
    auth_client: AsyncClient, fake_oidc: FakeProviders
) -> None:
    fake_oidc.google.discovery_down = True
    resp = await start(auth_client, "google")
    assert resp.status_code == 502
    assert problem(resp)["detail"] == (
        "Google sign-in is unavailable right now. Try again in a few minutes."
    )
    assert OIDC_COOKIE not in set_cookie_headers(resp)


async def test_discovery_and_keys_are_cached(
    users: Users, auth_client: AsyncClient, fake_oidc: FakeProviders
) -> None:
    await users.user(ADA)
    for _ in range(3):
        assert location(await sign_in(auth_client, fake_oidc.google)) == f"{FRONTEND}/"
    google = fake_oidc.google
    assert google.calls_to(google.discovery_url) == 1
    assert google.calls_to(google.jwks_uri) == 1
    assert google.calls_to(google.token_endpoint) == 3


# ── Rule 3: an existing account with the address the provider verified ──
async def test_google_links_an_existing_account_by_verified_email(
    users: Users,
    auth_client: AsyncClient,
    fake_oidc: FakeProviders,
    mailbox: Mailbox,
    db_session: AsyncSession,
) -> None:
    ada = await users.user(ADA)
    resp = await sign_in(auth_client, fake_oidc.google, next="/campaigns", email="Ada@Example.com")
    assert location(resp) == f"{FRONTEND}/campaigns"
    # The callback's URL (with the code) never reaches the frontend in a Referer, nor a cache.
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert resp.headers["cache-control"] == "no-store"

    # Signed in: new session and CSRF cookies; the flow's cookie is deleted.
    cookies = set_cookie_headers(resp)
    assert "HttpOnly" in cookies[SESSION_COOKIE] and "SameSite=lax" in cookies[SESSION_COOKIE]
    assert cookies[CSRF_COOKIE]
    assert "Max-Age=0" in cookies[OIDC_COOKIE]  # deleted
    me = await _me(auth_client)
    assert me["user"]["email"] == ADA
    assert me["active_agency"]["id"] == str(DEFAULT_AGENCY_ID) and me["role"] == "owner"

    [link] = await _links(db_session)
    assert (link.user_id, link.provider, link.issuer, link.subject) == (
        ada,
        "google",
        "https://accounts.google.com",
        GOOGLE_SUB,
    )
    assert link.email_at_link == ADA and link.last_used_at is not None
    [linked] = await _events(db_session, "auth.oidc_linked")
    assert (linked.actor_user_id, linked.agency_id) == (ada, DEFAULT_AGENCY_ID)
    assert linked.details == {
        "provider": "google",
        "issuer": "https://accounts.google.com",
        "via": "verified_email",
        "new_account": False,
    }
    [succeeded] = await _events(db_session, "auth.login_succeeded")
    assert succeeded.actor_user_id == ada and succeeded.details == {"method": "google"}

    # The account holder is told a new way in exists.
    message = mailbox.last(ADA)
    assert message.subject == "Google sign-in was added to your HireFlow AI account"
    assert "with the Google account ada@example.com" in message.text
    assert "http://localhost:3000/forgot-password" in message.text


async def test_a_linked_identity_signs_in_by_subject_never_by_email(
    users: Users,
    auth_client: AsyncClient,
    fake_oidc: FakeProviders,
    mailbox: Mailbox,
    db_session: AsyncSession,
) -> None:
    ada = await users.user(ADA)
    await users.user("bob@example.com")
    assert location(await sign_in(auth_client, fake_oidc.google)) == f"{FRONTEND}/"
    assert mailbox.count(ADA) == 1

    # Rule 1: the same Google identity, now with another (even unverified) address at Google,
    # still signs in as Ada; the address is never used to find or switch the account.
    resp = await sign_in(
        auth_client, fake_oidc.google, email="bob@example.com", email_verified=False
    )
    assert location(resp) == f"{FRONTEND}/"
    assert (await _me(auth_client))["user"]["id"] == str(ada)
    [link] = await _links(db_session)
    assert link.user_id == ada and link.email_at_link == ADA
    assert len(await _events(db_session, "auth.oidc_linked")) == 1
    assert len(await _events(db_session, "auth.login_succeeded")) == 2
    assert mailbox.count(ADA) == 1  # no second "new sign-in method" email


async def test_microsoft_links_with_xms_edov_and_stores_the_tenant_issuer(
    users: Users, auth_client: AsyncClient, fake_oidc: FakeProviders, db_session: AsyncSession
) -> None:
    ada = await users.user(ADA)
    resp = await sign_in(auth_client, fake_oidc.microsoft)
    assert location(resp) == f"{FRONTEND}/"
    [link] = await _links(db_session)
    assert (link.user_id, link.provider, link.issuer) == (
        ada,
        "microsoft",
        f"https://login.microsoftonline.com/{TENANT_ID}/v2.0",
    )
    [event] = await _events(db_session, "auth.login_succeeded")
    assert event.details == {"method": "microsoft"}


# ── Rule 4 and the "nOAuth" guard ─────────────────────────────
async def test_no_account_for_a_verified_address(
    auth_client: AsyncClient, fake_oidc: FakeProviders, db_session: AsyncSession
) -> None:
    resp = await sign_in(auth_client, fake_oidc.google, email="stranger@example.com")
    assert login_error(resp) == "no_account"
    assert _signed_out(auth_client)
    assert OIDC_COOKIE in set_cookie_headers(resp)  # deleted
    assert await _links(db_session) == []
    count = await db_session.scalar(select(func.count()).select_from(User))
    assert count == 0  # no self sign-up
    [event] = await _events(db_session, "auth.login_failed")
    assert event.details["method"] == "google" and event.details["reason"] == "no_account"
    assert "stranger" not in str(event.details) and len(event.details["email_hash"]) == 32


@pytest.mark.parametrize(
    ("provider", "claims"),
    [
        ("google", {"email_verified": False}),
        ("google", {"email_verified": None}),  # missing
        ("microsoft", {"xms_edov": None}),  # the nOAuth case: Entra's email is not proof
        ("microsoft", {"xms_edov": False}),
        ("google", {"email": None}),
    ],
)
async def test_an_address_the_provider_does_not_vouch_for_is_never_used(
    users: Users,
    auth_client: AsyncClient,
    fake_oidc: FakeProviders,
    db_session: AsyncSession,
    provider: str,
    claims: dict[str, Any],
) -> None:
    await users.user(ADA)  # the address an attacker's tenant could claim
    resp = await sign_in(auth_client, fake_oidc[provider], **claims)
    assert login_error(resp, provider) == "email_not_verified"
    assert _signed_out(auth_client)
    assert await _links(db_session) == []
    [event] = await _events(db_session, "auth.login_failed")
    assert event.details["reason"] == "email_not_verified"


async def test_vouching_claims_sent_as_strings_are_understood(
    users: Users, auth_client: AsyncClient, fake_oidc: FakeProviders
) -> None:
    await users.user(ADA)
    resp = await sign_in(auth_client, fake_oidc.microsoft, xms_edov="1")
    assert location(resp) == f"{FRONTEND}/"
    resp = await sign_in(auth_client, fake_oidc.google, email_verified="true")
    assert location(resp) == f"{FRONTEND}/"


# ── Access ────────────────────────────────────────────────────
async def test_a_disabled_user_or_one_without_an_agency_gets_no_access(
    users: Users, auth_client: AsyncClient, fake_oidc: FakeProviders, db_session: AsyncSession
) -> None:
    ada = await users.user(ADA)
    await db_session.execute(update(User).where(User.id == ada).values(status="disabled"))
    await db_session.flush()
    assert login_error(await sign_in(auth_client, fake_oidc.google)) == "no_access"
    await db_session.execute(update(User).where(User.id == ada).values(status="active"))
    await db_session.execute(
        update(Membership).where(Membership.user_id == ada).values(status="deactivated")
    )
    await db_session.flush()
    assert login_error(await sign_in(auth_client, fake_oidc.google)) == "no_access"
    assert _signed_out(auth_client)
    assert await _links(db_session) == []  # nothing linked for a refused sign-in
    reasons = [e.details["reason"] for e in await _events(db_session, "auth.login_failed")]
    assert reasons == ["user_disabled", "no_agency_access"]


async def test_several_agencies_pick_the_most_recent(
    users: Users, auth_client: AsyncClient, fake_oidc: FakeProviders
) -> None:
    northbank = await users.agency()
    await users.user(ADA, {DEFAULT_AGENCY_ID: Role.owner, northbank: Role.viewer})
    location(await sign_in(auth_client, fake_oidc.google))
    me = await _me(auth_client)
    assert me["active_agency"] is None  # neither used yet: the agency picker
    put = await auth_client.put(
        f"{V2}/me/active-agency", json={"agency_id": str(northbank)}, headers=csrf(auth_client)
    )
    assert put.status_code == 200
    location(await sign_in(auth_client, fake_oidc.google))
    assert (await _me(auth_client))["active_agency"]["id"] == str(northbank)


# ── Rotation and next ─────────────────────────────────────────
async def test_the_session_rotates(
    users: Users, auth_client: AsyncClient, fake_oidc: FakeProviders, db_session: AsyncSession
) -> None:
    await users.user(ADA)
    assert (await users.login(ADA)).status_code == 200
    before = auth_client.cookies.get(SESSION_COOKIE)
    assert before
    location(await sign_in(auth_client, fake_oidc.google))
    after = auth_client.cookies.get(SESSION_COOKIE)
    assert after and after != before
    db_session.expire_all()
    old = await db_session.get(UserSession, hashlib.sha256(before.encode()).digest())
    assert old is not None and old.revoked_at is not None


@pytest.mark.parametrize(
    ("next_path", "landing"),
    [
        ("/campaigns/123?tab=review#top", "/campaigns/123?tab=review#top"),
        ("/", "/"),
        (None, "/"),
        ("//evil.example/steal", "/"),
        ("https://evil.example/", "/"),
        ("/\\evil.example", "/"),
        ("\\\\evil.example", "/"),
        ("javascript:alert(1)", "/"),
        ("evil.example", "/"),
        ("/ok path", "/"),
        ("/café", "/"),
        ("@evil.example", "/"),
    ],
)
async def test_next_is_a_frontend_path_only(
    users: Users,
    auth_client: AsyncClient,
    fake_oidc: FakeProviders,
    next_path: str | None,
    landing: str,
) -> None:
    await users.user(ADA)
    assert location(await sign_in(auth_client, fake_oidc.google, next=next_path)) == (
        FRONTEND + landing
    )


# ── Invitations ───────────────────────────────────────────────
async def _invitation(
    users: Users, auth_client: AsyncClient, mailbox: Mailbox, email: str, role: str = "recruiter"
) -> str:
    """Ada (owner of the default agency) invites ``email``; returns the token. Ada then signs
    out, so the client is a fresh browser."""
    await users.user(ADA)
    assert (await users.login(ADA)).status_code == 200
    resp = await auth_client.post(
        f"{V2}/team/invitations", json={"email": email, "role": role}, headers=csrf(auth_client)
    )
    assert resp.status_code == 201, resp.text
    await auth_client.post(f"{V2}/auth/logout", headers=csrf(auth_client))
    auth_client.cookies.clear()
    return mailbox.token(email)


async def test_an_invitation_creates_the_account_and_links_the_identity(
    users: Users,
    auth_client: AsyncClient,
    fake_oidc: FakeProviders,
    mailbox: Mailbox,
    db_session: AsyncSession,
) -> None:
    token = await _invitation(users, auth_client, mailbox, "nina@example.com")
    # The identity's own address does not matter: the token proved the invited inbox.
    resp = await sign_in(
        auth_client,
        fake_oidc.microsoft,
        next="/",
        invitation_token=token,
        email="nina.shah@contoso.example",
        xms_edov=None,
        name="Nina Shah",
    )
    assert location(resp) == f"{FRONTEND}/"
    me = await _me(auth_client)
    assert me["user"]["email"] == "nina@example.com"
    assert me["user"]["display_name"] == "Nina Shah"
    assert me["active_agency"]["id"] == str(DEFAULT_AGENCY_ID) and me["role"] == "recruiter"

    db_session.expire_all()
    nina = (
        await db_session.execute(select(User).where(User.email == "nina@example.com"))
    ).scalar_one()
    nina_id = nina.id
    assert nina.password_hash is None and nina.email_verified_at is not None
    [link] = await _links(db_session)
    assert (link.user_id, link.provider, link.email_at_link) == (
        nina_id,
        "microsoft",
        "nina.shah@contoso.example",
    )
    invitation = (await db_session.execute(select(Invitation))).scalar_one()
    assert invitation.accepted_at is not None and invitation.accepted_by_user_id == nina_id
    [accepted] = await _events(db_session, "team.invitation_accepted")
    assert accepted.details == {"role": "recruiter", "new_account": True, "method": "microsoft"}
    [linked] = await _events(db_session, "auth.oidc_linked")
    assert linked.details["via"] == "invitation" and linked.details["new_account"] is True
    assert mailbox.count("nina@example.com") == 1  # the invitation only: no "new method" email

    # The link is spent; Nina signs in again with Microsoft by subject.
    auth_client.cookies.clear()
    assert location(await sign_in(auth_client, fake_oidc.microsoft, xms_edov=None)) == (
        f"{FRONTEND}/"
    )
    assert (await _me(auth_client))["user"]["email"] == "nina@example.com"


async def test_an_invitation_for_an_existing_account_needs_the_same_vouched_address(
    users: Users,
    auth_client: AsyncClient,
    fake_oidc: FakeProviders,
    mailbox: Mailbox,
    db_session: AsyncSession,
) -> None:
    northbank = await users.agency()
    nina = await users.user("nina@example.com", {northbank: Role.viewer})
    token = await _invitation(users, auth_client, mailbox, "nina@example.com", role="viewer")

    # Another address, or the same one unverified: a forwarded link must not attach a
    # stranger's identity to Nina's account.
    for claims in (
        {"email": "mallory@example.com"},
        {"email": "nina@example.com", "xms_edov": None},
    ):
        resp = await sign_in(auth_client, fake_oidc.microsoft, invitation_token=token, **claims)
        assert login_error(resp, "microsoft") == "account_exists"
    assert await _links(db_session) == []
    db_session.expire_all()
    assert (await db_session.execute(select(Invitation))).scalar_one().accepted_at is None

    resp = await sign_in(
        auth_client, fake_oidc.microsoft, invitation_token=token, email="nina@example.com"
    )
    assert location(resp) == f"{FRONTEND}/"
    me = await _me(auth_client)
    assert me["user"]["id"] == str(nina)
    assert me["active_agency"]["id"] == str(DEFAULT_AGENCY_ID)  # the invitation's agency
    assert {m["agency"]["id"] for m in me["memberships"]} == {
        str(DEFAULT_AGENCY_ID),
        str(northbank),
    }
    [link] = await _links(db_session)
    assert link.user_id == nina
    [accepted] = await _events(db_session, "team.invitation_accepted")
    assert accepted.details == {"role": "viewer", "new_account": False, "method": "microsoft"}
    assert "Microsoft sign-in was added" in mailbox.last("nina@example.com").subject


async def test_a_linked_identity_accepts_its_own_invitation_only(
    users: Users,
    auth_client: AsyncClient,
    fake_oidc: FakeProviders,
    mailbox: Mailbox,
    db_session: AsyncSession,
) -> None:
    northbank = await users.agency()
    await users.user("nina@example.com", {northbank: Role.viewer})
    # Nina's Google identity is linked (rule 3) ...
    location(await sign_in(auth_client, fake_oidc.google, email="nina@example.com"))
    auth_client.cookies.clear()
    token = await _invitation(users, auth_client, mailbox, "nina@example.com")
    other = await _invitation_for(users, auth_client, mailbox, "zed@example.com")

    # ... so it accepts her invitation whatever Google says her address is now,
    resp = await sign_in(
        auth_client, fake_oidc.google, invitation_token=token, email="x@example.com"
    )
    assert location(resp) == f"{FRONTEND}/"
    assert (await _me(auth_client))["active_agency"]["id"] == str(DEFAULT_AGENCY_ID)
    # but not somebody else's.
    auth_client.cookies.clear()
    resp = await sign_in(auth_client, fake_oidc.google, invitation_token=other)
    assert login_error(resp) == "invitation_mismatch"
    assert _signed_out(auth_client)


async def _invitation_for(
    users: Users, auth_client: AsyncClient, mailbox: Mailbox, email: str
) -> str:
    assert (await users.login(ADA)).status_code == 200
    resp = await auth_client.post(
        f"{V2}/team/invitations",
        json={"email": email, "role": "viewer"},
        headers=csrf(auth_client),
    )
    assert resp.status_code == 201, resp.text
    await auth_client.post(f"{V2}/auth/logout", headers=csrf(auth_client))
    auth_client.cookies.clear()
    return mailbox.token(email)


async def test_already_a_member(
    users: Users,
    auth_client: AsyncClient,
    fake_oidc: FakeProviders,
    mailbox: Mailbox,
    db_session: AsyncSession,
) -> None:
    token = await _invitation(users, auth_client, mailbox, "nina@example.com")
    # Nina joins with a password meanwhile (another tab), then uses the Google button too.
    northbank = await users.agency()
    await users.user("nina@example.com", {northbank: Role.viewer, DEFAULT_AGENCY_ID: Role.viewer})
    resp = await sign_in(
        auth_client, fake_oidc.google, invitation_token=token, email="nina@example.com"
    )
    assert login_error(resp) == "already_member"
    assert _signed_out(auth_client) and await _links(db_session) == []


async def test_an_invitation_gone_before_or_during_the_flow(
    users: Users,
    auth_client: AsyncClient,
    fake_oidc: FakeProviders,
    mailbox: Mailbox,
    db_session: AsyncSession,
) -> None:
    token = await _invitation(users, auth_client, mailbox, "nina@example.com")
    resp = await start(auth_client, "google", invitation_token="t" * 43)
    assert resp.status_code == 410
    assert problem(resp)["detail"] == (
        "This invitation has expired or is no longer valid. Ask for a new invitation."
    )
    assert OIDC_COOKIE not in set_cookie_headers(resp)

    # Revoked between the start and the callback: checked again on the way back.
    started = await start(auth_client, "google", invitation_token=token)
    assert started.status_code == 200
    flow = oidc.unseal(auth_client.cookies.get(OIDC_COOKIE))
    assert flow is not None and flow.invitation_hash == hashlib.sha256(token.encode()).digest()
    await db_session.execute(update(Invitation).values(revoked_at=func.now()))
    await db_session.flush()
    code, state = fake_oidc.google.authorize(started.json()["authorization_url"])
    resp = await callback(auth_client, "google", code=code, state=state)
    assert login_error(resp) == "invitation_invalid"
    assert _signed_out(auth_client)
    count = await db_session.scalar(select(func.count()).select_from(User))
    assert count == 1  # only Ada


# ── The state cookie (login CSRF) ─────────────────────────────
async def test_the_callback_needs_this_browsers_flow(
    users: Users,
    auth_client: AsyncClient,
    browser: AsyncClient,
    fake_oidc: FakeProviders,
    db_session: AsyncSession,
) -> None:
    await users.user(ADA)
    google = fake_oidc.google
    started = await start(auth_client, "google")
    code, state = google.authorize(started.json()["authorization_url"])

    # Another browser (an attacker's link in the victim's browser): no flow cookie.
    assert login_error(await callback(browser, "google", code=code, state=state)) == "expired"
    # This browser, the wrong state, or none.
    assert login_error(await callback(auth_client, "google", code=code, state="x" * 43)) == (
        "oidc_failed"
    )
    started = await start(auth_client, "google")
    code, state = google.authorize(started.json()["authorization_url"])
    assert login_error(await callback(auth_client, "google", code=code)) == "oidc_failed"
    # A cookie that was altered, or sealed for the other provider.
    started = await start(auth_client, "google")
    code, state = google.authorize(started.json()["authorization_url"])
    sealed = auth_client.cookies.get(OIDC_COOKIE)
    assert sealed
    auth_client.cookies.set(OIDC_COOKIE, sealed[:-4] + "AAAA", domain="test.local")
    assert login_error(await callback(auth_client, "google", code=code, state=state)) == "expired"
    await start(auth_client, "microsoft")
    resp = await callback(auth_client, "google", code=code, state=state)
    assert login_error(resp) == "expired"

    reasons = [e.details["reason"] for e in await _events(db_session, "auth.login_failed")]
    assert reasons == [
        "no_state_cookie",
        "state_mismatch",
        "state_mismatch",
        "invalid_state_cookie",
        "invalid_state_cookie",
    ]
    assert _signed_out(auth_client) and await _links(db_session) == []
    assert google.calls_to(google.token_endpoint) == 0  # no code was ever exchanged


async def test_the_flow_expires_after_ten_minutes(
    users: Users,
    auth_client: AsyncClient,
    fake_oidc: FakeProviders,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await users.user(ADA)
    started = await start(auth_client, "google")
    code, state = fake_oidc.google.authorize(started.json()["authorization_url"])
    real_time = oidc.time.time
    monkeypatch.setattr(oidc.time, "time", lambda: real_time() + 601)
    assert login_error(await callback(auth_client, "google", code=code, state=state)) == "expired"


async def test_a_code_works_once(
    users: Users, auth_client: AsyncClient, fake_oidc: FakeProviders
) -> None:
    await users.user(ADA)
    started = await start(auth_client, "google")
    code, state = fake_oidc.google.authorize(started.json()["authorization_url"])
    sealed = auth_client.cookies.get(OIDC_COOKIE)
    assert location(await callback(auth_client, "google", code=code, state=state)).endswith("/")
    # Replayed, with the same cookie.
    auth_client.cookies.set(OIDC_COOKIE, sealed or "", domain="test.local")
    resp = await callback(auth_client, "google", code=code, state=state)
    assert login_error(resp) == "oidc_failed"


# ── The provider's answer ─────────────────────────────────────
async def test_provider_errors(
    auth_client: AsyncClient, fake_oidc: FakeProviders, db_session: AsyncSession
) -> None:
    for error, expected in (("access_denied", "cancelled"), ("server_error", "oidc_failed")):
        started = await start(auth_client, "google")
        _, state = fake_oidc.google.authorize(started.json()["authorization_url"])
        resp = await callback(auth_client, "google", state=state, error=error)
        assert login_error(resp) == expected
    fake_oidc.google.token_error = "invalid_client"
    started = await start(auth_client, "google")
    code, state = fake_oidc.google.authorize(started.json()["authorization_url"])
    assert login_error(await callback(auth_client, "google", code=code, state=state)) == (
        "oidc_failed"
    )
    events = await _events(db_session, "auth.login_failed")
    assert [(e.details["reason"], e.details.get("error")) for e in events] == [
        ("provider_error", "access_denied"),
        ("provider_error", "server_error"),
        ("token_exchange_refused", None),
    ]


@pytest.mark.parametrize(
    ("provider", "claims", "reason"),
    [
        ("google", {"nonce": "another-nonce"}, "invalid_claim:nonce"),
        ("google", {"aud": "another-client"}, "invalid_claim:aud"),
        ("google", {"aud": ["another-client"]}, "invalid_claim:aud"),
        ("google", {"iss": "https://evil.example"}, "wrong_issuer"),
        ("google", {"exp": 1_700_000_000}, "expired"),
        ("google", {"iat": 4_000_000_000}, "invalid_claim:iat"),
        ("google", {"iat": None}, "missing_claim:iat"),
        ("google", {"sub": None}, "missing_claim:sub"),
        # Microsoft: the issuer must be the token's own tenant's.
        (
            "microsoft",
            {"iss": f"https://login.microsoftonline.com/{OTHER_TENANT_ID}/v2.0"},
            "wrong_issuer",
        ),
        ("microsoft", {"iss": "https://login.microsoftonline.com/common/v2.0"}, "wrong_issuer"),
        ("microsoft", {"tid": None}, "no_tenant"),
        (
            "microsoft",
            {
                "tid": oidc.MICROSOFT_CONSUMER_TENANT,
                "iss": f"https://login.microsoftonline.com/{oidc.MICROSOFT_CONSUMER_TENANT}/v2.0",
            },
            "personal_account",
        ),
    ],
)
async def test_an_invalid_id_token_signs_nobody_in(
    users: Users,
    auth_client: AsyncClient,
    fake_oidc: FakeProviders,
    db_session: AsyncSession,
    provider: str,
    claims: dict[str, Any],
    reason: str,
) -> None:
    await users.user(ADA)
    resp = await sign_in(auth_client, fake_oidc[provider], **claims)
    assert login_error(resp, provider) == "oidc_failed"
    assert _signed_out(auth_client) and await _links(db_session) == []
    [event] = await _events(db_session, "auth.login_failed")
    assert event.details == {"method": provider, "reason": reason}


async def test_a_forged_signature_is_refused(
    users: Users, auth_client: AsyncClient, fake_oidc: FakeProviders, db_session: AsyncSession
) -> None:
    await users.user(ADA)
    resp = await sign_in(auth_client, fake_oidc.google, sign_with=other_key())
    assert login_error(resp) == "oidc_failed"
    [event] = await _events(db_session, "auth.login_failed")
    assert event.details["reason"] == "bad_signature"
    # An unknown key id: the keys are fetched again once (rotation), then refused.
    resp = await sign_in(auth_client, fake_oidc.google, sign_with=other_key("unknown-kid"))
    assert login_error(resp) == "oidc_failed"
    assert (await _events(db_session, "auth.login_failed"))[-1].details["reason"] == "unknown_key"
    assert fake_oidc.google.calls_to(fake_oidc.google.jwks_uri) == 2


async def test_the_provider_rotates_its_keys(
    users: Users, auth_client: AsyncClient, fake_oidc: FakeProviders
) -> None:
    await users.user(ADA)
    location(await sign_in(auth_client, fake_oidc.google))  # keys cached
    fake_oidc.google.rotate_key()
    assert location(await sign_in(auth_client, fake_oidc.google)) == f"{FRONTEND}/"
    assert fake_oidc.google.calls_to(fake_oidc.google.jwks_uri) == 2


async def test_a_pinned_tenant_refuses_the_others(
    users: Users,
    auth_client: AsyncClient,
    fake_oidc: FakeProviders,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await users.user(ADA)
    monkeypatch.setattr(settings, "OIDC_MICROSOFT_TENANT", OTHER_TENANT_ID)
    fake = fake_oidc.microsoft
    fake.tenant = OTHER_TENANT_ID  # its endpoints live under the tenant's id
    resp = await sign_in(auth_client, fake)  # a token of TENANT_ID
    assert login_error(resp, "microsoft") == "oidc_failed"
    [event] = await _events(db_session, "auth.login_failed")
    assert event.details["reason"] == "wrong_tenant"
    resp = await sign_in(
        auth_client,
        fake,
        tid=OTHER_TENANT_ID,
        iss=f"https://login.microsoftonline.com/{OTHER_TENANT_ID}/v2.0",
    )
    assert location(resp) == f"{FRONTEND}/"


# ── Rate limits ───────────────────────────────────────────────
async def test_start_and_callback_are_rate_limited_per_ip(
    auth_client: AsyncClient,
    fake_oidc: FakeProviders,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "OIDC_MAX_ATTEMPTS_PER_IP", 3)
    for _ in range(3):
        assert (await start(auth_client, "google")).status_code == 200
    resp = await start(auth_client, "google")
    assert resp.status_code == 429
    assert problem(resp)["detail"] == "Too many attempts. Try again later."
    assert 0 < int(resp.headers["retry-after"]) <= 15 * 60

    for _ in range(3):  # its own counter
        assert login_error(await callback(auth_client, "google", code="c", state="s")) != (
            "rate_limited"
        )
    resp = await callback(auth_client, "google", code="c", state="s")
    assert login_error(resp) == "rate_limited"
    limits = [e.details["limit"] for e in await _events(db_session, "auth.rate_limited")]
    assert limits == ["oidc_start_ip", "oidc_callback_ip"]  # once per window each


async def test_redis_down_fails_open(
    users: Users, auth_client: AsyncClient, fake_oidc: FakeProviders, fake_redis: FakeRedis
) -> None:
    await users.user(ADA)
    fake_redis.server.connected = False
    assert location(await sign_in(auth_client, fake_oidc.google)) == f"{FRONTEND}/"


# ── Unit: the pieces ──────────────────────────────────────────
def _b64(data: dict[str, Any]) -> str:
    raw = json.dumps(data).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


async def _validate(fake_oidc: FakeProviders, token: str, nonce: str = "n") -> oidc.Identity:
    config = oidc.provider_config(OidcProvider.google)
    assert config is not None
    return await oidc.validate_id_token(config, token, nonce=nonce)


async def test_only_rs256_signatures_from_the_providers_keys(fake_oidc: FakeProviders) -> None:
    google = fake_oidc.google
    claims = google.claims("n")
    unsigned = f"{_b64({'alg': 'none', 'kid': google.key.kid})}.{_b64(claims)}."
    # Algorithm confusion: HS256 "signed" with a secret instead of the provider's RSA key.
    hs256 = jwt.encode(
        {"alg": "HS256", "kid": google.key.kid},
        claims,
        OctKey.import_key(b"k" * 32),
        algorithms=["HS256"],
    )
    for token in (unsigned, hs256, "not-a-jwt", ""):
        with pytest.raises(oidc.OidcError) as refused:
            await _validate(fake_oidc, token)
        assert refused.value.reason == "malformed_id_token", token
    assert (await _validate(fake_oidc, google.id_token(claims))).subject == GOOGLE_SUB


async def test_several_audiences_need_the_client_as_authorized_party(
    fake_oidc: FakeProviders,
) -> None:
    google = fake_oidc.google
    both = [google.client_id, "another-client"]
    token = google.id_token(google.claims("n", aud=both, azp="another-client"))
    with pytest.raises(oidc.OidcError, match="invalid_claim:azp"):
        await _validate(fake_oidc, token)
    token = google.id_token(google.claims("n", aud=both, azp=google.client_id))
    assert (await _validate(fake_oidc, token)).subject == GOOGLE_SUB


async def test_googles_short_issuer_is_stored_in_full(fake_oidc: FakeProviders) -> None:
    google = fake_oidc.google
    token = google.id_token(google.claims("n", iss="accounts.google.com"))
    identity = await _validate(fake_oidc, token)
    assert identity.issuer == "https://accounts.google.com"
    assert (identity.email, identity.email_verified, identity.name) == (
        ADA,
        True,
        "Ada Lovelace",
    )


def test_the_flow_cookie_is_sealed_and_expires() -> None:
    flow = oidc.new_flow(OidcProvider.microsoft, "/x", b"\x01" * 32)
    sealed = oidc.seal(flow, now=1_800_000_000)
    assert oidc.unseal(sealed, now=1_800_000_000 + 599) == flow
    assert oidc.unseal(sealed, now=1_800_000_000 + 601) is None
    assert oidc.unseal(sealed[:-2] + "xx", now=1_800_000_000) is None
    assert oidc.unseal("not.a.jwe", now=1_800_000_000) is None
    assert oidc.unseal(None) is None and oidc.unseal("") is None


def test_the_flow_cookie_key_comes_from_the_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    flow = oidc.new_flow(OidcProvider.google, "/", None)
    sealed = oidc.seal(flow)
    monkeypatch.setattr(settings, "SECRET_KEY", "another secret, at least 32 bytes long!")
    assert oidc.unseal(sealed) is None


def test_redirect_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "API_PUBLIC_URL", "https://app.example.com/")
    assert oidc.redirect_uri(OidcProvider.microsoft) == (
        "https://app.example.com/api/v2/auth/oidc/microsoft/callback"
    )


def test_the_settings_refuse_half_a_provider_and_personal_tenants() -> None:
    from pydantic import ValidationError

    from app.platform.config import Settings

    base: dict[str, Any] = {
        "DATABASE_URL": "postgresql+asyncpg://u:p@h/d",
        "SYNC_DATABASE_URL": "postgresql://u:p@h/d",
        "AI_PROVIDER": "fake",
    }
    for bad in (
        {"OIDC_GOOGLE_CLIENT_ID": "id"},
        {"OIDC_MICROSOFT_CLIENT_SECRET": "secret"},
        {"OIDC_MICROSOFT_TENANT": "common"},
        {"OIDC_MICROSOFT_TENANT": "consumers"},
        {"OIDC_MICROSOFT_TENANT": "contoso.com/../evil"},
    ):
        with pytest.raises(ValidationError, match="OIDC"):
            Settings(**base, **bad)
    tenant = Settings(**base, OIDC_MICROSOFT_TENANT="contoso.onmicrosoft.com")
    assert tenant.OIDC_MICROSOFT_TENANT == "contoso.onmicrosoft.com"
    # A deployment with a provider needs an https API URL.
    deployment = {
        **base,
        "APP_ENV": "production",
        "SECRET_KEY": "k" * 40,
        "EMAIL_BACKEND": "smtp",
        "OIDC_GOOGLE_CLIENT_ID": "id",
        "OIDC_GOOGLE_CLIENT_SECRET": "secret",
    }
    with pytest.raises(ValidationError, match="API_PUBLIC_URL"):
        Settings(**deployment, API_PUBLIC_URL="http://app.example.com")
    assert Settings(**deployment, API_PUBLIC_URL="https://app.example.com").API_PUBLIC_URL
