"""Enforce: row-level security on every tenant table (E2-04)

Phase 5 of the tenancy migration (EPIC-02 spec §6.2, §7.1; ADR 0009). Runbook
``docs/runbooks/tenancy-rls.md``.

Before it runs
    The roles ``hireflow_owner`` and ``hireflow_app`` must exist and ``hireflow_owner`` must own
    the tables: ``docker/postgres/roles.sql`` (the Compose init script on a new volume, ``make
    db-roles`` on an existing one). This migration runs **as** ``hireflow_owner``
    (``MIGRATION_DATABASE_URL``); it refuses, changing nothing, when the roles are missing, the
    tables belong to someone else or it runs as another user. A role cannot be created here:
    the owner role has no ``CREATEROLE``, on purpose.

What it does
    - ``app_current_agency()``: the transaction's ``app.agency_id`` as a uuid, ``NULL`` when it
      is unset or empty.
    - Grants for ``hireflow_app``: ``SELECT, INSERT, UPDATE, DELETE`` on every table but
      ``alembic_version`` (nothing), ``USAGE`` on every sequence; ``ALTER DEFAULT PRIVILEGES FOR
      ROLE hireflow_owner`` gives tables and sequences the owner creates later the same grants
      (E2-05's identity tables get them without a grant of their own; ``audit_events`` then
      revokes ``UPDATE`` and ``DELETE``).
    - On each tenant table (:data:`TENANT_TABLES`): ``ENABLE`` and ``FORCE ROW LEVEL
      SECURITY``, the policy ``tenant_isolation`` for ``hireflow_app`` (``USING`` and ``WITH
      CHECK`` ``agency_id = (SELECT app_current_agency())``: an unbound transaction reads and
      writes nothing) and the policy ``maintenance`` for ``hireflow_owner`` (every row).
    - The global tables (spec §6.4: ``agencies``, ``legacy_candidate_map``) get the grants but
      no policies.

    Each ``ALTER TABLE`` and ``CREATE POLICY`` takes a short ``ACCESS EXCLUSIVE`` lock (catalog
    only, no scan), under ``lock_timeout`` 5 s: behind a long transaction the migration fails
    and can simply be run again (it is one transaction).

Downgrade
    Drops the policies, ``NO FORCE`` / ``DISABLE`` row-level security, revokes every grant and
    default privilege it gave ``hireflow_app`` and drops the function. Point ``DATABASE_URL`` and
    ``SYNC_DATABASE_URL`` at the owner role **before** downgrading: afterwards the application
    role can read nothing (the runbook's rollback).

Revision ID: e2c04a7f3b18
Revises: e2b02c7a4d19
Create Date: 2026-11-24 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.platform.rls import (
    APP_ROLE,
    CREATE_CURRENT_AGENCY_FUNCTION,
    DROP_CURRENT_AGENCY_FUNCTION,
    OWNER_ROLE,
    disable_tenant_rls,
    enable_tenant_rls,
)

revision: str = "e2c04a7f3b18"
down_revision: str | None = "e2b02c7a4d19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"

# Every table with an agency_id at this revision (spec §3.3). Not imported from the models: a
# migration describes the schema of its own revision.
TENANT_TABLES = (
    "clients",
    "campaigns",
    "applications",
    "people",
    "person_identifiers",
    "identity_conflicts",
    "person_resumes",
    "resumes",
)

TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"


def _refuse_unless_ready(conn: sa.Connection) -> None:
    roles = set(
        conn.execute(
            sa.text("SELECT rolname FROM pg_roles WHERE rolname IN (:owner, :app)"),
            {"owner": OWNER_ROLE, "app": APP_ROLE},
        ).scalars()
    )
    missing = sorted({OWNER_ROLE, APP_ROLE} - roles)
    if missing:
        raise RuntimeError(
            f"the database roles {', '.join(missing)} do not exist. Create them first, as a "
            "superuser: `make db-roles` (docker/postgres/roles.sql); see "
            "docs/runbooks/tenancy-rls.md."
        )
    user = conn.execute(sa.text("SELECT current_user")).scalar_one()
    if user != OWNER_ROLE:
        raise RuntimeError(
            f"this migration runs as {OWNER_ROLE}, not {user}: set MIGRATION_DATABASE_URL to the "
            "owner role (docs/runbooks/tenancy-rls.md)."
        )
    foreign = conn.execute(
        sa.text(
            "SELECT tablename, tableowner FROM pg_tables "
            "WHERE schemaname = 'public' AND tableowner <> :owner ORDER BY tablename"
        ),
        {"owner": OWNER_ROLE},
    ).all()
    if foreign:
        listed = ", ".join(f"{table} ({owner})" for table, owner in foreign)
        raise RuntimeError(
            f"these tables are not owned by {OWNER_ROLE}: {listed}. Run `make db-roles` "
            "(docker/postgres/roles.sql) first; see docs/runbooks/tenancy-rls.md."
        )


def upgrade() -> None:
    conn = op.get_bind()
    _refuse_unless_ready(conn)
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")

    op.execute(CREATE_CURRENT_AGENCY_FUNCTION)

    # Grants on what exists now, then the same for what the owner creates later.
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT {TABLE_PRIVILEGES} ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    op.execute(f"REVOKE ALL ON alembic_version FROM {APP_ROLE}")
    op.execute(f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {OWNER_ROLE} IN SCHEMA public "
        f"GRANT {TABLE_PRIVILEGES} ON TABLES TO {APP_ROLE}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {OWNER_ROLE} IN SCHEMA public "
        f"GRANT USAGE ON SEQUENCES TO {APP_ROLE}"
    )

    for table in TENANT_TABLES:
        for statement in enable_tenant_rls(table):
            op.execute(statement)


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    for table in reversed(TENANT_TABLES):
        for statement in disable_tenant_rls(table):
            op.execute(statement)

    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {OWNER_ROLE} IN SCHEMA public "
        f"REVOKE USAGE ON SEQUENCES FROM {APP_ROLE}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {OWNER_ROLE} IN SCHEMA public "
        f"REVOKE {TABLE_PRIVILEGES} ON TABLES FROM {APP_ROLE}"
    )
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {APP_ROLE}")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {APP_ROLE}")

    op.execute(DROP_CURRENT_AGENCY_FUNCTION)
