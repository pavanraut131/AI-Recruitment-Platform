"""Every agency has a client: "General" for the agencies without one (E2-06)

Every campaign is run for one of its agency's clients (EPIC-02 spec §3.1). The E2-01 expand
migration gave the default agency its client, "General" (spec §15 Q7); agencies created since
(``create-agency``, the development seed's second agency) have none, so nobody could create a
campaign in them. From E2-06 a campaign is created for the client its request names, or the
caller's default client (the agency's oldest active client they have access to); this gives
each agency that has **no client at all** one named "General", so that default exists.

Data only, no schema change, idempotent: it inserts one row per agency without a client and
nothing else. It runs as ``hireflow_owner`` (``MIGRATION_DATABASE_URL``), whose ``maintenance``
policy covers every agency's rows.

Downgrade: nothing. The clients stay, because campaigns may be run for them by then (and a
client is archived, never deleted: campaigns refer to it).

Revision ID: e2d06b8c4f17
Revises: e2d07c3f9b12
Create Date: 2026-11-27 10:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e2d06b8c4f17"
down_revision: str | None = "e2d07c3f9b12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CLIENT_NAME = "General"


def upgrade() -> None:
    op.execute(
        f"""
        INSERT INTO clients (id, agency_id, name, status, created_at, updated_at)
        SELECT gen_random_uuid(), agencies.id, '{CLIENT_NAME}', 'active', now(), now()
        FROM agencies
        WHERE NOT EXISTS (SELECT 1 FROM clients WHERE clients.agency_id = agencies.id)
        """
    )


def downgrade() -> None:
    """Nothing: the clients may have campaigns by now (see the module docstring)."""
