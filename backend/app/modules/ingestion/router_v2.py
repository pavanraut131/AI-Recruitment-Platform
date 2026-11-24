"""ingestion HTTP layer of ``/api/v2``: uploads, the resume list, retrying failed resumes and the
per-campaign progress stream (EPIC-01 spec §5.3, §6).

Thin, like ``router.py``: each endpoint calls one ``service`` function. Errors become RFC 9457
problem details through the v2 application's handlers (``app.platform.problems``); every
operation has an explicit, stable ``operation_id`` (the generated client's function names).
Ingestion may call hiring, so these endpoints check the campaign themselves (404 for an unknown
one, where v1 answers 200 with nothing: B7).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.modules.ingestion import service
from app.modules.ingestion.schemas_v2 import ResumePage, ResumeStatus, RetryResult, UploadResult
from app.platform.pagination import PageParams
from app.platform.problems import errors_as_http, problem_responses
from app.platform.request_tenant import access_scope, tenant_db
from app.platform.tenancy import AccessScope
from app.platform.uploads import UploadRoute

# UploadRoute: the upload body is parsed with explicit limits (app.platform.uploads, S1).
router = APIRouter(prefix="/campaigns/{campaign_id}", tags=["uploads"], route_class=UploadRoute)

STATUS_CODES: dict[type[Exception], int] = {
    service.NotFoundError: status.HTTP_404_NOT_FOUND,
    # Too many files: a request that can never succeed as sent (v1 answers 400).
    service.InvalidInputError: status.HTTP_422_UNPROCESSABLE_CONTENT,
}


@router.post(
    "/uploads",
    operation_id="uploadResumes",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=UploadResult,
    responses=problem_responses(400, 404, 422),
)
async def upload_resumes(
    campaign_id: uuid.UUID,
    files: Annotated[list[UploadFile], File(description="PDF resumes (multipart field files).")],
    db: AsyncSession = Depends(tenant_db),
    scope: AccessScope = Depends(access_scope),
) -> UploadResult:
    """Upload PDFs to a campaign. Each accepted file is stored and queued for processing; a
    file that is not a PDF, too large, or a duplicate of one already processed or in progress
    is listed in ``rejected`` with the reason. Too many files is a 422. A body that is not
    parsed is a 400: malformed, or over a parsing limit (a non-file field over 16 KiB, more than
    10 non-file fields, more files than the batch limit plus one)."""
    with errors_as_http(STATUS_CODES):
        return await service.upload_resumes_v2(db, scope, campaign_id, files)


@router.get(
    "/resumes",
    operation_id="listCampaignResumes",
    response_model=ResumePage,
    responses=problem_responses(404, 422),
)
async def list_campaign_resumes(
    campaign_id: uuid.UUID,
    page: Annotated[PageParams, Depends()],
    resume_status: Annotated[
        list[ResumeStatus] | None,
        Query(alias="status", description="Only resumes in any of these statuses."),
    ] = None,
    db: AsyncSession = Depends(tenant_db),
    scope: AccessScope = Depends(access_scope),
) -> ResumePage:
    """The campaign's resumes, newest upload first."""
    statuses = [s.value for s in resume_status or []]
    with errors_as_http(STATUS_CODES):
        items, total = await service.list_resumes_page(
            db, scope, campaign_id, statuses, page.offset, page.limit
        )
    return ResumePage(items=items, total=total, page=page.page, page_size=page.page_size)


@router.post(
    "/resumes/retry-failed",
    operation_id="retryFailedResumes",
    response_model=RetryResult,
    responses=problem_responses(404, 422),
)
async def retry_failed_resumes(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(tenant_db),
    scope: AccessScope = Depends(access_scope),
) -> RetryResult:
    """Queue the campaign's failed and quota-exceeded resumes again; each resumes from the
    step it stopped at."""
    with errors_as_http(STATUS_CODES):
        return await service.retry_failed_v2(db, scope, campaign_id)


@router.get(
    "/events",
    operation_id="streamCampaignEvents",
    response_class=EventSourceResponse,
    responses={
        200: {
            "description": "Server-Sent Events: first `connected` (data: ConnectedEvent), then "
            "`resume_update` (data: ResumeEvent) each time one of the campaign's resumes changes "
            "status. Only this campaign's resumes.",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
        **problem_responses(404, 422),
    },
)
async def stream_campaign_events(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(tenant_db),
    scope: AccessScope = Depends(access_scope),
) -> EventSourceResponse:
    """Live processing progress of the campaign's resumes (one connection per open campaign)."""
    with errors_as_http(STATUS_CODES):
        stream = await service.open_campaign_stream(db, scope, campaign_id)
    return EventSourceResponse(stream)
