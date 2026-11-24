"""Explicit filtering (EPIC-02 spec §6.1): every repository query on a tenant table reads only the
bound agency's rows, and every insert writes it.

Rows written before E2-01 have ``agency_id IS NULL`` until the E2-02 backfill. They belong to the
default agency, so while it is bound they are listed too (the transition in
``app.platform.tenancy.tenant_filter``, removed in E2-04); another agency never sees them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.modules.ai.schemas import ScoringResult
from app.modules.hiring import service as hiring_service
from app.modules.hiring.models import Application, Campaign
from app.modules.ingestion import service as ingestion_service
from app.modules.ingestion.models import Resume
from app.modules.talent import service as talent_service
from app.modules.talent.models import Person, PersonIdentifier, PersonResume
from app.modules.talent.schemas import PersonProfile
from app.platform.tenancy import DEFAULT_AGENCY_ID, AccessScope
from tests.factories import make_campaign, make_candidate, make_resume
from tests.fakes import CeleryRecorder, InMemoryStorage
from tests.ingestion.test_service import PDF, FakeUpload
from tests.tenancy.conftest import OtherAgency, SessionFor

V1 = "/api/v1"
V2 = "/api/v2"
T0 = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)


async def _other_agencys_campaign(db: AsyncSession, other: OtherAgency) -> Application:
    """A campaign of the other agency with one processed application (and its resume and
    Person), all in that agency."""
    campaign = await make_campaign(
        db, title="Other agency's campaign", agency_id=other.agency_id, client_id=other.client_id
    )
    return await make_candidate(db, campaign, name="Other Person", email="other@example.com")


async def _ids(client: AsyncClient, path: str) -> set[str]:
    resp = await client.get(path)
    assert resp.status_code == 200, resp.text
    return {item["id"] for item in resp.json()["items"]}


# ── The default agency (every request until E2-05) ────────────
async def test_requests_never_see_another_agencys_rows(
    client: AsyncClient, db_session: AsyncSession, other_agency: OtherAgency
) -> None:
    mine = await make_candidate(db_session)
    theirs = await _other_agencys_campaign(db_session, other_agency)

    assert await _ids(client, f"{V2}/campaigns") == {str(mine.campaign_id)}
    assert await _ids(client, f"{V2}/people") == {str(mine.person_id)}
    stats = (await client.get(f"{V2}/stats")).json()
    assert (stats["campaigns"]["total"], stats["applications"]["total"], stats["people"]) == (
        1,
        1,
        1,
    )
    home = (await client.get(f"{V2}/home")).json()
    assert [c["id"] for c in home["active_campaigns"]] == [str(mine.campaign_id)]
    assert [m["id"] for m in home["recent_strong_matches"]] == [str(mine.id)]

    # Their objects are not found, by any URL, in v2 and v1; nothing of theirs changes.
    for path in (
        f"{V2}/campaigns/{theirs.campaign_id}",
        f"{V2}/campaigns/{theirs.campaign_id}/stats",
        f"{V2}/campaigns/{theirs.campaign_id}/applications",
        f"{V2}/campaigns/{theirs.campaign_id}/resumes",
        f"{V2}/applications/{theirs.id}",
        f"{V2}/people/{theirs.person_id}",
        f"{V1}/campaigns/{theirs.campaign_id}",
        f"{V1}/candidates/{theirs.id}",
        f"{V1}/resumes/{theirs.resume_id}",
        f"{V1}/resumes/{theirs.resume_id}/candidate",
    ):
        assert (await client.get(path)).status_code == 404, path
    assert (await client.delete(f"{V2}/applications/{theirs.id}")).status_code == 404
    assert (await client.delete(f"{V2}/campaigns/{theirs.campaign_id}")).status_code == 404
    resp = await client.post(
        f"{V2}/campaigns/{theirs.campaign_id}/applications/bulk",
        json={"action": "delete", "ids": [str(theirs.id)]},
    )
    assert resp.status_code == 404
    assert (await client.get(f"{V1}/campaigns/{theirs.campaign_id}/candidates")).json() == []
    assert await db_session.get(Application, theirs.id) is not None


async def test_rows_not_backfilled_yet_belong_to_the_default_agency(
    client: AsyncClient,
    db_session: AsyncSession,
    session_for: SessionFor,
    other_agency: OtherAgency,
) -> None:
    """``agency_id IS NULL`` (written before E2-01): listed, found and counted for the default
    agency until E2-02 backfills them; invisible to any other agency."""
    legacy_campaign = await make_campaign(db_session, agency_id=None, client_id=None)
    # make_candidate gives the Person and the application the resume's agency: NULL too.
    legacy = await make_candidate(
        db_session, resume=await make_resume(db_session, legacy_campaign, agency_id=None)
    )
    assert legacy.agency_id is None

    assert await _ids(client, f"{V2}/campaigns") == {str(legacy.campaign_id)}
    assert await _ids(client, f"{V2}/campaigns/{legacy.campaign_id}/applications") == {
        str(legacy.id)
    }
    assert await _ids(client, f"{V2}/people") == {str(legacy.person_id)}
    assert (await client.get(f"{V2}/applications/{legacy.id}")).status_code == 200

    other = session_for(other_agency.agency_id)
    scope = AccessScope(other_agency.agency_id)
    assert not await hiring_service.campaign_exists(other, scope, legacy.campaign_id)
    page, total = await hiring_service.list_campaigns_page(
        other, scope, hiring_service.CampaignQuery(), 0, 10, ingestion_service.resume_tallies(other)
    )
    assert (page, total) == ([], 0)
    assert await talent_service.count_people(other) == 0
    with pytest.raises(ingestion_service.NotFoundError):
        await ingestion_service.get_resume(other, scope, legacy.resume_id)


# ── Another agency's session ──────────────────────────────────
async def test_a_session_of_another_agency_reads_only_its_own_rows(
    db_session: AsyncSession, session_for: SessionFor, other_agency: OtherAgency
) -> None:
    mine = await make_candidate(db_session)
    theirs = await _other_agencys_campaign(db_session, other_agency)
    other = session_for(other_agency.agency_id)
    scope = AccessScope(other_agency.agency_id)

    page, total = await hiring_service.list_campaigns_page(
        other, scope, hiring_service.CampaignQuery(), 0, 10, ingestion_service.resume_tallies(other)
    )
    assert ([c.id for c in page], total) == ([theirs.campaign_id], 1)
    with pytest.raises(hiring_service.NotFoundError):
        await hiring_service.get_application_v2(
            other, scope, mine.id, ingestion_service.resume_profiles(other)
        )
    people, people_total = await talent_service.list_people(other, None, 0, 10)
    assert ([p.id for p in people], people_total) == ([theirs.person_id], 1)
    counts = await ingestion_service.resume_status_counts(other, scope)
    assert counts == {"done": 1}


async def test_inserts_take_the_agency_of_the_session(
    db_session: AsyncSession,
    session_for: SessionFor,
    other_agency: OtherAgency,
    celery_calls: CeleryRecorder,
    fake_storage: InMemoryStorage,
) -> None:
    campaign = await make_campaign(
        db_session, agency_id=other_agency.agency_id, client_id=other_agency.client_id
    )
    other = session_for(other_agency.agency_id)
    scope = AccessScope(other_agency.agency_id)

    result = await ingestion_service.upload_resumes_v2(
        other, scope, campaign.id, [FakeUpload("cv.pdf", PDF)]
    )
    [accepted] = result.accepted
    resume = await other.get_one(Resume, accepted.id)
    assert resume.agency_id == other_agency.agency_id
    assert resume.storage_path == (
        f"agencies/{other_agency.agency_id}/campaigns/{campaign.id}/resumes/{resume.id}.pdf"
    )
    assert list(fake_storage.objects) == [resume.storage_path]
    [call] = celery_calls.calls
    assert call.kwargs == {"agency_id": str(other_agency.agency_id)}

    def pipeline_writes(session: Session) -> uuid.UUID:
        person_id = talent_service.resolve_person(
            session,
            email="new.person@example.com",
            phone=None,
            profile=PersonProfile(full_name="New Person"),
            resume_id=resume.id,
            uploaded_at=T0,
            default_region="IN",
        ).person_id
        application = hiring_service.upsert_application(
            session,
            campaign_id=campaign.id,
            person_id=person_id,
            resume_id=resume.id,
            resume_uploaded_at=T0,
            scoring=ScoringResult(score=50.0, category="weak_match", summary="", recommendation=""),
        )
        return application.id

    application_id = await other.run_sync(pipeline_writes)
    await other.flush()
    application = await other.get_one(Application, application_id)
    rows = [
        application.agency_id,
        *(await other.scalars(select(Person.agency_id).where(Person.id == application.person_id))),
        *(await other.scalars(select(PersonIdentifier.agency_id))),
        *(await other.scalars(select(PersonResume.agency_id))),
    ]
    assert len(rows) == 4
    assert set(rows) == {other_agency.agency_id}

    # And the default agency's requests do not see any of it.
    assert not await hiring_service.campaign_exists(
        db_session, AccessScope(DEFAULT_AGENCY_ID), campaign.id
    )
    assert await db_session.get(Campaign, campaign.id) is not None  # it exists, unfiltered
