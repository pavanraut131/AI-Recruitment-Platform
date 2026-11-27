"""identity ORM models (EPIC-02 spec §3.3). Private to the identity module: other modules use
``app.modules.identity.service``.

- ``agencies`` (E2-01, migration ``e2a01d5c8f36``, with the default agency
  :data:`app.platform.tenancy.DEFAULT_AGENCY_ID`): the tenants.
- ``users``, ``memberships``, ``membership_clients``, ``user_sessions`` and ``audit_events``
  (E2-05, migration ``e2c05b3f7a21``).
- ``invitations`` and ``password_reset_tokens`` (E2-07, migration ``e2d07c3f9b12``): emailed,
  single-use tokens, stored as their SHA-256 only (spec §4.3).
- ``oidc_accounts`` (E2-08, migration ``e2e08a4c7d21``): a Google or Microsoft identity linked
  to a user, keyed by ``(provider, issuer, subject)``, never by email (spec §4.3).

Every identity table is **global**, not under agency row-level security (spec §6.4): they are
read before a tenant is known (sign-in by email, the session lookup on every request, the agency
switcher), and only this module reads them. ``agencies`` is referenced by ``agency_id`` on every
tenant table of ``hiring``, ``talent`` and ``ingestion``, always **without** a foreign key
(ADR 0003): row-level security (E2-04: a row's agency must be the transaction's) and the
verification (E2-02) keep those ids consistent instead; inside identity the foreign keys are
ordinary.

``audit_events`` is append-only for the application: the app role gets ``SELECT`` and
``INSERT`` only (granted in the migration when the role exists, E2-04), and its ids and agency
are plain values (no foreign key), so an event outlives what it describes.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    LargeBinary,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.database import Base


class AgencyStatus(str, enum.Enum):
    active = "active"
    # Members of a suspended agency cannot sign in to it (checked on every request).
    suspended = "suspended"


class UserStatus(str, enum.Enum):
    active = "active"
    # A platform-level switch (abuse, offboarding from every agency); per-agency deactivation
    # is on the membership.
    disabled = "disabled"


class MembershipStatus(str, enum.Enum):
    active = "active"
    deactivated = "deactivated"


class Agency(Base):
    """A tenant: the unit of isolation and data ownership (spec §3.1)."""

    __tablename__ = "agencies"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_agencies_slug"),
        CheckConstraint("status IN ('active', 'suspended')", name="ck_agencies_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Stable, URL-safe handle; read-only in the API (spec §8.2).
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=AgencyStatus.active.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class User(Base):
    """A human with a login. Global: one email, possibly memberships in several agencies."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        # Stored normalised (trimmed, lower-cased): the unique constraint is then
        # case-insensitive and sign-in looks the address up as typed, normalised.
        CheckConstraint("email = lower(btrim(email))", name="ck_users_email_normalised"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_users_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Argon2id PHC string; NULL for a user who signs in only with Google or Microsoft (E2-08).
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=UserStatus.active.value)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Membership(Base):
    """A user's role in one agency, optionally limited to some of its clients (spec §5.3)."""

    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("agency_id", "user_id", name="uq_memberships_agency_id_user_id"),
        CheckConstraint(
            "role IN ('owner', 'admin', 'recruiter', 'viewer')", name="ck_memberships_role"
        ),
        # Owners and admins always see every client.
        CheckConstraint(
            "all_clients OR role IN ('recruiter', 'viewer')", name="ck_memberships_all_clients"
        ),
        CheckConstraint("status IN ('active', 'deactivated')", name="ck_memberships_status"),
        # A user's agencies (sign-in, /me, the agency switcher).
        Index("ix_memberships_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agency_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agencies.id", name="fk_memberships_agency_id_agencies"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_memberships_user_id_users", ondelete="CASCADE"),
        nullable=False,
    )
    # app.modules.identity.schemas.Role
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    all_clients: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=MembershipStatus.active.value
    )
    # Who invited them (E2-07); NULL for owners created by the operator command and the seed.
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_memberships_invited_by_user_id_users", ondelete="SET NULL"),
        nullable=True,
    )
    # Last sign-in to or request in this agency (throttled like the session's last_seen_at);
    # sign-in picks the most recently used agency when the user has several.
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MembershipClient(Base):
    """One client a client-limited membership (``all_clients`` false) may see."""

    __tablename__ = "membership_clients"
    __table_args__ = (
        PrimaryKeyConstraint("membership_id", "client_id", name="pk_membership_clients"),
    )

    membership_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "memberships.id",
            name="fk_membership_clients_membership_id_memberships",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    # hiring's client (no foreign key, ADR 0003).
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


