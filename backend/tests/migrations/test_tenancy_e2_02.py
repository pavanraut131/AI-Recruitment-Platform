"""E2-02 tenancy contract migration (``e2b02c7a4d19``): schema, constraints, guard, reversibility.

- **Schema and constraints** on the session database (at ``head``), each in the usual rolled-back
  transaction: ``agency_id`` (and ``campaigns.client_id``) are ``NOT NULL``; the
  ``UNIQUE(agency_id, id)`` targets, the per-agency identifier unique and the composite
  same-module foreign keys exist, are valid and **reject a child of another agency**; the
  indexes exist and are valid; no helper check constraint is left behind.
- **On a throwaway database** (``scratch_db``), from the expand revision with a row in every
  tenant table and no agency anywhere (data written before E2-01):
  - the contract **refuses** before the backfill, naming the tables, and changes nothing;
  - after the backfill it applies **without rewriting any table** (same ``relfilenode``s);
  - downgrade restores the expand schema (single-column keys under their old names, the
    global identifier unique, nullable columns) and keeps every value; upgrading again works
    and ``alembic check`` is clean;
  - the downgrade refuses while two agencies share an identifier;
  - a run that failed half-way (an invalid index left by a cancelled ``CONCURRENTLY`` build,
    a validated check still in place) finishes when run again;
  - a lock held on a table makes it fail fast (``lock_timeout``) with nothing changed.
"""

from __future__ import annotations

import importlib.util
import time
import uuid
from types import ModuleType
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.tenancy import DEFAULT_AGENCY_ID, DEFAULT_CLIENT_ID, bind
from tests import postgres
from tests.factories import make_campaign, make_resume
from tests.migrations.tenancy import CONTRACT, EXPAND, backfill
from tests.migrations.test_expand_e0_14 import (  # noqa: F401 (scratch_db is a fixture)
    _alembic_config,
    scratch_db,
)
from tests.migrations.test_tenancy_e2_01 import IDS, TENANT_TABLES, _insert_rows

MIGRATION = postgres.BACKEND_DIR / "alembic" / "versions" / f"{CONTRACT}_tenancy_contract.py"
OTHER_AGENCY = uuid.UUID("00000000-0000-4000-8000-0000000000b2")
OTHER_CLIENT = uuid.UUID("00000000-0000-4000-8000-0000000000c2")

