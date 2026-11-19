"""Expand: agencies, clients and a nullable tenant key on every tenant table (E2-01)

Phase 1 of the tenancy migration (EPIC-02 spec §7.1, the same expand -> backfill -> contract
pattern as EPIC-00 §6). Expand only: nothing is dropped, renamed or tightened, and no response
changes.

- identity: ``agencies`` (global, not under agency RLS: spec §6.4)
- hiring: ``clients``
- the default agency **"Default agency"** (slug ``default``) and its default client
  **"General"** (spec §15, Q7), with the fixed ids of ``app.platform.tenancy``:
  ``DEFAULT_AGENCY_ID`` = ``00000000-0000-4000-8000-00000000a001`` and ``DEFAULT_CLIENT_ID`` =
  ``00000000-0000-4000-8000-00000000c001``. The values are written out here, not imported: a
  migration must keep doing what it did when it ran, whatever the code says later (a test
  checks the two agree).
- nullable ``agency_id`` on the seven existing tenant tables: ``campaigns``, ``applications``
  (hiring), ``people``, ``person_identifiers``, ``identity_conflicts``, ``person_resumes``
  (talent) and ``resumes`` (ingestion); ``campaigns.client_id`` and
  ``campaigns.created_by_user_id``; ``resumes.uploaded_by_user_id``.

Every ``agency_id``, ``client_id`` and ``*_user_id`` is a plain UUID **without a foreign key**:
``agency_id`` and the user ids refer to identity, another module (ADR 0003); the same-module
``(agency_id, client_id) -> clients`` and the other composite keys arrive with the contract
(E2-02), once every row has its agency. ``clients`` is new and filled here, so its own
``agency_id`` is ``NOT NULL`` and its unique constraints include the agency from the start
(spec §3.3).

Locking: each ``ADD COLUMN`` is nullable with no default, which in Postgres 16 is a
catalog-only change (no table rewrite, no scan) under a brief ACCESS EXCLUSIVE lock. They run
last, so those locks are held only until the commit right after, and ``lock_timeout`` makes the
migration fail fast (it is one transaction, safe to re-run) instead of queueing every query on
those tables behind a long-running one. A ``NOT NULL DEFAULT '<default agency>'`` column would
also be catalog-only (the "fast default"), but it makes the default agency a schema constant;
spec §7.1 rejected it for the backfill that E2-02 runs.

Existing rows keep ``NULL`` until the E2-02 backfill; from this release on, every insert path
writes the default agency (and new campaigns the default client).

Downgrade: drops the new columns, ``clients`` and ``agencies``, with the default agency and
client. It needs no data step: nothing reads the columns yet. Their values are lost; upgrading
again leaves them ``NULL`` for the backfill.

Revision ID: e2a01d5c8f36
Revises: e20c4a7d1b93
Create Date: 2026-11-19 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2a01d5c8f36"
down_revision: str | None = "e20c4a7d1b93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# app.platform.tenancy.DEFAULT_AGENCY_ID / DEFAULT_CLIENT_ID (tests/migrations checks they match).
DEFAULT_AGENCY_ID = "00000000-0000-4000-8000-00000000a001"
DEFAULT_CLIENT_ID = "00000000-0000-4000-8000-00000000c001"
DEFAULT_AGENCY_NAME = "Default agency"
DEFAULT_AGENCY_SLUG = "default"
DEFAULT_CLIENT_NAME = "General"

# The existing tables that get agency_id, in their module order (hiring, talent, ingestion).
TENANT_TABLES = (
    "campaigns",
    "applications",
    "people",
    "person_identifiers",
    "identity_conflicts",
    "person_resumes",
    "resumes",
)


def upgrade() -> None:
    # Fail fast rather than wait behind (and block everything queued after) a long lock holder.
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.create_table(
        "agencies",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
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
        sa.CheckConstraint("status IN ('active', 'suspended')", name="ck_agencies_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_agencies_slug"),
    )
    op.create_table(
        "clients",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("agency_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
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
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_clients_status"),
        sa.PrimaryKeyConstraint("id"),
        # Target of the composite foreign key from campaigns (E2-02).
        sa.UniqueConstraint("agency_id", "id", name="uq_clients_agency_id_id"),
    )
    # A client name is unique per agency, ignoring case.
    op.create_index(
        "uq_clients_agency_id_lower_name",
        "clients",
        ["agency_id", sa.text("lower(name)")],
        unique=True,
    )

    op.execute(
        sa.text(
            "INSERT INTO agencies (id, name, slug, status) VALUES (:id, :name, :slug, 'active')"
        ).bindparams(id=DEFAULT_AGENCY_ID, name=DEFAULT_AGENCY_NAME, slug=DEFAULT_AGENCY_SLUG)
    )
    op.execute(
        sa.text(
            "INSERT INTO clients (id, agency_id, name, status) "
            "VALUES (:id, :agency_id, :name, 'active')"
        ).bindparams(id=DEFAULT_CLIENT_ID, agency_id=DEFAULT_AGENCY_ID, name=DEFAULT_CLIENT_NAME)
    )

    # Existing tables, kept last: nullable ADD COLUMN without a default is catalog-only.
    for table in TENANT_TABLES:
        op.add_column(table, sa.Column("agency_id", sa.UUID(), nullable=True))
    op.add_column("campaigns", sa.Column("client_id", sa.UUID(), nullable=True))
    op.add_column("campaigns", sa.Column("created_by_user_id", sa.UUID(), nullable=True))
    op.add_column("resumes", sa.Column("uploaded_by_user_id", sa.UUID(), nullable=True))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column("resumes", "uploaded_by_user_id")
    op.drop_column("campaigns", "created_by_user_id")
    op.drop_column("campaigns", "client_id")
    for table in reversed(TENANT_TABLES):
        op.drop_column(table, "agency_id")
    op.drop_index("uq_clients_agency_id_lower_name", table_name="clients")
    op.drop_table("clients")
    op.drop_table("agencies")
