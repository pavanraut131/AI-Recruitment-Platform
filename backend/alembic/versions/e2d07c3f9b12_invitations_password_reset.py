"""Invitations and password-reset tokens (E2-07)

New tables only (EPIC-02 spec §3.3), owned by ``identity`` and **global** (not under agency
row-level security, spec §6.4: an invitation or reset token is looked up by its hash before any
agency is known). Nothing existing changes, so the upgrade takes no lock on a table in use.

- ``invitations``: an emailed invitation to an agency with a role and client access. Only the
  SHA-256 of the token is stored (``UNIQUE``). At most one **open** invitation (neither accepted
  nor revoked) per address and agency: a partial unique index. Email stored normalised, roles
  and the "owners and admins see every client" rule as on ``memberships``, and a client list
  exactly when access is limited (``client_ids`` refers to hiring's ``clients``, no foreign key,
  ADR 0003).
- ``password_reset_tokens``: single-use reset links (SHA-256 only, ``UNIQUE``), deleted with
  their user.

**Grants.** None here: E2-04's default privileges (``ALTER DEFAULT PRIVILEGES FOR ROLE
hireflow_owner``) give ``hireflow_app`` ``SELECT, INSERT, UPDATE, DELETE`` on tables the owner
creates, which is what both tables need (``identity.purge_expired`` deletes from them as the
application role). ``app.platform.rls.GLOBAL_TABLES`` lists them, with the reason.

Downgrade drops both tables, with every invitation and reset token in them (pending invitations
and reset links stop working; the memberships already accepted stay).

Revision ID: e2d07c3f9b12
Revises: e2c04a7f3b18
Create Date: 2026-11-27 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e2d07c3f9b12"
down_revision: str | None = "e2c04a7f3b18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.create_table(
        "invitations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("agency_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("all_clients", sa.Boolean(), nullable=False),
        sa.Column(
            "client_ids",
            postgresql.ARRAY(sa.UUID()),
            server_default=sa.text("'{}'::uuid[]"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("invited_by_user_id", sa.UUID(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by_user_id", sa.UUID(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("email = lower(btrim(email))", name="ck_invitations_email_normalised"),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'recruiter', 'viewer')", name="ck_invitations_role"
        ),
        sa.CheckConstraint(
            "all_clients OR role IN ('recruiter', 'viewer')", name="ck_invitations_all_clients"
        ),
        sa.CheckConstraint(
            "all_clients = (cardinality(client_ids) = 0)", name="ck_invitations_client_ids"
        ),
        sa.ForeignKeyConstraint(
            ["agency_id"], ["agencies.id"], name="fk_invitations_agency_id_agencies"
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["users.id"],
            name="fk_invitations_invited_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by_user_id"],
            ["users.id"],
            name="fk_invitations_accepted_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_invitations_token_hash"),
    )
    op.create_index(
        "uq_invitations_agency_id_email_open",
        "invitations",
        ["agency_id", "email"],
        unique=True,
        postgresql_where=sa.text("accepted_at IS NULL AND revoked_at IS NULL"),
    )

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_password_reset_tokens_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_password_reset_tokens_token_hash"),
    )
    op.create_index("ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"])


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_index("ix_password_reset_tokens_user_id", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
    op.drop_index("uq_invitations_agency_id_email_open", table_name="invitations")
    op.drop_table("invitations")
