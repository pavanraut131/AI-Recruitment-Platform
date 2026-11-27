"""E2-01 tenancy expand migration (``e2a01d5c8f36``): schema, default rows and reversibility.

Two kinds of test, as for the E0 expand migrations:

- **Schema, default rows and constraints** on the session database (at ``head``), each in the
  usual rolled-back transaction: ``agencies`` and ``clients`` exist with the default agency and
  client (the ids of ``app.platform.tenancy``), and every existing tenant table has an
  ``agency_id`` (``NOT NULL`` since the E2-02 contract) with no foreign key to ``agencies``
  (ADR 0003).
- **Upgrade -> downgrade -> upgrade** on a throwaway database (``scratch_db``): rows written at
  the previous head survive, their tables are **not rewritten** (same ``relfilenode``: the
  ``ADD COLUMN``s are catalog-only) and get ``agency_id`` ``NULL`` (the E2-02 backfill fills
  it); the downgrade restores the previous schema; back at ``head`` (through the backfill, which
  the E2-02 contract requires) ``alembic check`` is clean.
"""

from __future__ import annotations

import importlib.util
import uuid
from types import ModuleType
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity import service as identity_service
from app.platform.tenancy import bind
from tests import postgres
from tests.default_agency import DEFAULT_AGENCY_ID, DEFAULT_CLIENT_ID
from tests.migrations.tenancy import upgrade_to_head_through_backfill
from tests.migrations.test_expand_e0_14 import (  # noqa: F401 (scratch_db is a fixture)
    _alembic_config,
    scratch_db,
)

BEFORE = "e20c4a7d1b93"
EXPAND = "e2a01d5c8f36"
MIGRATION = postgres.BACKEND_DIR / "alembic" / "versions" / f"{EXPAND}_tenancy_expand.py"

