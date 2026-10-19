"""E0-20 contract migration (``e20c4a7d1b93``): ``candidates`` and ``backfill_checkpoints`` are
dropped, and so is the foreign key ``resumes.campaign_id -> campaigns``.

Upgrade -> downgrade -> upgrade on a throwaway database (``scratch_db``), starting from the E0-19
head with legacy rows:

- the upgrade copies a pre-switch resume's text from its candidate onto the resume (only where
  the resume has none), drops the tables and the foreign key, and keeps ``legacy_candidate_map``
  and every other row;
- the downgrade re-creates both tables **empty** with exactly the schema they had (columns in
  order, types, defaults, constraint names) and the foreign key;
- the runbook's rebuild SQL (``REBUILD_CANDIDATES``, §8.3) then gives one candidate per
  application, idempotently;
- a final ``alembic check`` is clean.
"""

from __future__ import annotations

import importlib.util
import json
import uuid
from typing import Any

from alembic import command
from sqlalchemy import Connection, create_engine, inspect, text

from tests import postgres
from tests.migrations.test_expand_e0_14 import (  # noqa: F401 (scratch_db is a fixture)
    _alembic_config,
    scratch_db,
)

BEFORE = "e19b7f3a9c42"
CONTRACT = "e20c4a7d1b93"
DROPPED = ("candidates", "backfill_checkpoints")
MIGRATION = (
    postgres.BACKEND_DIR / "alembic" / "versions" / f"{CONTRACT}_contract_drop_candidates.py"
)


def _schema(conn: Connection) -> dict[str, Any]:
    """Everything about the dropped tables and the resumes foreign keys that a re-creation must
    reproduce."""
    insp = inspect(conn)
    schema: dict[str, Any] = {}
    for table in DROPPED:
        schema[table] = {
            "columns": [
                (c["name"], str(c["type"]), c["nullable"], c["default"])
                for c in insp.get_columns(table)
            ],
            "pk": insp.get_pk_constraint(table),
            "uniques": sorted(
                (u["name"], tuple(u["column_names"])) for u in insp.get_unique_constraints(table)
            ),
            "fks": sorted(
                (
                    fk["name"],
                    tuple(fk["constrained_columns"]),
                    fk["referred_table"],
                    fk["options"].get("ondelete"),
                )
                for fk in insp.get_foreign_keys(table)
            ),
            "indexes": sorted(
                (ix["name"], tuple(ix["column_names"])) for ix in insp.get_indexes(table)
            ),
        }
    schema["resumes_fks"] = sorted(
        (fk["name"], fk["referred_table"], fk["options"].get("ondelete"))
        for fk in insp.get_foreign_keys("resumes")
    )
    return schema


def _insert_legacy_rows(conn: Connection) -> dict[str, uuid.UUID]:
    ids = {name: uuid.uuid4() for name in ("campaign", "pre", "post", "c_pre", "c_post", "app")}
    conn.execute(
        text(
            "INSERT INTO campaigns (id, title, role, required_skills, status) "
            "VALUES (:id, 'C', 'Engineer', '[]', 'active')"
        ),
        {"id": ids["campaign"]},
    )
    # "pre": extracted before the switch, its text only on the candidate. "post": the resume
    # already has its own text (extracted after the switch); the candidate's must not win.
    for resume, raw_text in (("pre", None), ("post", "resume text")):
        conn.execute(
            text(
                "INSERT INTO resumes (id, campaign_id, original_filename, storage_path, "
                "file_size_bytes, status, retry_count, raw_text) "
                "VALUES (:id, :cid, 'cv.pdf', 'k', 1, 'failed', 0, :raw)"
            ),
            {"id": ids[resume], "cid": ids["campaign"], "raw": raw_text},
        )
    for candidate, resume, raw_text in (
        ("c_pre", "pre", "candidate text"),
        ("c_post", "post", "stale candidate text"),
    ):
        conn.execute(
            text(
                "INSERT INTO candidates (id, resume_id, campaign_id, skills, education, "
                "work_experience, certifications, projects, strengths, weaknesses, "
                "missing_skills, raw_text) VALUES (:id, :rid, :cid, '[]', '[]', '[]', '[]', "
                "'[]', '[]', '[]', '[]', :raw)"
            ),
            {"id": ids[candidate], "rid": ids[resume], "cid": ids["campaign"], "raw": raw_text},
        )
    conn.execute(
        text(
            "INSERT INTO applications (id, campaign_id, person_id, resume_id, strengths, "
            "weaknesses, missing_skills) VALUES (:id, :cid, :pid, :rid, '[]', '[]', '[]')"
        ),
        {"id": ids["app"], "cid": ids["campaign"], "pid": uuid.uuid4(), "rid": ids["post"]},
    )
    conn.execute(
        text("INSERT INTO legacy_candidate_map VALUES (:c, :a)"),
        {"c": ids["c_pre"], "a": ids["app"]},
    )
    conn.execute(text("INSERT INTO backfill_checkpoints (name) VALUES ('person_application')"))
    return ids


