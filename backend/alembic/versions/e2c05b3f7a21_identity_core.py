"""Identity core: users, memberships, membership_clients, user_sessions, audit_events (E2-05)

New tables only (EPIC-02 spec §3.3), all owned by ``identity`` and **global** (not under agency
row-level security, spec §6.4): they are read before a tenant is known. Nothing existing is
changed, so the upgrade takes no lock on a table in use.

- ``users``: global accounts, email stored normalised (a CHECK keeps it lower-cased and
  trimmed, so ``UNIQUE(email)`` is case-insensitive).
- ``memberships``: a user's role in an agency; ``UNIQUE(agency_id, user_id)``; owners and
  admins always have every client (CHECK).
- ``membership_clients``: the clients of a client-limited membership (``client_id`` refers to
  hiring's ``clients`` with no foreign key, ADR 0003).
- ``user_sessions``: server-side sessions keyed by the SHA-256 of the cookie value (ADR 0010).
- ``audit_events``: append-only security events (spec §4.7).

**Grants (E2-04).** E2-04 creates the roles ``hireflow_owner`` (owns the schema, runs the
migrations) and ``hireflow_app`` (the API and workers) and default privileges that give the app
role ``SELECT, INSERT, UPDATE, DELETE`` on tables the owner creates later. This migration may run
before or after that one, so its grants are in a ``DO`` block that runs only when the role
``hireflow_app`` exists: the four read-write tables get the usual grants (idempotent with the
default privileges), and ``audit_events`` gets ``SELECT, INSERT`` only, with ``UPDATE``,
``DELETE`` and ``TRUNCATE`` revoked explicitly (the default privileges would have granted the
first two): the application can add events but never change or remove one. Without the role
(before E2-04, the CI ``migrations`` job, the test container today) the block does nothing.

Downgrade drops the five tables, with every user, session and audit event in them.

Revision ID: e2c05b3f7a21
Revises: e2b02c7a4d19
Create Date: 2026-11-24 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e2c05b3f7a21"
down_revision: str | None = "e2b02c7a4d19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "hireflow_app"
READ_WRITE_TABLES = ("users", "memberships", "membership_clients", "user_sessions")

GRANTS = f"""
DO $grants$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON {", ".join(READ_WRITE_TABLES)} TO {APP_ROLE};
        -- Append-only for the application (spec §3.3, §6.2).
        REVOKE ALL ON audit_events FROM {APP_ROLE};
        GRANT SELECT, INSERT ON audit_events TO {APP_ROLE};
        EXECUTE format(
            'GRANT USAGE ON SEQUENCE %s TO {APP_ROLE}',
            pg_get_serial_sequence('audit_events', 'id')
        );
    END IF;
END
$grants$;
"""


def _timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("email = lower(btrim(email))", name="ck_users_email_normalised"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_users_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "memberships",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("agency_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("all_clients", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("invited_by_user_id", sa.UUID(), nullable=True),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'recruiter', 'viewer')", name="ck_memberships_role"
        ),
        sa.CheckConstraint(
            "all_clients OR role IN ('recruiter', 'viewer')", name="ck_memberships_all_clients"
        ),
        sa.CheckConstraint("status IN ('active', 'deactivated')", name="ck_memberships_status"),
        sa.ForeignKeyConstraint(
            ["agency_id"], ["agencies.id"], name="fk_memberships_agency_id_agencies"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_memberships_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["users.id"],
            name="fk_memberships_invited_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agency_id", "user_id", name="uq_memberships_agency_id_user_id"),
    )
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])
    op.create_table(
        "membership_clients",
        sa.Column("membership_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["membership_id"],
            ["memberships.id"],
            name="fk_membership_clients_membership_id_memberships",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("membership_id", "client_id", name="pk_membership_clients"),
    )
    op.create_table(
        "user_sessions",
        sa.Column("id_hash", sa.LargeBinary(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("active_agency_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_sessions_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["active_agency_id"],
            ["agencies.id"],
            name="fk_user_sessions_active_agency_id_agencies",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id_hash"),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("agency_id", sa.UUID(), nullable=True),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_type", sa.String(length=50), nullable=True),
        sa.Column("target_id", sa.String(length=100), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_events_agency_id_occurred_at", "audit_events", ["agency_id", "occurred_at"]
    )

    op.execute(GRANTS)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_index("ix_audit_events_agency_id_occurred_at", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_user_sessions_user_id", table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_table("membership_clients")
    op.drop_index("ix_memberships_user_id", table_name="memberships")
    op.drop_table("memberships")
    op.drop_table("users")
