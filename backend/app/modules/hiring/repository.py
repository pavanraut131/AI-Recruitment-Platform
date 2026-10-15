"""hiring data access: every query against the ``campaigns``, ``candidates``, ``applications``
and ``legacy_candidate_map`` tables.

Private to the hiring module (only ``service.py`` calls it). The async functions take the
request's ``AsyncSession``; the ones that end a unit of work commit, exactly where the endpoints
committed before the move, so the SQL issued per request is unchanged. The ``*_sync`` functions
serve the upload pipeline's Celery tasks and the backfill / verification commands, which use a
synchronous ``Session``; they never commit (the caller owns the transaction).

Hiring never queries another module's tables (the ``resumes`` counts and deletes live in
``app.modules.ingestion``).
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, DateTime, Row, and_, func, literal, or_, select, tuple_
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.modules.hiring.models import Application, Campaign, Candidate, LegacyCandidateMap


# ── Campaigns ─────────────────────────────────────────────────
async def add_campaign(db: AsyncSession, campaign: Campaign) -> Campaign:
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)
    return campaign


async def list_campaigns(db: AsyncSession, skip: int, limit: int) -> Sequence[Campaign]:
    result = await db.execute(
        select(Campaign).order_by(Campaign.created_at.desc()).offset(skip).limit(limit)
    )
    return result.scalars().all()


async def get_campaign(db: AsyncSession, campaign_id: uuid.UUID) -> Campaign | None:
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    return result.scalar_one_or_none()


async def delete_campaign(db: AsyncSession, campaign: Campaign) -> None:
    await db.delete(campaign)
    await db.commit()


async def campaign_status_counts(db: AsyncSession) -> dict[str, int]:
    result = await db.execute(
        select(Campaign.status, func.count(Campaign.id)).group_by(Campaign.status)
    )
    return {status: count for status, count in result.tuples()}


# ── Candidates ────────────────────────────────────────────────
async def list_candidates(
    db: AsyncSession,
    filters: Sequence[ColumnElement[bool]],
    *,
    offset: int | None = None,
    limit: int | None = None,
) -> Sequence[Candidate]:
    """Candidates matching ``filters``, best score first (unscored last).

    ``offset``/``limit`` are applied only when given (the CSV export pages nothing).
    """
    query = select(Candidate).where(and_(*filters)).order_by(Candidate.score.desc().nullslast())
    if offset is not None:
        query = query.offset(offset)
    if limit is not None:
        query = query.limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


async def get_candidate(db: AsyncSession, candidate_id: uuid.UUID) -> Candidate | None:
    result = await db.execute(select(Candidate).where(Candidate.id == candidate_id))
    return result.scalar_one_or_none()


async def get_candidate_by_resume(db: AsyncSession, resume_id: uuid.UUID) -> Candidate | None:
    result = await db.execute(select(Candidate).where(Candidate.resume_id == resume_id))
    return result.scalar_one_or_none()


async def candidates_in_campaign(
    db: AsyncSession, campaign_id: uuid.UUID, candidate_ids: Sequence[uuid.UUID]
) -> Sequence[Candidate]:
    """The candidates among ``candidate_ids`` that belong to ``campaign_id`` (others are ignored)."""
    result = await db.execute(
        select(Candidate).where(
            Candidate.campaign_id == campaign_id,
            Candidate.id.in_(candidate_ids),
        )
    )
    return result.scalars().all()


async def candidate_category_stats(
    db: AsyncSession, campaign_id: uuid.UUID
) -> list[tuple[str | None, int, float | None]]:
    """One ``(category, count, average score)`` row per category in the campaign."""
    result = await db.execute(
        select(
            Candidate.category,
            func.count(Candidate.id),
            func.avg(Candidate.score),
        )
        .where(Candidate.campaign_id == campaign_id)
        .group_by(Candidate.category)
    )
    return [(category, count, avg) for category, count, avg in result.tuples()]


async def candidate_skill_lists(db: AsyncSession, campaign_id: uuid.UUID) -> list[list[str] | None]:
    """Each candidate's ``skills`` array in the campaign."""
    result = await db.execute(select(Candidate.skills).where(Candidate.campaign_id == campaign_id))
    return [skills for (skills,) in result.tuples()]


