"""Check the database's row-level security against EPIC-02 spec §6.2 (read-only).

Usage (inside the backend container, or from ``backend/``)::

    python -m app.devtools.check_rls

It connects with ``MIGRATION_DATABASE_URL`` (the owner role; it only reads the catalogue) and
reports, for the ``public`` schema: the roles (neither a superuser nor ``BYPASSRLS``, the app
role not a member of the owner role), the owner of every relation (``hireflow_owner``), ``ENABLE``
and ``FORCE`` row-level security and the ``tenant_isolation`` / ``maintenance`` policies on
every table that is not on the global allowlist (``app.platform.rls.GLOBAL_TABLES``), the app
role's grants table by table and on every sequence, the owner's default privileges, and
``app_current_agency()``. Then it checks the application's own URL (``SYNC_DATABASE_URL``) the
way the API and the workers do at start-up.

Exit code ``0`` when everything matches, ``1`` when anything does not (each problem printed),
``2`` when the database cannot be reached. Runbook: ``docs/runbooks/tenancy-rls.md``; the CI
``migrations`` job runs it after ``alembic upgrade head``.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from app.platform.config import settings
from app.platform.rls import GLOBAL_TABLES, catalogue_problems, role_problems, tenant_tables

PROG = "check_rls"


def main(argv: Sequence[str] | None = None) -> int:
    if argv:
        print(f"usage: python -m app.devtools.{PROG}", file=sys.stderr)
        return 2
    owner = create_engine(settings.migration_database_url, poolclass=NullPool)
    app = create_engine(settings.SYNC_DATABASE_URL, poolclass=NullPool)
    try:
        with owner.connect() as connection:
            problems = catalogue_problems(connection)
            tables = tenant_tables(connection)
        with app.connect() as connection:
            problems += [f"SYNC_DATABASE_URL: {p}" for p in role_problems(connection)]
    except SQLAlchemyError as exc:
        print(f"{PROG}: cannot read the database: {exc}", file=sys.stderr)
        return 2
    finally:
        owner.dispose()
        app.dispose()

    print(f"{PROG}: {len(tables)} tenant tables: {', '.join(tables)}")
    print(f"{PROG}: global (allowlisted) tables: {', '.join(sorted(GLOBAL_TABLES))}")
    if problems:
        for problem in problems:
            print(f"  PROBLEM {problem}")
        print(f"{PROG}: {len(problems)} problem(s)")
        return 1
    print(f"{PROG}: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
