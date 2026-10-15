"""ingestion HTTP layer: thin FastAPI router over ``app.modules.ingestion.service``.

Upload, resume list/detail, retry-failed and the SSE progress stream. Moved from
``app/api/v1/resumes.py`` (E0-10) with the published contract unchanged: route paths, the
``resumes`` tag, endpoint function names (OpenAPI ``operationId``\\ s), docstrings (OpenAPI
descriptions) and response models are pinned by ``tests/characterization``.
"""

import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.modules.ingestion import events, service
from app.modules.ingestion.models import Resume
from app.modules.ingestion.schemas import ResumeResponse, UploadBatchResponse
from app.platform.database import get_db

router = APIRouter(tags=["resumes"])

_STATUS_CODES: dict[type[service.IngestionError], int] = {
    service.NotFoundError: status.HTTP_404_NOT_FOUND,
    service.InvalidInputError: status.HTTP_400_BAD_REQUEST,
}


@contextmanager
def _http_errors() -> Iterator[None]:
    """Turn a service error into the ``HTTPException`` the endpoint has always returned."""
    try:
        yield
    except service.IngestionError as e:
        raise HTTPException(status_code=_STATUS_CODES[type(e)], detail=e.detail) from e


@router.post(
    "/campaigns/{campaign_id}/upload",
    response_model=UploadBatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def bulk_upload_resumes(
    campaign_id: uuid.UUID,
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
) -> UploadBatchResponse:
    with _http_errors():
        return await service.upload_resumes(db, campaign_id, files)


@router.get("/campaigns/{campaign_id}/resumes", response_model=list[ResumeResponse])
async def list_campaign_resumes(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Sequence[Resume]:
    return await service.list_resumes(db, campaign_id)


@router.get("/resumes/{resume_id}", response_model=ResumeResponse)
async def get_resume(resume_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> Resume:
    with _http_errors():
        return await service.get_resume(db, resume_id)


# response_model=None: no response schema was ever published, and FastAPI would otherwise
# derive one from the dict[str, int] return annotation.
@router.post("/campaigns/{campaign_id}/resumes/retry-failed", response_model=None)
async def retry_failed_resumes(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Re-queue all failed and quota_exceeded resumes for a campaign."""
    return {"retried": await service.retry_failed(db, campaign_id)}


@router.get("/campaigns/{campaign_id}/stream")
async def stream_campaign_status(campaign_id: uuid.UUID) -> EventSourceResponse:
    """
    SSE endpoint — streams real-time processing updates for a campaign.
    Frontend subscribes and receives events as each resume finishes.
    """
    return EventSourceResponse(events.campaign_event_stream(campaign_id))
