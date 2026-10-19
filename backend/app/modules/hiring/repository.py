"""hiring data access: every query against the ``campaigns``, ``applications`` and
``legacy_candidate_map`` tables.

Private to the hiring module (only ``service.py`` calls it). The async functions take the
request's ``AsyncSession``; the ones that end a unit of work commit. The ``*_sync`` functions
serve the upload pipeline's Celery tasks and the verification command, which use a synchronous
``Session``; they never commit (the caller owns the transaction).

Hiring never queries another module's tables (the ``resumes`` counts, extractions and deletes
live in ``app.modules.ingestion``).
"""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Row,
    and_,
    any_,
    func,
    or_,
    select,
    tuple_,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.modules.hiring.models import Application, Campaign, LegacyCandidateMap
from app.platform.sql import uuid_array


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
    """Delete the campaign and flush; its applications go with it in the database
    (``applications.campaign_id ON DELETE CASCADE``), and their ``legacy_candidate_map`` rows
    with them. The caller commits."""
    await db.delete(campaign)
    await db.flush()


async def campaign_status_counts(db: AsyncSession) -> dict[str, int]:
    result = await db.execute(
        select(Campaign.status, func.count(Campaign.id)).group_by(Campaign.status)
    )
    return {status: count for status, count in result.tuples()}


# ── Applications: the legacy candidate endpoints (E0-19, spec §7) ──
# Best score first, unscored last (the legacy order), then the id. Legacy ties came back in
# whatever order Postgres happened to sort them; the id makes pages of tied scores stable.
_LIST_ORDER = (Application.score.desc().nullslast(), Application.id)


async def list_applications(
    db: AsyncSession,
    filters: Sequence[ColumnElement[bool]],
    *,
    offset: int | None = None,
    limit: int | None = None,
) -> Sequence[Application]:
    """Applications matching ``filters``, best score first (unscored last, ties by id).

    ``offset``/``limit`` are applied only when given (the CSV export pages nothing).
    """
    query = select(Application).where(and_(*filters)).order_by(*_LIST_ORDER)
    if offset is not None:
        query = query.offset(offset)
    if limit is not None:
        query = query.limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


async def application_resume_ids(
    db: AsyncSession, filters: Sequence[ColumnElement[bool]]
) -> list[uuid.UUID]:
    """The ``resume_id`` of every application matching ``filters`` (unordered)."""
    result = await db.execute(select(Application.resume_id).where(and_(*filters)))
    return list(result.scalars())


async def get_application(db: AsyncSession, application_id: uuid.UUID) -> Application | None:
    """The application with this id, else the one ``legacy_candidate_map`` redirects this
    (merged legacy candidate's) id to; ``None`` if neither exists."""
    application = await db.get(Application, application_id)
    if application is not None:
        return application
    redirect = select(LegacyCandidateMap.application_id).where(
        LegacyCandidateMap.candidate_id == application_id
    )
    result = await db.execute(select(Application).where(Application.id.in_(redirect)))
    return result.scalar_one_or_none()


async def get_application_by_resume(db: AsyncSession, resume_id: uuid.UUID) -> Application | None:
    """The application whose current resume is ``resume_id``."""
    result = await db.execute(select(Application).where(Application.resume_id == resume_id))
    return result.scalars().first()


async def get_application_of_person(
    db: AsyncSession, campaign_id: uuid.UUID, person_id: uuid.UUID
) -> Application | None:
    result = await db.execute(
        select(Application).where(
            Application.campaign_id == campaign_id, Application.person_id == person_id
        )
    )
    return result.scalar_one_or_none()


async def applications_in_campaign(
    db: AsyncSession,
    campaign_id: uuid.UUID,
    ids: Sequence[uuid.UUID],
    *,
    for_update: bool = False,
) -> Sequence[Application]:
    """The campaign's applications that ``ids`` name, by their own id or by a merged legacy
    candidate's id (``legacy_candidate_map``), each once; ids of other campaigns and unknown
    ids are ignored. ``for_update`` locks them in id order (a fixed order, so two bulk requests
    over overlapping applications cannot deadlock)."""
    if not ids:
        return []
    redirected = select(LegacyCandidateMap.application_id).where(
        LegacyCandidateMap.candidate_id == any_(uuid_array(ids))
    )
    query = (
        select(Application)
        .where(
            Application.campaign_id == campaign_id,
            or_(Application.id == any_(uuid_array(ids)), Application.id.in_(redirected)),
        )
        .order_by(Application.id)
    )
    if for_update:
        query = query.with_for_update()
    result = await db.execute(query)
    return result.scalars().all()


async def application_category_stats(
    db: AsyncSession, campaign_id: uuid.UUID
) -> list[tuple[str | None, int, float | None]]:
    """One ``(category, count, average score)`` row per category in the campaign."""
    result = await db.execute(
        select(
            Application.category,
            func.count(Application.id),
            func.avg(Application.score),
        )
        .where(Application.campaign_id == campaign_id)
        .group_by(Application.category)
    )
    return [(category, count, avg) for category, count, avg in result.tuples()]


async def campaign_resume_ids(db: AsyncSession, campaign_id: uuid.UUID) -> list[uuid.UUID]:
    """The current resume of each of the campaign's applications, oldest application first."""
    result = await db.execute(
        select(Application.resume_id)
        .where(Application.campaign_id == campaign_id)
        .order_by(Application.created_at, Application.id)
    )
    return list(result.scalars())


async def count_applications(db: AsyncSession) -> int:
    result = await db.execute(select(func.count(Application.id)))
    return result.scalar_one() or 0


async def delete_application(db: AsyncSession, application: Application) -> None:
    """Mark the application for deletion; its ``legacy_candidate_map`` rows go with it in the
    database (``ON DELETE CASCADE``). The caller flushes or commits."""
    await db.delete(application)


async def flush(db: AsyncSession) -> None:
    await db.flush()


async def commit(db: AsyncSession) -> None:
    await db.commit()


async def commit_and_refresh(db: AsyncSession, row: Campaign | Application) -> None:
    await db.commit()
    await db.refresh(row)


# ── Upload pipeline (sync, Celery tasks) ──────────────────────
def get_campaign_sync(db: Session, campaign_id: uuid.UUID) -> Campaign | None:
    return db.query(Campaign).filter(Campaign.id == campaign_id).first()


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


# ── Verification (sync, ``app.modules.ingestion.verify_people_applications``) ──
def legacy_map_rows_sync(db: Session) -> dict[uuid.UUID, uuid.UUID]:
    """Every ``legacy_candidate_map`` row, ``candidate_id -> application_id``."""
    query = select(LegacyCandidateMap.candidate_id, LegacyCandidateMap.application_id)
    return {candidate_id: application_id for candidate_id, application_id in db.execute(query)}


def application_refs_sync(db: Session) -> Sequence[Row[Any]]:
    """``(id, campaign_id, person_id, resume_id)`` of every application."""
    return db.execute(
        select(
            Application.id, Application.campaign_id, Application.person_id, Application.resume_id
        )
    ).all()
