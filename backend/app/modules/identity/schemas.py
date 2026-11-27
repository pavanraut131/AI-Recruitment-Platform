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


class OidcProvider(str, enum.Enum):
    """A sign-in provider (OpenID Connect, spec §4.3; E2-08). Offered only when configured."""

    google = "google"
    microsoft = "microsoft"


class OidcLoginError(str, enum.Enum):
    """Why a Google or Microsoft sign-in signed nobody in: the `error` of the callback's redirect
    to `{FRONTEND_URL}/login?error=…&provider=…` (E2-08). The login page words each one; none
    says more than the person who signed in at the provider may know."""

    # No HireFlow account for the provider's (verified) address: ask the agency for an invite.
    no_account = "no_account"
    # The account is disabled, or is no longer an active member of an active agency.
    no_access = "no_access"
    # The provider does not vouch for the address (Google email_verified, Microsoft xms_edov),
    # so it cannot be used to find the account; sign in with the password, or be invited.
    email_not_verified = "email_not_verified"
    # Accepting an invitation: it expired, was revoked, replaced or already used.
    invitation_invalid = "invitation_invalid"
    # Accepting an invitation: the identity signs in to an account with another address.
    invitation_mismatch = "invitation_mismatch"
    # Accepting an invitation: its address has an account the provider does not vouch for;
    # sign in with the password first, then accept.
    account_exists = "account_exists"
    # Accepting an invitation to an agency the account is already a member of.
    already_member = "already_member"
    # The person cancelled at the provider.
    cancelled = "cancelled"
    # The sign-in took longer than 10 minutes, or this browser did not start it.
    expired = "expired"
    # Anything else (the provider's answer could not be verified); try again.
    oidc_failed = "oidc_failed"
    # Too many sign-ins from this network; try again in a few minutes.
    rate_limited = "rate_limited"


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


# ── Team (E2-07) ──────────────────────────────────────────────
class MemberStatus(str, enum.Enum):
    """A membership's status: a deactivated member keeps their account but has no access to
    the agency until reactivated."""

    active = "active"
    deactivated = "deactivated"


class InvitationStatus(str, enum.Enum):
    """An open invitation (not accepted, not revoked): ``pending`` until it expires, then
    ``expired`` (resend it to renew it)."""

    pending = "pending"
    expired = "expired"


@dataclass(frozen=True, slots=True)
class TeamUser:
    id: uuid.UUID
    email: str
    display_name: str


@dataclass(frozen=True, slots=True)
class TeamMember:
    """A membership of the agency with its user. ``client_ids`` is empty when ``all_clients``
    (the composition layer names the clients)."""

    id: uuid.UUID
    user: TeamUser
    role: Role
    all_clients: bool
    client_ids: tuple[uuid.UUID, ...]
    status: MemberStatus
    last_active_at: datetime | None
    joined_at: datetime
    deactivated_at: datetime | None


@dataclass(frozen=True, slots=True)
class TeamInvitation:
    """An open invitation of the agency."""

    id: uuid.UUID
    email: str
    role: Role
    all_clients: bool
    client_ids: tuple[uuid.UUID, ...]
    invited_by: TeamUser | None
    status: InvitationStatus
    expires_at: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ClientAccess:
    """Which clients a membership or invitation gives: all of them, or ``client_ids``."""

    all_clients: bool
    client_ids: tuple[uuid.UUID, ...] = ()
