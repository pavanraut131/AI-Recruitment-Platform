"""No N+1 on the v2 list endpoints: the number of SQL statements per request is fixed, the same
for a page of 3 rows as for a page of 30 (and for the aggregates, whatever the data)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.hiring.models import Application
from tests.contract_v2.data import V2, add_identifier, applicant, counting_statements
from tests.factories import at, make_campaign, make_resume


@asynccontextmanager
async def _requests(db: AsyncSession) -> AsyncIterator[list[str]]:
    db.expire_all()  # nothing served from the identity map
    with counting_statements(db) as statements:
        yield statements


async def _workspace(db: AsyncSession, n: int) -> str:
    """``n`` campaigns; the first has ``n`` applications (people with an email each) and ``n``
    resumes in progress."""
    campaigns = [await make_campaign(db, title=f"C{i}", created_at=at(i)) for i in range(n)]
    first = campaigns[0]
    for i in range(n):
        application = await applicant(
            db, first, i, score=float(i), category="strong_match", skills=["Python", f"S{i}"]
        )
        await add_identifier(db, application.person_id, "email", f"p{i}@example.com")
        await make_resume(
            db, campaigns[i], original_filename=f"wip{i}.pdf", status="pending", uploaded_at=at(i)
        )
    return str(first.id)


# (path, statements) per endpoint; {c} is the first campaign.
EXPECTED = [
    # campaign page + total, application tallies, resume tallies
    ("/campaigns?page_size=100", 4),
    # campaign exists, total, page, extractions
    ("/campaigns/{c}/applications?page_size=100", 4),
    # + resume ids of the filtered applications, ingestion keeps the matching ones (no count)
    ("/campaigns/{c}/applications?page_size=100&q=applicant&skill=py", 5),
    # campaign exists, (id, resume_id) pairs, names, the page by id, extractions
    ("/campaigns/{c}/applications?page_size=100&sort=name", 5),
    # campaign exists, total, all rows, extractions
    ("/campaigns/{c}/applications/export", 4),
    # campaign exists, total, page, the resumes' applications
    ("/campaigns/{c}/resumes?page_size=100", 4),
    # people total + page, first emails, application activity
    ("/people?page_size=100", 4),
    ("/people?page_size=100&q=example", 4),
    # resume tally, campaign, category rows, stages, buckets, resume ids, skills
    ("/campaigns/{c}/stats", 7),
    # resume tally, campaign counts, category rows, stages, people
    ("/stats", 5),
    # active campaigns (3), in progress (2) + their applications (none resolved: 0),
    # campaign refs, strong matches + extractions
    ("/home", 8),
]


@pytest.mark.parametrize("n", [3, 30])
@pytest.mark.parametrize(("path", "expected"), EXPECTED, ids=[p for p, _ in EXPECTED])
async def test_statements_per_request_do_not_grow_with_rows(
    client: AsyncClient, db_session: AsyncSession, n: int, path: str, expected: int
) -> None:
    campaign_id = await _workspace(db_session, n)
    async with _requests(db_session) as statements:
        resp = await client.get(V2 + path.format(c=campaign_id))
    assert resp.status_code == 200, resp.text
    assert len(statements) == expected, "\n\n".join(statements)


async def test_person_profile_is_a_fixed_number_of_queries(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    campaigns = [await make_campaign(db_session, title=f"C{i}") for i in range(5)]
    first = await applicant(db_session, campaigns[0], 0)
    person_id = first.person_id
    await add_identifier(db_session, person_id, "email", "p@example.com")
    for i, campaign in enumerate(campaigns[1:], start=1):
        resume = await make_resume(db_session, campaign, original_filename=f"again{i}.pdf")
        resume.person_id = person_id
        db_session.add(
            Application(agency_id=campaign.agency_id, campaign_id=campaign.id,
                        person_id=person_id, resume_id=resume.id,
                        created_at=at(i), updated_at=at(i))
        )  # fmt: skip
    await db_session.flush()
    async with _requests(db_session) as statements:
        resp = await client.get(f"{V2}/people/{person_id}")
    assert len(resp.json()["applications"]) == 5
    # person, identifiers, conflicts, applications with their campaigns
    assert len(statements) == 4, statements