REQUIRED = {(table, "agency_id") for table in TENANT_TABLES} | {("campaigns", "client_id")}
NEW_UNIQUES = {
    ("campaigns", "uq_campaigns_agency_id_id", ("agency_id", "id")),
    ("people", "uq_people_agency_id_id", ("agency_id", "id")),
    (
        "person_identifiers",
        "uq_person_identifiers_agency_id_kind_value_normalized",
        ("agency_id", "kind", "value_normalized"),
    ),
}
NEW_INDEXES = {
    "ix_campaigns_agency_id_status_updated_at",
    "ix_people_agency_id_updated_at",
    "ix_resumes_agency_id_campaign_id",
}
# name -> (table, columns, referenced table, referenced columns, on delete)
NEW_FOREIGN_KEYS = {
    "fk_campaigns_agency_id_client_id_clients": (
        "campaigns",
        ("agency_id", "client_id"),
        "clients",
        ("agency_id", "id"),
        "a",  # NO ACTION
    ),
    "fk_applications_agency_id_campaign_id_campaigns": (
        "applications",
        ("agency_id", "campaign_id"),
        "campaigns",
        ("agency_id", "id"),
        "c",
    ),
    "fk_person_identifiers_agency_id_person_id_people": (
        "person_identifiers",
        ("agency_id", "person_id"),
        "people",
        ("agency_id", "id"),
        "c",
    ),
    "fk_identity_conflicts_agency_id_chosen_person_id_people": (
        "identity_conflicts",
        ("agency_id", "chosen_person_id"),
        "people",
        ("agency_id", "id"),
        "c",
    ),
    "fk_identity_conflicts_agency_id_conflicting_person_id_people": (
        "identity_conflicts",
        ("agency_id", "conflicting_person_id"),
        "people",
        ("agency_id", "id"),
        "c",
    ),
    "fk_person_resumes_agency_id_person_id_people": (
        "person_resumes",
        ("agency_id", "person_id"),
        "people",
        ("agency_id", "id"),
        "c",
    ),
}
# The single-column keys and the global unique the contract replaces (Postgres' default names).
OLD_FOREIGN_KEYS = {
    "applications_campaign_id_fkey": ("applications", ("campaign_id",), "campaigns", ("id",), "c"),
    "person_identifiers_person_id_fkey": (
        "person_identifiers",
        ("person_id",),
        "people",
        ("id",),
        "c",
    ),
    "identity_conflicts_chosen_person_id_fkey": (
        "identity_conflicts",
        ("chosen_person_id",),
        "people",
        ("id",),
        "c",
    ),
    "identity_conflicts_conflicting_person_id_fkey": (
        "identity_conflicts",
        ("conflicting_person_id",),
        "people",
        ("id",),
        "c",
    ),
    "person_resumes_person_id_fkey": ("person_resumes", ("person_id",), "people", ("id",), "c"),
}
OLD_UNIQUE = ("person_identifiers", "uq_person_identifiers_kind_value_normalized", ("kind", "value_normalized"))  # fmt: skip


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(CONTRACT, MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── Catalog reads (sync, run on either kind of connection) ────
FOREIGN_KEYS_SQL = """
SELECT c.conname, c.conrelid::regclass::text, c.confrelid::regclass::text, c.confdeltype::text,
       c.convalidated,
       ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY AS k(n, i)
             JOIN pg_attribute AS a ON a.attrelid = c.conrelid AND a.attnum = k.n ORDER BY k.i),
       ARRAY(SELECT a.attname FROM unnest(c.confkey) WITH ORDINALITY AS k(n, i)
             JOIN pg_attribute AS a ON a.attrelid = c.confrelid AND a.attnum = k.n ORDER BY k.i)
FROM pg_constraint AS c WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace
"""


def _catalog(conn: Connection) -> dict[str, Any]:
    fks = {
        name: (table, tuple(cols), parent, tuple(parent_cols), on_delete)
        for name, table, parent, on_delete, _, cols, parent_cols in conn.execute(
            text(FOREIGN_KEYS_SQL)
        )
    }
    unvalidated = set(
        conn.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE NOT convalidated AND connamespace = 'public'::regnamespace"
            )
        ).scalars()
    )
    uniques = {
        (table, name, tuple(cols))
        for name, table, cols in conn.execute(
            text(
                "SELECT c.conname, c.conrelid::regclass::text, "
                "ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY AS k(n, i) "
                "JOIN pg_attribute AS a ON a.attrelid = c.conrelid AND a.attnum = k.n "
                "ORDER BY k.i) "
                "FROM pg_constraint AS c WHERE c.contype = 'u' "
                "AND c.connamespace = 'public'::regnamespace"
            )
        )
    }
    indexes = dict(
        conn.execute(
            text(
                "SELECT c.relname, i.indisvalid FROM pg_index AS i "
                "JOIN pg_class AS c ON c.oid = i.indexrelid "
                "WHERE c.relnamespace = 'public'::regnamespace"
            )
        ).all()
    )
    not_null = {
        (table, column)
        for table, column in REQUIRED
        if conn.execute(
            text(
                "SELECT attnotnull FROM pg_attribute "
                "WHERE attrelid = CAST(:t AS regclass) AND attname = :c"
            ),
            {"t": table, "c": column},
        ).scalar_one()
    }
    checks = set(
        conn.execute(
            text(
                "SELECT conname FROM pg_constraint WHERE contype = 'c' "
                "AND conname LIKE 'ck\\_%\\_not\\_null'"
            )
        ).scalars()
    )
    return {
        "fks": fks,
        "unvalidated": unvalidated,
        "uniques": uniques,
        "indexes": indexes,
        "not_null": not_null,
        "checks": checks,
    }


