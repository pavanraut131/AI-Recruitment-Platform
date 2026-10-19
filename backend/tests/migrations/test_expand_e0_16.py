"""E0-16 expand migration (``b7d2e4f61a90``): ``applications.resume_uploaded_at``.

Same approach as ``test_expand_e0_14``: the column on the session database, and upgrade →
downgrade → upgrade on a throwaway database (``scratch_db``), ending with a clean
``alembic check``.
"""

from __future__ import annotations

import uuid
from typing import Any

from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.migrations.test_expand_e0_14 import (  # noqa: F401 (scratch_db is a fixture)
    _alembic_config,
    scratch_db,
)

REVISION = "b7d2e4f61a90"
PREVIOUS_REVISION = "ad3bb2d36694"


async def test_applications_have_a_nullable_resume_uploaded_at(db_session: AsyncSession) -> None:
    conn = await db_session.connection()
    columns: dict[str, Any] = await conn.run_sync(
        lambda sync_conn: {c["name"]: c for c in inspect(sync_conn).get_columns("applications")}
    )
    column = columns["resume_uploaded_at"]
    assert (str(column["type"]), column["nullable"], column["default"]) == (
        "TIMESTAMP",
        True,
        None,
    )


def test_upgrade_downgrade_upgrade(scratch_db: str) -> None:  # noqa: F811
    cfg = _alembic_config()
    engine = create_engine(scratch_db)
    campaign_id, application_id = uuid.uuid4(), uuid.uuid4()
    try:
        command.upgrade(cfg, PREVIOUS_REVISION)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO campaigns (id, title, role, required_skills, status) "
                    "VALUES (:id, 'C', 'Engineer', '[]', 'active')"
                ),
                {"id": campaign_id},
            )
            conn.execute(
                text(
                    "INSERT INTO applications (id, campaign_id, person_id, resume_id, strengths, "
                    "weaknesses, missing_skills, score) VALUES (:id, :cid, :pid, :rid, '[]', "
                    "'[]', '[]', 70)"
                ),
                {
                    "id": application_id,
                    "cid": campaign_id,
                    "pid": uuid.uuid4(),
                    "rid": uuid.uuid4(),
                },
            )

        command.upgrade(cfg, REVISION)
        with engine.connect() as conn:
            row = conn.execute(text("SELECT score, resume_uploaded_at FROM applications")).one()
            assert tuple(row) == (70.0, None)  # existing rows untouched

        command.downgrade(cfg, PREVIOUS_REVISION)
        with engine.connect() as conn:
            names = {c["name"] for c in inspect(conn).get_columns("applications")}
            assert "resume_uploaded_at" not in names
            assert conn.execute(text("SELECT count(*) FROM applications")).scalar() == 1

        command.upgrade(cfg, "head")
        command.check(cfg)
    finally:
        engine.dispose()
