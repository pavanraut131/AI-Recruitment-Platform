"""The isolation API sweep (EPIC-02 spec §11.2 ``test_api_sweep``): generated from the ``/api/v2``
OpenAPI document, so a new endpoint is covered (or the test names it) without editing it.

The test client is the default agency's (agency A, every request until E2-05). Agency B has a
campaign, an application, a Person and a resume, through a session bound to it.

1. Every ``GET`` (lists, and the detail URLs with A's ids): no id of B anywhere in any body.
2. Every operation with an id in its path, called with **B's** ids: ``404``, and B's rows are
   unchanged afterwards. Operations with a request body take it from :data:`BODIES`; one with a
   body that has no entry fails the test, so it is added here when it is added to the API.
"""

from __future__ import annotations

import re
from typing import Any

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.hiring.models import Application, Campaign
from app.modules.ingestion.models import Resume
from app.modules.talent.models import Person
from app.v2 import v2_app
from tests.factories import make_campaign, make_candidate
from tests.tenancy.conftest import OtherAgency

V2 = "/api/v2"
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
    # No id in the path (agency-wide); listed so the coverage check knows them.
    ("POST", "/campaigns"): {"json": {"title": "A's campaign", "role": "Engineer"}},
    ("POST", "/campaigns/parse-jd"): {"json": {"job_description": "A role."}},
}
# Opened as a stream: the 404 for B's id is checked; A's own stream would not end.
STREAMS = {"/campaigns/{campaign_id}/events"}


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
    client: AsyncClient, db_session: AsyncSession, other_agency: OtherAgency
) -> None:
    mine, theirs = await _people(db_session, other_agency)
    ids = {
        "campaign_id": str(mine.campaign_id),
        "application_id": str(mine.id),
        "person_id": str(mine.person_id),
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
        )
    }

    called = 0
    for method, path, _ in _operations():
        if method != "GET" or path in STREAMS:
            continue
        resp = await client.get(V2 + _fill(path, ids))
        assert resp.status_code == 200, (path, resp.text)
        leaked = {value for value in forbidden if value in resp.text}
        assert not leaked, (path, leaked)
        called += 1
    assert called >= 10  # the sweep really ran


async def test_every_operation_with_another_agencys_ids_is_not_found(
    client: AsyncClient, db_session: AsyncSession, other_agency: OtherAgency
) -> None:
    _, theirs = await _people(db_session, other_agency)
    ids = {
        "campaign_id": str(theirs.campaign_id),
        "application_id": str(theirs.id),
        "person_id": str(theirs.person_id),
    }
    before = await _b_counts(other_agency)

    swept = []
    for method, path, _ in _operations():
        if "{" not in path:
            continue
        resp = await client.request(method, V2 + _fill(path, ids), **_request(method, path, ids))
        assert resp.status_code == 404, (method, path, resp.status_code, resp.text)
        swept.append(f"{method} {path}")

    assert len(swept) >= 15
    assert await _b_counts(other_agency) == before
    notes = await other_agency.db.scalar(
        select(Application.notes).where(Application.id == ids["application_id"])
    )
    title = await other_agency.db.scalar(
        select(Campaign.title).where(Campaign.id == ids["campaign_id"])
    )
    assert (notes, title) == (None, "B's campaign")


def test_the_sweep_knows_every_path_parameter() -> None:
    names = {match for _, path, _ in _operations() for match in re.findall(r"\{(\w+)\}", path)}
    assert names == {"campaign_id", "application_id", "person_id"}
