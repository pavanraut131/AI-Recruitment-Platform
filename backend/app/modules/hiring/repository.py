"""hiring data access: every query against the ``clients``, ``campaigns`` and ``applications``
tables.

Private to the hiring module (only ``service.py`` calls it). The async functions take the
request's ``AsyncSession``; the ones that end a unit of work commit. The ``*_sync`` functions
serve the upload pipeline's Celery tasks and the verification command, which use a synchronous
``Session``; they never commit (the caller owns the transaction).

Hiring never queries another module's tables (the ``resumes`` counts, extractions and deletes
live in ``app.modules.ingestion``).

**Tenancy (E2-03).** Every query on ``campaigns`` and ``applications`` is filtered to the agency
the session is bound to (:func:`_campaigns_of` / :func:`_applications_of`, through
``app.platform.tenancy.tenant_filter``), and raises ``TenantNotBound`` on an unbound session. The
caller sets ``agency_id`` on what it inserts (``service._new_campaign``,
``service.upsert_application``) from the same binding. The verification reads at the end read
**every agency** on purpose (an operator command: an unbound session as the owner role, which
row-level security lets see every agency, ``app.platform.maintenance``).

**Client scoping (E2-06, spec §5.3).** The functions that take an ``AccessScope`` also keep to
its clients: campaigns by ``client_id`` (:func:`_campaigns_in`), applications through their
campaign (:func:`_applications_in`, a subquery on ``campaigns``), clients by id. With every
client in scope (owners, admins, unlimited memberships) the clause is the agency filter alone, so
their queries are unchanged.
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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.modules.hiring.models import Application, Campaign, Client
from app.platform import backfill
from app.platform.sql import LIKE_ESCAPE, escape_like, uuid_array
from app.platform.tenancy import AccessScope, AnySession, tenant_filter


def _campaigns_of(db: AnySession) -> ColumnElement[bool]:
    """Campaigns of the session's agency."""
    return tenant_filter(db, Campaign.agency_id)


def _applications_of(db: AnySession) -> ColumnElement[bool]:
    """Applications of the session's agency."""
    return tenant_filter(db, Application.agency_id)


def _clients_in(db: AnySession, scope: AccessScope) -> ColumnElement[bool]:
    """Clients of the session's agency in ``scope``."""
    clause = tenant_filter(db, Client.agency_id)
    if scope.client_ids is None:
        return clause
    return and_(clause, Client.id == any_(uuid_array(scope.client_ids)))


def _campaigns_in(db: AnySession, scope: AccessScope) -> ColumnElement[bool]:
    """Campaigns of the session's agency run for a client in ``scope``."""
    clause = _campaigns_of(db)
    if scope.client_ids is None:
        return clause
    return and_(clause, Campaign.client_id == any_(uuid_array(scope.client_ids)))


def _applications_in(db: AnySession, scope: AccessScope) -> ColumnElement[bool]:
    """Applications of the session's agency to a campaign in ``scope``."""
    clause = _applications_of(db)
    if scope.client_ids is None:
        return clause
    visible = select(Campaign.id).where(_campaigns_in(db, scope))
    return and_(clause, Application.campaign_id.in_(visible))


# ── Clients ───────────────────────────────────────────────────
async def find_clients(
    db: AsyncSession,
    scope: AccessScope,
    statuses: Sequence[str],
    q: str | None,
    *,
    offset: int,
    limit: int,
) -> tuple[Sequence[Client], int]:
    """A page of the clients in ``scope`` (any of ``statuses``; ``q`` contained in the name,
    case-insensitive, wildcards literal), by name (ignoring case, ties by id), and how many
    match: two queries."""
    clauses = [_clients_in(db, scope)]
    if statuses:
        clauses.append(Client.status.in_(list(statuses)))
    if q:
        clauses.append(Client.name.ilike(f"%{escape_like(q)}%", escape=LIKE_ESCAPE))
    total = await db.scalar(select(func.count(Client.id)).where(*clauses))
    result = await db.execute(
        select(Client)
        .where(*clauses)
        .order_by(func.lower(Client.name), Client.id)
        .offset(offset)
        .limit(limit)
    )
    return result.scalars().all(), total or 0


async def get_client(db: AsyncSession, scope: AccessScope, client_id: uuid.UUID) -> Client | None:
    result = await db.execute(select(Client).where(Client.id == client_id, _clients_in(db, scope)))
    return result.scalar_one_or_none()


