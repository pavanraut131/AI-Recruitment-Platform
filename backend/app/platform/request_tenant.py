"""The tenant of an HTTP request (EPIC-02 spec §5.4, §6.3): FastAPI dependencies that give the
endpoints a database session bound to the request's agency, and the caller's access scope.

``/api/v2`` endpoints take :func:`tenant_db` (instead of ``get_db``) and :func:`access_scope`.
Both come from :func:`request_tenant`, the one place that decides the request's agency: the
default agency until E2-05, when the signed-in user's active agency replaces it (and these
dependencies move to ``app.modules.identity.dependencies``).

``/api/v1`` endpoints take :func:`default_agency_db` and :func:`default_agency_scope`: v1 is
unauthenticated and stays bound to the default agency until it is removed (E2-12, spec §7.4).

Each binds ``agency_id`` into the structlog context of the request, so every log line of the
request names its agency (spec §6.6). Tests that override ``get_db`` keep working: the session
the override yields is bound here.
"""

from __future__ import annotations

import structlog
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.database import get_db
from app.platform.tenancy import DEFAULT_AGENCY_ID, AccessScope, TenantContext, bind


def request_tenant() -> TenantContext:
    """The agency this ``/api/v2`` request runs for: the default agency until E2-05."""
    return TenantContext(DEFAULT_AGENCY_ID)


def _bound(db: AsyncSession, tenant: TenantContext) -> AsyncSession:
    bind(db, tenant.agency_id)
    structlog.contextvars.bind_contextvars(agency_id=str(tenant.agency_id))
    return db


async def tenant_db(
    db: AsyncSession = Depends(get_db), tenant: TenantContext = Depends(request_tenant)
) -> AsyncSession:
    """The request's session (``get_db``), bound to the request's agency."""
    return _bound(db, tenant)


def access_scope(tenant: TenantContext = Depends(request_tenant)) -> AccessScope:
    """What the caller may see: every client of the request's agency (until E2-06)."""
    return AccessScope(tenant.agency_id)


# ── /api/v1 (until E2-12) ─────────────────────────────────────
DEFAULT_TENANT = TenantContext(DEFAULT_AGENCY_ID)


async def default_agency_db(db: AsyncSession = Depends(get_db)) -> AsyncSession:
    """The request's session bound to the default agency (``/api/v1`` only)."""
    return _bound(db, DEFAULT_TENANT)


def default_agency_scope() -> AccessScope:
    """Every client of the default agency (``/api/v1`` only)."""
    return AccessScope(DEFAULT_AGENCY_ID)
