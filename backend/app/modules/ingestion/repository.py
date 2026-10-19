"""ingestion data access: every query against the ``resumes`` and ``backfill_checkpoints`` tables.

Private to the ingestion module. The async functions serve the HTTP endpoints (the request's
``AsyncSession``); the ``*_sync`` ones serve the Celery tasks and the backfill / verification
commands, which use a synchronous ``Session``. Only functions named ``commit`` commit; the caller decides where a unit of work ends.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Row,
    and_,
    any_,
    column,
    delete,
    exists,
    func,
    or_,
    select,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.modules.ingestion.models import BackfillCheckpoint, Resume
from app.platform.sql import LIKE_ESCAPE, escape_like, uuid_array

FAILED_STATUSES = ("failed", "quota_exceeded")


# ── Async (HTTP) ──────────────────────────────────────────────
async def latest_with_hash(
    db: AsyncSession, campaign_id: uuid.UUID, file_hash: str
) -> Resume | None:
    """The campaign's most recently uploaded resume with this content hash, if any."""
    result = await db.execute(
        select(Resume)
        .where(Resume.campaign_id == campaign_id, Resume.file_hash == file_hash)
        .order_by(Resume.uploaded_at.desc())
    )
    return result.scalars().first()


def add(db: AsyncSession, resume: Resume) -> None:
    db.add(resume)


async def list_for_campaign(db: AsyncSession, campaign_id: uuid.UUID) -> Sequence[Resume]:
    """The campaign's resumes, newest upload first."""
    result = await db.execute(
        select(Resume).where(Resume.campaign_id == campaign_id).order_by(Resume.uploaded_at.desc())
    )
    return result.scalars().all()


async def get(db: AsyncSession, resume_id: uuid.UUID) -> Resume | None:
    result = await db.execute(select(Resume).where(Resume.id == resume_id))
    return result.scalar_one_or_none()


async def failed_in_campaign(db: AsyncSession, campaign_id: uuid.UUID) -> Sequence[Resume]:
    """The campaign's ``failed`` and ``quota_exceeded`` resumes (unordered)."""
    result = await db.execute(
        select(Resume).where(
            Resume.campaign_id == campaign_id, Resume.status.in_(list(FAILED_STATUSES))
        )
    )
    return result.scalars().all()


async def status_counts(db: AsyncSession, campaign_id: uuid.UUID | None = None) -> dict[str, int]:
    """Resume counts by status, for one campaign or (``campaign_id=None``) for all of them."""
    query = select(Resume.status, func.count(Resume.id))
    if campaign_id is not None:
        query = query.where(Resume.campaign_id == campaign_id)
    result = await db.execute(query.group_by(Resume.status))
    return {status: count for status, count in result.tuples()}


async def delete_by_ids(db: AsyncSession, resume_ids: Sequence[uuid.UUID]) -> None:
    """Mark the resumes with these ids (those that exist) for deletion; the caller commits."""
    result = await db.execute(select(Resume).where(Resume.id.in_(resume_ids)))
    for resume in result.scalars().all():
        await db.delete(resume)


async def extractions(
    db: AsyncSession, resume_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, dict[str, Any] | None]:
    """``id -> extracted`` of the resumes among ``resume_ids`` that exist (one query)."""
    if not resume_ids:
        return {}
    result = await db.execute(
        select(Resume.id, Resume.extracted).where(Resume.id == any_(uuid_array(resume_ids)))
    )
    return {resume_id: extracted for resume_id, extracted in result.tuples()}


