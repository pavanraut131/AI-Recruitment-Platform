"""ingestion data access: every query against the ``resumes`` table.

Private to the ingestion module. The async functions serve the HTTP endpoints (the request's
``AsyncSession``); the ``*_sync`` ones serve the Celery tasks, the verification command and the
storage check, which use a synchronous ``Session``. Only functions named ``commit`` commit; the
caller decides where a unit of work ends.

**Tenancy (E2-03).** Every query is filtered to the agency the session is bound to
(:func:`_resumes_of`, through ``app.platform.tenancy.tenant_filter``) and raises
``TenantNotBound`` on an unbound session; :func:`add` sets ``agency_id`` from the same binding.
The functions under "Every agency" serve operator commands (``reset_stuck``, the verification,
``storage_orphans``) and read all agencies on purpose, through an unbound session.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import (
    ColumnElement,
    Row,
    Table,
    and_,
    any_,
    column,
    exists,
    func,
    or_,
    select,
    tuple_,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.modules.ingestion.models import Resume
from app.platform import backfill
from app.platform.sql import LIKE_ESCAPE, escape_like, uuid_array
from app.platform.tenancy import AnySession, agency_id_of, tenant_filter

FAILED_STATUSES = ("failed", "quota_exceeded")


def _resumes_of(db: AnySession) -> ColumnElement[bool]:
    """Resumes of the session's agency."""
    return tenant_filter(db, Resume.agency_id)


# ── Async (HTTP) ──────────────────────────────────────────────
async def latest_with_hash(
    db: AsyncSession, campaign_id: uuid.UUID, file_hash: str
) -> Resume | None:
    """The campaign's most recently uploaded resume with this content hash, if any."""
    result = await db.execute(
        select(Resume)
        .where(Resume.campaign_id == campaign_id, Resume.file_hash == file_hash, _resumes_of(db))
        .order_by(Resume.uploaded_at.desc())
    )
    return result.scalars().first()


def add(db: AsyncSession, resume: Resume) -> None:
    """Add a new resume to the session, in the session's agency."""
    resume.agency_id = agency_id_of(db)
    db.add(resume)


async def list_for_campaign(db: AsyncSession, campaign_id: uuid.UUID) -> Sequence[Resume]:
    """The campaign's resumes, newest upload first."""
    result = await db.execute(
        select(Resume)
        .where(Resume.campaign_id == campaign_id, _resumes_of(db))
        .order_by(Resume.uploaded_at.desc())
    )
    return result.scalars().all()


async def get(db: AsyncSession, resume_id: uuid.UUID) -> Resume | None:
    result = await db.execute(select(Resume).where(Resume.id == resume_id, _resumes_of(db)))
    return result.scalar_one_or_none()


async def failed_in_campaign(db: AsyncSession, campaign_id: uuid.UUID) -> Sequence[Resume]:
    """The campaign's ``failed`` and ``quota_exceeded`` resumes (unordered)."""
    result = await db.execute(
        select(Resume).where(
            Resume.campaign_id == campaign_id,
            Resume.status.in_(list(FAILED_STATUSES)),
            _resumes_of(db),
        )
    )
    return result.scalars().all()


async def status_counts(db: AsyncSession, campaign_id: uuid.UUID | None = None) -> dict[str, int]:
    """Resume counts by status, for one campaign or (``campaign_id=None``) for all of them."""
    query = select(Resume.status, func.count(Resume.id)).where(_resumes_of(db))
    if campaign_id is not None:
        query = query.where(Resume.campaign_id == campaign_id)
    result = await db.execute(query.group_by(Resume.status))
    return {status: count for status, count in result.tuples()}


async def extractions(
    db: AsyncSession, resume_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, dict[str, Any] | None]:
    """``id -> extracted`` of the resumes among ``resume_ids`` that exist (one query)."""
    if not resume_ids:
        return {}
    result = await db.execute(
        select(Resume.id, Resume.extracted).where(
            Resume.id == any_(uuid_array(resume_ids)), _resumes_of(db)
        )
    )
    return {resume_id: extracted for resume_id, extracted in result.tuples()}


async def skill_lists(db: AsyncSession, resume_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Any]:
    """``id -> extracted['skills']`` of the resumes among ``resume_ids`` that exist."""
    if not resume_ids:
        return {}
    result = await db.execute(
        select(Resume.id, Resume.extracted["skills"]).where(
            Resume.id == any_(uuid_array(resume_ids)), _resumes_of(db)
        )
    )
    return {resume_id: skills for resume_id, skills in result.tuples()}


