"""Expand: resumes.raw_text, the text extracted from the PDF (E0-19)

Until the read switch the extract step stored the PDF's text on the resume's legacy candidate
(``candidates.raw_text``), creating a placeholder candidate, and the AI step read it from there.
From E0-19 nothing writes ``candidates``, so the text moves to the resume that it was extracted
from, which ingestion owns. It is never part of an API response.

Expand only: one nullable column without a default. In Postgres 16 that is a catalog-only change
(no rewrite, no scan) under a brief ACCESS EXCLUSIVE lock; ``lock_timeout`` makes it fail fast
instead of queueing behind a long lock holder. The E0-18 code ignores the column, so this can be
applied before the E0-19 code is deployed. Existing rows keep ``NULL``: a resume extracted before
the switch still has its text on its candidate, and the pipeline reads it from there
(``hiring.service.legacy_resume_text``) until E0-20.

Revision ID: e19a0c5d2b71
Revises: b7d2e4f61a90
Create Date: 2026-10-20 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e19a0c5d2b71"
down_revision: str | None = "b7d2e4f61a90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column("resumes", sa.Column("raw_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column("resumes", "raw_text")
