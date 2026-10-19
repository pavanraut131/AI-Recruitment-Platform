"""Endpoints composed from the ``hiring`` and ``ingestion`` services (see the package docstring).

- Campaign detail, campaign stats and the global stats: hiring aggregates its own numbers plus
  the resume counts by status (and, for the top skills, the resumes' extractions) that
  ingestion reports.
- Every legacy candidate endpoint (E0-19, spec §7): hiring serves them from ``applications``
  and builds the legacy response from each application and the extraction snapshot of its
  current resume, which ingestion owns. The endpoint hands hiring ingestion's lookup
  (``ingestion.resume_profiles``), nothing more.
- Candidate delete and bulk delete: hiring deletes the applications, ingestion deletes their
  resumes, and both are committed in one transaction (the request's ``AsyncSession``).

Deleting a campaign stays in hiring: its resumes go with it through the database's
``resumes.campaign_id ON DELETE CASCADE``. When that cross-module foreign key is dropped
(ADR 0003), the campaign delete moves here and asks ingestion to delete the resumes.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.hiring import service as hiring
from app.modules.hiring.schemas import (
    BulkDeleteRequest,
    BulkPipelineUpdate,
    CampaignResponse,
    CampaignStats,
    CandidateCategoryUpdate,
    CandidateDetail,
    CandidateListItem,
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


async def _campaign_stats(db: AsyncSession, campaign_id: uuid.UUID) -> CampaignStats:
    resume_counts = await ingestion.resume_status_counts(db, campaign_id)
    return await hiring.campaign_stats(
        db, campaign_id, resume_counts, ingestion.resume_profiles(db)
    )


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


# ── Candidates: list ──────────────────────────────────────────
@candidates_router.get(
    "/campaigns/{campaign_id}/candidates", response_model=list[CandidateListItem]
)
async def list_candidates(
    campaign_id: uuid.UUID,
    skill: str | None = Query(None),
    category: str | None = Query(None),
    min_score: float | None = Query(None, ge=0, le=100),
    max_score: float | None = Query(None, ge=0, le=100),
    min_experience: float | None = Query(None, ge=0),
    search: str | None = Query(None),
    pipeline_stage: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> list[CandidateListItem]:
    filters = hiring.CandidateFilters(
        campaign_id=campaign_id,
        skill=skill,
        category=category,
        min_score=min_score,
        max_score=max_score,
        min_experience=min_experience,
        search=search,
        pipeline_stage=pipeline_stage,
    )
    return await hiring.list_candidates(db, filters, page, page_size, ingestion.resume_profiles(db))


# ── Candidates: CSV export ────────────────────────────────────
# NOTE: must be declared before /{candidate_id} to avoid routing conflict


@candidates_router.get("/campaigns/{campaign_id}/candidates/export")
async def export_candidates_csv(
    campaign_id: uuid.UUID,
    category: str | None = Query(None),
    min_score: float | None = Query(None, ge=0, le=100),
    max_score: float | None = Query(None, ge=0, le=100),
    search: str | None = Query(None),
    pipeline_stage: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """
    Download a filtered candidate list as CSV.
    Applies the same filters as the list endpoint.
    """
    # BUG (recorded, B8): the export accepts no `skill` / `min_experience`, so it can return
    # more rows than the list shows for the same filters. Fixing it is a separate ticket.
    filters = hiring.CandidateFilters(
        campaign_id=campaign_id,
        category=category,
        min_score=min_score,
        max_score=max_score,
        search=search,
        pipeline_stage=pipeline_stage,
    )
    csv_text = await hiring.export_candidates_csv(db, filters, ingestion.resume_profiles(db))
    return StreamingResponse(
        iter([csv_text]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=candidates_{campaign_id}.csv"},
    )


# ── Candidates: get one ──────────────────────────────
@candidates_router.get("/candidates/{candidate_id}", response_model=CandidateDetail)
async def get_candidate(
    candidate_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> CandidateDetail:
    with _not_found_as_404():
        return await hiring.get_candidate(db, candidate_id, ingestion.resume_profiles(db))


@candidates_router.get("/resumes/{resume_id}/candidate", response_model=CandidateDetail)
async def get_candidate_by_resume(
    resume_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> CandidateDetail:
    with _not_found_as_404():
        return await hiring.get_candidate_by_resume(db, resume_id, ingestion.resume_profiles(db))


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
    deleted = await hiring.bulk_delete_candidates(db, campaign_id, payload.candidate_ids)
    if deleted:
        await ingestion.delete_application_resumes(db, deleted)
    await db.commit()
    return {"deleted": len(deleted)}


@candidates_router.delete("/candidates/{candidate_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_candidate(candidate_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    with _not_found_as_404():
        deleted = await hiring.delete_candidate(db, candidate_id)
    await ingestion.delete_application_resumes(db, [deleted])
    await db.commit()


# ── Candidates: recruiter updates ─────────────────────────────
@candidates_router.put("/candidates/{candidate_id}/category", response_model=CandidateDetail)
async def update_candidate_category(
    candidate_id: uuid.UUID,
    payload: CandidateCategoryUpdate,
    db: AsyncSession = Depends(get_db),
) -> CandidateDetail:
    with _hiring_errors():
        return await hiring.update_candidate_category(
            db, candidate_id, payload.category, ingestion.resume_profiles(db)
        )


@candidates_router.patch("/candidates/{candidate_id}/notes", response_model=CandidateDetail)
async def update_candidate_notes(
    candidate_id: uuid.UUID,
    payload: CandidateNotesUpdate,
    db: AsyncSession = Depends(get_db),
) -> CandidateDetail:
    with _hiring_errors():
        return await hiring.update_candidate_notes(
            db, candidate_id, payload.notes, ingestion.resume_profiles(db)
        )


@candidates_router.patch("/candidates/{candidate_id}/pipeline", response_model=CandidateDetail)
async def update_candidate_pipeline(
    candidate_id: uuid.UUID,
    payload: CandidatePipelineUpdate,
    db: AsyncSession = Depends(get_db),
) -> CandidateDetail:
    with _hiring_errors():
        return await hiring.update_candidate_pipeline(
            db, candidate_id, payload.stage, ingestion.resume_profiles(db)
        )


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
            db, campaign_id, payload.candidate_ids, payload.stage
        )
    return {"updated": updated}