NEW_TABLES = {"agencies", "clients"}
TENANT_TABLES = (
    "campaigns",
    "applications",
    "people",
    "person_identifiers",
    "identity_conflicts",
    "person_resumes",
    "resumes",
)
# Every column the migration adds to an existing table: (table, column).
NEW_COLUMNS = {
    *((table, "agency_id") for table in TENANT_TABLES),
    ("campaigns", "client_id"),
    ("campaigns", "created_by_user_id"),
    ("resumes", "uploaded_by_user_id"),
}


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(EXPAND, MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── Schema and default rows on the session database ───────────
async def _inspect(db: AsyncSession, fn: Any) -> Any:
    conn = await db.connection()
    return await conn.run_sync(lambda sync_conn: fn(inspect(sync_conn)))


def test_the_migration_writes_the_ids_the_code_uses() -> None:
    """The migration spells the ids out (it must not change with the code); they must agree."""
    migration = _migration()
    assert uuid.UUID(migration.DEFAULT_AGENCY_ID) == DEFAULT_AGENCY_ID
    assert uuid.UUID(migration.DEFAULT_CLIENT_ID) == DEFAULT_CLIENT_ID
    assert set(migration.TENANT_TABLES) == set(TENANT_TABLES)


async def test_the_default_agency_and_client_exist(db_session: AsyncSession) -> None:
    agency = await identity_service.get_agency(db_session, DEFAULT_AGENCY_ID)
    assert agency is not None
    assert (agency.name, agency.slug, agency.status) == ("Default agency", "default", "active")
    assert await identity_service.get_agency(db_session, uuid.uuid4()) is None

    clients = (
        await db_session.execute(text("SELECT id, agency_id, name, status FROM clients"))
    ).all()
    assert [tuple(row) for row in clients] == [
        (DEFAULT_CLIENT_ID, DEFAULT_AGENCY_ID, "General", "active")
    ]


async def test_new_columns_are_uuids(db_session: AsyncSession) -> None:
    def columns(i: Any) -> dict[tuple[str, str], tuple[str, bool]]:
        return {
            (table, c["name"]): (str(c["type"]), c["nullable"])
            for table in TENANT_TABLES
            for c in i.get_columns(table)
        }

    cols = await _inspect(db_session, columns)
    for key in NEW_COLUMNS:
        # The expand adds them nullable (see the upgrade test below); since the E2-02 contract
        # agency_id and client_id are NOT NULL at head, the user ids stay nullable.
        required = key[1] in ("agency_id", "client_id")
        assert cols[key] == ("UUID", not required), key


async def test_new_tables_and_their_constraints(db_session: AsyncSession) -> None:
    def describe(i: Any) -> dict[str, Any]:
        return {
            table: {
                "columns": {
                    c["name"]: (str(c["type"]), c["nullable"]) for c in i.get_columns(table)
                },
                "uniques": {
                    (u["name"], tuple(u["column_names"])) for u in i.get_unique_constraints(table)
                },
                "checks": {c["name"] for c in i.get_check_constraints(table)},
                "fks": i.get_foreign_keys(table),
            }
            for table in NEW_TABLES
        }

    d = await _inspect(db_session, describe)
    assert d["agencies"]["columns"] == {
        "id": ("UUID", False),
        "name": ("VARCHAR(200)", False),
        "slug": ("VARCHAR(100)", False),
        "status": ("VARCHAR(20)", False),
        "created_at": ("TIMESTAMP", False),
        "updated_at": ("TIMESTAMP", False),
    }
    assert d["agencies"]["uniques"] == {("uq_agencies_slug", ("slug",))}
    assert d["agencies"]["checks"] == {"ck_agencies_status"}
    assert d["clients"]["columns"] == {
        "id": ("UUID", False),
        "agency_id": ("UUID", False),  # a new table: NOT NULL from the start
        "name": ("VARCHAR(200)", False),
        "status": ("VARCHAR(20)", False),
        "created_at": ("TIMESTAMP", False),
        "updated_at": ("TIMESTAMP", False),
    }
    assert d["clients"]["uniques"] == {("uq_clients_agency_id_id", ("agency_id", "id"))}
    assert d["clients"]["checks"] == {"ck_clients_status"}
    # clients.agency_id refers to identity's agencies: no foreign key (ADR 0003).
    assert d["agencies"]["fks"] == d["clients"]["fks"] == []


async def _add_client(
    db: AsyncSession, agency_id: uuid.UUID, name: str, status: str = "active"
) -> None:
    await db.execute(
        text("INSERT INTO clients (id, agency_id, name, status) VALUES (:id, :a, :n, :s)"),
        {"id": uuid.uuid4(), "a": agency_id, "n": name, "s": status},
    )


async def test_a_client_name_is_unique_per_agency_ignoring_case(db_session: AsyncSession) -> None:
    other_agency = uuid.uuid4()  # no foreign key: any id will do here
    # Written as that agency (row-level security, E2-04), on the test's connection.
    other = AsyncSession(bind=db_session.bind, join_transaction_mode="create_savepoint")
    bind(other, other_agency)
    await _add_client(other, other_agency, "General")  # same name, another agency: fine
    await other.close()
    await _add_client(db_session, DEFAULT_AGENCY_ID, "Acme")

    with pytest.raises(IntegrityError, match="uq_clients_agency_id_lower_name"):
        async with db_session.begin_nested():
            await _add_client(db_session, DEFAULT_AGENCY_ID, "gENERAL")


async def test_client_and_agency_status_are_checked(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="ck_clients_status"):
        async with db_session.begin_nested():
            await _add_client(db_session, DEFAULT_AGENCY_ID, "Acme", status="deleted")
    with pytest.raises(IntegrityError, match="ck_agencies_status"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("INSERT INTO agencies (id, name, slug, status) VALUES (:id, 'A', 'a', 'x')"),
                {"id": uuid.uuid4()},
            )
    with pytest.raises(IntegrityError, match="uq_agencies_slug"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO agencies (id, name, slug, status) VALUES (:id, 'A', 'default', 'active')"
                ),
                {"id": uuid.uuid4()},
            )


# ── Upgrade -> downgrade -> upgrade on a throwaway database ───
IDS = {
    name: uuid.UUID(f"{n:08x}-0000-4000-8000-000000000000")
    for n, name in enumerate(
        ("campaign", "resume", "person", "other_person", "identifier", "conflict", "application"),
        start=1,
    )
}


