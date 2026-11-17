"""``/api/v2/campaigns``: list, detail, update, delete and stats (hiring + ingestion).

A ``Campaign`` carries counts of its resumes (ingestion) and applications (hiring): hiring builds
the resource and asks ingestion for the resume tallies through ``ingestion.resume_tallies(db)``
(a port, as the v1 stats take ingestion's counts). Creating a campaign needs only hiring
(``app.modules.hiring.router_v2``). Deleting one deletes hiring's campaign and applications and
ingestion's resumes in one transaction, then the stored PDFs (as v1, spec §7.1).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.hiring import service as hiring
from app.modules.hiring.schemas_v2 import (
    Campaign,
    CampaignPage,
    CampaignStats,
    CampaignStatus,
    CampaignUpdate,
)
from app.modules.ingestion import service as ingestion
from app.platform.database import get_db
from app.platform.pagination import PageParams
from app.platform.problems import errors_as_http, problem_responses

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

HIRING_ERRORS: dict[type[Exception], int] = {
    hiring.NotFoundError: status.HTTP_404_NOT_FOUND,
    hiring.InvalidInputError: status.HTTP_422_UNPROCESSABLE_ENTITY,
}


@router.get(
    "",
    operation_id="listCampaigns",
    response_model=CampaignPage,
    responses=problem_responses(422),
)
async def list_campaigns(
    page: Annotated[PageParams, Depends()],
    campaign_status: Annotated[
        list[CampaignStatus] | None,
        Query(alias="status", description="Only campaigns in any of these statuses."),
    ] = None,
    q: Annotated[
        str | None, Query(max_length=200, description="Contained in the title or the role.")
    ] = None,
    db: AsyncSession = Depends(get_db),
) -> CampaignPage:
    """Campaigns, newest first, with their counts."""
    query = hiring.CampaignQuery(statuses=[s.value for s in campaign_status or []], q=q)
    items, total = await hiring.list_campaigns_page(
        db, query, page.offset, page.limit, ingestion.resume_tallies(db)
    )
    return CampaignPage(items=items, total=total, page=page.page, page_size=page.page_size)


@router.get(
    "/{campaign_id}",
    operation_id="getCampaign",
    response_model=Campaign,
    responses=problem_responses(404, 422),
)
async def get_campaign(campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> Campaign:
    with errors_as_http(HIRING_ERRORS):
        return await hiring.get_campaign_v2(db, campaign_id, ingestion.resume_tallies(db))


@router.patch(
    "/{campaign_id}",
    operation_id="updateCampaign",
    response_model=Campaign,
    responses=problem_responses(404, 422),
)
async def update_campaign(
    campaign_id: uuid.UUID, payload: CampaignUpdate, db: AsyncSession = Depends(get_db)
) -> Campaign:
    """Change the fields sent (title, role, description, required skills, status)."""
    with errors_as_http(HIRING_ERRORS):
        return await hiring.update_campaign_v2(
            db, campaign_id, payload, ingestion.resume_tallies(db)
        )


@router.delete(
    "/{campaign_id}",
    operation_id="deleteCampaign",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=problem_responses(404, 422),
)
async def delete_campaign(campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    """Delete the campaign with its applications, resumes and stored PDFs."""
    with errors_as_http(HIRING_ERRORS):
        await hiring.delete_campaign(db, campaign_id)
    files = await ingestion.delete_campaign_resumes(db, campaign_id)
    await db.commit()
    await ingestion.delete_stored_files(files)


@router.get(
    "/{campaign_id}/stats",
    operation_id="getCampaignStats",
    response_model=CampaignStats,
    responses=problem_responses(404, 422),
)
async def get_campaign_stats(
    campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> CampaignStats:
    """The Insights numbers: resumes by status, applications by category and stage, mean
    score, score histogram and top skills."""
    resumes = await ingestion.resume_tally(db, campaign_id)
    with errors_as_http(HIRING_ERRORS):
        return await hiring.campaign_insights(
            db, campaign_id, resumes, ingestion.resume_profiles(db)
        )
