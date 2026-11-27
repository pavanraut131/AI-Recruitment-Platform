"""E2-12 contract migration (``e2f12a6c9d40``): ``legacy_candidate_map`` is dropped with v1.

- **At head** (the session database): the table is gone, and nothing refers to it.
- **Upgrade -> downgrade -> upgrade** on a throwaway database, with a merged legacy id in the
  map: the upgrade drops the table (rows, index and foreign key with it) and changes nothing
  else; the downgrade re-creates it **empty**, exactly as the E0-14 expand migration made it
  (columns, primary key, the ``ON DELETE CASCADE`` foreign key to ``applications``, the index,
  their names) and with the application role's grants (E2-04's default privileges); upgrading
  again works, the row-level-security catalogue is clean and ``alembic check`` passes.
"""

from __future__ import annotations

import uuid
from typing import Any

from alembic import command
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform import rls
from tests.migrations.tenancy import DROP_LEGACY_MAP, catalogue_problems_before_e2_12
from tests.migrations.test_expand_e0_14 import (  # noqa: F401 (scratch_db is a fixture)
    _alembic_config,
    scratch_db,
)
from tests.migrations.test_identity_e2_07 import _app_privileges

BEFORE = "e2e08a4c7d21"
TABLE = "legacy_candidate_map"
DEFAULT_AGENCY = "00000000-0000-4000-8000-00000000a001"  # the E2-01 migration's
DEFAULT_CLIENT = "00000000-0000-4000-8000-00000000c001"


async def test_the_table_is_gone_at_head(db_session: AsyncSession) -> None:
    exists = await db_session.scalar(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": TABLE})
    assert exists is False
    references = await db_session.scalar(
        text("SELECT count(*) FROM pg_constraint WHERE conname LIKE :name"),
        {"name": f"{TABLE}%"},
    )
    assert references == 0


def _schema(conn: Connection) -> dict[str, Any]:
    """Everything about the table that the downgrade must restore."""
    insp = inspect(conn)
    return {
        "columns": [
            (c["name"], str(c["type"]), c["nullable"], c["default"])
            for c in insp.get_columns(TABLE)
        ],
        "pk": insp.get_pk_constraint(TABLE),
        "fks": [
            (
                fk["name"],
                fk["constrained_columns"],
                fk["referred_table"],
                fk["referred_columns"],
                fk["options"].get("ondelete"),
            )
            for fk in insp.get_foreign_keys(TABLE)
        ],
        "indexes": sorted(
            (ix["name"], tuple(ix["column_names"]), ix["unique"]) for ix in insp.get_indexes(TABLE)
        ),
        "owner": conn.execute(
            text("SELECT tableowner FROM pg_tables WHERE tablename = :t"), {"t": TABLE}
        ).scalar_one(),
        "app privileges": _app_privileges(conn, TABLE),
        "rls": conn.execute(
            text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :t"),
            {"t": TABLE},
        )
        .one()
        ._tuple(),
    }


def _application_with_a_merged_id(conn: Connection) -> tuple[uuid.UUID, uuid.UUID]:
    campaign, application, merged = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO campaigns (id, title, role, required_skills, status, agency_id, "
            "client_id) VALUES (:id, 'C', 'R', '[]', 'active', :agency, :client)"
        ),
        {"id": campaign, "agency": DEFAULT_AGENCY, "client": DEFAULT_CLIENT},
    )
    conn.execute(
        text(
            "INSERT INTO applications (id, campaign_id, person_id, resume_id, strengths, "
            "weaknesses, missing_skills, agency_id) "
            "VALUES (:id, :campaign, :person, :resume, '[]', '[]', '[]', :agency)"
        ),
        {
            "id": application,
            "campaign": campaign,
            "person": uuid.uuid4(),
            "resume": uuid.uuid4(),
            "agency": DEFAULT_AGENCY,
        },
    )
    conn.execute(
        text("INSERT INTO legacy_candidate_map VALUES (:c, :a)"), {"c": merged, "a": application}
    )
    return application, merged


def _tables(conn: Connection) -> set[str]:
    return set(inspect(conn).get_table_names())


def test_upgrade_drops_the_map_and_downgrade_recreates_it_empty(scratch_db: str) -> None:  # noqa: F811
    cfg = _alembic_config()
    engine = create_engine(scratch_db)
    try:
        command.upgrade(cfg, BEFORE)
        with engine.begin() as conn:
            created_by_e0_14 = _schema(conn)
            tables = _tables(conn)
            application, _ = _application_with_a_merged_id(conn)
        assert created_by_e0_14["fks"] == [
            (
                "legacy_candidate_map_application_id_fkey",
                ["application_id"],
                "applications",
                ["id"],
                "CASCADE",
            )
        ]
        assert created_by_e0_14["app privileges"] == set(rls.TABLE_PRIVILEGES)

        command.upgrade(cfg, DROP_LEGACY_MAP)
        with engine.connect() as conn:
            assert _tables(conn) == tables - {TABLE}  # and nothing else
            # The application the map led to stays.
            assert (
                conn.execute(
                    text("SELECT count(*) FROM applications WHERE id = :id"), {"id": application}
                ).scalar_one()
                == 1
            )
            assert rls.catalogue_problems(conn) == []

        command.downgrade(cfg, BEFORE)
        with engine.begin() as conn:
            assert _tables(conn) == tables
            assert _schema(conn) == created_by_e0_14
            assert conn.execute(text(f"SELECT count(*) FROM {TABLE}")).scalar_one() == 0
            assert catalogue_problems_before_e2_12(conn) == []
            # It works as before: a redirect, deleted with its application.
            conn.execute(
                text("INSERT INTO legacy_candidate_map VALUES (:c, :a)"),
                {"c": uuid.uuid4(), "a": application},
            )
            conn.execute(text("DELETE FROM applications WHERE id = :id"), {"id": application})
            assert conn.execute(text(f"SELECT count(*) FROM {TABLE}")).scalar_one() == 0

        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            assert TABLE not in _tables(conn)
            assert rls.catalogue_problems(conn) == []
        command.check(cfg)  # raises if the models and the migrated schema differ
    finally:
        engine.dispose()
