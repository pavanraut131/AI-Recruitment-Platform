"""E2-07 migration (``e2d07c3f9b12``): ``invitations`` and ``password_reset_tokens``.

- **Constraints** on the session database (at ``head``), in the usual rolled-back transaction:
  normalised emails, roles, owners and admins with every client, a client list exactly when
  access is limited, unique token hashes, one open invitation per address and agency.
- **Upgrade -> downgrade -> upgrade** on a throwaway database with rows in both tables: the
  downgrade removes the two tables (and nothing else), the second upgrade recreates them, and
  ``alembic check`` is clean.
- **Grants**: no GRANT of its own; E2-04's default privileges give the application role
  ``SELECT, INSERT, UPDATE, DELETE`` (no ``TRUNCATE``) on both, and the row-level-security
  catalogue is clean (both are on the global allowlist).
"""

from __future__ import annotations

import uuid

import pytest
from alembic import command
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform import rls
from tests.default_agency import DEFAULT_AGENCY_ID
from tests.migrations.tenancy import DROPPED_BY_E2_12, catalogue_problems_before_e2_12
from tests.migrations.test_expand_e0_14 import (  # noqa: F401 (scratch_db is a fixture)
    _alembic_config,
    scratch_db,
)

BEFORE = "e2c04a7f3b18"
REVISION = "e2d07c3f9b12"
TABLES = {"invitations", "password_reset_tokens"}
LATER_TABLES = {"oidc_accounts"}  # E2-08, a later revision

INSERT_INVITATION = text(
    "INSERT INTO invitations (id, agency_id, email, role, all_clients, client_ids, token_hash, "
    "expires_at, revoked_at, accepted_at) VALUES (gen_random_uuid(), :agency, :email, :role, "
    ":all_clients, CAST(:client_ids AS uuid[]), :hash, now() + interval '7 days', :revoked, "
    ":accepted)"
)


def _invitation(**values: object) -> dict[str, object]:
    return {
        "agency": DEFAULT_AGENCY_ID,
        "email": "nina@example.com",
        "role": "viewer",
        "all_clients": True,
        "client_ids": [],
        "hash": uuid.uuid4().bytes,
        "revoked": None,
        "accepted": None,
        **values,
    }


async def _refused(db: AsyncSession, constraint: str, **values: object) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        async with db.begin_nested():
            await db.execute(INSERT_INVITATION, _invitation(**values))


# ── Constraints on the session database ──────────────────────
async def test_invitation_constraints(db_session: AsyncSession) -> None:
    client = str(uuid.uuid4())
    await _refused(db_session, "ck_invitations_email_normalised", email="Nina@example.com")
    await _refused(db_session, "ck_invitations_role", role="superuser")
    await _refused(
        db_session,
        "ck_invitations_all_clients",
        role="admin",
        all_clients=False,
        client_ids=[client],
    )
    await _refused(db_session, "ck_invitations_client_ids", all_clients=False)  # no client
    await _refused(db_session, "ck_invitations_client_ids", client_ids=[client])  # all + a list

    await db_session.execute(INSERT_INVITATION, _invitation(hash=b"same"))
    await _refused(db_session, "uq_invitations_token_hash", email="other@example.com", hash=b"same")
    # One open invitation per address and agency; revoked and accepted ones do not count.
    await _refused(db_session, "uq_invitations_agency_id_email_open")
    await db_session.execute(
        text("UPDATE invitations SET revoked_at = now() WHERE email = 'nina@example.com'")
    )
    await db_session.execute(INSERT_INVITATION, _invitation())
    await db_session.execute(
        text("UPDATE invitations SET accepted_at = now() WHERE revoked_at IS NULL")
    )
    await db_session.execute(
        INSERT_INVITATION, _invitation(all_clients=False, client_ids=[client], role="recruiter")
    )


async def test_reset_token_hashes_are_unique(db_session: AsyncSession) -> None:
    user = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO users (id, email, display_name, status) "
            "VALUES (:id, 'ada@example.com', 'Ada', 'active')"
        ),
        {"id": user},
    )
    insert = text(
        "INSERT INTO password_reset_tokens (id, user_id, token_hash, expires_at) "
        "VALUES (gen_random_uuid(), :user, '\\x01', now())"
    )
    await db_session.execute(insert, {"user": user})
    with pytest.raises(IntegrityError, match="uq_password_reset_tokens_token_hash"):
        async with db_session.begin_nested():
            await db_session.execute(insert, {"user": user})
    # Deleted with their user.
    await db_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user})
    count = await db_session.scalar(text("SELECT count(*) FROM password_reset_tokens"))
    assert count == 0


# ── Reversibility and grants ──────────────────────────────────
def _tables(conn: Connection) -> set[str]:
    return set(inspect(conn).get_table_names())


def _fill(conn: Connection) -> None:
    user = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO users (id, email, display_name, status) "
            "VALUES (:id, :email, 'A', 'active')"
        ),
        {"id": user, "email": f"{user.hex}@example.com"},
    )
    conn.execute(INSERT_INVITATION, _invitation())
    conn.execute(
        text(
            "INSERT INTO password_reset_tokens (id, user_id, token_hash, expires_at) "
            "VALUES (gen_random_uuid(), :user, '\\x02', now())"
        ),
        {"user": user},
    )


PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")


def _app_privileges(conn: Connection, table: str) -> set[str]:
    return {
        privilege
        for privilege in PRIVILEGES
        if conn.execute(
            text("SELECT has_table_privilege(:role, :table, :privilege)"),
            {"role": rls.APP_ROLE, "table": table, "privilege": privilege},
        ).scalar_one()
    }


def test_upgrade_downgrade_upgrade_and_grants(scratch_db: str) -> None:  # noqa: F811
    cfg = _alembic_config()
    engine = create_engine(scratch_db)
    try:
        command.upgrade(cfg, REVISION)
        with engine.begin() as conn:
            before = _tables(conn)
            assert before >= TABLES
            _fill(conn)
            for table in TABLES:  # from E2-04's default privileges, no GRANT of its own
                assert _app_privileges(conn, table) == {"SELECT", "INSERT", "UPDATE", "DELETE"}
            assert catalogue_problems_before_e2_12(conn) == []
        command.downgrade(cfg, BEFORE)
        with engine.connect() as conn:
            assert _tables(conn) == before - TABLES
            assert catalogue_problems_before_e2_12(conn) == []
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            assert _tables(conn) - LATER_TABLES == before - DROPPED_BY_E2_12
            assert conn.execute(text("SELECT count(*) FROM invitations")).scalar_one() == 0
            _fill(conn)
            assert rls.catalogue_problems(conn) == []
        command.check(cfg)  # raises if the models and the migrated schema differ
    finally:
        engine.dispose()