async def skill_lists(db: AsyncSession, resume_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Any]:
    """``id -> extracted['skills']`` of the resumes among ``resume_ids`` that exist."""
    if not resume_ids:
        return {}
    result = await db.execute(
        select(Resume.id, Resume.extracted["skills"]).where(
            Resume.id == any_(uuid_array(resume_ids))
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
    clauses: list[ColumnElement[bool]] = [Resume.id == any_(uuid_array(resume_ids))]
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
        select(Resume.campaign_id, Resume.person_id).where(Resume.id == resume_id)
    )
    row = result.one_or_none()
    return (row.campaign_id, row.person_id) if row is not None else None


async def delete_of_people(
    db: AsyncSession,
    resume_ids: Sequence[uuid.UUID],
    people: Sequence[tuple[uuid.UUID, uuid.UUID]],
) -> None:
    """Mark for deletion the resumes with these ids and every resume of these ``(campaign_id,
    person_id)`` pairs; the caller commits."""
    clauses: list[ColumnElement[bool]] = []
    if resume_ids:
        clauses.append(Resume.id == any_(uuid_array(resume_ids)))
    if people:
        clauses.append(tuple_(Resume.campaign_id, Resume.person_id).in_(people))
    if not clauses:
        return
    result = await db.execute(select(Resume).where(or_(*clauses)))
    for resume in result.scalars().all():
        await db.delete(resume)


async def commit(db: AsyncSession) -> None:
    await db.commit()


# ── Sync (Celery tasks) ───────────────────────────────────────
def get_sync(db: Session, resume_id: uuid.UUID) -> Resume | None:
    return db.query(Resume).filter(Resume.id == resume_id).first()


def reset_stuck_sync(db: Session) -> list[tuple[uuid.UUID, str]]:
    """Set every ``extracting``, ``processing`` or ``failed`` resume back to ``pending`` (retry
    count and error cleared); returns their ``(id, original_filename)``. The caller commits."""
    result = db.execute(
        update(Resume)
        .where(Resume.status.in_(["extracting", "processing", "failed"]))
        .values(status="pending", retry_count=0, error_message=None)
        .returning(Resume.id, Resume.original_filename)
    )
    return [(resume_id, filename) for resume_id, filename in result.tuples()]


# ── Backfill and verification (sync, E0-17) ───────────────────
def uploaded_at_sync(db: Session, resume_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, datetime]:
    """``id -> uploaded_at`` of the resumes among ``resume_ids`` that exist."""
    if not resume_ids:
        return {}
    rows = db.execute(
        select(Resume.id, Resume.uploaded_at).where(Resume.id.in_(resume_ids))
    ).tuples()
    return {resume_id: uploaded_at for resume_id, uploaded_at in rows}


def resume_refs_sync(
    db: Session,
) -> Sequence[Row[tuple[uuid.UUID, uuid.UUID, str, uuid.UUID | None]]]:
    """``(id, campaign_id, status, person_id)`` of every resume."""
    return db.execute(select(Resume.id, Resume.campaign_id, Resume.status, Resume.person_id)).all()


def extracted_sync(db: Session, resume_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Any]:
    """``id -> extracted`` (the stored extraction snapshot, possibly ``None``)."""
    if not resume_ids:
        return {}
    rows = db.execute(select(Resume.id, Resume.extracted).where(Resume.id.in_(resume_ids))).tuples()
    return {resume_id: extracted for resume_id, extracted in rows}


def get_checkpoint_sync(db: Session, name: str) -> BackfillCheckpoint | None:
    return db.get(BackfillCheckpoint, name, populate_existing=True)


def save_checkpoint_sync(
    db: Session, name: str, last_created_at: datetime, last_id: uuid.UUID
) -> None:
    """Upsert the job's keyset position (``INSERT … ON CONFLICT (name) DO UPDATE``)."""
    stmt = pg_insert(BackfillCheckpoint).values(
        name=name, last_created_at=last_created_at, last_id=last_id
    )
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=["name"],
            set_={
                "last_created_at": stmt.excluded.last_created_at,
                "last_id": stmt.excluded.last_id,
                "updated_at": func.now(),
            },
        )
    )


def delete_checkpoint_sync(db: Session, name: str) -> bool:
    result = db.execute(
        delete(BackfillCheckpoint)
        .where(BackfillCheckpoint.name == name)
        .returning(BackfillCheckpoint.name)
    )
    return result.scalar_one_or_none() is not None