def _insert_rows(conn: Connection) -> None:
    """One row in each of the seven tenant tables, written as the E0-20 schema allows."""
    conn.execute(
        text(
            "INSERT INTO campaigns (id, title, role, required_skills, status) "
            "VALUES (:id, 'C', 'Engineer', '[]', 'active')"
        ),
        {"id": IDS["campaign"]},
    )
    conn.execute(
        text(
            "INSERT INTO resumes (id, campaign_id, original_filename, storage_path, "
            "file_size_bytes, status, retry_count, person_id) "
            "VALUES (:id, :cid, 'cv.pdf', 'k', 1, 'done', 0, :pid)"
        ),
        {"id": IDS["resume"], "cid": IDS["campaign"], "pid": IDS["person"]},
    )
    for person in ("person", "other_person"):
        conn.execute(
            text(
                "INSERT INTO people (id, skills, education, work_experience, certifications, "
                "projects) VALUES (:id, '[]', '[]', '[]', '[]', '[]')"
            ),
            {"id": IDS[person]},
        )
    conn.execute(
        text(
            "INSERT INTO person_identifiers (id, person_id, kind, value_normalized, raw_value) "
            "VALUES (:id, :pid, 'email', 'a@example.com', 'a@example.com')"
        ),
        {"id": IDS["identifier"], "pid": IDS["person"]},
    )
    conn.execute(
        text(
            "INSERT INTO identity_conflicts (id, resume_id, chosen_person_id, "
            "conflicting_person_id, reason) VALUES (:id, :rid, :a, :b, 'test')"
        ),
        {"id": IDS["conflict"], "rid": IDS["resume"], "a": IDS["person"], "b": IDS["other_person"]},
    )
    conn.execute(
        text(
            "INSERT INTO person_resumes (resume_id, person_id, uploaded_at, matched_existing, "
            "conflict_id) VALUES (:rid, :pid, now(), false, :cid)"
        ),
        {"rid": IDS["resume"], "pid": IDS["person"], "cid": IDS["conflict"]},
    )
    conn.execute(
        text(
            "INSERT INTO applications (id, campaign_id, person_id, resume_id, strengths, "
            "weaknesses, missing_skills) VALUES (:id, :cid, :pid, :rid, '[]', '[]', '[]')"
        ),
        {
            "id": IDS["application"],
            "cid": IDS["campaign"],
            "pid": IDS["person"],
            "rid": IDS["resume"],
        },
    )


def _relfilenodes(conn: Connection) -> dict[str, int]:
    rows = conn.execute(
        text("SELECT relname, relfilenode FROM pg_class WHERE relname = ANY(:names)"),
        {"names": list(TENANT_TABLES)},
    )
    return {name: node for name, node in rows}


def _row_counts(conn: Connection) -> dict[str, int]:
    return {
        table: conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
        for table in TENANT_TABLES
    }


def _columns(conn: Connection) -> set[tuple[str, str]]:
    insp = inspect(conn)
    return {(table, c["name"]) for table in TENANT_TABLES for c in insp.get_columns(table)}


def test_upgrade_downgrade_upgrade_keeps_rows_and_rewrites_nothing(scratch_db: str) -> None:  # noqa: F811
    cfg = _alembic_config()
    engine = create_engine(scratch_db)
    try:
        command.upgrade(cfg, BEFORE)
        with engine.begin() as conn:
            _insert_rows(conn)
            nodes_before = _relfilenodes(conn)
            columns_before = _columns(conn)
            counts_before = _row_counts(conn)
            assert not NEW_COLUMNS & columns_before

        command.upgrade(cfg, EXPAND)
        with engine.connect() as conn:
            assert set(inspect(conn).get_table_names()) >= NEW_TABLES
            # Same physical files: the ADD COLUMNs did not rewrite any table.
            assert _relfilenodes(conn) == nodes_before
            assert _columns(conn) == columns_before | NEW_COLUMNS
            assert _row_counts(conn) == counts_before
            # Existing rows are left for the backfill (E2-02).
            for table in TENANT_TABLES:
                assert conn.execute(text(f"SELECT agency_id FROM {table}")).scalar() is None
            assert conn.execute(text("SELECT client_id FROM campaigns")).scalar() is None
            assert conn.execute(text("SELECT id, slug FROM agencies")).one() == (
                DEFAULT_AGENCY_ID,
                "default",
            )
            assert conn.execute(text("SELECT id, agency_id, name FROM clients")).one() == (
                DEFAULT_CLIENT_ID,
                DEFAULT_AGENCY_ID,
                "General",
            )

        command.downgrade(cfg, BEFORE)
        with engine.connect() as conn:
            assert not NEW_TABLES & set(inspect(conn).get_table_names())
            assert _columns(conn) == columns_before
            assert _row_counts(conn) == counts_before

        # Through the tenancy backfill: the contract (E2-02) refuses rows without an agency.
        upgrade_to_head_through_backfill(cfg, scratch_db)
        command.check(cfg)  # raises if the models and the migrated schema differ
    finally:
        engine.dispose()
