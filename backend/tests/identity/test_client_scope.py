"""Client scoping (EPIC-02 spec §5.3, §8.3, §11.3, §15 Q2), signed in for real.

The default agency has three clients: Acme and Globex, and "General" (the migration's). A
recruiter and a viewer are limited to **Acme**. They see:

- in lists (campaigns, clients, Home, stats) only Acme's campaigns and what belongs to them, and
  the ``client_id`` filter narrows that further but never widens it;
- a ``404`` for any campaign, application or client outside Acme, on every operation that takes
  one (the same answer as for another agency's, so nothing confirms it exists);
- the whole talent pool, but on a Person's profile only their applications to Acme's campaigns
  (scores and stages of the others left out), and only those counted in the list;
- new campaigns only for Acme (their default client when none is named; 422 for another one);
- the same when the membership comes from an invitation limited to Acme (E2-07).

An owner (every client) sees everything, as before.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.hiring.models import Application, Campaign, Client
from app.modules.identity.permissions import ROLE_PERMISSIONS
from app.modules.identity.schemas import Role
from app.modules.talent.models import Person
from app.platform.tenancy import DEFAULT_AGENCY_ID, DEFAULT_CLIENT_ID
from app.v2 import route_permissions, v2_app
from tests.factories import at, make_campaign, make_candidate, make_client, make_resume
from tests.identity.conftest import V2, Mailbox, Users, anonymous_csrf, csrf, problem
from tests.tenancy.test_api_sweep import BODIES, STREAMS


@dataclass
class World:
    acme: Client
    globex: Client
    acme_campaign: Campaign
    globex_campaign: Campaign
    general_campaign: Campaign
    person: Person
    acme_application: Application
    globex_application: Application


async def _world(db: AsyncSession) -> World:
    acme = await make_client(db, "Acme", created_at=at(1))
    globex = await make_client(db, "Globex", created_at=at(2))
    acme_campaign = await make_campaign(db, title="Acme backend", client_id=acme.id)
    globex_campaign = await make_campaign(
        db, title="Globex data", client_id=globex.id, created_at=at(5)
    )
    general_campaign = await make_campaign(db, title="General ops", created_at=at(6))
    # One Person who applied to Acme and to Globex.
    acme_application = await make_candidate(
        db, acme_campaign, name="Jane Doe", score=82.0, category="strong_match", notes="Acme"
    )
    person = await db.get(Person, acme_application.person_id)
    assert person is not None
    resume = await make_resume(db, globex_campaign, original_filename="jane-globex.pdf")
    resume.person_id = person.id
    globex_application = Application(
        agency_id=DEFAULT_AGENCY_ID,
        campaign_id=globex_campaign.id,
        person_id=person.id,
        resume_id=resume.id,
        resume_uploaded_at=resume.uploaded_at,
        score=91.0,
        category="strong_match",
        pipeline_stage="offer",
        notes="Globex only",
        created_at=at(10),
        updated_at=at(10),
    )
    db.add(globex_application)
    # Resumes still being processed, in each campaign.
    for campaign in (acme_campaign, globex_campaign, general_campaign):
        await make_resume(db, campaign, original_filename=f"{campaign.title}.pdf", status="pending")
    await db.flush()
    return World(
        acme,
        globex,
        acme_campaign,
        globex_campaign,
        general_campaign,
        person,
        acme_application,
        globex_application,
    )


@pytest.fixture
async def world(db_session: AsyncSession) -> World:
    return await _world(db_session)


async def _sign_in(users: Users, role: Role, world: World | None, *, limit: bool = True) -> None:
    email = f"{role.value}.scoped@acme.example"
    user = await users.user(email, {DEFAULT_AGENCY_ID: role})
    if limit:
        await users.limit(user, [world.acme.id] if world else [])
    assert (await users.login(email)).status_code == 200


def _ids(resp: Response, key: str = "id") -> list[str]:
    assert resp.status_code == 200, resp.text
    return [item[key] for item in resp.json()["items"]]


# ── Lists ─────────────────────────────────────────────────────
@pytest.mark.parametrize("role", [Role.recruiter, Role.viewer], ids=lambda r: r.value)
async def test_lists_show_only_the_scopes_clients(
    users: Users, auth_client: AsyncClient, world: World, role: Role
) -> None:
    await _sign_in(users, role, world)
    acme, globex = str(world.acme.id), str(world.globex.id)
    acme_campaign = str(world.acme_campaign.id)

    assert _ids(await auth_client.get(f"{V2}/campaigns")) == [acme_campaign]
    assert _ids(await auth_client.get(f"{V2}/campaigns", params={"client_id": acme})) == [
        acme_campaign
    ]
    # A filter outside the scope narrows to nothing; it never widens.
    for params in ({"client_id": globex}, {"client_id": str(DEFAULT_CLIENT_ID)}):
        assert _ids(await auth_client.get(f"{V2}/campaigns", params=params)) == []

    clients = await auth_client.get(f"{V2}/clients")
    assert _ids(clients) == [acme]
    assert clients.json()["items"][0]["name"] == "Acme"

    home = (await auth_client.get(f"{V2}/home")).json()
    assert [c["id"] for c in home["active_campaigns"]] == [acme_campaign]
    assert home["active_campaigns"][0]["client"] == {"id": acme, "name": "Acme"}
    assert home["processing"]["total"] == 1
    assert [r["campaign_id"] for r in home["processing"]["items"]] == [acme_campaign]
    assert [m["id"] for m in home["recent_strong_matches"]] == [str(world.acme_application.id)]
    narrowed = (await auth_client.get(f"{V2}/home", params={"client_id": globex})).json()
    assert (narrowed["active_campaigns"], narrowed["processing"]["total"]) == ([], 0)
    assert narrowed["recent_strong_matches"] == []

    stats = (await auth_client.get(f"{V2}/stats")).json()
    assert stats["campaigns"]["total"] == 1
    assert stats["applications"]["total"] == 1
    assert stats["applications"]["by_stage"]["offer"] == 0  # Globex's offer is not counted
    assert stats["resumes"]["total"] == 2  # Acme's processed resume and its pending one
    assert stats["people"] == 1  # the talent pool is agency-wide


async def test_an_owner_sees_every_client_and_can_filter(
    users: Users, auth_client: AsyncClient, world: World
) -> None:
    await _sign_in(users, Role.owner, world, limit=False)
    everything = _ids(await auth_client.get(f"{V2}/campaigns"))
    assert set(everything) == {
        str(c.id) for c in (world.acme_campaign, world.globex_campaign, world.general_campaign)
    }
    two = {"client_id": [str(world.acme.id), str(world.globex.id)]}
    assert set(_ids(await auth_client.get(f"{V2}/campaigns", params=two))) == {
        str(world.acme_campaign.id),
        str(world.globex_campaign.id),
    }
    assert (await auth_client.get(f"{V2}/stats", params=two)).json()["campaigns"]["total"] == 2
    assert len(_ids(await auth_client.get(f"{V2}/clients"))) == 3
    person = (await auth_client.get(f"{V2}/people/{world.person.id}")).json()
    assert len(person["applications"]) == 2


# ── Single objects: 404 ───────────────────────────────────────
SCOPED_IDS = {"campaign_id", "application_id", "client_id"}


def _operations() -> list[tuple[str, str]]:
    """Every operation with a campaign, application or client id in its path (People are
    agency-wide; team members and invitations are not client-owned)."""
    document = v2_app.openapi()
    return [
        (method.upper(), path)
        for path, operations in document["paths"].items()
        for method in operations
        if (names := set(re.findall(r"\{(\w+)\}", path))) and names <= SCOPED_IDS
    ]


def _permissions_of(method: str, path: str) -> frozenset[Any]:
    from fastapi.routing import APIRoute, iter_route_contexts

    for route in iter_route_contexts(v2_app.routes):
        if (
            isinstance(route.original_route, APIRoute)
            and route.path_format == path
            and method in (route.methods or ())
        ):
            return route_permissions(route)
    raise AssertionError((method, path))


def _fill(value: Any, ids: dict[str, str]) -> Any:
    if isinstance(value, str):
        return re.sub(r"\{(\w+)\}", lambda m: ids[m.group(1)], value)
    if isinstance(value, list):
        return [_fill(item, ids) for item in value]
    if isinstance(value, dict):
        return {key: _fill(item, ids) for key, item in value.items()}
    return value


@pytest.mark.parametrize("role", [Role.recruiter, Role.viewer], ids=lambda r: r.value)
async def test_every_object_outside_the_scope_is_not_found(
    users: Users, auth_client: AsyncClient, db_session: AsyncSession, world: World, role: Role
) -> None:
    """Every operation with a campaign, application or client id in its path, with Globex's:
    ``404``, or ``403`` first where the role lacks the permission anyway. Nothing changes."""
    await _sign_in(users, role, world)
    ids = {
        "campaign_id": str(world.globex_campaign.id),
        "application_id": str(world.globex_application.id),
        "client_id": str(world.globex.id),
    }
    swept = []
    for method, path in _operations():
        body = BODIES.get((method, path), {})
        kwargs: dict[str, Any] = (
            {"json": _fill(body["json"], ids)} if "json" in body else dict(body)
        )
        if method != "GET":
            kwargs["headers"] = csrf(auth_client)
        resp = await auth_client.request(method, V2 + _fill(path, ids), **kwargs)
        allowed = _permissions_of(method, path) <= ROLE_PERMISSIONS[role]
        expected = 404 if allowed else 403
        assert resp.status_code == expected, (method, path, resp.status_code, resp.text)
        if expected == 404:
            assert path in STREAMS or problem(resp)["detail"].endswith("not found"), resp.text
        swept.append(f"{method} {path}")
    assert len(swept) == 16  # every operation with a campaign, application or client id

    db_session.expire_all()
    application = await db_session.get(Application, uuid.UUID(ids["application_id"]))
    campaign = await db_session.get(Campaign, uuid.UUID(ids["campaign_id"]))
    client = await db_session.get(Client, uuid.UUID(ids["client_id"]))
    assert application is not None and application.notes == "Globex only"
    assert campaign is not None and campaign.title == "Globex data"
    assert client is not None and (client.name, client.status) == ("Globex", "active")


async def test_objects_in_the_scope_are_found(
    users: Users, auth_client: AsyncClient, world: World
) -> None:
    await _sign_in(users, Role.recruiter, world)
    for url in (
        f"/campaigns/{world.acme_campaign.id}",
        f"/campaigns/{world.acme_campaign.id}/applications",
        f"/campaigns/{world.acme_campaign.id}/resumes",
        f"/campaigns/{world.acme_campaign.id}/stats",
        f"/campaigns/{world.acme_campaign.id}/applications/export",
        f"/applications/{world.acme_application.id}",
        f"/clients/{world.acme.id}",
    ):
        assert (await auth_client.get(V2 + url)).status_code == 200, url


# ── People: agency-wide, their applications are not ───────────
@pytest.mark.parametrize("role", [Role.recruiter, Role.viewer], ids=lambda r: r.value)
async def test_a_persons_applications_outside_the_scope_are_hidden(
    users: Users, auth_client: AsyncClient, world: World, role: Role
) -> None:
    await _sign_in(users, role, world)
    resp = await auth_client.get(f"{V2}/people/{world.person.id}")
    assert resp.status_code == 200, resp.text
    profile = resp.json()
    assert [a["id"] for a in profile["applications"]] == [str(world.acme_application.id)]
    assert str(world.globex_campaign.id) not in resp.text
    assert "offer" not in resp.text and "91.0" not in resp.text

    people = (await auth_client.get(f"{V2}/people")).json()
    assert [(p["id"], p["application_count"]) for p in people["items"]] == [
        (str(world.person.id), 1)
    ]


async def test_a_person_with_no_application_in_the_scope_is_still_found(
    users: Users, auth_client: AsyncClient, db_session: AsyncSession, world: World
) -> None:
    only_globex = await make_candidate(db_session, world.globex_campaign, name="Raj Patel")
    await _sign_in(users, Role.viewer, world)
    resp = await auth_client.get(f"{V2}/people/{only_globex.person_id}")
    assert resp.status_code == 200
    assert (resp.json()["name"], resp.json()["applications"]) == ("Raj Patel", [])


# ── Campaigns are created and moved within the scope ──────────
async def test_campaigns_are_created_and_moved_within_the_scope(
    users: Users, auth_client: AsyncClient, world: World
) -> None:
    await _sign_in(users, Role.recruiter, world)
    headers = csrf(auth_client)
    # No client named: the recruiter's default client, the oldest active one in their scope.
    created = await auth_client.post(
        f"{V2}/campaigns", json={"title": "New", "role": "Dev"}, headers=headers
    )
    assert created.status_code == 201, created.text
    assert created.json()["client"] == {"id": str(world.acme.id), "name": "Acme"}

    for client_id in (world.globex.id, DEFAULT_CLIENT_ID, uuid.uuid4()):
        resp = await auth_client.post(
            f"{V2}/campaigns",
            json={"title": "T", "role": "R", "client_id": str(client_id)},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        assert problem(resp)["errors"] == [{"loc": ["body", "client_id"], "msg": "Unknown client"}]
        moved = await auth_client.patch(
            f"{V2}/campaigns/{world.acme_campaign.id}",
            json={"client_id": str(client_id)},
            headers=headers,
        )
        assert moved.status_code == 422, moved.text
    campaign = (await auth_client.get(f"{V2}/campaigns/{world.acme_campaign.id}")).json()
    assert campaign["client"]["id"] == str(world.acme.id)


async def test_an_empty_scope_sees_no_campaign_and_has_no_default_client(
    users: Users, auth_client: AsyncClient, world: World
) -> None:
    await _sign_in(users, Role.recruiter, None)
    assert _ids(await auth_client.get(f"{V2}/campaigns")) == []
    assert _ids(await auth_client.get(f"{V2}/clients")) == []
    assert (await auth_client.get(f"{V2}/stats")).json()["campaigns"]["total"] == 0
    resp = await auth_client.post(
        f"{V2}/campaigns", json={"title": "T", "role": "R"}, headers=csrf(auth_client)
    )
    assert resp.status_code == 422
    assert problem(resp)["errors"] == [
        {
            "loc": ["body", "client_id"],
            "msg": "Choose a client: there is no active client you can use",
        }
    ]


async def test_me_shows_the_client_scope(
    users: Users, auth_client: AsyncClient, world: World
) -> None:
    await _sign_in(users, Role.viewer, world)
    me = (await auth_client.get(f"{V2}/me")).json()
    assert me["client_scope"] == {"all_clients": False, "client_ids": [str(world.acme.id)]}
    assert me["permissions"] == ["workspace:read", "client:read"]


async def test_uploads_follow_the_campaigns_client(
    users: Users, auth_client: AsyncClient, db_session: AsyncSession, world: World
) -> None:
    """Uploads go through the campaign (404 above for Globex's); one to Acme's is stored and
    records who uploaded it."""
    await _sign_in(users, Role.recruiter, world)
    pdf = b"%PDF-1.4\n% scoped\n"
    resp = await auth_client.post(
        f"{V2}/campaigns/{world.acme_campaign.id}/uploads",
        files=[("files", ("cv.pdf", pdf, "application/pdf"))],
        headers=csrf(auth_client),
    )
    assert resp.status_code == 202, resp.text
    from app.modules.identity.models import User
    from app.modules.ingestion.models import Resume

    (accepted,) = resp.json()["accepted"]
    uploader = await db_session.scalar(
        select(Resume.uploaded_by_user_id).where(Resume.id == accepted["id"])
    )
    user = await db_session.scalar(
        select(User).where(User.email == "recruiter.scoped@acme.example")
    )
    assert user is not None and uploader == user.id


# ── A member invited for one client (E2-07's invitations) ─────
async def test_a_member_invited_for_one_client_sees_only_that_client(
    users: Users,
    auth_client: AsyncClient,
    browser: AsyncClient,
    mailbox: Mailbox,
    world: World,
) -> None:
    """An owner invites a recruiter limited to Acme; once they accept, their requests carry
    that scope: Acme's campaign and application only, Globex's are 404s."""
    await _sign_in(users, Role.owner, world, limit=False)
    invited = await auth_client.post(
        f"{V2}/team/invitations",
        json={
            "email": "rita@acme.example",
            "role": "recruiter",
            "all_clients": False,
            "client_ids": [str(world.acme.id)],
        },
        headers=csrf(auth_client),
    )
    assert invited.status_code == 201, invited.text
    assert invited.json()["clients"] == [{"id": str(world.acme.id), "name": "Acme"}]

    accepted = await browser.post(
        f"{V2}/auth/invitations/accept",
        json={
            "token": mailbox.token("rita@acme.example"),
            "display_name": "Rita",
            "password": "rita's long passphrase",
        },
        headers=await anonymous_csrf(browser),
    )
    assert accepted.status_code == 200, accepted.text
    me = accepted.json()
    assert me["client_scope"] == {"all_clients": False, "client_ids": [str(world.acme.id)]}
    assert me["role"] == "recruiter" and "campaign:delete" not in me["permissions"]

    assert _ids(await browser.get(f"{V2}/campaigns")) == [str(world.acme_campaign.id)]
    assert _ids(await browser.get(f"{V2}/clients")) == [str(world.acme.id)]
    for url in (
        f"/campaigns/{world.globex_campaign.id}",
        f"/applications/{world.globex_application.id}",
        f"/clients/{world.globex.id}",
    ):
        assert (await browser.get(V2 + url)).status_code == 404, url
    profile = (await browser.get(f"{V2}/people/{world.person.id}")).json()
    assert [a["id"] for a in profile["applications"]] == [str(world.acme_application.id)]
