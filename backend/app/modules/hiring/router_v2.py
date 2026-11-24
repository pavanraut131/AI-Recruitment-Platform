"""hiring HTTP layer of ``/api/v2``: the endpoints that need only the hiring module.

Thin, like ``router.py``: each endpoint calls one ``service`` function and returns its resource.
Errors become RFC 9457 problem details through the v2 application's handlers
(``app.platform.problems``). Every operation has an explicit, stable ``operation_id``: the
generated TypeScript client uses them as function names (EPIC-01 spec §6).

Most campaign and application endpoints also need ingestion (resume counts, the resumes'
extraction snapshots, deleting resumes), so they are composed in ``app.api.v2`` (ADR 0005). Here:
creating a campaign (a new campaign has no resumes, so its counts are zero) and parsing a job
description.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.hiring import service
from app.modules.hiring.schemas_v2 import (
    Campaign,
    CampaignCreate,
    JobDescription,
    ParsedJobDescription,
)
from app.modules.identity.dependencies import access_scope, tenant_db
from app.platform.problems import errors_as_http, problem_responses
from app.platform.tenancy import AccessScope

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

STATUS_CODES: dict[type[Exception], int] = {
    service.NotFoundError: status.HTTP_404_NOT_FOUND,
    service.InvalidInputError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    service.JDParseError: status.HTTP_502_BAD_GATEWAY,
}


@router.post(
    "",
    operation_id="createCampaign",
    status_code=status.HTTP_201_CREATED,
    response_model=Campaign,
    responses=problem_responses(422),
)
async def create_campaign(
    payload: CampaignCreate,
    db: AsyncSession = Depends(tenant_db),
    scope: AccessScope = Depends(access_scope),
) -> Campaign:
    """Create a campaign (status ``active`` unless given)."""
    return await service.create_campaign_v2(db, scope, payload)


@router.post(
    "/parse-jd",
    operation_id="parseJobDescription",
    response_model=ParsedJobDescription,
    responses=problem_responses(422, 502),
)
async def parse_job_description(payload: JobDescription) -> ParsedJobDescription:
    """Extract the role title, seniority and skills from a job description with the AI.
    502 when the AI provider is not configured or its call fails."""
    with errors_as_http(STATUS_CODES):
        return await service.parse_job_description_v2(payload)
