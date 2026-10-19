"""Contract: drop ``candidates``, ``backfill_checkpoints`` and the last cross-module foreign key (E0-20)

EPIC-00 spec §6.1 phase 5. Since the read switch (E0-19) the API serves applications and nothing
writes ``candidates``; this removes the legacy model and the schema coupling ADR 0003 forbids.

Upgrade, in one transaction (``lock_timeout`` makes each lock fail fast instead of queueing
behind a long lock holder, as in the expand migrations):

1. **Data:** a resume whose text was extracted before the switch still has it only on its legacy
   candidate (``candidates.raw_text``); it is copied to ``resumes.raw_text`` where that is
   ``NULL``, so such a resume can still be analysed or retried (the pipeline read it from the
   candidate until now, ``hiring.legacy_resume_text``, removed with this migration). One
   ``UPDATE`` over the resumes extracted before E0-19.
2. ``resumes.campaign_id -> campaigns.id`` (``resumes_campaign_id_fkey``, ``ON DELETE CASCADE``)
   is dropped: the last foreign key between two modules' tables (ingestion -> hiring). Deleting
   a campaign no longer deletes its resumes in the database; ``app.api`` asks ingestion to
   delete them in the same transaction (``DELETE /campaigns/{id}``).
3. ``candidates`` is dropped, with its foreign keys to ``resumes`` and ``campaigns``.
4. ``backfill_checkpoints`` is dropped: its only writer was the backfill command, removed with
   the legacy model.

``legacy_candidate_map`` stays: old candidate ids (bookmarks, links in e-mails) keep resolving to
the application they were merged into. Its ``candidate_id`` never had a foreign key, and from
here on refers to nothing: it is a historical id map.

Downgrade: re-creates ``backfill_checkpoints`` and ``candidates`` **empty**, with their original
schema (columns in their original order, the same constraint names), and the foreign key from
``resumes`` to ``campaigns``. It does not bring the legacy rows back: the E0-18 code would
then list no candidates (the E0-19 code does not need them: it serves applications). If the
E0-18 code ever has to run again, rebuild the rows from Applications and the resumes'
extraction snapshots with ``REBUILD_CANDIDATES`` below (shown in
``docs/runbooks/person-application-backfill.md`` §8.3), after this downgrade and before
starting that code. The foreign key is added back validated: it fails if a
resume refers to a deleted campaign (not possible through the API, whose campaign delete
removes the resumes in the same transaction).

Revision ID: e20c4a7d1b93
Revises: e19b7f3a9c42
Create Date: 2026-10-21 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e20c4a7d1b93"
down_revision: str | None = "e19b7f3a9c42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COPY_RAW_TEXT = """
UPDATE resumes AS r
SET raw_text = c.raw_text
FROM candidates AS c
WHERE c.resume_id = r.id
  AND r.raw_text IS NULL
  AND c.raw_text IS NOT NULL
"""

RESUMES_CAMPAIGN_FK = "resumes_campaign_id_fkey"

# Not run by the migration: the rebuild documented in the runbook (§8.3), kept here so that
# ``tests/migrations/test_contract_e0_20.py`` runs the exact SQL the runbook shows. After the
# downgrade, one legacy candidate per application, under the application's id, from the
# application (scoring, recruiter fields, timestamps) and its current resume (extraction
# snapshot, text): the row the E0-19 read path showed. Older resumes of merged applications get
# none (their own scoring is not kept anywhere), and ``legacy_candidate_map`` is left as it is.
REBUILD_CANDIDATES = """
INSERT INTO candidates (id, resume_id, campaign_id, name, email, phone, github_url, linkedin_url,
    skills, education, work_experience, certifications, projects, years_of_experience, summary,
    strengths, weaknesses, missing_skills, recommendation, score, score_breakdown, category,
    notes, pipeline_stage, raw_text, created_at, updated_at)
SELECT a.id, r.id, a.campaign_id,
       r.extracted->>'name', r.extracted->>'email', r.extracted->>'phone',
       r.extracted->>'github_url', r.extracted->>'linkedin_url',
       coalesce(r.extracted->'skills', '[]'), coalesce(r.extracted->'education', '[]'),
       coalesce(r.extracted->'work_experience', '[]'),
       coalesce(r.extracted->'certifications', '[]'), coalesce(r.extracted->'projects', '[]'),
       (r.extracted->>'years_of_experience')::float,
       a.summary, a.strengths, a.weaknesses, a.missing_skills, a.recommendation, a.score,
       a.score_breakdown, a.category, a.notes, a.pipeline_stage, r.raw_text,
       a.created_at, a.updated_at
FROM applications AS a
JOIN resumes AS r ON r.id = a.resume_id
WHERE NOT EXISTS (SELECT 1 FROM candidates AS c WHERE c.id = a.id OR c.resume_id = r.id)
"""


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(COPY_RAW_TEXT)
    op.drop_constraint(RESUMES_CAMPAIGN_FK, "resumes", type_="foreignkey")
    op.drop_table("candidates")
    op.drop_table("backfill_checkpoints")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_table(
        "backfill_checkpoints",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("last_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_id", sa.UUID(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_foreign_key(
        RESUMES_CAMPAIGN_FK, "resumes", "campaigns", ["campaign_id"], ["id"], ondelete="CASCADE"
    )
    # The original column order: the initial table, then notes and pipeline_stage
    # (7b755c874cf7), then score_breakdown (19e4a9fb3e33).
    op.create_table(
        "candidates",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("resume_id", sa.UUID(), nullable=False),
        sa.Column("campaign_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("github_url", sa.String(length=500), nullable=True),
        sa.Column("linkedin_url", sa.String(length=500), nullable=True),
        sa.Column("skills", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("education", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("work_experience", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("certifications", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("projects", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("years_of_experience", sa.Float(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("strengths", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("weaknesses", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("missing_skills", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("category", sa.String(length=30), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
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
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("pipeline_stage", sa.String(length=30), nullable=True),
        sa.Column("score_breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resume_id"),
    )
