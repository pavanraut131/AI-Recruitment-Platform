"""Endpoints composed from the ``hiring`` and ``ingestion`` services (see the package docstring).

- Campaign detail, campaign stats and the global stats: hiring aggregates its own numbers plus
  the resume counts by status that ingestion reports.
- Candidate delete and bulk delete: hiring deletes the candidates and returns their resume ids,
  ingestion deletes those resumes, and both are committed in one transaction (the request's
  ``AsyncSession``).

Deleting a campaign stays in hiring: its resumes go with it through the database's
``resumes.campaign_id ON DELETE CASCADE``. When that cross-module foreign key is dropped
(ADR 0003), the campaign delete moves here and asks ingestion to delete the resumes.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.hiring import service as hiring
from app.modules.hiring.schemas import (
    BulkDeleteRequest,
    CampaignResponse,
    CampaignStats,
    GlobalStats,
)
from app.modules.ingestion import service as ingestion
from app.platform.database import get_db

campaigns_router = APIRouter(prefix="/campaigns", tags=["campaigns"])
candidates_router = APIRouter(tags=["candidates"])
stats_router = APIRouter(prefix="/stats", tags=["stats"])


@contextmanager
def _not_found_as_404() -> Iterator[None]:
    try:
        yield
    except hiring.NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=e.detail) from e


async def _campaign_stats(db: AsyncSession, campaign_id: uuid.UUID) -> CampaignStats:
    resume_counts = await ingestion.resume_status_counts(db, campaign_id)
    return await hiring.campaign_stats(db, campaign_id, resume_counts)


# ── Campaigns ─────────────────────────────────────────────────
@campaigns_router.get("/{campaign_id}", response_model=CampaignResponse)
async def get_campaign(
    campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> CampaignResponse:
    with _not_found_as_404():
        campaign = await hiring.get_campaign(db, campaign_id)
    campaign.stats = await _campaign_stats(db, campaign_id)
    return campaign


@campaigns_router.get("/{campaign_id}/stats", response_model=CampaignStats)
async def campaign_stats(
    campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> CampaignStats:
    return await _campaign_stats(db, campaign_id)


# ── Global stats ──────────────────────────────────────────────
@stats_router.get("", response_model=GlobalStats)
async def get_global_stats(db: AsyncSession = Depends(get_db)) -> GlobalStats:
    """
    Return aggregate counts across all campaigns.
    Three single-table queries — no joins, very fast.
    """
    resume_counts = await ingestion.resume_status_counts(db)
    return await hiring.global_stats(db, resume_counts)


# ── Candidates: delete ────────────────────────────────────────
# response_model=None: no response schema was ever published for the bulk endpoint, and
# FastAPI would otherwise derive one from the dict[str, int] return annotation.
@candidates_router.delete(
    "/campaigns/{campaign_id}/candidates/bulk", status_code=status.HTTP_200_OK, response_model=None
)
async def bulk_delete_candidates(
    campaign_id: uuid.UUID,
    payload: BulkDeleteRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Delete multiple candidates (and their resumes) in one request."""
    resume_ids = await hiring.bulk_delete_candidates(db, campaign_id, payload.candidate_ids)
    if resume_ids:
        await ingestion.delete_resumes(db, resume_ids)
    await db.commit()
    return {"deleted": len(resume_ids)}


@candidates_router.delete("/candidates/{candidate_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_candidate(candidate_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    with _not_found_as_404():
        resume_id = await hiring.delete_candidate(db, candidate_id)
    await ingestion.delete_resumes(db, [resume_id])
    await db.commit()
