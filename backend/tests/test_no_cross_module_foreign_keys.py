"""ADR 0003: no foreign key crosses a module boundary.

Reads every foreign key of the migrated schema (the session database, at ``head``) from
``information_schema`` and checks that the referencing and the referenced table belong to the
same module. :data:`TABLE_OWNERS` is the ownership map (spec §4); every table in the schema
must be in it, so a new table has to be given an owner before this passes. Since the contract
migration (E0-20) dropped ``resumes.campaign_id -> campaigns``, there are none left.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.database import Base

TABLE_OWNERS = {
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

FOREIGN_KEYS = """
SELECT tc.constraint_name, tc.table_name, ccu.table_name AS referenced_table
FROM information_schema.table_constraints AS tc
JOIN information_schema.constraint_column_usage AS ccu
  ON ccu.constraint_schema = tc.constraint_schema AND ccu.constraint_name = tc.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
ORDER BY tc.constraint_name
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
        ("applications", "campaigns"),
        ("legacy_candidate_map", "applications"),
        ("person_identifiers", "people"),
        ("identity_conflicts", "people"),
        ("person_resumes", "people"),
        ("person_resumes", "identity_conflicts"),
    }