def _rebuild_sql() -> str:
    spec = importlib.util.spec_from_file_location(CONTRACT, MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.REBUILD_CANDIDATES)


def _raw_texts(conn: Connection) -> dict[uuid.UUID, str | None]:
    return {row[0]: row[1] for row in conn.execute(text("SELECT id, raw_text FROM resumes"))}


def test_upgrade_contracts_then_downgrade_recreates_the_tables_empty(scratch_db: str) -> None:  # noqa: F811
    cfg = _alembic_config()
    engine = create_engine(scratch_db)
    try:
        command.upgrade(cfg, BEFORE)
        with engine.begin() as conn:
            before = _schema(conn)
            ids = _insert_legacy_rows(conn)
        assert before["resumes_fks"] == [("resumes_campaign_id_fkey", "campaigns", "CASCADE")]

        command.upgrade(cfg, CONTRACT)
        with engine.connect() as conn:
            insp = inspect(conn)
            assert not set(DROPPED) & set(insp.get_table_names())
            assert insp.get_foreign_keys("resumes") == []
            assert _raw_texts(conn) == {ids["pre"]: "candidate text", ids["post"]: "resume text"}
            assert conn.execute(text("SELECT * FROM legacy_candidate_map")).all() == [
                (ids["c_pre"], ids["app"])
            ]
            assert conn.execute(text("SELECT count(*) FROM applications")).scalar() == 1
            # No foreign key any more: a resume may name a campaign that does not exist (the
            # services keep them consistent; verify_people_applications checks it).
            conn.execute(
                text(
                    "INSERT INTO resumes (id, campaign_id, original_filename, storage_path, "
                    "file_size_bytes, status, retry_count) "
                    "VALUES (:id, :cid, 'x.pdf', 'x', 1, 'done', 0)"
                ),
                {"id": uuid.uuid4(), "cid": uuid.uuid4()},
            )
            conn.rollback()

        command.downgrade(cfg, BEFORE)
        with engine.connect() as conn:
            assert _schema(conn) == before  # the original schema, constraint names included
            for table in DROPPED:
                assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0
            assert _raw_texts(conn) == {ids["pre"]: "candidate text", ids["post"]: "resume text"}
            assert conn.execute(text("SELECT count(*) FROM legacy_candidate_map")).scalar() == 1

        # The runbook's rebuild (§8.3): one candidate per application, as the read path showed it.
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE resumes SET extracted = CAST(:x AS jsonb) WHERE id = :id"),
                {
                    "x": json.dumps({"name": "Ann", "skills": ["Py"], "years_of_experience": 3}),
                    "id": ids["post"],
                },
            )
            conn.execute(text(_rebuild_sql()))
            conn.execute(text(_rebuild_sql()))  # idempotent
            rows = conn.execute(
                text(
                    "SELECT id, resume_id, name, skills, years_of_experience, raw_text FROM candidates"
                )
            ).all()
        assert [tuple(r) for r in rows] == [
            (ids["app"], ids["post"], "Ann", ["Py"], 3.0, "resume text")
        ]

        command.upgrade(cfg, "head")
        command.check(cfg)  # raises if the models and the migrated schema differ
    finally:
        engine.dispose()