class UserSession(Base):
    """A signed-in browser (ADR 0010). The cookie holds a 256-bit random id; only its SHA-256 is
    stored, so reading this table yields no usable session."""

    __tablename__ = "user_sessions"
    __table_args__ = (Index("ix_user_sessions_user_id", "user_id"),)

    id_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_user_sessions_user_id_users", ondelete="CASCADE"),
        nullable=False,
    )
    # NULL until the user has chosen an agency (several memberships, none used yet).
    active_agency_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "agencies.id", name="fk_user_sessions_active_agency_id_agencies", ondelete="SET NULL"
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Written at most every few minutes (app.modules.identity.service.TOUCH_INTERVAL).
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)


class AuditEvent(Base):
    """A security-relevant event (spec §4.7). Append-only; queryable by SQL (no viewer before
    E7)."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_agency_id_occurred_at", "agency_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # NULL for events before an agency is known (a failed sign-in).
    agency_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )


class Invitation(Base):
    """An invitation to join an agency with a role (spec §4.3, E2-07).

    The email carries a link with a 256-bit token in its URL fragment; only the token's SHA-256
    is stored (``token_hash``), so this table yields no usable link. An invitation is **open**
    until it is accepted or revoked, and at most one is open per address and agency (a new one
    replaces it: the old one is revoked, its token stops working). It can be accepted while it
    is open and before ``expires_at``. ``client_ids`` lists hiring's clients (no foreign key,
    ADR 0003) when ``all_clients`` is false, and is empty otherwise.
    """

    __tablename__ = "invitations"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_invitations_token_hash"),
        # One open invitation per address per agency (the list of pending invitations).
        Index(
            "uq_invitations_agency_id_email_open",
            "agency_id",
            "email",
            unique=True,
            postgresql_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
        ),
        CheckConstraint("email = lower(btrim(email))", name="ck_invitations_email_normalised"),
        CheckConstraint(
            "role IN ('owner', 'admin', 'recruiter', 'viewer')", name="ck_invitations_role"
        ),
        # As on memberships: owners and admins always see every client.
        CheckConstraint(
            "all_clients OR role IN ('recruiter', 'viewer')", name="ck_invitations_all_clients"
        ),
        # A client list exactly when access is limited to some clients.
        CheckConstraint(
            "all_clients = (cardinality(client_ids) = 0)", name="ck_invitations_client_ids"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agency_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agencies.id", name="fk_invitations_agency_id_agencies"),
        nullable=False,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    # app.modules.identity.schemas.Role
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    all_clients: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    client_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)),
        nullable=False,
        default=list,
        server_default=text("'{}'::uuid[]"),
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # NULL when the inviter's account is gone, or for the development fallback owner.
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_invitations_invited_by_user_id_users", ondelete="SET NULL"),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id", name="fk_invitations_accepted_by_user_id_users", ondelete="SET NULL"
        ),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PasswordResetToken(Base):
    """A password-reset link (spec §4.3, E2-07): single use, short-lived, stored as the SHA-256
    of its 256-bit token. A new request for the user deletes their older unused tokens."""

    __tablename__ = "password_reset_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_password_reset_tokens_token_hash"),
        Index("ix_password_reset_tokens_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_password_reset_tokens_user_id_users", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OidcAccount(Base):
    """A Google or Microsoft identity that signs a user in (spec §3.3, §4.3; E2-08).

    Keyed by what the provider's signed ID token says about **who** it is: ``(provider, issuer,
    subject)``. The issuer is per tenant for Microsoft (``https://login.microsoftonline.com/{tid}
    /v2.0``), and ``subject`` is only unique within its issuer, so all three form the key. Never
    looked up by email: an address is only how an identity gets **linked** the first time (a
    provider-verified email or an invitation), and ``email_at_link`` records the provider's
    address at that moment (it may change at the provider later). The provider's tokens are not
    stored (ADR 0010). Deleted with the user.
    """

    __tablename__ = "oidc_accounts"
    __table_args__ = (
        UniqueConstraint(
            "provider", "issuer", "subject", name="uq_oidc_accounts_provider_issuer_subject"
        ),
        CheckConstraint("provider IN ('google', 'microsoft')", name="ck_oidc_accounts_provider"),
        # A user's sign-in methods (and the cascade when a user is deleted).
        Index("ix_oidc_accounts_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_oidc_accounts_user_id_users", ondelete="CASCADE"),
        nullable=False,
    )
    # app.modules.identity.schemas.OidcProvider
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    issuer: Mapped[str] = mapped_column(String(300), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    # The provider's address when the identity was linked (normalised; NULL if it gave none).
    email_at_link: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