async def matching_extractions(
    db: AsyncSession,
    resume_ids: Sequence[uuid.UUID],
    *,
    search: str | None,
    skill: str | None,
    min_experience: float | None,
) -> list[uuid.UUID]:
    """The resumes among ``resume_ids`` whose extraction matches every given filter.

    The same SQL the legacy list ran on ``candidates`` (B2), on the snapshot's fields:

    - ``search``: ``extracted->>'name'`` or ``extracted->>'email'`` contains it (``ILIKE``,
      wildcards escaped);
    - ``skill``: ``EXISTS (SELECT 1 FROM jsonb_array_elements_text(extracted->'skills') AS
      skill WHERE skill.value ILIKE :pattern)``: any one skill contains it, never the JSON text;
    - ``min_experience``: ``(extracted->>'years_of_experience')::float >= :min``; an unknown
      experience never matches.
    """
    if not resume_ids:
        return []
    clauses: list[ColumnElement[bool]] = [
        Resume.id == any_(uuid_array(resume_ids)),
        _resumes_of(db),
    ]
    if search:
        pattern = f"%{escape_like(search)}%"
        clauses.append(
            or_(
                Resume.extracted["name"].astext.ilike(pattern, escape=LIKE_ESCAPE),
                Resume.extracted["email"].astext.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    if skill:
        skill_elem = (
            func.jsonb_array_elements_text(Resume.extracted["skills"])
            .table_valued(column("value"))
            .alias("skill")
        )
        clauses.append(
            exists()
            .select_from(skill_elem)
            .where(skill_elem.c.value.ilike(f"%{escape_like(skill)}%", escape=LIKE_ESCAPE))
        )
    if min_experience is not None:
        clauses.append(Resume.extracted["years_of_experience"].as_float() >= min_experience)
    result = await db.execute(select(Resume.id).where(and_(*clauses)))
    return list(result.scalars())


async def owner(
    db: AsyncSession, resume_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID | None] | None:
    """``(campaign_id, person_id)`` of the resume, or ``None`` if it does not exist."""
    result = await db.execute(
        select(Resume.campaign_id, Resume.person_id).where(Resume.id == resume_id, _resumes_of(db))
    )
    row = result.one_or_none()
    return (row.campaign_id, row.person_id) if row is not None else None


async def delete_of_people(
    db: AsyncSession,
    resume_ids: Sequence[uuid.UUID],
    people: Sequence[tuple[uuid.UUID, uuid.UUID]],
) -> list[str]:
    """Delete the resumes with these ids and every resume of these ``(campaign_id, person_id)``
    pairs (flushed, not committed: the caller commits); returns their storage paths."""
    clauses: list[ColumnElement[bool]] = []
    if resume_ids:
        clauses.append(Resume.id == any_(uuid_array(resume_ids)))
    if people:
        clauses.append(tuple_(Resume.campaign_id, Resume.person_id).in_(people))
    if not clauses:
        return []
    return await _delete_where(db, or_(*clauses))


async def delete_for_campaign(db: AsyncSession, campaign_id: uuid.UUID) -> list[str]:
    """Delete every resume of the campaign (flushed, not committed: the caller commits); returns
    their storage paths."""
    return await _delete_where(db, Resume.campaign_id == campaign_id)


async def _delete_where(db: AsyncSession, clause: ColumnElement[bool]) -> list[str]:
    result = await db.execute(select(Resume).where(clause, _resumes_of(db)).order_by(Resume.id))
    resumes = result.scalars().all()
    paths = [resume.storage_path for resume in resumes]
    for resume in resumes:
        await db.delete(resume)
    await db.flush()
    return paths


async def commit(db: AsyncSession) -> None:
    await db.commit()


# ── /api/v2 (E1-02) ───────────────────────────────────────────
TallyRow = tuple[uuid.UUID, str, int, int, datetime | None]


async def tallies(
    db: AsyncSession, campaign_ids: Sequence[uuid.UUID] | None, recent: timedelta
) -> list[TallyRow]:
    """``(campaign_id, status, resumes, uploaded within `recent`, latest updated_at)`` per
    campaign and status, for the given campaigns or (``None``) all of them: one grouped query."""
    if campaign_ids is not None and not campaign_ids:
        return []
    query = select(
        Resume.campaign_id,
        Resume.status,
        func.count(Resume.id),
        func.count(Resume.id).filter(Resume.uploaded_at >= func.now() - recent),
        func.max(Resume.updated_at),
    ).where(_resumes_of(db))
    if campaign_ids is not None:
        query = query.where(Resume.campaign_id == any_(uuid_array(campaign_ids)))
    result = await db.execute(query.group_by(Resume.campaign_id, Resume.status))
    return [(cid, status, n, new, latest) for cid, status, n, new, latest in result.tuples()]


async def page_for_campaign(
    db: AsyncSession,
    campaign_id: uuid.UUID,
    statuses: Sequence[str],
    *,
    offset: int,
    limit: int,
) -> tuple[Sequence[Resume], int]:
    """A page of the campaign's resumes (any of ``statuses``, or all), newest upload first (ties
    by id), and how many match in all."""
    clauses: list[ColumnElement[bool]] = [Resume.campaign_id == campaign_id]
    if statuses:
        clauses.append(Resume.status.in_(list(statuses)))
    return await _page(db, clauses, offset=offset, limit=limit)


async def page_in_statuses(
    db: AsyncSession, statuses: Sequence[str], *, limit: int
) -> tuple[Sequence[Resume], int]:
    """The newest resumes in any of ``statuses`` across campaigns, and how many there are."""
    return await _page(db, [Resume.status.in_(list(statuses))], offset=0, limit=limit)


async def _page(
    db: AsyncSession, clauses: Sequence[ColumnElement[bool]], *, offset: int, limit: int
) -> tuple[Sequence[Resume], int]:
    clauses = [_resumes_of(db), *clauses]
    total = await db.scalar(select(func.count(Resume.id)).where(*clauses))
    if not total:
        return [], 0
    result = await db.execute(
        select(Resume)
        .where(*clauses)
        .order_by(Resume.uploaded_at.desc(), Resume.id)
        .offset(offset)
        .limit(limit)
    )
    return result.scalars().all(), total


async def by_ids(db: AsyncSession, resume_ids: Sequence[uuid.UUID]) -> Sequence[Resume]:
    """The resumes with these ids, fresh from the database (server defaults loaded)."""
    if not resume_ids:
        return []
    result = await db.execute(
        select(Resume)
        .where(Resume.id == any_(uuid_array(resume_ids)), _resumes_of(db))
        .execution_options(populate_existing=True)
    )
    return result.scalars().all()


async def names(db: AsyncSession, resume_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str | None]:
    """``id -> extracted->>'name'`` of the resumes among ``resume_ids`` that exist."""
    if not resume_ids:
        return {}
    result = await db.execute(
        select(Resume.id, Resume.extracted["name"].astext).where(
            Resume.id == any_(uuid_array(resume_ids)), _resumes_of(db)
        )
    )
    return {resume_id: name for resume_id, name in result.tuples()}


# ── Sync (Celery tasks) ───────────────────────────────────────
def get_sync(db: Session, resume_id: uuid.UUID) -> Resume | None:
    return db.execute(
        select(Resume).where(Resume.id == resume_id, _resumes_of(db))
    ).scalar_one_or_none()


# ── Every agency: operator commands and the verification (sync, unbound session) ──
def reset_stuck_sync(db: Session) -> list[tuple[uuid.UUID, uuid.UUID | None, str]]:
    """Set every ``extracting``, ``processing`` or ``failed`` resume of **every agency** back to
    ``pending`` (retry count and error cleared); returns their ``(id, agency_id,
    original_filename)`` (``agency_id`` is ``None`` for a row not backfilled yet, E2-02). The
    caller commits."""
    result = db.execute(
        update(Resume)
        .where(Resume.status.in_(["extracting", "processing", "failed"]))
        .values(status="pending", retry_count=0, error_message=None)
        .returning(Resume.id, Resume.agency_id, Resume.original_filename)
    )
    return [(resume_id, agency_id, filename) for resume_id, agency_id, filename in result.tuples()]


def resume_refs_sync(
    db: Session,
) -> Sequence[Row[tuple[uuid.UUID, uuid.UUID, str, uuid.UUID | None, uuid.UUID]]]:
    """``(id, campaign_id, status, person_id, agency_id)`` of every resume."""
    return db.execute(
        select(Resume.id, Resume.campaign_id, Resume.status, Resume.person_id, Resume.agency_id)
    ).all()


def extracted_sync(db: Session, resume_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Any]:
    """``id -> extracted`` (the stored extraction snapshot, possibly ``None``)."""
    if not resume_ids:
        return {}
    rows = db.execute(select(Resume.id, Resume.extracted).where(Resume.id.in_(resume_ids))).tuples()
    return {resume_id: extracted for resume_id, extracted in rows}


def storage_paths_sync(db: Session) -> set[str]:
    """The ``storage_path`` of every resume."""
    return set(db.execute(select(Resume.storage_path)).scalars())


# ── Tenancy backfill (sync, ``backfill_tenancy``, E2-02) ──────
_resumes = cast(Table, Resume.__table__)


def fill_tenancy_batch_sync(
    db: Session, *, agency_id: uuid.UUID, after: uuid.UUID | None, limit: int
) -> list[uuid.UUID]:
    """The next ``limit`` resumes after ``after`` (by id) without an agency get ``agency_id``
    (``updated_at`` unchanged). Returns their ids, ascending; the caller commits."""
    return backfill.update_next_batch(
        db,
        _resumes,
        missing=_resumes.c.agency_id.is_(None),
        values={"agency_id": agency_id},
        after=after,
        limit=limit,
    )


def count_tenancy_gaps_sync(db: Session) -> int:
    """Resumes still without an agency."""
    return backfill.count_missing(db, _resumes, _resumes.c.agency_id.is_(None))


def agency_census_sync(db: Session) -> dict[uuid.UUID | None, int]:
    """``agency_id -> resumes`` (``None``: no agency yet)."""
    return backfill.agency_census(db, _resumes)
