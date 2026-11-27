"""E2-08 migration (``e2e08a4c7d21``): ``oidc_accounts``.

- **Constraints** on the session database (at ``head``), in the usual rolled-back transaction:
  the providers, ``UNIQUE(provider, issuer, subject)`` (the same subject under another issuer,
  e.g. another Microsoft tenant, is another identity), and the rows go with their user.
- **Upgrade -> downgrade -> upgrade** on a throwaway database with a row in the table: the
  downgrade removes the table (and nothing else), the second upgrade recreates it, and
  ``alembic check`` is clean.
- **Grants**: no GRANT of its own; E2-04's default privileges give the application role
  ``SELECT, INSERT, UPDATE, DELETE`` (no ``TRUNCATE``), and the row-level-security catalogue is
  clean (the table is on the global allowlist).
"""

from __future__ import annotations

import uuid

import pytest
from alembic import command
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform import rls
from tests.migrations.tenancy import DROPPED_BY_E2_12, catalogue_problems_before_e2_12
from tests.migrations.test_expand_e0_14 import (  # noqa: F401 (scratch_db is a fixture)
    _alembic_config,
    scratch_db,
)
from tests.migrations.test_identity_e2_07 import _app_privileges

BEFORE = "e2d06b8c4f17"
REVISION = "e2e08a4c7d21"
TABLE = "oidc_accounts"

INSERT_USER = text(
    "INSERT INTO users (id, email, display_name, status) VALUES (:id, :email, 'A', 'active')"
)
INSERT_ACCOUNT = text(
    "INSERT INTO oidc_accounts (id, user_id, provider, issuer, subject, email_at_link) "
    "VALUES (gen_random_uuid(), :user, :provider, :issuer, :subject, 'a@example.com')"
)
GOOGLE = "https://accounts.google.com"


async def _user(db: AsyncSession) -> uuid.UUID:
    user = uuid.uuid4()
    await db.execute(INSERT_USER, {"id": user, "email": f"{user.hex}@example.com"})
    return user


async def test_oidc_account_constraints(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    other = await _user(db_session)
    row = {"user": user, "provider": "google", "issuer": GOOGLE, "subject": "123"}
    await db_session.execute(INSERT_ACCOUNT, row)

    with pytest.raises(IntegrityError, match="uq_oidc_accounts_provider_issuer_subject"):
        async with db_session.begin_nested():
            await db_session.execute(INSERT_ACCOUNT, {**row, "user": other})
    with pytest.raises(IntegrityError, match="ck_oidc_accounts_provider"):
        async with db_session.begin_nested():
            await db_session.execute(INSERT_ACCOUNT, {**row, "provider": "github"})
    # The same subject under another issuer (another Microsoft tenant) is another identity, and
    # a user may have several.
    tenant = "https://login.microsoftonline.com/{}/v2.0"
    for issuer in (tenant.format(uuid.uuid4()), tenant.format(uuid.uuid4())):
        await db_session.execute(INSERT_ACCOUNT, {**row, "provider": "microsoft", "issuer": issuer})
    # Deleted with their user.
    await db_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user})
    assert await db_session.scalar(text("SELECT count(*) FROM oidc_accounts")) == 0


def _tables(conn: Connection) -> set[str]:
    return set(inspect(conn).get_table_names())


def _fill(conn: Connection) -> None:
    user = uuid.uuid4()
    conn.execute(INSERT_USER, {"id": user, "email": f"{user.hex}@example.com"})
    conn.execute(
        INSERT_ACCOUNT, {"user": user, "provider": "google", "issuer": GOOGLE, "subject": "1"}
    )


def test_upgrade_downgrade_upgrade_and_grants(scratch_db: str) -> None:  # noqa: F811
    cfg = _alembic_config()
    engine = create_engine(scratch_db)
    try:
        command.upgrade(cfg, REVISION)
        with engine.begin() as conn:
            before = _tables(conn)
            assert TABLE in before
            _fill(conn)
            # From E2-04's default privileges, no GRANT of its own.
            assert _app_privileges(conn, TABLE) == {"SELECT", "INSERT", "UPDATE", "DELETE"}
            assert catalogue_problems_before_e2_12(conn) == []
        command.downgrade(cfg, BEFORE)
        with engine.connect() as conn:
            assert _tables(conn) == before - {TABLE}
            assert catalogue_problems_before_e2_12(conn) == []
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            assert _tables(conn) == before - DROPPED_BY_E2_12
            assert conn.execute(text("SELECT count(*) FROM oidc_accounts")).scalar_one() == 0
            _fill(conn)
            assert rls.catalogue_problems(conn) == []
        command.check(cfg)  # raises if the models and the migrated schema differ
    finally:
        engine.dispose()
