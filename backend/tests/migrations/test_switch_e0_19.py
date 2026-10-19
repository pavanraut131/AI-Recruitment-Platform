"""E0-19 migrations: ``e19a0c5d2b71`` (expand: ``resumes.raw_text``) and ``e19b7f3a9c42`` (data:
applications take their legacy candidates' timestamps at the read switch).

The column on the session database; on a throwaway database (``scratch_db``), legacy rows
backfilled before the switch get the timestamps the legacy endpoints showed, the downgrade
keeps the data, and a final ``alembic check`` is clean.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from alembic import command
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.migrations.test_expand_e0_14 import (  # noqa: F401 (scratch_db is a fixture)
    _alembic_config,
    scratch_db,
)

BEFORE = "b7d2e4f61a90"
EXPAND = "e19a0c5d2b71"
ALIGN = "e19b7f3a9c42"


def t(day: int, hour: int = 9) -> datetime:
    return datetime(2026, 10, day, hour, tzinfo=UTC)


BACKFILL_TIME = t(18)


async def test_resumes_have_a_nullable_raw_text(db_session: AsyncSession) -> None:
    conn = await db_session.connection()
    columns: dict[str, Any] = await conn.run_sync(
        lambda sync_conn: {c["name"]: c for c in inspect(sync_conn).get_columns("resumes")}
    )
    column = columns["raw_text"]
    assert (str(column["type"]), column["nullable"], column["default"]) == ("TEXT", True, None)


def _campaign(conn: Connection) -> uuid.UUID:
    campaign_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO campaigns (id, title, role, required_skills, status) "
            "VALUES (:id, 'C', 'Engineer', '[]', 'active')"
        ),
        {"id": campaign_id},
    )
    return campaign_id


def _candidate(
    conn: Connection, campaign_id: uuid.UUID, created: datetime, updated: datetime
) -> uuid.UUID:
    resume_id, candidate_id = uuid.uuid4(), uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO resumes (id, campaign_id, original_filename, storage_path, "
            "file_size_bytes, status, retry_count) VALUES (:id, :cid, 'cv.pdf', 'k', 1, 'done', 0)"
        ),
        {"id": resume_id, "cid": campaign_id},
    )
    conn.execute(
        text(
            "INSERT INTO candidates (id, resume_id, campaign_id, skills, education, "
            "work_experience, certifications, projects, strengths, weaknesses, missing_skills, "
            "created_at, updated_at) VALUES (:id, :rid, :cid, '[]', '[]', '[]', '[]', '[]', "
            "'[]', '[]', '[]', :created, :updated)"
        ),
        {
            "id": candidate_id,
            "rid": resume_id,
            "cid": campaign_id,
            "created": created,
            "updated": updated,
        },
    )
    return candidate_id


def _application(
    conn: Connection, campaign_id: uuid.UUID, application_id: uuid.UUID | None = None
) -> uuid.UUID:
    """An application as the backfill wrote it: stamped with the backfill run's time."""
    application_id = application_id or uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO applications (id, campaign_id, person_id, resume_id, strengths, "
            "weaknesses, missing_skills, created_at, updated_at) VALUES (:id, :cid, :pid, :rid, "
            "'[]', '[]', '[]', :at, :at)"
        ),
        {
            "id": application_id,
            "cid": campaign_id,
            "pid": uuid.uuid4(),
            "rid": uuid.uuid4(),
            "at": BACKFILL_TIME,
        },
    )
    return application_id


def _timestamps(conn: Connection) -> dict[uuid.UUID, tuple[datetime, datetime]]:
    rows = conn.execute(text("SELECT id, created_at, updated_at FROM applications")).tuples()
    return {row[0]: (row[1], row[2]) for row in rows}


def test_upgrade_aligns_timestamps_then_downgrade_and_upgrade_again(scratch_db: str) -> None:  # noqa: F811
    cfg = _alembic_config()
    engine = create_engine(scratch_db)
    try:
        command.upgrade(cfg, BEFORE)
        with engine.begin() as conn:
            campaign = _campaign(conn)
            # One candidate, one application with its id: the legacy pair.
            single = _candidate(conn, campaign, t(1), t(3))
            _application(conn, campaign, single)
            # Two candidates merged into one application (the second by legacy_candidate_map).
            first = _candidate(conn, campaign, t(2), t(9))
            second = _candidate(conn, campaign, t(4), t(5))
            _application(conn, campaign, first)
            conn.execute(
                text("INSERT INTO legacy_candidate_map VALUES (:c, :a)"),
                {"c": second, "a": first},
            )
            # An application without a candidate: untouched.
            alone = _application(conn, campaign)

        command.upgrade(cfg, ALIGN)
        with engine.connect() as conn:
            stamps = _timestamps(conn)
            names = {c["name"] for c in inspect(conn).get_columns("resumes")}
        assert "raw_text" in names
        assert stamps[single] == (t(1), t(3))
        assert stamps[first] == (t(2), t(9))  # earliest created_at, latest updated_at
        assert stamps[alone] == (BACKFILL_TIME, BACKFILL_TIME)

        command.downgrade(cfg, BEFORE)
        with engine.connect() as conn:
            assert "raw_text" not in {c["name"] for c in inspect(conn).get_columns("resumes")}
            assert _timestamps(conn) == stamps  # the data step's downgrade changes nothing

        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            assert _timestamps(conn) == stamps  # idempotent
        command.check(cfg)
    finally:
        engine.dispose()
