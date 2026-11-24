"""E2-01: every insert path writes the tenant key (EPIC-02 spec §7.1 phase 1).

Until tenant context exists (E2-03) that is the default agency
(``app.platform.tenancy.DEFAULT_AGENCY_ID``), and a new campaign is run for the default client.
One test per insert path: campaigns (v1 and v2), resumes (the upload), the pipeline's writes
(People, identifiers, conflicts, ``person_resumes``, applications) and the seed. When E2-03 takes
the agency from the session, these become "writes the bound agency". Since the E2-02 contract the
columns are ``NOT NULL``, so a missing insert path fails at once as well.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.devtools import seed
from app.modules.ai.schemas import ScoringResult
from app.modules.hiring import service as hiring_service
from app.modules.hiring.models import Application, Campaign
from app.modules.ingestion import service as ingestion_service
from app.modules.ingestion.models import Resume
from app.modules.talent import service as talent_service
from app.modules.talent.models import IdentityConflict, Person, PersonIdentifier, PersonResume
from app.modules.talent.schemas import PersonProfile
from app.platform.tenancy import DEFAULT_AGENCY_ID, DEFAULT_CLIENT_ID
from tests.factories import make_campaign
from tests.ingestion.test_service import PDF, FakeUpload

T0 = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
SCORING = ScoringResult(
    score=70.0,
    score_breakdown={"skills_match": 30},
    category="moderate_match",
    summary="",
    strengths=[],
    weaknesses=[],
    missing_skills=[],
    recommendation="Phone screen first",
)
TENANT_MODELS: tuple[type[Any], ...] = (
    Campaign,
    Application,
    Person,
    PersonIdentifier,
    IdentityConflict,
    PersonResume,
    Resume,
)


@pytest.mark.parametrize("path", ["/api/v1/campaigns", "/api/v2/campaigns"])
async def test_a_new_campaign_is_in_the_default_agency_and_client(
    client: AsyncClient, db_session: AsyncSession, path: str
) -> None:
    resp = await client.post(path, json={"title": "Backend", "role": "Backend Engineer"})
    assert resp.status_code == 201, resp.text
    assert not {"agency_id", "client_id", "created_by_user_id"} & resp.json().keys()

    campaign = await db_session.get_one(Campaign, uuid.UUID(resp.json()["id"]))
    assert (campaign.agency_id, campaign.client_id, campaign.created_by_user_id) == (
        DEFAULT_AGENCY_ID,
        DEFAULT_CLIENT_ID,
        None,  # nobody signs in yet (E2-05)
    )


async def test_an_uploaded_resume_is_in_the_default_agency(db_session: AsyncSession) -> None:
    campaign = await make_campaign(db_session)
    result = await ingestion_service.upload_resumes(
        db_session, campaign.id, [FakeUpload("cv.pdf", PDF)]
    )
    [resume_id] = result.resume_ids
    resume = await db_session.get_one(Resume, resume_id)
    assert (resume.agency_id, resume.uploaded_by_user_id) == (DEFAULT_AGENCY_ID, None)


def _resolve(
    session: Session, email: str | None, phone: str | None, resume_id: uuid.UUID
) -> uuid.UUID:
    return talent_service.resolve_person(
        session,
        agency_id=DEFAULT_AGENCY_ID,
        email=email,
        phone=phone,
        profile=PersonProfile(full_name="Asha Rao"),
        resume_id=resume_id,
        uploaded_at=T0,
        default_region="IN",
    ).person_id


def _pipeline_writes(session: Session) -> None:
    """Two People, then a resume with one's email and the other's phone (a conflict), and an
    application: every table the pipeline inserts into gets a row."""
    campaign = Campaign(
        title="C",
        role="Engineer",
        required_skills=[],
        agency_id=DEFAULT_AGENCY_ID,
        client_id=DEFAULT_CLIENT_ID,
    )
    session.add(campaign)
    session.flush()
    _resolve(session, "asha@example.com", None, uuid.uuid4())
    _resolve(session, None, "+919876543210", uuid.uuid4())
    resume_id = uuid.uuid4()
    person_id = _resolve(session, "asha@example.com", "+919876543210", resume_id)
    hiring_service.upsert_application(
        session,
        campaign_id=campaign.id,
        person_id=person_id,
        resume_id=resume_id,
        resume_uploaded_at=T0,
        scoring=SCORING,
    )
    session.flush()


async def test_the_pipeline_writes_the_default_agency(db_session: AsyncSession) -> None:
    await db_session.run_sync(_pipeline_writes)

    for model in (Application, Person, PersonIdentifier, IdentityConflict, PersonResume):
        agencies = (await db_session.scalars(select(model.agency_id))).all()
        assert agencies, model.__tablename__
        assert set(agencies) == {DEFAULT_AGENCY_ID}, model.__tablename__


async def test_an_application_keeps_its_agency_when_rescored(db_session: AsyncSession) -> None:
    """The upsert sets ``agency_id`` on insert only, so a row's agency never changes. (Before the
    E2-02 contract this also left a row written before E2-01 at ``NULL`` for the backfill; now
    the column is ``NOT NULL`` and the composite key ties it to the campaign's agency.)"""
    campaign = await make_campaign(db_session)

    def upsert(session: Session, resume_id: uuid.UUID) -> Application:
        return hiring_service.upsert_application(
            session,
            campaign_id=campaign.id,
            person_id=person_id,
            resume_id=resume_id,
            resume_uploaded_at=T0,
            scoring=SCORING,
        )

    person_id = uuid.uuid4()
    first = await db_session.run_sync(upsert, uuid.uuid4())
    again = await db_session.run_sync(upsert, uuid.uuid4())
    assert (again.id, again.agency_id) == (first.id, DEFAULT_AGENCY_ID)


async def test_the_seed_writes_the_default_agency_and_client(db_session: AsyncSession) -> None:
    await db_session.run_sync(seed.generate, people=20, campaigns=3, seed=1)

    for model in TENANT_MODELS:
        agencies = set((await db_session.scalars(select(model.agency_id))).all())
        assert agencies == {DEFAULT_AGENCY_ID}, model.__tablename__
    clients = set((await db_session.scalars(select(Campaign.client_id))).all())
    assert clients == {DEFAULT_CLIENT_ID}

    # --reset empties the seeded tables but keeps the default agency and client.
    await db_session.run_sync(seed.reset)
    for table in ("agencies", "clients"):
        assert (await db_session.execute(text(f"SELECT count(*) FROM {table}"))).scalar() == 1
