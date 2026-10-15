"""Expand: applications.resume_uploaded_at, the upload time of the application's resume (E0-16)

``hiring.service.upsert_application`` lets a newer resume replace an application's scoring and
never lets an older one overwrite it (EPIC-00 spec §5.1, §6.2). "Newer" is the resume's upload
time, which lives in ingestion's ``resumes`` table; hiring may not read it (spec §3, ADR 0003),
so the application stores the upload time of the resume it currently holds, next to
``resume_id``.

Expand only: one nullable column without a default on a table nothing reads yet. In Postgres 16
that is a catalog-only change (no rewrite, no scan) under a brief ACCESS EXCLUSIVE lock;
``lock_timeout`` makes it fail fast instead of queueing behind a long lock holder. Rows written
before this column existed have ``NULL`` and count as older than any resume.

Revision ID: b7d2e4f61a90
Revises: ad3bb2d36694
Create Date: 2026-10-15 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d2e4f61a90"
down_revision: str | None = "ad3bb2d36694"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        "applications",
        sa.Column("resume_uploaded_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column("applications", "resume_uploaded_at")
