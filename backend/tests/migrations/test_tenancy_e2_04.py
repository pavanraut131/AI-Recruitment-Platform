"""The row-level-security migration (E2-04, ``e2c04a7f3b18``) and ``docker/postgres/roles.sql``.

On throwaway databases (``scratch_db``, with the roles applied; Alembic runs as the owner role):

- **Upgrade -> downgrade -> upgrade** with rows in every tenant table: the catalogue matches spec
  §6.2 after each upgrade (``app.platform.rls.catalogue_problems``), the downgrade removes every
  policy, grant, default privilege and the function, and no value changes.
- **Default privileges**: a table the owner creates after the migration (E2-05's identity tables)
  is usable by the app role at once, its identity sequence too.
- It **refuses** (nothing changed) when run as another user, or when the tables belong to
  someone else (a volume ``make db-roles`` has not handed over yet).
- ``roles.sql`` on a database written before E2-04 (tables owned by the bootstrap superuser):
  hands everything to ``hireflow_owner``, and a second run changes nothing.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url

from app.platform import rls
from app.platform.config import settings
from app.platform.tenancy import DEFAULT_AGENCY_ID
from tests import postgres
from tests.migrations.tenancy import CONTRACT, EXPAND, backfill
from tests.migrations.test_expand_e0_14 import (  # noqa: F401 (scratch_db is a fixture)
    _alembic_config,
    scratch_db,
)
from tests.migrations.test_tenancy_e2_01 import IDS, TENANT_TABLES, _insert_rows

RLS = "e2c04a7f3b18"
ALL_TENANT_TABLES = ("clients", *TENANT_TABLES)


def _revision(conn: Connection) -> str:
    return str(conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one())


def _values(conn: Connection) -> dict[str, list[tuple[Any, ...]]]:
    return {
        table: sorted(map(tuple, conn.execute(text(f"SELECT * FROM {table}")).all()), key=str)
        for table in ALL_TENANT_TABLES
    }


def _rls_state(conn: Connection) -> dict[str, Any]:
    return {
        "policies": conn.execute(text("SELECT count(*) FROM pg_policies")).scalar_one(),
        "enabled": conn.execute(
            text(
                "SELECT count(*) FROM pg_class WHERE relnamespace = 'public'::regnamespace "
                "AND (relrowsecurity OR relforcerowsecurity)"
            )
        ).scalar_one(),
        "app_grants": conn.execute(
            text(
                "SELECT count(*) FROM information_schema.role_table_grants "
                "WHERE grantee = 'hireflow_app'"
            )
        ).scalar_one(),
        "default_privileges": conn.execute(
            text("SELECT count(*) FROM pg_default_acl WHERE defaclrole = 'hireflow_owner'::regrole")
        ).scalar_one(),
        "function": conn.execute(
            text("SELECT to_regprocedure('public.app_current_agency()') IS NOT NULL")
        ).scalar_one(),
    }


NOTHING = {
    "policies": 0,
    "enabled": 0,
    "app_grants": 0,
    "default_privileges": 0,
    "function": False,
}


def _app_counts(url: str, agency: uuid.UUID | None) -> dict[str, int]:
    """Rows of each tenant table the app role sees, in a transaction bound to ``agency``."""
    database = make_url(url).database
    assert database is not None
    engine = create_engine(postgres.role_url(database, postgres.APP_ROLE))
    try:
        with engine.begin() as conn:
            if agency is not None:
                conn.execute(
                    text("SELECT set_config('app.agency_id', :a, true)"), {"a": str(agency)}
                )
            return {
                table: conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
                for table in ALL_TENANT_TABLES
            }
    finally:
        engine.dispose()


def test_upgrade_downgrade_upgrade(scratch_db: str) -> None:  # noqa: F811
    cfg = _alembic_config()
    engine = create_engine(scratch_db)
    try:
        command.upgrade(cfg, "e20c4a7d1b93")
        with engine.begin() as conn:
            _insert_rows(conn)
        command.upgrade(cfg, EXPAND)
        assert backfill(scratch_db).complete
        command.upgrade(cfg, CONTRACT)
        with engine.connect() as conn:
            values = _values(conn)
            assert _rls_state(conn) == NOTHING

        command.upgrade(cfg, RLS)
        with engine.connect() as conn:
            assert _revision(conn) == RLS
            assert rls.catalogue_problems(conn) == []
            assert _values(conn) == values  # the owner sees every row (maintenance policy)
        seen = _app_counts(scratch_db, DEFAULT_AGENCY_ID)
        assert seen == {table: len(rows) for table, rows in values.items()}
        assert set(_app_counts(scratch_db, None).values()) == {0}  # unbound: nothing
        assert set(_app_counts(scratch_db, uuid.uuid4()).values()) == {0}  # another agency

        command.downgrade(cfg, CONTRACT)
        with engine.connect() as conn:
            assert _revision(conn) == CONTRACT
            assert _rls_state(conn) == NOTHING
            assert _values(conn) == values

        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            assert rls.catalogue_problems(conn) == []
            assert _values(conn) == values
        command.check(cfg)  # the models agree with the schema
        assert IDS["campaign"] in {row[0] for row in values["campaigns"]}
    finally:
        engine.dispose()


def test_tables_created_later_get_the_grants(scratch_db: str) -> None:  # noqa: F811
    """What E2-05 relies on: its tables (created by the owner) need no grant of their own."""
    command.upgrade(_alembic_config(), "head")
    owner = create_engine(scratch_db)
    app = create_engine(postgres.as_role(owner, postgres.APP_ROLE))
    try:
        with owner.begin() as conn:
            conn.execute(
                text("CREATE TABLE later_table (id bigint GENERATED ALWAYS AS IDENTITY, note text)")
            )
        with app.begin() as conn:
            conn.execute(text("INSERT INTO later_table (note) VALUES ('from the app')"))
            conn.execute(text("UPDATE later_table SET note = 'changed'"))
            assert conn.execute(text("SELECT note FROM later_table")).scalar_one() == "changed"
            conn.execute(text("DELETE FROM later_table"))
        with owner.connect() as conn:
            problems = rls.catalogue_problems(conn)
        # Not on the allowlist and without policies: the catalogue says so.
        assert "later_table: no tenant_isolation policy" in problems
    finally:
        app.dispose()
        owner.dispose()


def test_it_refuses_to_run_as_another_user(
    scratch_db: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _alembic_config()
    command.upgrade(cfg, CONTRACT)
    monkeypatch.setattr(settings, "MIGRATION_DATABASE_URL", postgres.admin_url(scratch_db))
    with pytest.raises(RuntimeError, match="this migration runs as hireflow_owner, not test"):
        command.upgrade(cfg, RLS)
    engine = create_engine(scratch_db)
    with engine.connect() as conn:
        assert _revision(conn) == CONTRACT
        assert _rls_state(conn) == NOTHING
    engine.dispose()


def test_it_refuses_tables_another_role_owns(scratch_db: str) -> None:  # noqa: F811
    cfg = _alembic_config()
    command.upgrade(cfg, CONTRACT)
    admin = create_engine(postgres.admin_url(scratch_db))
    with admin.begin() as conn:  # as on a volume db-roles has not handed over yet
        conn.execute(text("ALTER TABLE campaigns OWNER TO test"))
    with pytest.raises(RuntimeError, match=r"not owned by hireflow_owner: campaigns \(test\)"):
        command.upgrade(cfg, RLS)

    postgres.apply_roles(str(admin.url.database))  # what `make db-roles` does
    command.upgrade(cfg, RLS)
    admin.dispose()


def test_roles_sql_hands_an_old_database_to_the_owner_and_is_idempotent(
    scratch_db: str,  # noqa: F811
) -> None:
    """A volume created before E2-04: everything belongs to the bootstrap superuser."""
    admin = create_engine(postgres.admin_url(scratch_db))
    with admin.begin() as conn:
        conn.execute(text("ALTER SCHEMA public OWNER TO test"))
        conn.execute(text("CREATE TABLE old_table (id serial PRIMARY KEY, name text)"))
        conn.execute(text("CREATE SEQUENCE old_free_sequence"))
        conn.execute(text("CREATE VIEW old_view AS SELECT id FROM old_table"))
        conn.execute(text("CREATE FUNCTION old_function() RETURNS int LANGUAGE sql AS 'SELECT 1'"))
        conn.execute(text("INSERT INTO old_table (name) VALUES ('kept')"))

    database = str(admin.url.database)
    postgres.apply_roles(database)
    postgres.apply_roles(database)  # again: nothing to do, no error

    query = text(
        "SELECT c.relname, pg_get_userbyid(c.relowner) FROM pg_class c "
        "WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'S', 'v') "
        "ORDER BY 1"
    )
    with admin.connect() as conn:
        owners = dict(conn.execute(query).tuples().all())
        assert owners == {
            "old_free_sequence": "hireflow_owner",
            "old_table": "hireflow_owner",
            "old_table_id_seq": "hireflow_owner",  # followed its table
            "old_view": "hireflow_owner",
        }
        assert (
            conn.execute(
                text("SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE proname = 'old_function'")
            ).scalar_one()
            == "hireflow_owner"
        )
        assert (
            conn.execute(
                text("SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname = 'public'")
            ).scalar_one()
            == "hireflow_owner"
        )
        assert conn.execute(text("SELECT name FROM old_table")).scalar_one() == "kept"
        roles = conn.execute(
            text(
                "SELECT rolname, rolsuper, rolbypassrls, rolcanlogin, rolcreaterole, "
                "rolcreatedb FROM pg_roles WHERE rolname LIKE 'hireflow_%' ORDER BY 1"
            )
        ).all()
        assert [tuple(r) for r in roles] == [
            ("hireflow_app", False, False, True, False, False),
            ("hireflow_owner", False, False, True, False, False),
        ]
    admin.dispose()