async def _session_catalog(db: AsyncSession) -> dict[str, Any]:
    conn = await db.connection()
    return await conn.run_sync(_catalog)


def _assert_contracted(catalog: dict[str, Any]) -> None:
    assert catalog["not_null"] == REQUIRED
    assert catalog["checks"] == set()  # the helper checks are dropped
    assert catalog["uniques"] >= NEW_UNIQUES
    assert OLD_UNIQUE not in catalog["uniques"]
    for name, fk in NEW_FOREIGN_KEYS.items():
        assert catalog["fks"][name] == fk, name
    assert not OLD_FOREIGN_KEYS.keys() & catalog["fks"].keys()
    assert catalog["unvalidated"] == set()
    for name in NEW_INDEXES | {u[1] for u in NEW_UNIQUES}:
        assert catalog["indexes"][name] is True, name  # exists and valid


# ── At head ───────────────────────────────────────────────────
def test_the_migration_lists_every_tenant_table() -> None:
    migration = _migration()
    assert set(migration.TENANT_TABLES) == set(TENANT_TABLES)
    assert {fk.name for fk in migration.FOREIGN_KEYS} == NEW_FOREIGN_KEYS.keys()
    assert {fk.replaces for fk in migration.FOREIGN_KEYS} - {None} == OLD_FOREIGN_KEYS.keys()


async def test_the_schema_at_head_is_contracted(db_session: AsyncSession) -> None:
    _assert_contracted(await _session_catalog(db_session))


async def _expect_violation(db: AsyncSession, constraint: str, sql: str, **params: Any) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        async with db.begin_nested():
            await db.execute(text(sql), params)


async def _other_agency(db: AsyncSession) -> AsyncSession:
    """Agency B and its client; returns a session bound to B on the test's connection. Row-level
    security (E2-04) accepts B's rows only from such a session, so the composite keys below are
    exercised with a row that passes the policy and then fails the key."""
    await db.execute(
        text("INSERT INTO agencies (id, name, slug, status) VALUES (:a, 'B', 'b', 'active')"),
        {"a": OTHER_AGENCY},
    )
    other = AsyncSession(bind=db.bind, join_transaction_mode="create_savepoint")
    bind(other, OTHER_AGENCY)
    await other.execute(
        text("INSERT INTO clients (id, agency_id, name, status) VALUES (:c, :a, 'B', 'active')"),
        {"a": OTHER_AGENCY, "c": OTHER_CLIENT},
    )
    return other


