"""``/api/v2/campaigns``: list, detail, update, delete and stats (hiring + ingestion).

A ``Campaign`` carries counts of its resumes (ingestion) and applications (hiring): hiring builds
the resource and asks ingestion for the resume tallies through ``ingestion.resume_tallies(db)``
(a port, as the v1 stats take ingestion's counts). Creating a campaign needs only hiring
(``app.modules.hiring.router_v2``). Deleting one deletes hiring's campaign and applications and
ingestion's resumes in one transaction, then the stored PDFs (as v1, spec §7.1).

Each endpoint declares its permission (EPIC-02 §5.2, §8.3) and passes the caller's
``AccessScope`` on: a campaign outside it is a 404, and the list's ``client_id`` filter narrows
it (:data:`ClientFilter`).
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
from app.modules.identity.dependencies import (
    Permission,
    access_scope,
    require_permission,
    tenant_db,
)
from app.modules.ingestion import service as ingestion
from app.platform.pagination import PageParams
from app.platform.problems import errors_as_http, problem_responses
from app.platform.tenancy import AccessScope

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

HIRING_ERRORS: dict[type[Exception], int] = {
    hiring.NotFoundError: status.HTTP_404_NOT_FOUND,
    hiring.InvalidInputError: status.HTTP_422_UNPROCESSABLE_CONTENT,
}

READ = Depends(require_permission(Permission.workspace_read))
WRITE = Depends(require_permission(Permission.campaign_write))
DELETE = Depends(require_permission(Permission.campaign_delete))

ClientFilter = Annotated[
    list[uuid.UUID] | None,
    Query(
        alias="client_id",
        max_length=100,
        description="Only campaigns of any of these clients (within the clients you have "
        "access to).",
    ),
]
"""The repeatable ``client_id`` filter of the campaign list, Home and stats (spec §8.3): it
narrows the caller's scope (``AccessScope.restricted_to``), never widens it."""


@router.get(
    "",
    operation_id="listCampaigns",
    response_model=CampaignPage,
    responses=problem_responses(422),
    dependencies=[READ],
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
    client_ids: ClientFilter = None,
    db: AsyncSession = Depends(tenant_db),
    scope: AccessScope = Depends(access_scope),
) -> CampaignPage:
    """Campaigns of the clients you have access to, newest first, with their counts."""
    query = hiring.CampaignQuery(statuses=[s.value for s in campaign_status or []], q=q)
    items, total = await hiring.list_campaigns_page(
        db,
        scope.restricted_to(client_ids),
        query,
        page.offset,
        page.limit,
        ingestion.resume_tallies(db),
    )
    return CampaignPage(items=items, total=total, page=page.page, page_size=page.page_size)


@router.get(
    "/{campaign_id}",
    operation_id="getCampaign",
    response_model=Campaign,
    responses=problem_responses(404, 422),
    dependencies=[READ],
)
async def get_campaign(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(tenant_db),
    scope: AccessScope = Depends(access_scope),
) -> Campaign:
    with errors_as_http(HIRING_ERRORS):
        return await hiring.get_campaign_v2(db, scope, campaign_id, ingestion.resume_tallies(db))


@router.patch(
    "/{campaign_id}",
    operation_id="updateCampaign",
    response_model=Campaign,
    responses=problem_responses(404, 422),
    dependencies=[WRITE],
)
async def update_campaign(
    campaign_id: uuid.UUID,
    payload: CampaignUpdate,
    db: AsyncSession = Depends(tenant_db),
    scope: AccessScope = Depends(access_scope),
) -> Campaign:
    """Change the fields sent (title, role, description, required skills, status, client).
    A new client must be an active client you have access to (422 otherwise)."""
    with errors_as_http(HIRING_ERRORS):
        return await hiring.update_campaign_v2(
            db, scope, campaign_id, payload, ingestion.resume_tallies(db)
        )


@router.delete(
    "/{campaign_id}",
    operation_id="deleteCampaign",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=problem_responses(404, 422),
    dependencies=[DELETE],
)
async def delete_campaign(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(tenant_db),
    scope: AccessScope = Depends(access_scope),
) -> None:
    """Delete the campaign with its applications, resumes and stored PDFs."""
    with errors_as_http(HIRING_ERRORS):
        await hiring.delete_campaign(db, scope, campaign_id)
    files = await ingestion.delete_campaign_resumes(db, campaign_id)
    await db.commit()
    await ingestion.delete_stored_files(files)


@router.get(
    "/{campaign_id}/stats",
    operation_id="getCampaignStats",
    response_model=CampaignStats,
    responses=problem_responses(404, 422),
    dependencies=[READ],
)
async def get_campaign_stats(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(tenant_db),
    scope: AccessScope = Depends(access_scope),
) -> CampaignStats:
    """The Insights numbers: resumes by status, applications by category and stage, mean
    score, score histogram and top skills."""
    resumes = await ingestion.resume_tally(db, scope, campaign_id)
    with errors_as_http(HIRING_ERRORS):
        return await hiring.campaign_insights(
            db, scope, campaign_id, resumes, ingestion.resume_profiles(db)
        )