async def count_candidates(db: AsyncSession) -> int:
    result = await db.execute(select(func.count(Candidate.id)))
    return result.scalar_one() or 0


async def delete_candidate(db: AsyncSession, candidate: Candidate) -> None:
    """Mark ``candidate`` for deletion; the caller flushes or commits."""
    await db.delete(candidate)


async def flush(db: AsyncSession) -> None:
    await db.flush()


async def commit(db: AsyncSession) -> None:
    await db.commit()


async def commit_and_refresh(db: AsyncSession, row: Campaign | Candidate) -> None:
    await db.commit()
    await db.refresh(row)


# ── Upload pipeline (sync, Celery tasks) ──────────────────────
def get_campaign_sync(db: Session, campaign_id: uuid.UUID) -> Campaign | None:
    return db.query(Campaign).filter(Campaign.id == campaign_id).first()


def get_candidate_by_resume_sync(db: Session, resume_id: uuid.UUID) -> Candidate | None:
    return db.query(Candidate).filter(Candidate.resume_id == resume_id).first()


def add_sync(db: Session, row: Candidate) -> None:
    db.add(row)


# Columns the pipeline owns on an application: the resume it holds and that resume's scoring.
# The recruiter's columns (pipeline_stage, notes) are deliberately not here.
APPLICATION_PIPELINE_COLUMNS = (
    "resume_id",
    "resume_uploaded_at",
    "score",
    "score_breakdown",
    "category",
    "summary",
    "strengths",
    "weaknesses",
    "missing_skills",
    "recommendation",
)


def upsert_application_sync(db: Session, values: dict[str, Any]) -> Application:
    """``INSERT … ON CONFLICT (campaign_id, person_id) DO UPDATE`` one application.

    ``values`` holds ``id``, ``campaign_id``, ``person_id`` and every column in
    :data:`APPLICATION_PIPELINE_COLUMNS`. On a conflict the pipeline columns are replaced only
    when the incoming resume is not older than the stored one, comparing ``(resume_uploaded_at,
    resume_id)`` as a tuple; a stored row whose upload time is unknown (``NULL``) always counts
    as older. The row's ``id``, ``created_at`` and recruiter columns never change. Returns the
    row as stored after the statement, whether or not it changed.
    """
    stmt = pg_insert(Application).values(**values)
    excluded = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        constraint="uq_applications_campaign_id_person_id",
        set_={
            **{name: excluded[name] for name in APPLICATION_PIPELINE_COLUMNS},
            "updated_at": func.now(),
        },
        where=or_(
            Application.resume_uploaded_at.is_(None),
            tuple_(excluded.resume_uploaded_at, excluded.resume_id)
            >= tuple_(Application.resume_uploaded_at, Application.resume_id),
        ),
    )
    db.execute(stmt)
    return db.execute(
        select(Application)
        .where(
            Application.campaign_id == values["campaign_id"],
            Application.person_id == values["person_id"],
        )
        .execution_options(populate_existing=True)
    ).scalar_one()


# ── Legacy candidates -> applications (backfill and verification, sync) ──
# What the backfill reads of a candidate: identity, contact details, profile, scoring and the
# recruiter's fields (not ``raw_text``).
LEGACY_CANDIDATE_COLUMNS = (
    Candidate.id,
    Candidate.campaign_id,
    Candidate.resume_id,
    Candidate.created_at,
    Candidate.name,
    Candidate.email,
    Candidate.phone,
    Candidate.github_url,
    Candidate.linkedin_url,
    Candidate.skills,
    Candidate.education,
    Candidate.work_experience,
    Candidate.certifications,
    Candidate.projects,
    Candidate.years_of_experience,
    Candidate.score,
    Candidate.score_breakdown,
    Candidate.category,
    Candidate.summary,
    Candidate.strengths,
    Candidate.weaknesses,
    Candidate.missing_skills,
    Candidate.recommendation,
    Candidate.pipeline_stage,
    Candidate.notes,
)