async def test_a_child_of_another_agency_is_rejected(db_session: AsyncSession) -> None:
    other = await _other_agency(db_session)
    campaign = await make_campaign(db_session)
    resume = await make_resume(db_session, campaign)

    # A campaign run for another agency's client.
    await _expect_violation(
        db_session,
        "fk_campaigns_agency_id_client_id_clients",
        "INSERT INTO campaigns (id, agency_id, client_id, title, role, required_skills, status) "
        "VALUES (:id, :a, :c, 'T', 'R', '[]', 'active')",
        id=uuid.uuid4(),
        a=DEFAULT_AGENCY_ID,
        c=OTHER_CLIENT,
    )
    # An application in another agency than its campaign.
    await _expect_violation(
        other,
        "fk_applications_agency_id_campaign_id_campaigns",
        "INSERT INTO applications (id, agency_id, campaign_id, person_id, resume_id, strengths, "
        "weaknesses, missing_skills) VALUES (:id, :a, :c, :p, :r, '[]', '[]', '[]')",
        id=uuid.uuid4(),
        a=OTHER_AGENCY,
        c=campaign.id,
        p=uuid.uuid4(),
        r=resume.id,
    )
    # An identifier, a resolution or a conflict in another agency than its Person.
    person = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO people (id, agency_id, skills, education, work_experience, "
            "certifications, projects) VALUES (:id, :a, '[]', '[]', '[]', '[]', '[]')"
        ),
        {"id": person, "a": DEFAULT_AGENCY_ID},
    )
    await _expect_violation(
        other,
        "fk_person_identifiers_agency_id_person_id_people",
        "INSERT INTO person_identifiers (id, agency_id, person_id, kind, value_normalized, "
        "raw_value) VALUES (:id, :a, :p, 'email', 'x@example.com', 'x@example.com')",
        id=uuid.uuid4(),
        a=OTHER_AGENCY,
        p=person,
    )
    await _expect_violation(
        other,
        "fk_person_resumes_agency_id_person_id_people",
        "INSERT INTO person_resumes (resume_id, agency_id, person_id, uploaded_at, "
        "matched_existing) VALUES (:r, :a, :p, now(), false)",
        r=uuid.uuid4(),
        a=OTHER_AGENCY,
        p=person,
    )
    await _expect_violation(
        other,
        "fk_identity_conflicts_agency_id_chosen_person_id_people",
        "INSERT INTO identity_conflicts (id, agency_id, resume_id, chosen_person_id, "
        "conflicting_person_id, reason) VALUES (:id, :a, :r, :p, :p, 'x')",
        id=uuid.uuid4(),
        a=OTHER_AGENCY,
        r=uuid.uuid4(),
        p=person,
    )
    # And no agency at all: as the app role, row-level security refuses it before the NOT NULL
    # constraint is checked (the constraint itself is in the catalogue check above).
    with pytest.raises(DBAPIError, match='row-level security policy for table "resumes"'):
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE resumes SET agency_id = NULL WHERE id = :id"), {"id": resume.id}
            )
    await other.close()


async def test_an_identifier_is_unique_per_agency(db_session: AsyncSession) -> None:
    sessions = {DEFAULT_AGENCY_ID: db_session, OTHER_AGENCY: await _other_agency(db_session)}
    people = {DEFAULT_AGENCY_ID: uuid.uuid4(), OTHER_AGENCY: uuid.uuid4()}
    insert_identifier = (
        "INSERT INTO person_identifiers (id, agency_id, person_id, kind, value_normalized, "
        "raw_value) VALUES (:id, :a, :p, 'email', 'asha@example.com', 'asha@example.com')"
    )
    for agency, person in people.items():
        await sessions[agency].execute(
            text(
                "INSERT INTO people (id, agency_id, skills, education, work_experience, "
                "certifications, projects) VALUES (:id, :a, '[]', '[]', '[]', '[]', '[]')"
            ),
            {"id": person, "a": agency},
        )
        # The same value in two agencies: two People, both stored.
        await sessions[agency].execute(
            text(insert_identifier), {"id": uuid.uuid4(), "a": agency, "p": person}
        )

    await _expect_violation(
        db_session,
        "uq_person_identifiers_agency_id_kind_value_normalized",
        insert_identifier,
        id=uuid.uuid4(),
        a=DEFAULT_AGENCY_ID,
        p=people[DEFAULT_AGENCY_ID],
    )
    await sessions[OTHER_AGENCY].close()


# ── On a throwaway database ───────────────────────────────────
def _relfilenodes(conn: Connection) -> dict[str, int]:
    rows = conn.execute(
        text("SELECT relname, relfilenode FROM pg_class WHERE relname = ANY(:names)"),
        {"names": list(TENANT_TABLES)},
    )
    return {name: node for name, node in rows}


def _values(conn: Connection) -> dict[str, list[tuple[Any, ...]]]:
    return {
        table: sorted(map(tuple, conn.execute(text(f"SELECT * FROM {table}")).all()), key=str)
        for table in TENANT_TABLES
    }


def _revision(conn: Connection) -> str:
    return str(conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one())


