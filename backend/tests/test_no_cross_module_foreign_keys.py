"""ADR 0003: no foreign key crosses a module boundary.

Reads every foreign key of the migrated schema (the session database, at ``head``) from
``pg_constraint`` and checks that the referencing and the referenced table belong to the
same module. :data:`TABLE_OWNERS` is the ownership map (spec §4); every table in the schema
must be in it, so a new table has to be given an owner before this passes. Since the contract
migration (E0-20) dropped ``resumes.campaign_id -> campaigns``, there are none left.

The tenant key (EPIC-02, E2-01) adds none either: ``agency_id`` on hiring, talent and ingestion
tables refers to identity's ``agencies`` as a plain UUID (ADR 0003). The E2-02 contract makes
the same-module keys composite (``(agency_id, ...)``) and adds ``campaigns -> clients``, both
within hiring.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.database import Base

TABLE_OWNERS = {
    "agencies": "identity",
    # E2-05: foreign keys among these stay inside identity (memberships -> agencies / users,
    # user_sessions -> users / agencies, membership_clients -> memberships).
    "users": "identity",
    "memberships": "identity",
    "membership_clients": "identity",
    "user_sessions": "identity",
    "audit_events": "identity",
    # E2-07: invitations -> agencies / users, password_reset_tokens -> users.
    "invitations": "identity",
    "password_reset_tokens": "identity",
    # E2-08: oidc_accounts -> users.
    "oidc_accounts": "identity",
    "clients": "hiring",
    "campaigns": "hiring",
    "applications": "hiring",
    "legacy_candidate_map": "hiring",
    "resumes": "ingestion",
    "people": "talent",
    "person_identifiers": "talent",
    "identity_conflicts": "talent",
    "person_resumes": "talent",
}
NOT_OWNED = {"alembic_version"}  # Alembic's bookkeeping

# From pg_catalog, not information_schema: the suite runs as the application role
# (hireflow_app, E2-04), and information_schema.constraint_column_usage only lists the tables a
# role owns.
FOREIGN_KEYS = """
SELECT c.conname AS constraint_name, c.conrelid::regclass::text AS table_name,
       c.confrelid::regclass::text AS referenced_table
FROM pg_constraint AS c
WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace
ORDER BY c.conname
"""

TABLES = """
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
"""


async def test_every_table_has_an_owning_module(db_session: AsyncSession) -> None:
    tables = set((await db_session.execute(text(TABLES))).scalars())
    assert tables - NOT_OWNED == set(TABLE_OWNERS)
    # The ORM models declare the same tables (no model without an owner either).
    assert set(Base.metadata.tables) == set(TABLE_OWNERS)


async def test_no_foreign_key_crosses_a_module_boundary(db_session: AsyncSession) -> None:
    rows = (await db_session.execute(text(FOREIGN_KEYS))).all()
    assert rows, "the schema has same-module foreign keys; the query must see them"
    crossing = [
        f"{name}: {table} ({TABLE_OWNERS[table]}) -> {referenced} ({TABLE_OWNERS[referenced]})"
        for name, table, referenced in rows
        if TABLE_OWNERS[table] != TABLE_OWNERS[referenced]
    ]
    assert crossing == []


async def test_the_same_module_foreign_keys_are_the_expected_ones(db_session: AsyncSession) -> None:
    rows = (await db_session.execute(text(FOREIGN_KEYS))).all()
    assert {(table, referenced) for _, table, referenced in rows} == {
        ("campaigns", "clients"),  # (agency_id, client_id), E2-02
        ("applications", "campaigns"),
        ("legacy_candidate_map", "applications"),
        ("person_identifiers", "people"),
        ("identity_conflicts", "people"),
        ("person_resumes", "people"),
        ("person_resumes", "identity_conflicts"),
        # identity (E2-05)
        ("memberships", "agencies"),
        ("memberships", "users"),  # user_id and invited_by_user_id
        ("membership_clients", "memberships"),
        ("user_sessions", "users"),
        ("user_sessions", "agencies"),
        # identity (E2-07)
        ("invitations", "agencies"),
        ("invitations", "users"),  # invited_by_user_id and accepted_by_user_id
        ("password_reset_tokens", "users"),
        # identity (E2-08)
        ("oidc_accounts", "users"),
    }
