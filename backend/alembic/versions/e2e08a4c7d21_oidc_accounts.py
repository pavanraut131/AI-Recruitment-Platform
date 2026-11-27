"""OIDC accounts: Google and Microsoft identities linked to users (E2-08)

A new table only (EPIC-02 spec §3.3), owned by ``identity`` and **global** (not under agency
row-level security, spec §6.4: the OIDC callback looks an identity up before any agency is
known). Nothing existing changes, so the upgrade takes no lock on a table in use.

- ``oidc_accounts``: one row per provider identity that signs a user in, keyed by
  ``UNIQUE(provider, issuer, subject)`` (never by email: spec §4.3, the "nOAuth" class of
  account takeovers). ``email_at_link`` is the provider's address when it was linked, for the
  record. Deleted with its user; ``(user_id)`` is indexed for that cascade and a user's methods.

**Grants.** None here: E2-04's default privileges (``ALTER DEFAULT PRIVILEGES FOR ROLE
hireflow_owner``) give ``hireflow_app`` ``SELECT, INSERT, UPDATE, DELETE`` on tables the owner
creates. ``app.platform.rls.GLOBAL_TABLES`` lists the table, with the reason.

Downgrade drops the table: every linked Google or Microsoft identity is forgotten (the users
stay; one without a password then signs in by resetting it, or links again by verified email).

Revision ID: e2e08a4c7d21
Revises: e2d06b8c4f17
Create Date: 2026-11-27 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2e08a4c7d21"
down_revision: str | None = "e2d06b8c4f17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.create_table(
        "oidc_accounts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("issuer", sa.String(length=300), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("email_at_link", sa.String(length=320), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("provider IN ('google', 'microsoft')", name="ck_oidc_accounts_provider"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_oidc_accounts_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "issuer", "subject", name="uq_oidc_accounts_provider_issuer_subject"
        ),
    )
    op.create_index("ix_oidc_accounts_user_id", "oidc_accounts", ["user_id"])


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_index("ix_oidc_accounts_user_id", table_name="oidc_accounts")
    op.drop_table("oidc_accounts")