def test_the_contract_refuses_to_run_before_the_backfill(scratch_db: str) -> None:  # noqa: F811
    cfg = _alembic_config()
    engine = create_engine(scratch_db)
    try:
        command.upgrade(cfg, "e20c4a7d1b93")
        with engine.begin() as conn:
            _insert_rows(conn)  # written before E2-01: no agency once expanded
        command.upgrade(cfg, EXPAND)
        with engine.connect() as conn:
            before = _catalog(conn)

        with pytest.raises(Exception, match="tenancy backfill has not completed") as refused:
            command.upgrade(cfg, "head")
        message = str(refused.value)
        for table in TENANT_TABLES:
            rows = 2 if table == "people" else 1  # _insert_rows writes two People
            assert f"{table}: {rows} row(s) without agency_id" in message
        assert "campaigns: 1 row(s) without client_id" in message
        assert "backfill_tenancy" in message

        with engine.connect() as conn:
            assert _revision(conn) == EXPAND
            assert _catalog(conn) == before  # nothing changed

        # A partial backfill is refused too.
        with engine.begin() as conn:
            conn.execute(text("UPDATE campaigns SET agency_id = :a"), {"a": DEFAULT_AGENCY_ID})
        with pytest.raises(Exception, match="campaigns: 1 row\\(s\\) without client_id"):
            command.upgrade(cfg, "head")
    finally:
        engine.dispose()


def test_upgrade_downgrade_upgrade_after_the_backfill(scratch_db: str) -> None:  # noqa: F811
    cfg = _alembic_config()
    engine = create_engine(scratch_db)
    try:
        command.upgrade(cfg, "e20c4a7d1b93")
        with engine.begin() as conn:
            _insert_rows(conn)
        command.upgrade(cfg, EXPAND)
        assert backfill(scratch_db).complete
        with engine.connect() as conn:
            expanded = _catalog(conn)
            nodes = _relfilenodes(conn)
            values = _values(conn)
            assert conn.execute(text("SELECT client_id FROM campaigns")).scalar() == (
                DEFAULT_CLIENT_ID
            )

        started = time.perf_counter()
        command.upgrade(cfg, CONTRACT)
        elapsed = time.perf_counter() - started
        with engine.connect() as conn:
            assert _revision(conn) == CONTRACT
            _assert_contracted(_catalog(conn))
            # No table was rewritten (SET NOT NULL used the validated checks; the indexes were
            # built beside the tables) and every value is as it was.
            assert _relfilenodes(conn) == nodes
            assert _values(conn) == values
        print(f"\ncontract on the small test data: {elapsed:.2f}s")

        command.downgrade(cfg, EXPAND)
        with engine.connect() as conn:
            assert _revision(conn) == EXPAND
            assert _catalog(conn) == expanded  # exactly the expand schema again
            assert _values(conn) == values

        command.upgrade(cfg, "head")
        command.check(cfg)  # the models agree with the contracted schema
    finally:
        engine.dispose()


def test_the_downgrade_refuses_while_two_agencies_share_an_identifier(scratch_db: str) -> None:  # noqa: F811
    cfg = _alembic_config()
    engine = create_engine(scratch_db)
    try:
        command.upgrade(cfg, CONTRACT)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO agencies (id, name, slug, status) VALUES (:b, 'B', 'b', 'active')"
                ),
                {"b": OTHER_AGENCY},
            )
            for agency in (DEFAULT_AGENCY_ID, OTHER_AGENCY):
                person = uuid.uuid4()
                conn.execute(
                    text(
                        "INSERT INTO people (id, agency_id, skills, education, work_experience, "
                        "certifications, projects) VALUES (:id, :a, '[]', '[]', '[]', '[]', '[]')"
                    ),
                    {"id": person, "a": agency},
                )
                conn.execute(
                    text(
                        "INSERT INTO person_identifiers (id, agency_id, person_id, kind, "
                        "value_normalized, raw_value) VALUES (:id, :a, :p, 'email', "
                        "'asha@example.com', 'asha@example.com')"
                    ),
                    {"id": uuid.uuid4(), "a": agency, "p": person},
                )

        with pytest.raises(Exception, match="stored in more than one agency"):
            command.downgrade(cfg, EXPAND)
        with engine.connect() as conn:
            assert _revision(conn) == CONTRACT
            _assert_contracted(_catalog(conn))
    finally:
        engine.dispose()


