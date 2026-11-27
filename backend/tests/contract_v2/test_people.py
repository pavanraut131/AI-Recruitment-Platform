"""Contract: /api/v2/people (talent list with search; Person profile with applications and
identity-conflict flags)."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from syrupy.assertion import SnapshotAssertion

from app.modules.hiring.models import Application
from app.modules.talent.models import Person
from tests.contract_v2.data import UNKNOWN_ID, V2, add_conflict, add_identifier, applicant
from tests.default_agency import DEFAULT_AGENCY_ID
from tests.factories import at, make_campaign, make_resume
from tests.snapshot_utils import response_data


async def _people(db: AsyncSession) -> dict[str, Person]:
    """Three people: Jane (two applications, two emails, an inferred phone), Raj (one), Old
    Timer (none). Most recently updated: Raj, Jane, Old Timer."""
    first = await make_campaign(db, title="Backend")
    second = await make_campaign(db, title="Platform", status="closed")
    jane_app = await applicant(db, first, 1, name="Jane Doe", work_experience=[
        {"title": "Staff Engineer", "company": "Acme"}, {"title": "Engineer"}])  # fmt: skip
    jane = await db.get(Person, jane_app.person_id)
    assert jane is not None
    jane.updated_at = at(20)
    second_resume = await make_resume(db, second, original_filename="jane2.pdf", uploaded_at=at(30))
    second_resume.person_id = jane.id
    db.add(
        Application(
            agency_id=second.agency_id,
            campaign_id=second.id, person_id=jane.id, resume_id=second_resume.id,
            resume_uploaded_at=at(30), score=55.0, category="moderate_match",
            pipeline_stage="technical", created_at=at(30), updated_at=at(31),
        )
    )  # fmt: skip
    await add_identifier(db, jane, "email", "Jane.Doe@Example.com", created_at=at(1))
    await add_identifier(db, jane, "email", "jd@work.io", created_at=at(2))
    await add_identifier(db, jane, "phone", "98765 43210", inferred=True, created_at=at(3))

    raj_app = await applicant(db, first, 2, name="Raj Patel", work_experience=[])
    raj = await db.get(Person, raj_app.person_id)
    assert raj is not None
    raj.updated_at = at(40)
    await add_identifier(db, raj, "email", "raj@example.com")

    old = Person(
        full_name="Old Timer",
        skills=["COBOL"],
        created_at=at(0),
        updated_at=at(0),
        agency_id=DEFAULT_AGENCY_ID,
    )
    db.add(old)
    await db.flush()
    return {"jane": jane, "raj": raj, "old": old}


# ── GET /people ───────────────────────────────────────────────
async def test_list_people(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    await _people(db_session)
    resp = await client.get(f"{V2}/people")
    assert response_data(resp) == snapshot


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        pytest.param("jane", ["Jane Doe"], id="name"),
        pytest.param("PATEL", ["Raj Patel"], id="name-case-insensitive"),
        pytest.param("work.io", ["Jane Doe"], id="second-email"),
        pytest.param("example.com", ["Raj Patel", "Jane Doe"], id="email-domain"),
        pytest.param("98765", [], id="phone-is-not-searched"),
        pytest.param("%", [], id="wildcard-literal"),
        pytest.param("", ["Raj Patel", "Jane Doe", "Old Timer"], id="empty-is-everyone"),
    ],
)
async def test_people_search(
    client: AsyncClient, db_session: AsyncSession, q: str, expected: list[str]
) -> None:
    await _people(db_session)
    body = (await client.get(f"{V2}/people", params={"q": q})).json()
    assert [p["name"] for p in body["items"]] == expected
    assert body["total"] == len(expected)


async def test_people_paging(client: AsyncClient, db_session: AsyncSession) -> None:
    await _people(db_session)
    body = (await client.get(f"{V2}/people?page=2&page_size=2")).json()
    assert ([p["name"] for p in body["items"]], body["total"]) == (["Old Timer"], 3)


async def test_list_people_invalid_query(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.get(f"{V2}/people?page_size=500")
    assert response_data(resp) == snapshot


# ── GET /people/{id} ──────────────────────────────────────────
async def test_get_person(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    people = await _people(db_session)
    jane, raj = people["jane"], people["raj"]
    # Jane got a resume whose phone was Raj's (chosen); and one of hers went to Raj.
    await add_conflict(db_session, jane.id, raj.id, uuid.uuid4(), created_at=at(100))
    await add_conflict(db_session, raj.id, jane.id, uuid.uuid4(), created_at=at(101))
    resp = await client.get(f"{V2}/people/{jane.id}")
    assert {"response": response_data(resp), "jane": jane.id, "raj": raj.id} == snapshot


async def test_get_person_without_applications(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    people = await _people(db_session)
    resp = await client.get(f"{V2}/people/{people['old'].id}")
    assert response_data(resp) == snapshot


async def test_get_person_not_found(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.get(f"{V2}/people/{UNKNOWN_ID}")
    assert response_data(resp) == snapshot