async def client_named(
    db: AsyncSession, name: str, *, other_than: uuid.UUID | None = None
) -> Client | None:
    """The agency's client with this name, ignoring case (any status, any scope: names are
    unique per agency), other than ``other_than``."""
    query = select(Client).where(
        func.lower(Client.name) == name.lower(), tenant_filter(db, Client.agency_id)
    )
    if other_than is not None:
        query = query.where(Client.id != other_than)
    result = await db.execute(query)
    return result.scalars().first()


class DuplicateClientName(Exception):
    """``uq_clients_agency_id_lower_name``: another client of the agency has the name."""


async def flush_client(db: AsyncSession, client: Client) -> None:
    """Add (if new) and flush ``client`` in a savepoint: a name taken meanwhile (the unique
    index) raises :class:`DuplicateClientName` and leaves the transaction usable. The caller
    commits."""
    try:
        async with db.begin_nested():
            db.add(client)
            await db.flush()
    except IntegrityError as exc:
        if "uq_clients_agency_id_lower_name" in str(exc.orig):
            raise DuplicateClientName() from exc
        raise
    await db.refresh(client)


async def default_client(db: AsyncSession, scope: AccessScope) -> Client | None:
    """The oldest active client in ``scope`` (ties by id), or ``None``."""
    result = await db.execute(
        select(Client)
        .where(Client.status == "active", _clients_in(db, scope))
        .order_by(Client.created_at, Client.id)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def count_clients(db: AsyncSession) -> int:
    """Every client of the agency, in any status."""
    total = await db.scalar(
        select(func.count(Client.id)).where(tenant_filter(db, Client.agency_id))
    )
    return total or 0


async def campaign_ids_in(db: AsyncSession, scope: AccessScope) -> list[uuid.UUID]:
    """The id of every campaign in ``scope`` (unordered)."""
    result = await db.execute(select(Campaign.id).where(_campaigns_in(db, scope)))
    return list(result.scalars())


# ── Campaigns ─────────────────────────────────────────────────
async def add_campaign(db: AsyncSession, campaign: Campaign) -> Campaign:
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)
    return campaign


async def get_campaign(
    db: AsyncSession, scope: AccessScope, campaign_id: uuid.UUID
) -> Campaign | None:
    result = await db.execute(
        select(Campaign).where(Campaign.id == campaign_id, _campaigns_in(db, scope))
    )
    return result.scalar_one_or_none()


async def clients_by_ids(
    db: AsyncSession, scope: AccessScope, client_ids: Sequence[uuid.UUID]
) -> Sequence[Client]:
    """The clients in ``scope`` with these ids (any status), by name."""
    if not client_ids:
        return []
    result = await db.execute(
        select(Client)
        .where(Client.id == any_(uuid_array(client_ids)), _clients_in(db, scope))
        .order_by(func.lower(Client.name), Client.id)
    )
    return result.scalars().all()


async def delete_campaign(db: AsyncSession, campaign: Campaign) -> None:
    """Delete the campaign and flush; its applications go with it in the database
    (``applications.campaign_id ON DELETE CASCADE``). The caller commits."""
    await db.delete(campaign)
    await db.flush()


async def campaign_status_counts(db: AsyncSession, scope: AccessScope) -> dict[str, int]:
    result = await db.execute(
        select(Campaign.status, func.count(Campaign.id))
        .where(_campaigns_in(db, scope))
        .group_by(Campaign.status)
    )
    return {status: count for status, count in result.tuples()}


async def campaign_resume_ids(db: AsyncSession, campaign_id: uuid.UUID) -> list[uuid.UUID]:
    """The current resume of each of the campaign's applications, oldest application first."""
    result = await db.execute(
        select(Application.resume_id)
        .where(Application.campaign_id == campaign_id, _applications_of(db))
        .order_by(Application.created_at, Application.id)
    )
    return list(result.scalars())


async def delete_application(db: AsyncSession, application: Application) -> None:
    """Mark the application for deletion. The caller flushes or commits."""
    await db.delete(application)


async def flush(db: AsyncSession) -> None:
    await db.flush()


async def commit(db: AsyncSession) -> None:
    await db.commit()


async def commit_and_refresh(db: AsyncSession, row: Client | Campaign | Application) -> None:
    await db.commit()
    await db.refresh(row)


