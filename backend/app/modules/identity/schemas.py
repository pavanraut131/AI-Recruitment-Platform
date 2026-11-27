"""identity data contracts: what callers get back from ``identity.service`` and
``identity.dependencies``.

Public: the composition layer and the v2 routers may import these (import-linter allows
``app.modules.identity.schemas``). The ``/api/v2`` resources are in ``schemas_v2``.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.platform.tenancy import AccessScope


class Role(str, enum.Enum):
    """An agency role (spec §5.1). What each may do is E2-06's permission matrix."""

    owner = "owner"
    admin = "admin"
    recruiter = "recruiter"
    viewer = "viewer"


class AgencySummary(BaseModel):
    """An agency as other code sees it."""

    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ActiveMembership:
    """The signed-in user's membership in the session's active agency (active, in an active
    agency: anything else is no membership at all)."""

    id: uuid.UUID
    agency_id: uuid.UUID
    agency_name: str
    role: Role
    all_clients: bool
    client_ids: frozenset[uuid.UUID]

    @property
    def scope(self) -> AccessScope:
        return AccessScope(self.agency_id, None if self.all_clients else self.client_ids)


@dataclass(frozen=True, slots=True)
class SignedIn:
    """A valid session (not revoked, not expired, its user active), read live on each request.

    ``token`` is the cookie value of this request, kept only in memory (it signs the CSRF token
    and is never stored or logged). ``membership`` is ``None`` when the session has no active
    agency or the user's access to it has ended.
    """

    token: str
    id_hash: bytes
    user_id: uuid.UUID
    email: str
    display_name: str
    active_agency_id: uuid.UUID | None
    absolute_expires_at: datetime
    membership: ActiveMembership | None


@dataclass(frozen=True, slots=True)
class Principal:
    """Who a ``/api/v2`` request acts as, in which agency (spec §5.4).

    ``session`` and ``user_id`` are ``None`` only for the development fallback
    (``AUTH_REQUIRED=false``: a request without a session acts as the default agency's owner).
    """

    agency_id: uuid.UUID
    role: Role
    scope: AccessScope
    user_id: uuid.UUID | None
    membership_id: uuid.UUID | None
    session: SignedIn | None

    @property
    def is_fallback(self) -> bool:
        return self.session is None
