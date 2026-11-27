"""The isolation API sweep (EPIC-02 spec §11.2 ``test_api_sweep``): generated from the ``/api/v2``
OpenAPI document, so a new endpoint is covered (or the test names it) without editing it.

The caller is **signed in for real** (E2-05: ``tests/identity/conftest.py``'s ``users`` and
``auth_client``, session cookie and CSRF header) as an owner of the default agency, agency A;
every request gets a fresh session that ``tenant_db`` binds to the principal's agency. Agency B
has a campaign, an application, a Person and a resume, through a session bound to it.

1. Every ``GET`` (lists, the detail URLs with A's ids, ``/me``, ``/agency``, the team, the
   sign-in providers): no id of B anywhere in any body (B has a member and an invitation too,
   E2-07). The OIDC callback, a redirect, is left to step 2 (E2-08).
2. Every operation with an id in its path, called with **B's** ids: ``404``, and B's rows are
   unchanged afterwards. Operations with a request body take it from :data:`BODIES`; one with a
   body that has no entry fails the test, so it is added here when it is added to the API.
3. The one operation that takes an agency id in its body (``PUT /me/active-agency``) refuses B's,
   and the ones that take a client id (``POST /campaigns``, ``PATCH /campaigns/{id}``) refuse B's
   client (E2-06).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.hiring.models import Application, Campaign, Client
from app.modules.identity import service as identity
from app.modules.identity.models import Invitation, Membership
from app.modules.identity.schemas import Role
from app.modules.ingestion.models import Resume
from app.modules.talent.models import Person
from app.platform.tenancy import DEFAULT_CLIENT_ID
from app.v2 import v2_app
from tests.factories import make_campaign, make_candidate
from tests.identity.conftest import PASSWORD, Users, csrf
from tests.tenancy.conftest import OtherAgency

V2 = "/api/v2"
EMAIL = "owner.a@agency-a.example"
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"

# Request bodies by (method, path template): valid for the operation, so that the agency check,
# not validation, decides the answer.
BODIES: dict[tuple[str, str], dict[str, Any]] = {
    ("PATCH", "/applications/{application_id}"): {"json": {"notes": "from A"}},
    ("PATCH", "/campaigns/{campaign_id}"): {"json": {"title": "from A"}},
    ("POST", "/campaigns/{campaign_id}/applications/bulk"): {
        "json": {"action": "delete", "ids": ["{application_id}"]}
    },
    ("POST", "/campaigns/{campaign_id}/uploads"): {
        "files": [("files", ("cv.pdf", PDF, "application/pdf"))]
    },
    ("PATCH", "/clients/{client_id}"): {"json": {"name": "from A", "status": "archived"}},
    # No id in the path (agency-wide or the caller's own); listed so the coverage check knows
    # them. PUT /me/active-agency is checked with B's id separately.
    ("POST", "/campaigns"): {"json": {"title": "A's campaign", "role": "Engineer"}},
    ("POST", "/campaigns/parse-jd"): {"json": {"job_description": "A role."}},
    ("POST", "/auth/login"): {"json": {"email": EMAIL, "password": PASSWORD}},
    ("PATCH", "/me"): {"json": {"display_name": "A's owner"}},
    ("POST", "/me/password"): {"json": {"current_password": PASSWORD, "new_password": "x" * 16}},
    ("PUT", "/me/active-agency"): {"json": {"agency_id": "{agency_id}"}},
    ("PATCH", "/agency"): {"json": {"name": "Agency A"}},
    ("POST", "/clients"): {"json": {"name": "A's new client"}},
    # E2-07. The team: A's caller is an owner, so only the agency check can refuse B's ids.
    ("PATCH", "/team/members/{membership_id}"): {"json": {"role": "viewer"}},
    ("POST", "/team/invitations"): {"json": {"email": "new@agency-a.example", "role": "viewer"}},
    # Anonymous, by emailed token or address (no agency in the request).
    ("POST", "/auth/password-reset"): {"json": {"email": EMAIL}},
    ("POST", "/auth/password-reset/confirm"): {
        "json": {"token": "t" * 43, "new_password": "x" * 16}
    },
    ("POST", "/auth/invitations/preview"): {"json": {"token": "t" * 43}},
    ("POST", "/auth/invitations/accept"): {"json": {"token": "t" * 43}},
    # E2-08: signing in with Google or Microsoft (no agency in the request either).
    ("POST", "/auth/oidc/{provider}/start"): {"json": {"next": "/"}},
}
# Opened as a stream: the 404 for B's id is checked; A's own stream would not end.
STREAMS = {"/campaigns/{campaign_id}/events"}
# A redirect without a body (the OIDC callback, E2-08): only in the 404 sweep.
REDIRECTS = {"/auth/oidc/{provider}/callback"}
# Path parameters that name no agency's object: the sign-in provider (E2-08). The sweep runs
# with none configured, so its operations are a 404 like any unknown object.
NOT_AN_AGENCYS = {"provider": "google"}


def _operations() -> list[tuple[str, str, dict[str, Any]]]:
    document = v2_app.openapi()
    return [
        (method.upper(), path, operation)
        for path, operations in document["paths"].items()
        for method, operation in operations.items()
    ]


def _fill(template: str, ids: dict[str, str]) -> str:
    def value(match: re.Match[str]) -> str:
        name = match.group(1)
        assert name in ids, f"no id for path parameter {name!r}: add it to the sweep"
        return ids[name]

    return re.sub(r"\{(\w+)\}", value, template)


def _substitute(value: Any, ids: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _fill(value, ids)
    if isinstance(value, list):
        return [_substitute(item, ids) for item in value]
    if isinstance(value, dict):
        return {key: _substitute(item, ids) for key, item in value.items()}
    return value


def _request(method: str, path: str, ids: dict[str, str]) -> dict[str, Any]:
    body = BODIES.get((method, path), {})
    return {"json": _substitute(body["json"], ids)} if "json" in body else dict(body)


async def _people(db: AsyncSession, other: OtherAgency) -> tuple[Application, Application]:
    mine = await make_candidate(db, name="Agency A", email="a@a.example")
    campaign = await make_campaign(other.db, title="B's campaign", client_id=other.client_id)
    theirs = await make_candidate(other.db, campaign, name="Agency B", email="b@b.example")
    return mine, theirs


@dataclass(frozen=True)
class TeamOfB:
    user_id: uuid.UUID
    membership_id: uuid.UUID
    invitation_id: uuid.UUID


async def _team_of_b(db: AsyncSession, other: OtherAgency) -> TeamOfB:
    """A recruiter and an open invitation in agency B (identity's tables are global: written
    through the test's session)."""
    user = await identity.ensure_user(
        db, "recruiter.b@agency-b.example", display_name="B's recruiter", password=PASSWORD
    )
    await identity.ensure_membership(db, other.agency_id, user.id, Role.recruiter)
    membership = await db.scalar(
        select(Membership.id).where(
            Membership.agency_id == other.agency_id, Membership.user_id == user.id
        )
    )
    invitation = Invitation(
        id=uuid.uuid4(),
        agency_id=other.agency_id,
        email="invited.b@agency-b.example",
        role="viewer",
        all_clients=False,
        client_ids=[other.client_id],
        token_hash=identity.hash_token("b's invitation"),
        expires_at=identity.utcnow() + timedelta(days=7),
    )
    db.add(invitation)
    await db.flush()
    assert membership is not None
    return TeamOfB(user.id, membership, invitation.id)


async def _b_team_state(db: AsyncSession, team: TeamOfB) -> tuple[object, ...]:
    db.expire_all()
    membership = await db.get(Membership, team.membership_id)
    invitation = await db.get(Invitation, team.invitation_id)
    assert membership is not None and invitation is not None
    return (
        membership.role,
        membership.status,
        invitation.token_hash,
        invitation.expires_at,
        invitation.revoked_at,
    )


async def _signed_in_as_a(users: Users) -> AsyncClient:
    """An owner of the default agency (A), signed in: ``users.client`` carries the cookies."""
    await users.user(EMAIL)
    assert (await users.login(EMAIL)).status_code == 200
    return users.client


async def _call(client: AsyncClient, method: str, url: str, **kwargs: Any) -> Response:
    headers = csrf(client) if method not in {"GET", "HEAD", "OPTIONS"} else {}
    return await client.request(method, url, headers=headers, **kwargs)


async def _b_counts(other: OtherAgency) -> dict[str, int]:
    other.db.expire_all()
    return {
        model.__tablename__: await other.db.scalar(select(func.count()).select_from(model)) or 0
        for model in (Campaign, Application, Person, Resume)
    }


def test_every_operation_with_a_body_has_one_in_the_sweep() -> None:
    missing = [
        f"{method} {path}"
        for method, path, operation in _operations()
        if "requestBody" in operation and (method, path) not in BODIES
    ]
    assert missing == []


async def test_no_response_shows_another_agencys_ids(
    users: Users, db_session: AsyncSession, other_agency: OtherAgency
) -> None:
    client = await _signed_in_as_a(users)
    mine, theirs = await _people(db_session, other_agency)
    team = await _team_of_b(db_session, other_agency)
    ids = {
        "campaign_id": str(mine.campaign_id),
        "application_id": str(mine.id),
        "person_id": str(mine.person_id),
        "client_id": str(DEFAULT_CLIENT_ID),
    }
    forbidden = {
        str(value)
        for value in (
            theirs.campaign_id,
            theirs.id,
            theirs.person_id,
            theirs.resume_id,
            other_agency.agency_id,
            other_agency.client_id,
            team.user_id,
            team.membership_id,
            team.invitation_id,
        )
    } | {"agency-b.example"}

    called = 0
    for method, path, _ in _operations():
        if method != "GET" or path in STREAMS or path in REDIRECTS:
            continue
        resp = await client.get(V2 + _fill(path, ids))
        assert resp.status_code == 200, (path, resp.text)
        leaked = {value for value in forbidden if value in resp.text}
        assert not leaked, (path, leaked)
        called += 1
    assert called >= 10  # the sweep really ran


async def test_every_operation_with_another_agencys_ids_is_not_found(
    users: Users, db_session: AsyncSession, other_agency: OtherAgency
) -> None:
    client = await _signed_in_as_a(users)
    _, theirs = await _people(db_session, other_agency)
    team = await _team_of_b(db_session, other_agency)
    ids = {
        "campaign_id": str(theirs.campaign_id),
        "application_id": str(theirs.id),
        "person_id": str(theirs.person_id),
        "client_id": str(other_agency.client_id),
        "membership_id": str(team.membership_id),
        "invitation_id": str(team.invitation_id),
        **NOT_AN_AGENCYS,
    }
    before = await _b_counts(other_agency)
    team_before = await _b_team_state(db_session, team)

    swept = []
    for method, path, _ in _operations():
        if "{" not in path:
            continue
        resp = await _call(client, method, V2 + _fill(path, ids), **_request(method, path, ids))
        assert resp.status_code == 404, (method, path, resp.status_code, resp.text)
        swept.append(f"{method} {path}")

    assert len(swept) >= 20
    assert await _b_counts(other_agency) == before
    assert await _b_team_state(db_session, team) == team_before
    notes = await other_agency.db.scalar(
        select(Application.notes).where(Application.id == ids["application_id"])
    )
    title = await other_agency.db.scalar(
        select(Campaign.title).where(Campaign.id == ids["campaign_id"])
    )
    client = await other_agency.db.scalar(select(Client).where(Client.id == ids["client_id"]))
    assert (notes, title) == (None, "B's campaign")
    assert client is not None and (client.name, client.status) == ("Other client", "active")


async def test_another_agency_cannot_be_made_active(
    users: Users, other_agency: OtherAgency
) -> None:
    client = await _signed_in_as_a(users)
    path = "/me/active-agency"
    body = _request("PUT", path, {"agency_id": str(other_agency.agency_id)})
    resp = await _call(client, "PUT", V2 + path, **body)
    assert resp.status_code in {403, 404}, resp.text
    me = (await client.get(f"{V2}/me")).json()
    assert str(other_agency.agency_id) not in str(me)


async def test_another_agencys_client_cannot_be_given_to_a_campaign(
    users: Users, db_session: AsyncSession, other_agency: OtherAgency
) -> None:
    client = await _signed_in_as_a(users)
    mine = await make_campaign(db_session)
    theirs = str(other_agency.client_id)
    created = await _call(
        client, "POST", f"{V2}/campaigns", json={"title": "T", "role": "R", "client_id": theirs}
    )
    moved = await _call(client, "PATCH", f"{V2}/campaigns/{mine.id}", json={"client_id": theirs})
    for resp in (created, moved):
        assert resp.status_code == 422, resp.text
        assert resp.json()["errors"] == [{"loc": ["body", "client_id"], "msg": "Unknown client"}]
    assert (await db_session.scalar(select(func.count()).select_from(Campaign))) == 1


def test_the_sweep_knows_every_path_parameter() -> None:
    names = {match for _, path, _ in _operations() for match in re.findall(r"\{(\w+)\}", path)}
    assert names == {
        "campaign_id",
        "application_id",
        "person_id",
        "client_id",  # E2-06
        "membership_id",  # E2-07
        "invitation_id",  # E2-07
        "provider",  # E2-08
    }
