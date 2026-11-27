"""Operator commands that span agencies connect as the owner role (EPIC-02 spec §6.2, §6.3).

``reset_stuck`` resets the stuck resumes of every agency and re-queues each with its own row's
``agency_id``: as the owner (``app.platform.maintenance``), whose ``maintenance`` policy covers
every row. As the application role the same unbound session would see, and reset, nothing.

On a throwaway database (``tests/postgres.py``): the rows must be committed for the owner's
connection to see them. Its engine is the owner's; the app role's engine is derived from it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.modules.hiring.models import Campaign, Client
from app.modules.identity.models import Agency
from app.modules.ingestion import repository, reset_stuck, tasks
from app.modules.ingestion.models import Resume
from app.platform import maintenance
from app.platform.tenancy import DEFAULT_AGENCY_ID, DEFAULT_CLIENT_ID
from tests import postgres
from tests.fakes import CeleryRecorder

OTHER = uuid.UUID("00000000-0000-4000-8000-0000000000b4")
OTHER_CLIENT = uuid.UUID("00000000-0000-4000-8000-0000000000c4")


@pytest.fixture(scope="module")
def owner_engine() -> Iterator[Engine]:
    with postgres.throwaway_database("e2_04_ops") as engine:
        yield engine


@pytest.fixture(scope="module")
def app_engine(owner_engine: Engine) -> Iterator[Engine]:
    engine = create_engine(postgres.as_role(owner_engine, postgres.APP_ROLE))
    try:
        yield engine
    finally:
        engine.dispose()


def _stuck_resumes(owner_engine: Engine) -> dict[uuid.UUID, uuid.UUID]:
    """A failed resume in the default agency and a processing one in another (resume id ->
    agency id), written as the owner."""
    with owner_engine.begin() as conn:
        conn.execute(text("TRUNCATE campaigns, resumes CASCADE"))
    with Session(owner_engine) as db:
        if db.get(Agency, OTHER) is None:
            db.add(Agency(id=OTHER, name="Other", slug="other-ops"))
            db.flush()
            db.add(Client(id=OTHER_CLIENT, agency_id=OTHER, name="Other client"))
        rows = {}
        for agency_id, client_id, status in (
            (DEFAULT_AGENCY_ID, DEFAULT_CLIENT_ID, "failed"),
            (OTHER, OTHER_CLIENT, "processing"),
        ):
            campaign = Campaign(
                title="Ops",
                role="Ops",
                required_skills=[],
                agency_id=agency_id,
                client_id=client_id,
            )
            db.add(campaign)
            db.flush()
            resume = Resume(
                agency_id=agency_id,
                campaign_id=campaign.id,
                original_filename="cv.pdf",
                storage_path=f"agencies/{agency_id}/campaigns/{campaign.id}/resumes/x.pdf",
                file_size_bytes=1,
                status=status,
            )
            db.add(resume)
            db.flush()
            rows[resume.id] = agency_id
        db.commit()
    return rows


def test_reset_stuck_requeues_each_resume_of_every_agency_with_its_own_agency(
    owner_engine: Engine, celery_calls: CeleryRecorder
) -> None:
    stuck = _stuck_resumes(owner_engine)

    reset_stuck.main(session_factory=sessionmaker(bind=owner_engine))

    queued = {c.args[0]: c.kwargs for c in celery_calls.for_task(tasks.EXTRACT_TASK_NAME)}
    assert queued == {str(rid): {"agency_id": str(agency)} for rid, agency in stuck.items()}
    with Session(owner_engine) as db:
        assert set(db.scalars(select(Resume.status))) == {"pending"}


def test_as_the_app_role_an_unbound_session_resets_nothing(
    owner_engine: Engine, app_engine: Engine
) -> None:
    _stuck_resumes(owner_engine)
    with Session(app_engine) as db:
        assert repository.reset_stuck_sync(db) == []
        db.commit()
    with Session(owner_engine) as db:
        assert set(db.scalars(select(Resume.status))) == {"failed", "processing"}


def test_operator_sessions_are_the_owner_role() -> None:
    with maintenance.owner_session() as db:
        assert db.scalar(text("SELECT current_user")) == "hireflow_owner"