def legacy_candidates_after_sync(
    db: Session, after: tuple[datetime, uuid.UUID] | None, limit: int
) -> Sequence[Row[Any]]:
    """The next ``limit`` candidates in ``(created_at, id)`` order, strictly after ``after``.

    Keyset pagination: a row-value comparison ``(created_at, id) > (:t, :id)`` instead of
    ``OFFSET``, so rows inserted or deleted behind the cursor never shift a page.
    """
    query = select(*LEGACY_CANDIDATE_COLUMNS).order_by(Candidate.created_at, Candidate.id)
    if after is not None:
        created_at, candidate_id = after
        query = query.where(
            tuple_(Candidate.created_at, Candidate.id)
            > tuple_(
                literal(created_at, DateTime(timezone=True)),
                literal(candidate_id, UUID(as_uuid=True)),
            )
        )
    return db.execute(query.limit(limit)).all()


def get_candidate_id_by_resume_sync(db: Session, resume_id: uuid.UUID) -> uuid.UUID | None:
    return db.execute(
        select(Candidate.id).where(Candidate.resume_id == resume_id)
    ).scalar_one_or_none()


def existing_application_ids_sync(db: Session, ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
    if not ids:
        return set()
    return set(db.execute(select(Application.id).where(Application.id.in_(ids))).scalars())


def legacy_map_rows_sync(
    db: Session, candidate_ids: Sequence[uuid.UUID] | None = None
) -> dict[uuid.UUID, uuid.UUID]:
    """``candidate_id -> application_id`` rows of ``legacy_candidate_map`` (all, or these)."""
    query = select(LegacyCandidateMap.candidate_id, LegacyCandidateMap.application_id)
    if candidate_ids is not None:
        if not candidate_ids:
            return {}
        query = query.where(LegacyCandidateMap.candidate_id.in_(candidate_ids))
    return {candidate_id: application_id for candidate_id, application_id in db.execute(query)}


def insert_legacy_map_row_sync(
    db: Session, candidate_id: uuid.UUID, application_id: uuid.UUID
) -> bool:
    """``INSERT … ON CONFLICT (candidate_id) DO NOTHING``; whether a row was inserted."""
    result = db.execute(
        pg_insert(LegacyCandidateMap)
        .values(candidate_id=candidate_id, application_id=application_id)
        .on_conflict_do_nothing(index_elements=["candidate_id"])
        .returning(LegacyCandidateMap.candidate_id)
    )
    return result.scalar_one_or_none() is not None


def candidates_linked_to_sync(db: Session, application_id: uuid.UUID) -> Sequence[Row[Any]]:
    """``(id, resume_id, pipeline_stage, notes)`` of every candidate that *is* this application:
    the one whose id it reuses and those ``legacy_candidate_map`` redirects to it."""
    redirected = select(LegacyCandidateMap.candidate_id).where(
        LegacyCandidateMap.application_id == application_id
    )
    return db.execute(
        select(Candidate.id, Candidate.resume_id, Candidate.pipeline_stage, Candidate.notes).where(
            or_(Candidate.id == application_id, Candidate.id.in_(redirected))
        )
    ).all()


def get_application_sync(db: Session, application_id: uuid.UUID) -> Application | None:
    return db.get(Application, application_id, populate_existing=True)


def candidate_refs_sync(db: Session) -> Sequence[Row[Any]]:
    """``(id, campaign_id, resume_id)`` of every candidate."""
    return db.execute(select(Candidate.id, Candidate.campaign_id, Candidate.resume_id)).all()


def application_refs_sync(db: Session) -> Sequence[Row[Any]]:
    """``(id, campaign_id, person_id, resume_id)`` of every application."""
    return db.execute(
        select(
            Application.id, Application.campaign_id, Application.person_id, Application.resume_id
        )
    ).all()
