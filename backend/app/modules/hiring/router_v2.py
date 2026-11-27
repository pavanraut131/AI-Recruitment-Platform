"""hiring HTTP layer of ``/api/v2``: the endpoints that need only the hiring module.

Thin, like ``router.py``: each endpoint calls one ``service`` function and returns its resource.
Errors become RFC 9457 problem details through the v2 application's handlers
(``app.platform.problems``). Every operation has an explicit, stable ``operation_id``: the
generated TypeScript client uses them as function names (EPIC-01 spec §6), and declares the
permission it needs (``require_permission``, EPIC-02 spec §5.2).

Most campaign and application endpoints also need ingestion (resume counts, the resumes'
extraction snapshots, deleting resumes), so they are composed in ``app.api.v2`` (ADR 0005). Here:
creating a campaign (a new campaign has no resumes, so its counts are zero), parsing a job
description, and reading clients. Creating and changing clients is composed
(``app.api.v2.clients``), because it records audit events (identity).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.hiring import service
from app.modules.hiring.schemas_v2 import (
    Campaign,
    CampaignCreate,
    Client,
    ClientPage,
    ClientStatus,
    JobDescription,
    ParsedJobDescription,
)
from app.modules.identity.dependencies import (
    Permission,
    access_scope,
    require_permission,
    tenant_db,
)
from app.modules.identity.schemas import Principal
from app.platform.pagination import PageParams
from app.platform.problems import errors_as_http, problem_responses
from app.platform.tenancy import AccessScope

router = APIRouter(prefix="/campaigns", tags=["campaigns"])
clients_router = APIRouter(prefix="/clients", tags=["clients"])

routers = (router, clients_router)

STATUS_CODES: dict[type[Exception], int] = {
    service.NotFoundError: status.HTTP_404_NOT_FOUND,
    service.InvalidInputError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    service.JDParseError: status.HTTP_502_BAD_GATEWAY,
}

CAMPAIGN_WRITE = Depends(require_permission(Permission.campaign_write))
CLIENT_READ = Depends(require_permission(Permission.client_read))


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
    principal: Principal = CAMPAIGN_WRITE,
) -> Campaign:
    """Create a campaign (status ``active`` unless given) for the client ``client_id`` names, or
    your default client. 422 for a client that is unknown, archived or not one of yours."""
    with errors_as_http(STATUS_CODES):
        return await service.create_campaign_v2(db, scope, payload, principal.user_id)


@router.post(
    "/parse-jd",
    operation_id="parseJobDescription",
    response_model=ParsedJobDescription,
    responses=problem_responses(422, 502),
    dependencies=[CAMPAIGN_WRITE],
)
async def parse_job_description(payload: JobDescription) -> ParsedJobDescription:
    """Extract the role title, seniority and skills from a job description with the AI.
    502 when the AI provider is not configured or its call fails."""
    with errors_as_http(STATUS_CODES):
        return await service.parse_job_description_v2(payload)


# ── /clients (reads) ──────────────────────────────────────────
@clients_router.get(
    "",
    operation_id="listClients",
    response_model=ClientPage,
    responses=problem_responses(422),
    dependencies=[CLIENT_READ],
)
async def list_clients(
    page: Annotated[PageParams, Depends()],
    client_status: Annotated[
        list[ClientStatus] | None,
        Query(alias="status", description="Only clients in any of these statuses."),
    ] = None,
    q: Annotated[str | None, Query(max_length=200, description="Contained in the name.")] = None,
    db: AsyncSession = Depends(tenant_db),
    scope: AccessScope = Depends(access_scope),
) -> ClientPage:
    """The agency's clients you have access to, by name (ignoring case)."""
    query = service.ClientQuery(statuses=[s.value for s in client_status or []], q=q)
    items, total = await service.list_clients(db, scope, query, page.offset, page.limit)
    return ClientPage(items=items, total=total, page=page.page, page_size=page.page_size)


@clients_router.get(
    "/{client_id}",
    operation_id="getClient",
    response_model=Client,
    responses=problem_responses(404, 422),
    dependencies=[CLIENT_READ],
)
async def get_client(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(tenant_db),
    scope: AccessScope = Depends(access_scope),
) -> Client:
    """One client (404 unless it is one you have access to)."""
    with errors_as_http(STATUS_CODES):
        return await service.get_client(db, scope, client_id)