# ── /api/v2 (E1-02) ───────────────────────────────────────────
async def find_campaigns(
    db: AsyncSession,
    scope: AccessScope,
    filters: Sequence[ColumnElement[bool]],
    *,
    offset: int,
    limit: int,
) -> tuple[Sequence[Campaign], int]:
    """A page of the campaigns in ``scope`` matching ``filters``, newest first (ties by id), and
    how many match in all (two queries)."""
    clauses = [_campaigns_in(db, scope), *filters]
    total = await db.scalar(select(func.count(Campaign.id)).where(*clauses))
    result = await db.execute(
        select(Campaign)
        .where(*clauses)
        .order_by(Campaign.created_at.desc(), Campaign.id)
        .offset(offset)
        .limit(limit)
    )
    return result.scalars().all(), total or 0


async def recently_updated_campaigns(
    db: AsyncSession, scope: AccessScope, status: str, limit: int
) -> Sequence[Campaign]:
    result = await db.execute(
        select(Campaign)
        .where(Campaign.status == status, _campaigns_in(db, scope))
        .order_by(Campaign.updated_at.desc(), Campaign.id)
        .limit(limit)
    )
    return result.scalars().all()


async def campaigns_by_id(
    db: AsyncSession, scope: AccessScope, ids: Sequence[uuid.UUID]
) -> Sequence[Campaign]:
    if not ids:
        return []
    result = await db.execute(
        select(Campaign).where(Campaign.id == any_(uuid_array(ids)), _campaigns_in(db, scope))
    )
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
        .where(Application.campaign_id == any_(uuid_array(campaign_ids)), _applications_of(db))
        .group_by(Application.campaign_id)
    )
    return {cid: (count, strong, latest) for cid, count, strong, latest in result.tuples()}


async def category_score_rows(
    db: AsyncSession, scope: AccessScope, campaign_id: uuid.UUID | None = None
) -> list[tuple[str | None, int, int, float | None]]:
    """``(category, applications, scored applications, sum of scores)`` per category, for one
    campaign or (``None``) every campaign in ``scope``."""
    query = select(
        Application.category,
        func.count(Application.id),
        func.count(Application.score),
        func.sum(Application.score),
    ).where(_applications_in(db, scope))
    if campaign_id is not None:
        query = query.where(Application.campaign_id == campaign_id)
    result = await db.execute(query.group_by(Application.category))
    return [(category, n, scored, total) for category, n, scored, total in result.tuples()]


async def stage_counts(
    db: AsyncSession, scope: AccessScope, campaign_id: uuid.UUID | None = None
) -> dict[str | None, int]:
    query = select(Application.pipeline_stage, func.count(Application.id)).where(
        _applications_in(db, scope)
    )
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
        .where(
            Application.campaign_id == campaign_id,
            Application.score.is_not(None),
            _applications_of(db),
        )
        .group_by(bucket)
    )
    return {int(b): count for b, count in result.tuples()}


async def count_where(db: AsyncSession, filters: Sequence[ColumnElement[bool]]) -> int:
    total = await db.scalar(
        select(func.count(Application.id)).where(_applications_of(db), *filters)
    )
    return total or 0


async def page_of_applications(
    db: AsyncSession,
    filters: Sequence[ColumnElement[bool]],
    order: Sequence[ColumnElement[Any]],
    *,
    offset: int | None = None,
    limit: int | None = None,
) -> Sequence[Application]:
    query = select(Application).where(_applications_of(db), *filters).order_by(*order)
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
    result = await db.execute(
        select(Application.id, Application.resume_id).where(_applications_of(db), *filters)
    )
    return [(application_id, resume_id) for application_id, resume_id in result.tuples()]


async def applications_by_id(db: AsyncSession, ids: Sequence[uuid.UUID]) -> Sequence[Application]:
    """The applications with these ids (unordered; unknown ids are left out)."""
    if not ids:
        return []
    result = await db.execute(
        select(Application).where(Application.id == any_(uuid_array(ids)), _applications_of(db))
    )
    return result.scalars().all()


async def get_application_by_id(
    db: AsyncSession, scope: AccessScope, application_id: uuid.UUID
) -> Application | None:
    """The application with this id in ``scope``, or ``None``."""
    result = await db.execute(
        select(Application).where(Application.id == application_id, _applications_in(db, scope))
    )
    return result.scalar_one_or_none()


