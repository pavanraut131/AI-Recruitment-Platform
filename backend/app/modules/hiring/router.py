"""hiring HTTP layer: thin FastAPI routers over ``app.modules.hiring.service``.

Each endpoint parses the request, calls one service function and returns its result; the
service's :class:`~app.modules.hiring.service.HiringError` subclasses become ``HTTPException``\\ s
here. The routers keep the published contract identical to the pre-module app: the route
paths, tags, endpoint function names (they are the OpenAPI ``operationId``\\ s) and docstrings
(the OpenAPI descriptions) are all part of the snapshot in
``tests/characterization/__snapshots__/test_stats_health.ambr``. ``app.main`` includes them.

The endpoints that also need the ``ingestion`` module are composed in ``app.api`` instead:
campaign detail and stats, the global stats and, since the read switch (E0-19), every
candidate endpoint (they read the resumes' extraction snapshots).
"""

import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.hiring import service
from app.modules.hiring.models import Campaign
from app.modules.hiring.schemas import (
    CampaignCreate,
    CampaignResponse,
    CampaignUpdate,
    JDParseRequest,
    JDParseResponse,
)
from app.platform.database import get_db

campaigns_router = APIRouter(prefix="/campaigns", tags=["campaigns"])
parse_jd_router = APIRouter(prefix="/campaigns", tags=["campaigns"])

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
