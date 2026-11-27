"""The application's field sources (EPIC-00 spec §7, E0-19), on the session database.

Per-resume facts (name, contacts, skills, experience) come from the extraction snapshot of the
application's *current* resume, never from the Person's profile, which follows the person's
newest resume across all campaigns. Until E2-12 this file also covered the ``/api/v1`` candidate
endpoints served from applications (their query counts, merged legacy candidate ids, the
resume-to-candidate lookup); the v2 query counts are ``tests/contract_v2/test_query_counts.py``.
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.talent.models import Person
from tests.factories import make_campaign, make_candidate, make_resume

API = "/api/v2"


async def test_per_resume_facts_come_from_the_current_resume_not_the_person(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    application = await make_candidate(
        db_session, name="Name On This Resume", skills=["Go"], years_of_experience=3.0
    )
    person = await db_session.get_one(Person, application.person_id)
    # The Person's profile follows the person's newest resume in any campaign.
    person.full_name, person.skills, person.years_of_experience = (
        "Name On A Later Resume",
        ["Rust"],
        9.0,
    )
    await db_session.flush()

    body = (await client.get(f"{API}/applications/{application.id}")).json()

    assert (body["person"]["name"], body["skills"], body["years_of_experience"]) == (
        "Name On This Resume",
        ["Go"],
        3.0,
    )
    listed = (await client.get(f"{API}/campaigns/{application.campaign_id}/applications")).json()
    assert [a["person"]["name"] for a in listed["items"]] == ["Name On This Resume"]


async def test_a_resume_still_processing_has_no_application_yet(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Since E0-19 an application exists only once a resume is scored; the resume list shows the
    # resume without one.
    campaign = await make_campaign(db_session)
    resume = await make_resume(db_session, campaign, status="processing", raw_text="text")

    applications = (await client.get(f"{API}/campaigns/{campaign.id}/applications")).json()
    resumes = (await client.get(f"{API}/campaigns/{campaign.id}/resumes")).json()

    assert (applications["items"], applications["total"]) == ([], 0)
    assert [(r["id"], r["application_id"]) for r in resumes["items"]] == [(str(resume.id), None)]
