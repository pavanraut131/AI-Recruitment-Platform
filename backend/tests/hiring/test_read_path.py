"""E0-19: the legacy candidate endpoints served from Applications (spec §7), on the session
database (factories, rolled back).

- **No N+1:** the number of SQL statements per request does not grow with the number of rows:
  a page of 50 costs the same as a page of 5 (application page + one extraction lookup), and
  so do the export, the detail and the campaign stats.
- **Legacy ids:** a merged legacy candidate's id (``legacy_candidate_map``) answers with the
  surviving application, whose id the response carries.
- **Field sources:** per-resume facts come from the extraction of the application's *current*
  resume, never from the Person's (cross-campaign) profile.

The byte-for-byte equivalence with the legacy responses is proven by the characterization
snapshots (``tests/characterization``) and, on the seed, by ``tests/ingestion/test_read_switch.py``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import Engine, event
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.hiring.models import Campaign, LegacyCandidateMap
from app.modules.talent.models import Person
from tests.factories import at, make_campaign, make_candidate, make_resume

API = "/api/v1"


@contextmanager
def counting_statements(db: AsyncSession) -> Iterator[list[str]]:
    """Every SQL statement run on the test's connection while the block runs."""
    statements: list[str] = []
    engine: Engine = db.bind.engine.sync_engine  # type: ignore[union-attr]

    def record(conn: Any, cursor: Any, statement: str, *_: Any) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


async def _campaign_with(db: AsyncSession, n: int) -> Campaign:
    campaign = await make_campaign(db)
    for i in range(n):
        resume = await make_resume(db, campaign, original_filename=f"cv_{i}.pdf", uploaded_at=at(i))
        await make_candidate(
            db,
            resume=resume,
            name=f"Applicant {i}",
            email=f"applicant{i}@example.com",
            score=float(i),
            skills=["Python", f"Skill{i}"],
            created_at=at(i),
        )
    return campaign


@asynccontextmanager
async def _requests(db: AsyncSession) -> AsyncIterator[list[str]]:
    db.expire_all()  # nothing served from the identity map
    with counting_statements(db) as statements:
        yield statements


@pytest.mark.parametrize("n", [5, 50])
async def test_a_page_costs_the_same_number_of_queries_whatever_its_size(
    client: AsyncClient, db_session: AsyncSession, n: int
) -> None:
    campaign_id = (await _campaign_with(db_session, n)).id

    async with _requests(db_session) as statements:
        resp = await client.get(f"{API}/campaigns/{campaign_id}/candidates?page_size=50")
    assert len(resp.json()) == n
    # The page of applications, then every extraction of the page in one query.
    assert len(statements) == 2, statements

    async with _requests(db_session) as statements:
        resp = await client.get(
            f"{API}/campaigns/{campaign_id}/candidates?page_size=50&search=applicant&skill=py"
        )
    assert len(resp.json()) == n
    # Application filters -> resume ids, ingestion keeps the matching ones, the page, its
    # extractions.
    assert len(statements) == 4, statements

    async with _requests(db_session) as statements:
        resp = await client.get(f"{API}/campaigns/{campaign_id}/candidates/export")
    assert len(resp.text.split("\r\n")) == n + 2
    assert len(statements) == 2, statements

    async with _requests(db_session) as statements:
        resp = await client.get(f"{API}/campaigns/{campaign_id}/stats")
    assert resp.json()["top_skills"][0] == {"skill": "Python", "count": n}
    # Resume counts, category stats, the applications' resumes, their skills.
    assert len(statements) == 4, statements


async def test_a_merged_legacy_id_answers_with_the_surviving_application(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    application = await make_candidate(db_session, pipeline_stage="screened")
    merged_id = uuid.uuid4()  # a legacy candidate merged into it by the backfill
    db_session.add(LegacyCandidateMap(candidate_id=merged_id, application_id=application.id))
    await db_session.flush()

    by_own_id = (await client.get(f"{API}/candidates/{application.id}")).json()
    by_merged_id = (await client.get(f"{API}/candidates/{merged_id}")).json()

    assert by_merged_id == by_own_id
    assert by_merged_id["id"] == str(application.id)  # the application's id, not the legacy one
    resp = await client.patch(f"{API}/candidates/{merged_id}/notes", json={"notes": "via legacy"})
    assert (resp.json()["id"], resp.json()["notes"]) == (str(application.id), "via legacy")
    assert (await client.delete(f"{API}/candidates/{merged_id}")).status_code == 204
    assert (await client.get(f"{API}/candidates/{application.id}")).status_code == 404
    assert (await client.get(f"{API}/candidates/{merged_id}")).status_code == 404


async def test_per_resume_facts_come_from_the_current_resume_not_the_person(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    application = await make_candidate(db_session, name="Name On This Resume", skills=["Go"])
    person = await db_session.get_one(Person, application.person_id)
    # The Person's profile follows the person's newest resume in any campaign.
    person.full_name, person.skills = "Name On A Later Resume", ["Rust"]
    await db_session.flush()

    body = (await client.get(f"{API}/candidates/{application.id}")).json()

    assert (body["name"], body["skills"]) == ("Name On This Resume", ["Go"])


async def test_an_older_resume_of_a_merged_application_answers_with_the_application(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    application = await make_candidate(db_session)
    older = await make_resume(db_session, original_filename="older.pdf")
    older.campaign_id, older.person_id = application.campaign_id, application.person_id
    await db_session.flush()

    body = (await client.get(f"{API}/resumes/{older.id}/candidate")).json()

    assert (body["id"], body["resume_id"]) == (str(application.id), str(application.resume_id))


async def test_a_resume_still_processing_has_no_row_yet(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Before the switch the extract step created a placeholder candidate (empty row in the
    # list, 200 here). Since E0-19 a row exists only once the resume is scored.
    campaign = await make_campaign(db_session)
    resume = await make_resume(db_session, campaign, status="processing", raw_text="text")

    resp = await client.get(f"{API}/resumes/{resume.id}/candidate")

    assert (resp.status_code, resp.json()) == (404, {"detail": "Candidate not yet processed"})
    assert (await client.get(f"{API}/campaigns/{campaign.id}/candidates")).json() == []
