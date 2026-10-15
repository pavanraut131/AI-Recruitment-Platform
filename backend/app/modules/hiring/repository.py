"""hiring data access: every query against the ``campaigns`` and ``candidates`` tables.

Private to the hiring module (only ``service.py`` calls it). Functions take the request's
``AsyncSession``; the ones that end a unit of work commit, exactly where the endpoints committed
before the move, so the SQL issued per request is unchanged.

The functions under "Resume reads and deletes" query the ingestion-owned ``resumes`` table and
are the only places hiring touches it. They are temporary: TODO(E0-10) see ``service.py``,
"Cross-module dependencies", for how each one leaves this module.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.resume import Resume
from app.modules.hiring.models import Campaign, Candidate


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
    """Mark ``candidate`` for deletion; the caller commits."""
    await db.delete(candidate)


async def commit(db: AsyncSession) -> None:
    await db.commit()


async def commit_and_refresh(db: AsyncSession, row: Campaign | Candidate) -> None:
    await db.commit()
    await db.refresh(row)


# ── Resume reads and deletes (cross-module, TODO(E0-10)) ─────
async def resume_status_counts(
    db: AsyncSession, campaign_id: uuid.UUID | None = None
) -> dict[str, int]:
    """Resume counts by status, for one campaign or (``campaign_id=None``) for all of them."""
    query = select(Resume.status, func.count(Resume.id))
    if campaign_id is not None:
        query = query.where(Resume.campaign_id == campaign_id)
    result = await db.execute(query.group_by(Resume.status))
    return {status: count for status, count in result.tuples()}


async def delete_resumes(db: AsyncSession, resume_ids: Sequence[uuid.UUID]) -> None:
    """Mark the resumes with these ids for deletion; the caller commits."""
    result = await db.execute(select(Resume).where(Resume.id.in_(resume_ids)))
    for resume in result.scalars().all():
        await db.delete(resume)


async def delete_resume(db: AsyncSession, resume_id: uuid.UUID) -> None:
    """Mark the resume with this id (if any) for deletion; the caller commits."""
    result = await db.execute(select(Resume).where(Resume.id == resume_id))
    resume = result.scalar_one_or_none()
    if resume:
        await db.delete(resume)
