"""Endpoints composed from the ``hiring`` and ``ingestion`` services (see the package docstring).

- Campaign detail, campaign stats and the global stats: hiring aggregates its own numbers plus
  the resume counts by status that ingestion reports.
- Candidate delete and bulk delete: hiring deletes the candidates (and applies its delete rule
  to their applications, E0-18) and returns their resume ids, ingestion deletes those resumes,
  and both are committed in one transaction (the request's ``AsyncSession``).
- Recruiter updates (category, notes, pipeline stage, bulk pipeline; E0-18): hiring writes the
  candidate and mirrors it onto its application in one transaction. Both this and the deletes
  order a merged application's candidates by resume upload time, which ingestion owns: the
  endpoint hands hiring ingestion's lookup (``_upload_times``), nothing more.

Deleting a campaign stays in hiring: its resumes go with it through the database's
``resumes.campaign_id ON DELETE CASCADE``. When that cross-module foreign key is dropped
(ADR 0003), the campaign delete moves here and asks ingestion to delete the resumes.
"""

import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.hiring import service as hiring
from app.modules.hiring.schemas import (
    BulkDeleteRequest,
    BulkPipelineUpdate,
    CampaignResponse,
    CampaignStats,
    CandidateCategoryUpdate,
    CandidateDetail,
    CandidateNotesUpdate,
    CandidatePipelineUpdate,
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


@contextmanager
def _hiring_errors() -> Iterator[None]:
    """A hiring error as the status code the hiring router has always used for it."""
    try:
        yield
    except hiring.NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=e.detail) from e
    except hiring.InvalidInputError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=e.detail) from e


def _upload_times(db: AsyncSession) -> hiring.AsyncUploadTimes:
    """Ingestion's resume upload times, in the form hiring asks for them."""

    async def lookup(resume_ids: Sequence[uuid.UUID]) -> Mapping[uuid.UUID, datetime]:
        return await ingestion.resume_upload_times(db, resume_ids)

    return lookup


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
    resume_ids = await hiring.bulk_delete_candidates(
        db, campaign_id, payload.candidate_ids, _upload_times(db)
    )
    if resume_ids:
        await ingestion.delete_resumes(db, resume_ids)
    await db.commit()
    return {"deleted": len(resume_ids)}


@candidates_router.delete("/candidates/{candidate_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_candidate(candidate_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    with _not_found_as_404():
        resume_id = await hiring.delete_candidate(db, candidate_id, _upload_times(db))
    await ingestion.delete_resumes(db, [resume_id])
    await db.commit()


# ── Candidates: recruiter updates ─────────────────────────────
@candidates_router.put("/candidates/{candidate_id}/category", response_model=CandidateDetail)
async def update_candidate_category(
    candidate_id: uuid.UUID,
    payload: CandidateCategoryUpdate,
    db: AsyncSession = Depends(get_db),
) -> CandidateDetail:
    with _hiring_errors():
        candidate = await hiring.update_candidate_category(
            db, candidate_id, payload.category, _upload_times(db)
        )
    return CandidateDetail.model_validate(candidate)


@candidates_router.patch("/candidates/{candidate_id}/notes", response_model=CandidateDetail)
async def update_candidate_notes(
    candidate_id: uuid.UUID,
    payload: CandidateNotesUpdate,
    db: AsyncSession = Depends(get_db),
) -> CandidateDetail:
    with _hiring_errors():
        candidate = await hiring.update_candidate_notes(
            db, candidate_id, payload.notes, _upload_times(db)
        )
    return CandidateDetail.model_validate(candidate)


@candidates_router.patch("/candidates/{candidate_id}/pipeline", response_model=CandidateDetail)
async def update_candidate_pipeline(
    candidate_id: uuid.UUID,
    payload: CandidatePipelineUpdate,
    db: AsyncSession = Depends(get_db),
) -> CandidateDetail:
    with _hiring_errors():
        candidate = await hiring.update_candidate_pipeline(
            db, candidate_id, payload.stage, _upload_times(db)
        )
    return CandidateDetail.model_validate(candidate)


# response_model=None: no response schema was ever published for the bulk endpoints, and
# FastAPI would otherwise derive one from the dict[str, int] return annotation.
@candidates_router.patch("/campaigns/{campaign_id}/candidates/bulk-pipeline", response_model=None)
async def bulk_update_pipeline(
    campaign_id: uuid.UUID,
    payload: BulkPipelineUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Move multiple candidates to the same pipeline stage in one request."""
    with _hiring_errors():
        updated = await hiring.bulk_update_pipeline(
            db, campaign_id, payload.candidate_ids, payload.stage, _upload_times(db)
        )
    return {"updated": updated}
