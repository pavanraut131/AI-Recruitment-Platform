"""E2-05 identity core migration (``e2c05b3f7a21``): schema and constraints, reversibility, and
the grants for the app role that E2-04 introduces.

- **Schema** on the session database (at ``head``), in the usual rolled-back transaction: the
  five tables exist with their constraints (normalised unique email, roles, owners and admins
  always see every client, one membership per agency and user).
- **Upgrade -> downgrade -> upgrade** on a throwaway database with rows in every new table:
  the downgrade removes the five tables (and nothing else), the second upgrade recreates them,
  and ``alembic check`` is clean.
- **Grants.** With a role ``hireflow_app`` present and default privileges that would give it
  everything on new tables (what E2-04 sets up), the migration leaves ``audit_events``
  append-only for it (``SELECT``, ``INSERT``; no ``UPDATE``, ``DELETE`` or ``TRUNCATE``) and the
  other four tables read-write. Without the role the grant block does nothing (every other
  test).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.default_agency import DEFAULT_AGENCY_ID
from tests.migrations.test_expand_e0_14 import (  # noqa: F401 (scratch_db is a fixture)
    _alembic_config,
    scratch_db,
)

BEFORE = "e2b02c7a4d19"
REVISION = "e2c05b3f7a21"
TABLES = {"users", "memberships", "membership_clients", "user_sessions", "audit_events"}
# Later revisions: E2-07, E2-08.
LATER_TABLES = {"invitations", "password_reset_tokens", "oidc_accounts"}
APP_ROLE = "hireflow_app"


# ── Schema on the session database ────────────────────────────
async def test_the_tables_exist(db_session: AsyncSession) -> None:
    conn = await db_session.connection()
    names = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    assert names >= TABLES


async def _insert_user(db: AsyncSession, email: str) -> uuid.UUID:
    user_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO users (id, email, display_name, status) "
            "VALUES (:id, :email, 'U', 'active')"
        ),
        {"id": user_id, "email": email},
    )
    return user_id


@pytest.mark.parametrize(
    "email", ["Ada@example.com", " ada@example.com", "ada@example.com "], ids=str
)
async def test_emails_are_stored_normalised(db_session: AsyncSession, email: str) -> None:
    with pytest.raises(IntegrityError, match="ck_users_email_normalised"):
        async with db_session.begin_nested():
            await _insert_user(db_session, email)


async def test_membership_constraints(db_session: AsyncSession) -> None:
    user = await _insert_user(db_session, "ada@example.com")
    insert = text(
        "INSERT INTO memberships (id, agency_id, user_id, role, all_clients, status) "
        "VALUES (gen_random_uuid(), :agency, :user, :role, :all_clients, 'active')"
    )
    values = {"agency": DEFAULT_AGENCY_ID, "user": user}
    for role, all_clients, constraint in (
        ("superuser", True, "ck_memberships_role"),
        ("admin", False, "ck_memberships_all_clients"),  # owners and admins: every client
        ("owner", False, "ck_memberships_all_clients"),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            async with db_session.begin_nested():
                await db_session.execute(
                    insert, {**values, "role": role, "all_clients": all_clients}
                )
    await db_session.execute(insert, {**values, "role": "viewer", "all_clients": False})
    with pytest.raises(IntegrityError, match="uq_memberships_agency_id_user_id"):
        async with db_session.begin_nested():
            await db_session.execute(insert, {**values, "role": "admin", "all_clients": True})


# ── Reversibility ─────────────────────────────────────────────
def _tables(conn: Connection) -> set[str]:
    return set(inspect(conn).get_table_names())


def _fill(conn: Connection) -> None:
    user = uuid.uuid4()
    membership = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO users (id, email, display_name, status) VALUES (:id, 'a@b.c', 'A', 'active')"
        ),
        {"id": user},
    )
    conn.execute(
        text(
            "INSERT INTO memberships (id, agency_id, user_id, role, all_clients, status) "
            "VALUES (:id, :agency, :user, 'viewer', false, 'active')"
        ),
        {"id": membership, "agency": DEFAULT_AGENCY_ID, "user": user},
    )
    conn.execute(
        text("INSERT INTO membership_clients (membership_id, client_id) VALUES (:m, :c)"),
        {"m": membership, "c": uuid.uuid4()},
    )
    conn.execute(
        text(
            "INSERT INTO user_sessions (id_hash, user_id, created_at, last_seen_at, "
            "idle_expires_at, absolute_expires_at) VALUES ('\\x01', :user, now(), now(), now(), now())"
        ),
        {"user": user},
    )
    conn.execute(text("INSERT INTO audit_events (action) VALUES ('test')"))


def test_upgrade_downgrade_upgrade(scratch_db: str) -> None:  # noqa: F811
    cfg = _alembic_config()
    engine = create_engine(scratch_db)
    try:
        command.upgrade(cfg, REVISION)
        with engine.begin() as conn:
            before = _tables(conn)
            assert before >= TABLES
            _fill(conn)
        command.downgrade(cfg, BEFORE)
        with engine.connect() as conn:
            assert _tables(conn) == before - TABLES
        command.upgrade(cfg, "head")  # with what follows it (E2-04): alembic check needs head
        with engine.begin() as conn:
            assert _tables(conn) - LATER_TABLES == before
            assert conn.execute(text("SELECT count(*) FROM users")).scalar_one() == 0
            _fill(conn)
        command.check(cfg)  # raises if the models and the migrated schema differ
    finally:
        engine.dispose()


# ── Grants for the app role (E2-04) ───────────────────────────
@pytest.fixture
def app_role() -> Iterator[str]:
    """``hireflow_app`` (a cluster-wide role), created for the test unless it exists already
    (E2-04 may create it for the whole session); dropped afterwards only if created here.
    Requested before ``scratch_db``, so it is dropped after that database is."""
    from sqlalchemy.engine import make_url

    from app.platform.config import settings

    admin = create_engine(make_url(settings.SYNC_DATABASE_URL), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}
        ).scalar()
        if not exists:
            conn.execute(text(f"CREATE ROLE {APP_ROLE} NOLOGIN"))
    try:
        yield APP_ROLE
    finally:
        if not exists:
            with admin.connect() as conn:
                conn.execute(text(f"DROP ROLE IF EXISTS {APP_ROLE}"))
        admin.dispose()


PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")


def _privileges(conn: Connection, table: str) -> set[str]:
    return {
        privilege
        for privilege in PRIVILEGES
        if conn.execute(
            text("SELECT has_table_privilege(:role, :table, :privilege)"),
            {"role": APP_ROLE, "table": table, "privilege": privilege},
        ).scalar_one()
    }


def test_audit_events_are_append_only_for_the_app_role(
    app_role: str,
    scratch_db: str,  # noqa: F811
) -> None:
    cfg = _alembic_config()
    engine = create_engine(scratch_db)
    try:
        command.upgrade(cfg, BEFORE)
        with engine.begin() as conn:
            # What E2-04's default privileges do for tables the migrations create later.
            conn.execute(
                text(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO {app_role}")
            )
        command.upgrade(cfg, REVISION)
        with engine.connect() as conn:
            assert _privileges(conn, "audit_events") == {"SELECT", "INSERT"}
            for table in TABLES - {"audit_events"}:
                assert {"SELECT", "INSERT", "UPDATE", "DELETE"} <= _privileges(conn, table), table
            sequence = conn.execute(
                text("SELECT pg_get_serial_sequence('audit_events', 'id')")
            ).scalar_one()
            assert conn.execute(
                text("SELECT has_sequence_privilege(:r, :s, 'USAGE')"),
                {"r": app_role, "s": sequence},
            ).scalar_one()
        command.downgrade(cfg, BEFORE)  # the grants go with the tables
        command.upgrade(cfg, REVISION)
        with engine.connect() as conn:
            assert _privileges(conn, "audit_events") == {"SELECT", "INSERT"}
    finally:
        engine.dispose()
