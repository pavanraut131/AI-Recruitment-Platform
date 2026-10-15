"""E0-14 expand migration (``404e09095076``): schema, constraints and reversibility.

Two kinds of test:

- **Schema and constraints** on the session database (already at ``head``, see
  ``tests/postgres.py``): the new tables, columns, indexes and constraints exist as in spec §4, and
  the unique / cascade rules hold. Each test runs in the usual rolled-back transaction.
- **Upgrade → downgrade → upgrade** on a *separate, throwaway* database in the same container, so
  the session database is never downgraded: legacy rows written at the previous head survive the
  upgrade untouched (no table rewrite), the downgrade restores the previous schema, and
  ``alembic check`` is clean afterwards.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.hiring.models import Application
from app.modules.talent.models import Person, PersonIdentifier
from tests import postgres
from tests.factories import make_campaign, make_resume

EXPAND_REVISION = "404e09095076"
PREVIOUS_REVISION = "5618884e3aec"

NEW_TABLES = {
    "people",
    "person_identifiers",
    "identity_conflicts",
    "applications",
    "legacy_candidate_map",
    "backfill_checkpoints",
}
NEW_RESUME_COLUMNS = {"person_id", "extracted", "processing_step"}


# ── Schema on the session database ────────────────────────────
async def _inspect(db: AsyncSession, fn: Any) -> Any:
    conn = await db.connection()
    return await conn.run_sync(lambda sync_conn: fn(inspect(sync_conn)))


async def test_new_tables_exist(db_session: AsyncSession) -> None:
    tables = set(await _inspect(db_session, lambda i: i.get_table_names()))
    assert tables >= NEW_TABLES


async def test_columns_match_spec(db_session: AsyncSession) -> None:
    def columns(i: Any) -> dict[str, dict[str, tuple[str, bool]]]:
        return {
            table: {c["name"]: (str(c["type"]), c["nullable"]) for c in i.get_columns(table)}
            for table in [*NEW_TABLES, "resumes", "candidates"]
        }

    cols = await _inspect(db_session, columns)

    assert cols["person_identifiers"]["kind"] == ("VARCHAR(10)", False)
    assert cols["person_identifiers"]["value_normalized"] == ("VARCHAR(320)", False)
    assert cols["person_identifiers"]["raw_value"] == ("VARCHAR(320)", False)
    assert cols["person_identifiers"]["inferred"] == ("BOOLEAN", False)
    assert cols["person_identifiers"]["source_resume_id"] == ("UUID", True)
    assert cols["people"]["latest_resume_id"] == ("UUID", True)
    assert cols["identity_conflicts"]["resolved_at"] == ("TIMESTAMP", True)
    assert cols["legacy_candidate_map"].keys() == {"candidate_id", "application_id"}
    assert cols["backfill_checkpoints"].keys() == {
        "name",
        "last_created_at",
        "last_id",
        "updated_at",
    }

    # Scoring, insight and recruiter columns mirror candidates exactly (type and nullability).
    mirrored = [
        "score",
        "score_breakdown",
        "category",
        "summary",
        "strengths",
        "weaknesses",
        "missing_skills",
        "recommendation",
        "pipeline_stage",
        "notes",
    ]
    for name in mirrored:
        assert cols["applications"][name] == cols["candidates"][name], name
    assert cols["applications"]["person_id"] == ("UUID", False)
    assert cols["applications"]["resume_id"] == ("UUID", False)

    # The new resumes columns are nullable, so existing rows need no value.
    assert cols["resumes"]["person_id"] == ("UUID", True)
    assert cols["resumes"]["extracted"] == ("JSONB", True)
    assert cols["resumes"]["processing_step"] == ("VARCHAR(20)", True)


async def test_constraints_and_indexes(db_session: AsyncSession) -> None:
    def describe(i: Any) -> dict[str, Any]:
        return {
            "uniques": {
                t: {tuple(u["column_names"]) for u in i.get_unique_constraints(t)}
                for t in ("person_identifiers", "applications")
            },
            "checks": {c["name"] for c in i.get_check_constraints("person_identifiers")},
            "indexes": {
                t: {tuple(ix["column_names"]) for ix in i.get_indexes(t)}
                for t in (
                    "person_identifiers",
                    "applications",
                    "resumes",
                    "identity_conflicts",
                    "legacy_candidate_map",
                )
            },
            "fks": {
                t: {
                    (
                        tuple(fk["constrained_columns"]),
                        fk["referred_table"],
                        fk["options"].get("ondelete"),
                    )
                    for fk in i.get_foreign_keys(t)
                }
                for t in NEW_TABLES | {"resumes"}
            },
        }

    d = await _inspect(db_session, describe)

    assert ("kind", "value_normalized") in d["uniques"]["person_identifiers"]
    assert ("campaign_id", "person_id") in d["uniques"]["applications"]
    assert "ck_person_identifiers_kind" in d["checks"]

    assert ("person_id",) in d["indexes"]["person_identifiers"]
    assert ("person_id",) in d["indexes"]["applications"]
    assert ("resume_id",) in d["indexes"]["applications"]
    assert ("person_id",) in d["indexes"]["resumes"]
    assert ("chosen_person_id",) in d["indexes"]["identity_conflicts"]
    assert ("conflicting_person_id",) in d["indexes"]["identity_conflicts"]
    assert ("application_id",) in d["indexes"]["legacy_candidate_map"]

    # Foreign keys only within a module (ADR 0003).
    fks = d["fks"]
    assert fks["person_identifiers"] == {(("person_id",), "people", "CASCADE")}
    assert fks["identity_conflicts"] == {
        (("chosen_person_id",), "people", "CASCADE"),
        (("conflicting_person_id",), "people", "CASCADE"),
    }
    assert fks["applications"] == {(("campaign_id",), "campaigns", "CASCADE")}
    assert fks["legacy_candidate_map"] == {(("application_id",), "applications", "CASCADE")}
    assert fks["people"] == set()
    assert fks["backfill_checkpoints"] == set()
    # resumes keeps only its legacy campaign FK; the new person_id has none.
    assert fks["resumes"] == {(("campaign_id",), "campaigns", "CASCADE")}


# ── Constraint behaviour ──────────────────────────────────────
async def _person(db: AsyncSession) -> Person:
    person = Person(full_name="Asha Rao")
    db.add(person)
    await db.flush()
    return person


def _identifier(person: Person, kind: str, value: str) -> PersonIdentifier:
    return PersonIdentifier(person_id=person.id, kind=kind, value_normalized=value, raw_value=value)


async def test_identifier_defaults_to_not_inferred(db_session: AsyncSession) -> None:
    person = await _person(db_session)
    await db_session.execute(
        text(
            "INSERT INTO person_identifiers (id, person_id, kind, value_normalized, raw_value) "
            "VALUES (:id, :pid, 'email', 'a@example.com', 'A@example.com')"
        ),
        {"id": uuid.uuid4(), "pid": person.id},
    )
    inferred = (
        await db_session.execute(text("SELECT inferred FROM person_identifiers"))
    ).scalar_one()
    assert inferred is False


async def test_identifier_unique_per_kind_and_value(db_session: AsyncSession) -> None:
    first, second = await _person(db_session), await _person(db_session)
    db_session.add(_identifier(first, "email", "asha@example.com"))
    await db_session.flush()
    # The same value under the other kind is a different identifier.
    db_session.add(_identifier(second, "phone", "asha@example.com"))
    await db_session.flush()

    with pytest.raises(IntegrityError, match="uq_person_identifiers_kind_value_normalized"):
        async with db_session.begin_nested():
            db_session.add(_identifier(second, "email", "asha@example.com"))
            await db_session.flush()


async def test_identifier_kind_is_checked(db_session: AsyncSession) -> None:
    person = await _person(db_session)
    with pytest.raises(IntegrityError, match="ck_person_identifiers_kind"):
        async with db_session.begin_nested():
            db_session.add(_identifier(person, "fax", "+911234567890"))
            await db_session.flush()


async def test_application_unique_per_campaign_and_person(db_session: AsyncSession) -> None:
    campaign = await make_campaign(db_session)
    resume = await make_resume(db_session, campaign=campaign)
    person_id = uuid.uuid4()  # plain UUID: no FK to people (ADR 0003)
    db_session.add(Application(campaign_id=campaign.id, person_id=person_id, resume_id=resume.id))
    await db_session.flush()

    with pytest.raises(IntegrityError, match="uq_applications_campaign_id_person_id"):
        async with db_session.begin_nested():
            db_session.add(
                Application(campaign_id=campaign.id, person_id=person_id, resume_id=resume.id)
            )
            await db_session.flush()


async def test_deleting_a_campaign_cascades_to_its_applications(db_session: AsyncSession) -> None:
    campaign = await make_campaign(db_session)
    other = await make_campaign(db_session, title="Other")
    resume = await make_resume(db_session, campaign=campaign)
    for c in (campaign, other):
        db_session.add(Application(campaign_id=c.id, person_id=uuid.uuid4(), resume_id=resume.id))
    await db_session.flush()

    await db_session.execute(text("DELETE FROM campaigns WHERE id = :id"), {"id": campaign.id})

    remaining = (await db_session.execute(text("SELECT campaign_id FROM applications"))).all()
    assert remaining == [(other.id,)]


async def test_deleting_a_person_cascades_to_identifiers(db_session: AsyncSession) -> None:
    person = await _person(db_session)
    db_session.add(_identifier(person, "email", "asha@example.com"))
    await db_session.flush()

    await db_session.execute(text("DELETE FROM people WHERE id = :id"), {"id": person.id})

    count = (await db_session.execute(text("SELECT count(*) FROM person_identifiers"))).scalar()
    assert count == 0


# ── Upgrade → downgrade → upgrade on a throwaway database ─────
@pytest.fixture
def scratch_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A new, empty database in the test container; ``alembic`` is pointed at it.

    ``alembic/env.py`` takes its URL from ``settings.SYNC_DATABASE_URL``, so that is patched for
    the test. The database is dropped afterwards.
    """
    from app.platform.config import settings

    name = f"e0_14_{uuid.uuid4().hex[:8]}"
    base_url = make_url(settings.SYNC_DATABASE_URL)
    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = base_url.set(database=name).render_as_string(hide_password=False)
    monkeypatch.setattr(settings, "SYNC_DATABASE_URL", url)
    try:
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _alembic_config() -> Config:
    # No ini file on purpose: env.py would otherwise run logging.fileConfig(), which disables the
    # loggers that already exist in this test process.
    cfg = Config()
    cfg.set_main_option("script_location", str(postgres.BACKEND_DIR / "alembic"))
    return cfg


