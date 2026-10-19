"""Data: give each application the timestamps of its legacy candidates (E0-19 read switch)

The legacy candidate endpoints return ``created_at`` and ``updated_at``. Before the switch they
were the candidate's; after it they are the application's (spec §7). The backfill created
applications with the backfill run's time, and the dual-write pipeline at the end of processing
(the candidate was created earlier, when the PDF's text was extracted), so without this step
every timestamp in every response would change at the switch.

For every application that legacy candidates lead to (its own id, or ``legacy_candidate_map``):

- ``created_at`` = the earliest ``created_at`` of those candidates (when the application first
  existed, as a candidate);
- ``updated_at`` = the latest ``updated_at`` of those candidates.

For an application with one candidate that is exactly the candidate's pair, so its responses
stay byte-identical. Applications without a candidate (none exist before the switch) and rows
already equal are not touched. Run it right before the E0-19 code starts serving (runbook §8):
it is part of ``alembic upgrade head`` of the switch deploy. Running it again changes nothing
unless legacy candidates changed in between (only the E0-18 code writes them).

Data only: no schema change, so ``alembic check`` is unaffected. One ``UPDATE`` (row locks on
the applications it changes, held for the statement); at our sizes (thousands of rows) it takes
milliseconds. The downgrade does nothing: the previous timestamps (backfill or pipeline times)
carried no information, and the E0-18 code does not read them.

Revision ID: e19b7f3a9c42
Revises: e19a0c5d2b71
Create Date: 2026-10-20 09:05:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e19b7f3a9c42"
down_revision: str | None = "e19a0c5d2b71"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALIGN_TIMESTAMPS = """
UPDATE applications AS a
SET created_at = legacy.created_at,
    updated_at = legacy.updated_at
FROM (
    SELECT coalesce(m.application_id, c.id) AS application_id,
           min(c.created_at) AS created_at,
           max(c.updated_at) AS updated_at
    FROM candidates AS c
    LEFT JOIN legacy_candidate_map AS m ON m.candidate_id = c.id
    GROUP BY 1
) AS legacy
WHERE a.id = legacy.application_id
  AND (a.created_at, a.updated_at) IS DISTINCT FROM (legacy.created_at, legacy.updated_at)
"""


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(ALIGN_TIMESTAMPS)


def downgrade() -> None:
    pass
