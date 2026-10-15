"""``hiring.service.upsert_application`` (E0-16): one application per (campaign, person), the
newer resume's scoring wins, the recruiter's fields are never touched.

Runs on a synchronous ``Session`` (the pipeline's) inside an outer transaction that is rolled
back after each test.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.modules.ai.schemas import ScoringResult
from app.modules.hiring import service
from app.modules.hiring.models import Application, Campaign

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
LOW = ScoringResult(
    score=40.0,
    score_breakdown={"skills_match": 10},
    category="weak_match",
    summary="Older resume",
    strengths=["Python"],
    weaknesses=["No Go"],
    missing_skills=["Go"],
    recommendation="Not recommended",
)
HIGH = ScoringResult(
    score=85.0,
    score_breakdown={"skills_match": 40},
    category="strong_match",
    summary="Newer resume",
    strengths=["Python", "Go"],
    weaknesses=[],
    missing_skills=[],
    recommendation="Proceed to technical interview",
)


@pytest.fixture
def db() -> Iterator[Session]:
    from app.platform.config import settings

    engine = create_engine(settings.SYNC_DATABASE_URL, poolclass=NullPool)
    with engine.connect() as conn:
        outer = conn.begin()
        session = Session(bind=conn, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            outer.rollback()
    engine.dispose()


@pytest.fixture
def campaign(db: Session) -> Campaign:
    row = Campaign(title="Backend", role="Backend Engineer", required_skills=["Python", "Go"])
    db.add(row)
    db.commit()  # (releases a savepoint only; the outer transaction is rolled back)
    return row


PERSON = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
# Resume ids in a known order, for the tie-break.
SMALL_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
BIG_ID = uuid.UUID("ffffffff-0000-0000-0000-000000000001")


def upsert(
    db: Session,
    campaign: Campaign,
    resume_id: uuid.UUID,
    uploaded_at: datetime,
    scoring: ScoringResult,
    application_id: uuid.UUID | None = None,
) -> Application:
    return service.upsert_application(
        db,
        campaign_id=campaign.id,
        person_id=PERSON,
        resume_id=resume_id,
        resume_uploaded_at=uploaded_at,
        scoring=scoring,
        application_id=application_id,
    )


def count(db: Session) -> int:
    return db.execute(select(func.count()).select_from(Application)).scalar_one()


def scoring_of(app: Application) -> ScoringResult:
    return ScoringResult(
        score=app.score or 0.0,
        score_breakdown=app.score_breakdown,
        category=app.category or "",
        summary=app.summary or "",
        strengths=app.strengths,
        weaknesses=app.weaknesses,
        missing_skills=app.missing_skills,
        recommendation=app.recommendation or "",
    )


def test_creates_the_application_with_the_scoring(db: Session, campaign: Campaign) -> None:
    resume_id = uuid.uuid4()

    app = upsert(db, campaign, resume_id, T0, HIGH)

    assert count(db) == 1
    assert (app.campaign_id, app.person_id, app.resume_id, app.resume_uploaded_at) == (
        campaign.id,
        PERSON,
        resume_id,
        T0,
    )
    assert scoring_of(app) == HIGH
    assert (app.pipeline_stage, app.notes) == (None, None)


def test_a_newer_resume_replaces_the_scoring_and_keeps_the_id(
    db: Session, campaign: Campaign
) -> None:
    first = upsert(db, campaign, uuid.uuid4(), T0, LOW)
    first_id = first.id
    newer = uuid.uuid4()

    app = upsert(db, campaign, newer, T0 + timedelta(days=1), HIGH)

    assert count(db) == 1
    assert app.id == first_id
    assert (app.resume_id, app.resume_uploaded_at) == (newer, T0 + timedelta(days=1))
    assert scoring_of(app) == HIGH


def test_an_older_resume_never_overwrites_a_newer_one(db: Session, campaign: Campaign) -> None:
    newer = uuid.uuid4()
    upsert(db, campaign, newer, T0 + timedelta(days=1), HIGH)

    app = upsert(db, campaign, uuid.uuid4(), T0, LOW)

    assert count(db) == 1
    assert (app.resume_id, app.resume_uploaded_at) == (newer, T0 + timedelta(days=1))
    assert scoring_of(app) == HIGH


@pytest.mark.parametrize("big_first", [False, True])
def test_a_tie_on_upload_time_goes_to_the_larger_resume_id(
    db: Session, campaign: Campaign, big_first: bool
) -> None:
    calls = [(BIG_ID, HIGH), (SMALL_ID, LOW)] if big_first else [(SMALL_ID, LOW), (BIG_ID, HIGH)]

    for resume_id, scoring in calls:
        app = upsert(db, campaign, resume_id, T0, scoring)

    assert (app.resume_id, scoring_of(app)) == (BIG_ID, HIGH)


def test_the_same_resume_again_replaces_the_scoring(db: Session, campaign: Campaign) -> None:
    # A retried pipeline rescored the same resume: the application follows the legacy candidate.
    resume_id = uuid.uuid4()
    upsert(db, campaign, resume_id, T0, LOW)

    app = upsert(db, campaign, resume_id, T0, HIGH)

    assert count(db) == 1
    assert scoring_of(app) == HIGH


def test_recruiter_fields_are_never_overwritten(db: Session, campaign: Campaign) -> None:
    app = upsert(db, campaign, uuid.uuid4(), T0, LOW)
    app.pipeline_stage = "technical"
    app.notes = "Strong system design answers"
    db.flush()

    app = upsert(db, campaign, uuid.uuid4(), T0 + timedelta(days=1), HIGH)

    assert scoring_of(app) == HIGH
    assert (app.pipeline_stage, app.notes) == ("technical", "Strong system design answers")


def test_application_id_is_used_for_a_new_row_only(db: Session, campaign: Campaign) -> None:
    legacy_candidate_id = uuid.uuid4()

    app = upsert(db, campaign, uuid.uuid4(), T0, LOW, application_id=legacy_candidate_id)
    assert app.id == legacy_candidate_id

    other_candidate_id = uuid.uuid4()
    app = upsert(
        db, campaign, uuid.uuid4(), T0 + timedelta(days=1), HIGH, application_id=other_candidate_id
    )
    assert app.id == legacy_candidate_id  # an existing application keeps its id
    assert scoring_of(app) == HIGH
    assert db.get(Application, other_candidate_id) is None


def test_an_application_without_an_upload_time_counts_as_older(
    db: Session, campaign: Campaign
) -> None:
    # A row written before resume_uploaded_at existed (NULL).
    db.add(
        Application(
            campaign_id=campaign.id,
            person_id=PERSON,
            resume_id=BIG_ID,
            score=LOW.score,
            category=LOW.category,
        )
    )
    db.flush()

    app = upsert(db, campaign, SMALL_ID, T0, HIGH)

    assert (app.resume_id, app.resume_uploaded_at, app.score) == (SMALL_ID, T0, HIGH.score)


def test_applications_are_per_campaign_and_person(db: Session, campaign: Campaign) -> None:
    other = Campaign(title="Platform", role="SRE", required_skills=[])
    db.add(other)
    db.flush()

    upsert(db, campaign, uuid.uuid4(), T0, LOW)
    service.upsert_application(
        db,
        campaign_id=other.id,
        person_id=PERSON,
        resume_id=uuid.uuid4(),
        resume_uploaded_at=T0,
        scoring=HIGH,
    )
    service.upsert_application(
        db,
        campaign_id=campaign.id,
        person_id=uuid.uuid4(),
        resume_id=uuid.uuid4(),
        resume_uploaded_at=T0,
        scoring=HIGH,
    )

    assert count(db) == 3


def test_upload_time_must_be_timezone_aware(db: Session, campaign: Campaign) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        upsert(db, campaign, uuid.uuid4(), datetime(2026, 10, 1, 9, 0), LOW)


def test_does_not_commit(db: Session, campaign: Campaign) -> None:
    upsert(db, campaign, uuid.uuid4(), T0, LOW)

    db.rollback()

    assert count(db) == 0
