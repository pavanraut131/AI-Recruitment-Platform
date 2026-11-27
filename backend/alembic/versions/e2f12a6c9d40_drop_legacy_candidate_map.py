"""Contract: drop ``legacy_candidate_map`` with ``/api/v1`` (E2-12)

EPIC-02 spec §7.4, §12 E2-12. The table mapped the ids of legacy candidates that the E0 backfill
merged into another application (E0-17) to that application, so that old ``/api/v1/candidates``
links kept working; ``candidate_id`` has referred to nothing since ``candidates`` was dropped
(E0-20). ``/api/v1`` was its only reader and is removed in the same release, and the v2 API never
resolved the old ids (application ids only). The links it served predate any customer, so the
ids are not kept anywhere else.

Upgrade drops the table (its index and its foreign key to ``applications`` go with it), in one
transaction under ``lock_timeout``: ``legacy_candidate_map`` is locked exclusively, and so is
``applications`` for a moment (dropping the foreign key's triggers); both changes are in the
catalog only, so the locks are held for milliseconds, and behind a long transaction the migration
fails fast and can simply be run again.

Downgrade re-creates the table **empty**, with the schema of the E0-14 expand migration
(``404e09095076``: the same columns, primary key, ``ON DELETE CASCADE`` foreign key and index
names). The rows are not brought back; the pre-E2-12 code runs without them (an old merged
candidate id then answers 404 on ``/api/v1``, as an unknown id). The owner's default privileges
(E2-04) give ``hireflow_app`` its grants again, and the table is back on that code's RLS
allowlist (``app.platform.rls.GLOBAL_TABLES``).

Revision ID: e2f12a6c9d40
Revises: e2e08a4c7d21
Create Date: 2026-11-27 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2f12a6c9d40"
down_revision: str | None = "e2e08a4c7d21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.drop_table("legacy_candidate_map")


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    # As 404e09095076 created it (Postgres's default constraint names: no naming convention).
    op.create_table(
        "legacy_candidate_map",
        sa.Column("candidate_id", sa.UUID(), nullable=False),
        sa.Column("application_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("candidate_id"),
    )
    op.create_index(
        op.f("ix_legacy_candidate_map_application_id"),
        "legacy_candidate_map",
        ["application_id"],
        unique=False,
    )
