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
from datetime import datetime
from typing import Any, cast

from sqlalchemy import (
    ColumnElement,
    Row,
    Table,
    and_,
    any_,
    case,
    func,
    or_,
    select,
    tuple_,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.modules.hiring.models import Application, Campaign, Client, LegacyCandidateMap
from app.platform import backfill
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


# ── /api/v2 (E1-02) ───────────────────────────────────────────
async def find_campaigns(
    db: AsyncSession,
    filters: Sequence[ColumnElement[bool]],
    *,
    offset: int,
    limit: int,
) -> tuple[Sequence[Campaign], int]:
    """A page of the campaigns matching ``filters``, newest first (ties by id), and how many
    match in all (two queries)."""
    total = await db.scalar(select(func.count(Campaign.id)).where(*filters))
    result = await db.execute(
        select(Campaign)
        .where(*filters)
        .order_by(Campaign.created_at.desc(), Campaign.id)
        .offset(offset)
        .limit(limit)
    )
    return result.scalars().all(), total or 0


async def recently_updated_campaigns(
    db: AsyncSession, status: str, limit: int
) -> Sequence[Campaign]:
    result = await db.execute(
        select(Campaign)
        .where(Campaign.status == status)
        .order_by(Campaign.updated_at.desc(), Campaign.id)
        .limit(limit)
    )
    return result.scalars().all()


async def campaigns_by_id(db: AsyncSession, ids: Sequence[uuid.UUID]) -> Sequence[Campaign]:
    if not ids:
        return []
    result = await db.execute(select(Campaign).where(Campaign.id == any_(uuid_array(ids))))
    return result.scalars().all()


async def application_tallies(
    db: AsyncSession, campaign_ids: Sequence[uuid.UUID], strong_category: str
) -> dict[uuid.UUID, tuple[int, int, datetime | None]]:
    """``campaign_id -> (applications, strong matches, latest updated_at)`` for the campaigns
    among ``campaign_ids`` that have applications (one grouped query)."""
    if not campaign_ids:
        return {}
    result = await db.execute(
        select(
            Application.campaign_id,
            func.count(Application.id),
            func.count(Application.id).filter(Application.category == strong_category),
            func.max(Application.updated_at),
        )
        .where(Application.campaign_id == any_(uuid_array(campaign_ids)))
        .group_by(Application.campaign_id)
    )
    return {cid: (count, strong, latest) for cid, count, strong, latest in result.tuples()}


async def category_score_rows(
    db: AsyncSession, campaign_id: uuid.UUID | None = None
) -> list[tuple[str | None, int, int, float | None]]:
    """``(category, applications, scored applications, sum of scores)`` per category, for one
    campaign or (``None``) all of them."""
    query = select(
        Application.category,
        func.count(Application.id),
        func.count(Application.score),
        func.sum(Application.score),
    )
    if campaign_id is not None:
        query = query.where(Application.campaign_id == campaign_id)
    result = await db.execute(query.group_by(Application.category))
    return [(category, n, scored, total) for category, n, scored, total in result.tuples()]


async def stage_counts(
    db: AsyncSession, campaign_id: uuid.UUID | None = None
) -> dict[str | None, int]:
    query = select(Application.pipeline_stage, func.count(Application.id))
    if campaign_id is not None:
        query = query.where(Application.campaign_id == campaign_id)
    result = await db.execute(query.group_by(Application.pipeline_stage))
    return {stage: count for stage, count in result.tuples()}


async def score_buckets(db: AsyncSession, campaign_id: uuid.UUID) -> dict[int, int]:
    """``bucket -> applications`` for the scored applications, bucket ``floor(score / 10)``
    capped to 0..9 (a score of 100 is in bucket 9)."""
    bucket = func.least(func.greatest(func.floor(Application.score / 10), 0), 9).label("bucket")
    result = await db.execute(
        select(bucket, func.count(Application.id))
        .where(Application.campaign_id == campaign_id, Application.score.is_not(None))
        .group_by(bucket)
    )
    return {int(b): count for b, count in result.tuples()}


async def count_where(db: AsyncSession, filters: Sequence[ColumnElement[bool]]) -> int:
    total = await db.scalar(select(func.count(Application.id)).where(and_(*filters)))
    return total or 0


async def page_of_applications(
    db: AsyncSession,
    filters: Sequence[ColumnElement[bool]],
    order: Sequence[ColumnElement[Any]],
    *,
    offset: int | None = None,
    limit: int | None = None,
) -> Sequence[Application]:
    query = select(Application).where(and_(*filters)).order_by(*order)
    if offset is not None:
        query = query.offset(offset)
    if limit is not None:
        query = query.limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


async def application_resume_pairs(
    db: AsyncSession, filters: Sequence[ColumnElement[bool]]
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """``(id, resume_id)`` of every application matching ``filters`` (unordered)."""
    result = await db.execute(select(Application.id, Application.resume_id).where(and_(*filters)))
    return [(application_id, resume_id) for application_id, resume_id in result.tuples()]


async def applications_by_id(db: AsyncSession, ids: Sequence[uuid.UUID]) -> Sequence[Application]:
    """The applications with these ids (unordered; unknown ids are left out)."""
    if not ids:
        return []
    result = await db.execute(select(Application).where(Application.id == any_(uuid_array(ids))))
    return result.scalars().all()


async def get_application_by_id(db: AsyncSession, application_id: uuid.UUID) -> Application | None:
    """The application with this id; unlike :func:`get_application`, a merged legacy candidate's
    id is not redirected (v2 clients only ever hold application ids)."""
    return await db.get(Application, application_id)


async def lock_campaign_applications(
    db: AsyncSession, campaign_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> Sequence[Application]:
    """The campaign's applications among ``ids`` (own ids only), locked in id order (a fixed
    order, so two bulk requests over overlapping applications cannot deadlock)."""
    if not ids:
        return []
    result = await db.execute(
        select(Application)
        .where(Application.campaign_id == campaign_id, Application.id == any_(uuid_array(ids)))
        .order_by(Application.id)
        .with_for_update()
    )
    return result.scalars().all()


async def applications_of_person(
    db: AsyncSession, person_id: uuid.UUID
) -> list[tuple[Application, Campaign]]:
    """The Person's applications with their campaigns, newest application first (ties by id)."""
    result = await db.execute(
        select(Application, Campaign)
        .join(Campaign, Campaign.id == Application.campaign_id)
        .where(Application.person_id == person_id)
        .order_by(Application.created_at.desc(), Application.id)
    )
    return [(application, campaign) for application, campaign in result.tuples()]


async def person_activity(
    db: AsyncSession, person_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, tuple[int, datetime | None]]:
    """``person_id -> (applications, latest resume upload)`` for the people among ``person_ids``
    that have applications. An application whose upload time is unknown counts with its
    ``created_at``."""
    if not person_ids:
        return {}
    result = await db.execute(
        select(
            Application.person_id,
            func.count(Application.id),
            func.max(func.coalesce(Application.resume_uploaded_at, Application.created_at)),
        )
        .where(Application.person_id == any_(uuid_array(person_ids)))
        .group_by(Application.person_id)
    )
    return {pid: (count, latest) for pid, count, latest in result.tuples()}


async def recent_applications_in_category(
    db: AsyncSession, category: str, limit: int
) -> list[tuple[Application, Campaign]]:
    """The newest applications in ``category`` (ties by id), with their campaigns."""
    result = await db.execute(
        select(Application, Campaign)
        .join(Campaign, Campaign.id == Application.campaign_id)
        .where(Application.category == category)
        .order_by(Application.created_at.desc(), Application.id)
        .limit(limit)
    )
    return [(application, campaign) for application, campaign in result.tuples()]


async def application_ids_of(
    db: AsyncSession, owners: Sequence[tuple[uuid.UUID, uuid.UUID]]
) -> dict[tuple[uuid.UUID, uuid.UUID], uuid.UUID]:
    """``(campaign_id, person_id) -> application id`` for the pairs that have an application."""
    if not owners:
        return {}
    result = await db.execute(
        select(Application.campaign_id, Application.person_id, Application.id).where(
            tuple_(Application.campaign_id, Application.person_id).in_(list(owners))
        )
    )
    return {(cid, pid): application_id for cid, pid, application_id in result.tuples()}


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

    ``values`` holds ``id``, ``agency_id``, ``campaign_id``, ``person_id`` and every column in
    :data:`APPLICATION_PIPELINE_COLUMNS`. On a conflict the pipeline columns are replaced only
    when the incoming resume is not older than the stored one, comparing ``(resume_uploaded_at,
    resume_id)`` as a tuple; a stored row whose upload time is unknown (``NULL``) always counts
    as older. The row's ``id``, ``agency_id``, ``created_at`` and recruiter columns never change
    (a row from before E2-01 keeps ``agency_id`` NULL until the E2-02 backfill). Returns the row
    as stored after the statement, whether or not it changed.
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
    """``(id, campaign_id, person_id, resume_id, agency_id)`` of every application."""
    return db.execute(
        select(
            Application.id,
            Application.campaign_id,
            Application.person_id,
            Application.resume_id,
            Application.agency_id,
        )
    ).all()


def campaign_tenancy_sync(db: Session) -> Sequence[Row[Any]]:
    """``(id, agency_id, client_id)`` of every campaign."""
    return db.execute(select(Campaign.id, Campaign.agency_id, Campaign.client_id)).all()


def client_agencies_sync(db: Session) -> dict[uuid.UUID, uuid.UUID]:
    """``client_id -> agency_id`` of every client."""
    rows = db.execute(select(Client.id, Client.agency_id)).tuples()
    return {client_id: agency_id for client_id, agency_id in rows}


# ── Tenancy backfill (sync, ``app.modules.ingestion.backfill_tenancy``, E2-02) ──
_TENANT_TABLES = {
    "campaigns": cast(Table, Campaign.__table__),
    "applications": cast(Table, Application.__table__),
}
# hiring's tenant tables, in the order the backfill fills them.
TENANCY_TABLES = tuple(_TENANT_TABLES)


def _missing(table: str) -> ColumnElement[bool]:
    t = _TENANT_TABLES[table]
    if table == "campaigns":
        return or_(t.c.agency_id.is_(None), t.c.client_id.is_(None))
    return t.c.agency_id.is_(None)


def fill_tenancy_batch_sync(
    db: Session,
    table: str,
    *,
    agency_id: uuid.UUID,
    client_id: uuid.UUID,
    after: uuid.UUID | None,
    limit: int,
) -> list[uuid.UUID]:
    """Give the next ``limit`` rows of ``table`` without an agency (campaigns: or without a
    client) the agency ``agency_id``; campaigns of that agency also get the client
    ``client_id``. A campaign of another agency is never given this client (it would belong to
    the wrong agency); it stays without one, for the verification to report. Returns the keys
    updated; the caller commits."""
    t = _TENANT_TABLES[table]
    values: dict[str, Any] = {"agency_id": func.coalesce(t.c.agency_id, agency_id)}
    if table == "campaigns":
        values["client_id"] = case(
            (
                and_(t.c.client_id.is_(None), func.coalesce(t.c.agency_id, agency_id) == agency_id),
                client_id,
            ),
            else_=t.c.client_id,
        )
    return backfill.update_next_batch(
        db, t, missing=_missing(table), values=values, after=after, limit=limit
    )


def count_tenancy_gaps_sync(db: Session, table: str) -> int:
    """Rows of ``table`` the backfill has still to fill."""
    return backfill.count_missing(db, _TENANT_TABLES[table], _missing(table))


def agency_census_sync(db: Session) -> dict[str, dict[uuid.UUID | None, int]]:
    """``table -> {agency_id: rows}`` for hiring's tenant tables (``None``: no agency yet)."""
    return {table: backfill.agency_census(db, _TENANT_TABLES[table]) for table in TENANCY_TABLES}
