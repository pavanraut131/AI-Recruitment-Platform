"""ingestion data access: every query against the ``resumes`` and ``backfill_checkpoints`` tables.

Private to the ingestion module. The async functions serve the HTTP endpoints (the request's
``AsyncSession``); the ``*_sync`` ones serve the Celery tasks and the backfill / verification
commands, which use a synchronous ``Session``. Only functions named ``commit`` commit; the caller decides where a unit of work ends.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Row, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.modules.ingestion.models import BackfillCheckpoint, Resume

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


async def uploaded_at(
    db: AsyncSession, resume_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, datetime]:
    """``id -> uploaded_at`` of the resumes among ``resume_ids`` that exist."""
    if not resume_ids:
        return {}
    result = await db.execute(
        select(Resume.id, Resume.uploaded_at).where(Resume.id.in_(resume_ids))
    )
    return {resume_id: uploaded for resume_id, uploaded in result.tuples()}


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