async def lock_campaign_applications(
    db: AsyncSession, campaign_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> Sequence[Application]:
    """The campaign's applications among ``ids`` (own ids only), locked in id order (a fixed
    order, so two bulk requests over overlapping applications cannot deadlock)."""
    if not ids:
        return []
    result = await db.execute(
        select(Application)
        .where(
            Application.campaign_id == campaign_id,
            Application.id == any_(uuid_array(ids)),
            _applications_of(db),
        )
        .order_by(Application.id)
        .with_for_update()
    )
    return result.scalars().all()


async def applications_of_person(
    db: AsyncSession, scope: AccessScope, person_id: uuid.UUID
) -> list[tuple[Application, Campaign]]:
    """The Person's applications to campaigns in ``scope``, with their campaigns, newest
    application first (ties by id)."""
    result = await db.execute(
        select(Application, Campaign)
        .join(Campaign, Campaign.id == Application.campaign_id)
        .where(Application.person_id == person_id, _applications_of(db), _campaigns_in(db, scope))
        .order_by(Application.created_at.desc(), Application.id)
    )
    return [(application, campaign) for application, campaign in result.tuples()]


async def person_activity(
    db: AsyncSession, scope: AccessScope, person_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, tuple[int, datetime | None]]:
    """``person_id -> (applications, latest resume upload)`` for the people among ``person_ids``
    that have applications to campaigns in ``scope``. An application whose upload time is
    unknown counts with its ``created_at``."""
    if not person_ids:
        return {}
    result = await db.execute(
        select(
            Application.person_id,
            func.count(Application.id),
            func.max(func.coalesce(Application.resume_uploaded_at, Application.created_at)),
        )
        .where(Application.person_id == any_(uuid_array(person_ids)), _applications_in(db, scope))
        .group_by(Application.person_id)
    )
    return {pid: (count, latest) for pid, count, latest in result.tuples()}


async def recent_applications_in_category(
    db: AsyncSession, scope: AccessScope, category: str, limit: int
) -> list[tuple[Application, Campaign]]:
    """The newest applications in ``category`` to campaigns in ``scope`` (ties by id), with
    their campaigns."""
    result = await db.execute(
        select(Application, Campaign)
        .join(Campaign, Campaign.id == Application.campaign_id)
        .where(Application.category == category, _applications_of(db), _campaigns_in(db, scope))
        .order_by(Application.created_at.desc(), Application.id)
        .limit(limit)
    )
    return [(application, campaign) for application, campaign in result.tuples()]


async def application_ids_of(
    db: AsyncSession, scope: AccessScope, owners: Sequence[tuple[uuid.UUID, uuid.UUID]]
) -> dict[tuple[uuid.UUID, uuid.UUID], uuid.UUID]:
    """``(campaign_id, person_id) -> application id`` for the pairs that have an application to
    a campaign in ``scope``."""
    if not owners:
        return {}
    result = await db.execute(
        select(Application.campaign_id, Application.person_id, Application.id).where(
            tuple_(Application.campaign_id, Application.person_id).in_(list(owners)),
            _applications_in(db, scope),
        )
    )
    return {(cid, pid): application_id for cid, pid, application_id in result.tuples()}


# ── Upload pipeline (sync, Celery tasks) ──────────────────────
def get_campaign_sync(db: Session, campaign_id: uuid.UUID) -> Campaign | None:
    return db.execute(
        select(Campaign).where(Campaign.id == campaign_id, _campaigns_of(db))
    ).scalar_one_or_none()


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

    ``values`` holds ``id``, ``agency_id`` (the session's agency), ``campaign_id``, ``person_id`` and every column in
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
            _applications_of(db),
        )
        .execution_options(populate_existing=True)
    ).scalar_one()


# ── Verification (sync, ``app.modules.ingestion.verify_people_applications``) ──
# Every agency on purpose: an operator command checks all rows (unbound owner session).
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


# ── Tenancy backfill (sync, ``app.modules.ingestion.backfill_tenancy``, E2-02): every agency ──
_TENANT_TABLES = {
    "campaigns": cast(Table, Campaign.__table__),
    "applications": cast(Table, Application.__table__),
}
# hiring's tenant tables, in the order the backfill fills them.
TENANCY_TABLES = tuple(_TENANT_TABLES)


def first_client_id_sync(db: Session, agency_id: uuid.UUID) -> uuid.UUID | None:
    """The agency's oldest active client (ties by id), or ``None``."""
    return db.execute(
        select(Client.id)
        .where(Client.agency_id == agency_id, Client.status == "active")
        .order_by(Client.created_at, Client.id)
        .limit(1)
    ).scalar_one_or_none()


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
