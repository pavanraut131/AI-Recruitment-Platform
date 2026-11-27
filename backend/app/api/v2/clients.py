"""``/api/v2/clients``: creating, renaming and archiving clients (hiring + identity).

Hiring owns clients; the reads are hiring's own endpoints (``app.modules.hiring.router_v2``).
Changes are composed here because each records an audit event (identity, EPIC-02 spec §4.7) in
the same transaction as the change: hiring flushes the client, identity adds the event, and the
endpoint commits both. They need ``client:manage`` (owners and admins, who always have every
client).
"""

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.hiring import service as hiring
from app.modules.hiring.schemas_v2 import Client, ClientCreate, ClientUpdate
from app.modules.identity import service as identity
from app.modules.identity.dependencies import (
    Permission,
    access_scope,
    client_info,
    require_permission,
    tenant_db,
)
from app.modules.identity.schemas import Principal
from app.platform.problems import errors_as_http, problem_responses
from app.platform.tenancy import AccessScope

router = APIRouter(prefix="/clients", tags=["clients"])

HIRING_ERRORS: dict[type[Exception], int] = {
    hiring.NotFoundError: status.HTTP_404_NOT_FOUND,
    hiring.ConflictError: status.HTTP_409_CONFLICT,
    hiring.InvalidInputError: status.HTTP_422_UNPROCESSABLE_CONTENT,
}

MANAGE = Depends(require_permission(Permission.client_manage))


@router.post(
    "",
    operation_id="createClient",
    status_code=status.HTTP_201_CREATED,
    response_model=Client,
    responses=problem_responses(409, 422),
)
async def create_client(
    payload: ClientCreate,
    db: AsyncSession = Depends(tenant_db),
    scope: AccessScope = Depends(access_scope),
    principal: Principal = MANAGE,
    client: identity.ClientInfo = Depends(client_info),
) -> Client:
    """Add an active client. 409 when the agency already has a client with this name (ignoring
    case, archived ones included)."""
    with errors_as_http(HIRING_ERRORS):
        created = await hiring.create_client(db, scope, payload)
    await identity.record_audit(
        db,
        "client.created",
        agency_id=principal.agency_id,
        actor_user_id=principal.user_id,
        target_type="client",
        target_id=created.id,
        client=client,
        details={"name": created.name},
    )
    await db.commit()
    return created


@router.patch(
    "/{client_id}",
    operation_id="updateClient",
    response_model=Client,
    responses=problem_responses(404, 409, 422),
)
async def update_client(
    client_id: uuid.UUID,
    payload: ClientUpdate,
    db: AsyncSession = Depends(tenant_db),
    scope: AccessScope = Depends(access_scope),
    principal: Principal = MANAGE,
    client: identity.ClientInfo = Depends(client_info),
) -> Client:
    """Rename the client, archive it (``status: archived``: hidden from pickers, its campaigns
    keep it) or restore it. 409 for a name another client of the agency has."""
    with errors_as_http(HIRING_ERRORS):
        change = await hiring.update_client(db, scope, client_id, payload)
    if change.changes:
        await identity.record_audit(
            db,
            "client.updated",
            agency_id=principal.agency_id,
            actor_user_id=principal.user_id,
            target_type="client",
            target_id=client_id,
            client=client,
            details={
                name: {"old": old, "new": new}
                for name, (old, new) in sorted(change.changes.items())
            },
        )
    await db.commit()
    return change.client