CAMPAIGN_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
RESUME_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
CANDIDATE_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")


def _insert_legacy_rows(conn: Connection) -> None:
    conn.execute(
        text(
            "INSERT INTO campaigns (id, title, role, required_skills, status) "
            "VALUES (:id, 'Legacy', 'Engineer', '[]', 'active')"
        ),
        {"id": CAMPAIGN_ID},
    )
    conn.execute(
        text(
            "INSERT INTO resumes (id, campaign_id, original_filename, storage_path, "
            "file_size_bytes, status, retry_count) "
            "VALUES (:id, :cid, 'a.pdf', 'resumes/a.pdf', 10, 'done', 0)"
        ),
        {"id": RESUME_ID, "cid": CAMPAIGN_ID},
    )
    conn.execute(
        text(
            "INSERT INTO candidates (id, resume_id, campaign_id, name, email, skills, education, "
            "work_experience, certifications, projects, strengths, weaknesses, missing_skills, "
            "score) VALUES (:id, :rid, :cid, 'Ann', 'ann@example.com', '[\"Python\"]', '[]', "
            "'[]', '[]', '[]', '[]', '[]', '[]', 87.5)"
        ),
        {"id": CANDIDATE_ID, "rid": RESUME_ID, "cid": CAMPAIGN_ID},
    )