def test_a_run_that_failed_half_way_finishes_when_run_again(scratch_db: str) -> None:  # noqa: F811
    """The state a failure in step 3 leaves (steps 1 and 2 committed: validated checks, the
    indexes built), with one index left **invalid**, as a cancelled ``CONCURRENTLY`` build
    leaves it. Upgrading again drops and rebuilds it and finishes."""
    cfg = _alembic_config()
    engine = create_engine(scratch_db)
    try:
        command.upgrade(cfg, "e20c4a7d1b93")
        with engine.begin() as conn:
            _insert_rows(conn)
        command.upgrade(cfg, EXPAND)
        assert backfill(scratch_db).complete
        with engine.begin() as conn:
            for table, column in sorted(REQUIRED):
                name = f"ck_{table}_{column}_not_null"
                conn.execute(
                    text(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({column} IS NOT NULL)")
                )
            conn.execute(
                text("CREATE UNIQUE INDEX uq_people_agency_id_id ON people (agency_id, id)")
            )
            conn.execute(
                text(
                    "CREATE INDEX ix_resumes_agency_id_campaign_id ON resumes (agency_id, campaign_id)"
                )
            )
        # As the test container's superuser (the owner role may not write the catalog): mark
        # it as a failed build would.
        admin = create_engine(postgres.admin_url(scratch_db))
        with admin.begin() as conn:
            conn.execute(
                text(
                    "UPDATE pg_index SET indisvalid = false "
                    "WHERE indexrelid = 'ix_resumes_agency_id_campaign_id'::regclass"
                )
            )
        admin.dispose()
        with engine.connect() as conn:
            assert _catalog(conn)["indexes"]["ix_resumes_agency_id_campaign_id"] is False

        command.upgrade(cfg, "head")

        with engine.connect() as conn:
            _assert_contracted(_catalog(conn))
        command.check(cfg)
    finally:
        engine.dispose()


def test_a_held_lock_makes_it_fail_fast_with_nothing_changed(scratch_db: str) -> None:  # noqa: F811
    cfg = _alembic_config()
    engine = create_engine(scratch_db)
    try:
        command.upgrade(cfg, EXPAND)
        with engine.connect() as conn:
            before = _catalog(conn)

        with engine.connect() as holder:
            holder.execute(text("LOCK TABLE resumes IN ACCESS SHARE MODE"))  # a long reader
            started = time.perf_counter()
            with pytest.raises(Exception, match="lock timeout"):
                command.upgrade(cfg, "head")
            waited = time.perf_counter() - started
            holder.rollback()

        assert 4 < waited < 30  # lock_timeout is 5 s
        with engine.connect() as conn:
            assert _revision(conn) == EXPAND
            assert _catalog(conn) == before
        command.upgrade(cfg, "head")
    finally:
        engine.dispose()


def test_campaign_deletes_still_cascade_through_the_composite_key(scratch_db: str) -> None:  # noqa: F811
    cfg = _alembic_config()
    engine = create_engine(scratch_db)
    try:
        command.upgrade(cfg, "e20c4a7d1b93")
        with engine.begin() as conn:
            _insert_rows(conn)
        command.upgrade(cfg, EXPAND)
        assert backfill(scratch_db).complete
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM campaigns WHERE id = :id"), {"id": IDS["campaign"]})
            assert conn.execute(text("SELECT count(*) FROM applications")).scalar() == 0
            conn.execute(text("DELETE FROM people WHERE id = :id"), {"id": IDS["person"]})
            for table in ("person_identifiers", "identity_conflicts", "person_resumes"):
                assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0
    finally:
        engine.dispose()
