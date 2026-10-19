"""hiring HTTP layer: thin FastAPI routers over ``app.modules.hiring.service``.

Each endpoint parses the request, calls one service function and returns its result; the
service's :class:`~app.modules.hiring.service.HiringError` subclasses become ``HTTPException``\\ s
here. Three routers keep the published contract identical to the pre-module app: the route
paths, tags, endpoint function names (they are the OpenAPI ``operationId``\\ s) and docstrings
(the OpenAPI descriptions) are all part of the snapshot in
``tests/characterization/__snapshots__/test_stats_health.ambr``. ``app.main`` includes them.

The endpoints that also need the ``ingestion`` module (campaign detail and stats, the global
stats, candidate deletes, and since E0-18 the recruiter updates, which mirror onto applications
and need the resumes' upload times) are composed in ``app.api`` instead.
"""

import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.hiring import service
from app.modules.hiring.models import Campaign, Candidate
from app.modules.hiring.schemas import (
    CampaignCreate,
    CampaignResponse,
    CampaignUpdate,
    CandidateDetail,
    CandidateListItem,
    JDParseRequest,
    JDParseResponse,
)
from app.platform.database import get_db

campaigns_router = APIRouter(prefix="/campaigns", tags=["campaigns"])
parse_jd_router = APIRouter(prefix="/campaigns", tags=["campaigns"])
candidates_router = APIRouter(tags=["candidates"])

_STATUS_CODES: dict[type[service.HiringError], int] = {
    service.NotFoundError: status.HTTP_404_NOT_FOUND,
    service.InvalidInputError: status.HTTP_400_BAD_REQUEST,
    service.JDParseError: status.HTTP_500_INTERNAL_SERVER_ERROR,
}


@contextmanager
def _http_errors() -> Iterator[None]:
    """Turn a service error into the ``HTTPException`` the endpoint has always returned."""
    try:
        yield
    except service.HiringError as e:
        raise HTTPException(status_code=_STATUS_CODES[type(e)], detail=e.detail) from e


# ── Campaigns ─────────────────────────────────────────────────
@campaigns_router.post("", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED)
async def create_campaign(payload: CampaignCreate, db: AsyncSession = Depends(get_db)) -> Campaign:
    return await service.create_campaign(db, payload)


@campaigns_router.get("", response_model=list[CampaignResponse])
async def list_campaigns(
    skip: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
) -> Sequence[Campaign]:
    return await service.list_campaigns(db, skip, limit)


@campaigns_router.put("/{campaign_id}", response_model=CampaignResponse)
async def update_campaign(
    campaign_id: uuid.UUID,
    payload: CampaignUpdate,
    db: AsyncSession = Depends(get_db),
) -> Campaign:
    with _http_errors():
        return await service.update_campaign(db, campaign_id, payload)


@campaigns_router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_campaign(campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    with _http_errors():
        await service.delete_campaign(db, campaign_id)


# ── Job description parsing ───────────────────────────────────
@parse_jd_router.post("/parse-jd", response_model=JDParseResponse)
async def parse_job_description(payload: JDParseRequest) -> JDParseResponse:
    with _http_errors():
        return await service.parse_job_description(payload)


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
) -> Sequence[Candidate]:
    filters = service.CandidateFilters(
        campaign_id=campaign_id,
        skill=skill,
        category=category,
        min_score=min_score,
        max_score=max_score,
        min_experience=min_experience,
        search=search,
        pipeline_stage=pipeline_stage,
    )
    return await service.list_candidates(db, filters, page, page_size)


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
    filters = service.CandidateFilters(
        campaign_id=campaign_id,
        category=category,
        min_score=min_score,
        max_score=max_score,
        search=search,
        pipeline_stage=pipeline_stage,
    )
    csv_text = await service.export_candidates_csv(db, filters)
    return StreamingResponse(
        iter([csv_text]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=candidates_{campaign_id}.csv"},
    )


# ── Candidates: get one ──────────────────────────────
@candidates_router.get("/candidates/{candidate_id}", response_model=CandidateDetail)
async def get_candidate(candidate_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> Candidate:
    with _http_errors():
        return await service.get_candidate(db, candidate_id)


@candidates_router.get("/resumes/{resume_id}/candidate", response_model=CandidateDetail)
async def get_candidate_by_resume(
    resume_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> Candidate:
    with _http_errors():
        return await service.get_candidate_by_resume(db, resume_id)
