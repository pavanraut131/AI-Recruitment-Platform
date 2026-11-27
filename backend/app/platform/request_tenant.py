"""The tenant of an HTTP request (EPIC-02 spec §6.3, §7.4).

``/api/v1`` is unauthenticated and stays bound to the **default agency** until it is removed
(E2-12): its endpoints take :func:`default_agency_db` (instead of ``get_db``) and
:func:`default_agency_scope`.

``/api/v2`` takes its session and scope from the signed-in principal instead
(``app.modules.identity.dependencies``: ``tenant_db``, ``access_scope``; E2-05), which binds the
session to the session's active agency through :func:`bind_request`.

Both bind ``agency_id`` into the structlog context of the request, so every log line of the
request names its agency (spec §6.6). Tests that override ``get_db`` keep working: the session
the override yields is bound here.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.database import get_db
from app.platform.tenancy import DEFAULT_AGENCY_ID, AccessScope, bind


def bind_request(db: AsyncSession, agency_id: uuid.UUID) -> AsyncSession:
    """Bind the request's session to ``agency_id`` and name the agency in its log lines."""
    bind(db, agency_id)
    structlog.contextvars.bind_contextvars(agency_id=str(agency_id))
    return db


async def default_agency_db(db: AsyncSession = Depends(get_db)) -> AsyncSession:
    """The request's session bound to the default agency (``/api/v1`` only)."""
    return bind_request(db, DEFAULT_AGENCY_ID)


def default_agency_scope() -> AccessScope:
    """Every client of the default agency (``/api/v1`` only)."""
    return AccessScope(DEFAULT_AGENCY_ID)