def _relfilenodes(conn: Connection) -> dict[str, int]:
    rows = conn.execute(
        text("SELECT relname, relfilenode FROM pg_class WHERE relname IN ('resumes', 'candidates')")
    )
    return {name: node for name, node in rows}


def test_upgrade_downgrade_upgrade_keeps_legacy_rows(scratch_db: str) -> None:
    cfg = _alembic_config()
    engine = create_engine(scratch_db)
    try:
        command.upgrade(cfg, PREVIOUS_REVISION)
        with engine.begin() as conn:
            _insert_legacy_rows(conn)
            nodes_before = _relfilenodes(conn)

        command.upgrade(cfg, EXPAND_REVISION)
        with engine.connect() as conn:
            insp = inspect(conn)
            assert set(insp.get_table_names()) >= NEW_TABLES
            # Same physical files: the ALTERs did not rewrite resumes, candidates was untouched.
            assert _relfilenodes(conn) == nodes_before
            resume = conn.execute(
                text("SELECT status, person_id, extracted, processing_step FROM resumes")
            ).one()
            assert tuple(resume) == ("done", None, None, None)
            candidate = conn.execute(text("SELECT id, name, score FROM candidates")).one()
            assert tuple(candidate) == (CANDIDATE_ID, "Ann", 87.5)

        command.downgrade(cfg, PREVIOUS_REVISION)
        with engine.connect() as conn:
            insp = inspect(conn)
            assert not NEW_TABLES & set(insp.get_table_names())
            assert not NEW_RESUME_COLUMNS & {c["name"] for c in insp.get_columns("resumes")}
            assert conn.execute(text("SELECT count(*) FROM resumes")).scalar() == 1
            assert conn.execute(text("SELECT count(*) FROM candidates")).scalar() == 1

        command.upgrade(cfg, "head")
        command.check(cfg)  # raises if the models and the migrated schema differ
    finally:
        engine.dispose()
