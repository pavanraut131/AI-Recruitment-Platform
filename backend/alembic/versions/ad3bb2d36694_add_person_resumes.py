"""Expand: person_resumes, talent's record of which Person each resume resolved to (E0-15)

``talent.service.resolve_person`` writes one row per resolved resume. It makes resolution
idempotent per resume (a retried pipeline step gets the same Person back, even for a resume with
no usable identifiers) and stores each resume's ``uploaded_at`` for the profile-refresh ordering
(EPIC-00 spec §5.3). Talent cannot use ``resumes.person_id`` for this: that column belongs to
ingestion (spec §3).

Expand only: one new, empty table. FKs stay inside talent (``people``, ``identity_conflicts``);
``resume_id`` is a plain UUID (ADR 0003). No existing table is altered or locked beyond the brief
SHARE ROW EXCLUSIVE lock that adding a foreign key takes on the referenced ``people`` and
``identity_conflicts``, which nothing writes yet.

Revision ID: ad3bb2d36694
Revises: 404e09095076
Create Date: 2026-10-15 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ad3bb2d36694"
down_revision: str | None = "404e09095076"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_table(
        "person_resumes",
        sa.Column("resume_id", sa.UUID(), nullable=False),
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("matched_existing", sa.Boolean(), nullable=False),
        sa.Column("conflict_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["conflict_id"], ["identity_conflicts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("resume_id"),
    )
    op.create_index(
        op.f("ix_person_resumes_person_id"), "person_resumes", ["person_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_person_resumes_person_id"), table_name="person_resumes")
    op.drop_table("person_resumes")
