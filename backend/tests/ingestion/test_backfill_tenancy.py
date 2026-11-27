"""The tenancy backfill (E2-02, EPIC-02 spec §7.1 phase 2) on data written before E2-01.

The module gets its own throwaway database at the **expand** revision (``e2a01d5c8f36``), where
``agency_id`` is still nullable, as the owner role (the backfill is an operator command, and
there is no row-level security at that revision). Each test seeds it with the seed generator and then clears
every ``agency_id`` and ``campaigns.client_id``: exactly what a database written before E2-01
looks like after the expand migration. The contract migration itself is tested in
``tests/migrations/test_tenancy_e2_02.py``.

Covered: every table is filled and nothing else changes (``updated_at`` included); a second
run changes nothing; an interrupted run resumes; uploads during the backfill leave a database
that verifies; a batch
blocked by a live row lock is retried; what the backfill must not fill is reported; the
verification's tenancy checks before and after.
"""

from __future__ import annotations

import io
import re
import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.devtools import seed
from app.modules.ingestion import backfill_tenancy, verify_people_applications
from app.platform.tenancy import bind
from tests import postgres
from tests.default_agency import DEFAULT_AGENCY_ID, DEFAULT_CLIENT_ID
from tests.ingestion.test_people_applications import make_application, make_campaign

EXPAND = "e2a01d5c8f36"
REGION = "IN"
TABLES = backfill_tenancy.TABLES
# Tables with an updated_at that a backfill must not touch.
TIMESTAMPED = ("campaigns", "applications", "people", "resumes")
OTHER_AGENCY = uuid.UUID("00000000-0000-4000-8000-0000000000b2")


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    with postgres.throwaway_database("e2_02", EXPAND, pool_size=5, max_overflow=0) as eng:
        yield eng


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine)


@pytest.fixture
def manifest(engine: Engine, sessions: sessionmaker[Session]) -> dict[str, Any]:
    """The seed (60 people, 4 campaigns) as a database written before E2-01 holds it: no
    agency anywhere, no client on any campaign."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE campaigns, resumes, people, person_identifiers, identity_conflicts, "
                "person_resumes, applications CASCADE"
            )
        )
        conn.execute(text("DELETE FROM agencies WHERE id <> :id"), {"id": DEFAULT_AGENCY_ID})
    with sessions() as db:
        bind(db, DEFAULT_AGENCY_ID)  # the seed writes the session's agency (E2-03)
        generated = seed.generate(db, people=60, campaigns=4, seed=7)
        db.commit()
    with engine.begin() as conn:
        for table in TABLES:
            conn.execute(text(f"UPDATE {table} SET agency_id = NULL"))
        conn.execute(text("UPDATE campaigns SET client_id = NULL"))
    return generated


def run(sessions: sessionmaker[Session], **kwargs: Any) -> backfill_tenancy.Summary:
    return backfill_tenancy.run(sessions, out=io.StringIO(), **kwargs)


def verify(
    sessions: sessionmaker[Session], manifest: dict[str, Any] | None = None
) -> verify_people_applications.Report:
    with sessions() as db:
        return verify_people_applications.verify(db, manifest=manifest, default_region=REGION)


def failed_checks(report: verify_people_applications.Report) -> set[str]:
    return {check.name for check in report.checks if not check.ok}


def census(engine: Engine) -> dict[str, dict[Any, int]]:
    """``table -> {agency_id: rows}``, plus ``campaigns.client_id``."""
    with engine.connect() as conn:
        out = {
            table: dict(
                conn.execute(text(f"SELECT agency_id, count(*) FROM {table} GROUP BY 1")).all()
            )
            for table in TABLES
        }
        out["campaigns.client_id"] = dict(
            conn.execute(text("SELECT client_id, count(*) FROM campaigns GROUP BY 1")).all()
        )
    return out


def timestamps(engine: Engine) -> dict[str, dict[Any, Any]]:
    with engine.connect() as conn:
        return {
            table: dict(conn.execute(text(f"SELECT id, updated_at FROM {table}")).all())
            for table in TIMESTAMPED
        }


# ── A full run ────────────────────────────────────────────────
def test_the_seed_without_agencies_fails_the_tenancy_checks(
    sessions: sessionmaker[Session], manifest: dict[str, Any]
) -> None:
    report = verify(sessions, manifest)
    # The data itself is consistent; only the tenant key is missing.
    assert failed_checks(report) == {"tenancy_complete", "clients_same_agency"}
    assert "campaigns: 4 row(s) without an agency (run backfill_tenancy)" in report.render()
    assert all(expected == actual for _, expected, actual in report.manifest)


def test_the_backfill_finds_the_default_agency_by_its_slug(
    engine: Engine,
    sessions: sessionmaker[Session],
    manifest: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Since E2-12 the code has no fixed agency id: the backfill fills rows with the agency whose
    slug is ``default`` and its oldest active client ("General"), and changes nothing without
    them."""
    with sessions() as db:
        assert backfill_tenancy.default_agency(db) == backfill_tenancy.DefaultAgency(
            DEFAULT_AGENCY_ID, DEFAULT_CLIENT_ID
        )
    rename = text("UPDATE agencies SET slug = :slug WHERE id = :id")
    with engine.begin() as conn:
        conn.execute(rename, {"slug": "renamed", "id": DEFAULT_AGENCY_ID})
    try:
        capsys.readouterr()
        assert backfill_tenancy.main([], session_factory=sessions) == 1
        assert "no agency has the slug 'default'" in capsys.readouterr().out
        assert backfill_tenancy.status(sessions)["campaigns"] == 4  # nothing filled
    finally:
        with engine.begin() as conn:
            conn.execute(rename, {"slug": "default", "id": DEFAULT_AGENCY_ID})


def test_the_backfill_fills_every_table_and_a_second_run_changes_nothing(
    engine: Engine, sessions: sessionmaker[Session], manifest: dict[str, Any]
) -> None:
    before = census(engine)
    rows = {table: sum(n for n in before[table].values()) for table in TABLES}
    assert all(set(before[table]) == {None} for table in TABLES)
    stamps = timestamps(engine)

    summary = run(sessions, batch_size=50)

    assert summary.complete and not summary.stopped_early
    assert {r.table: r.rows for r in summary.results} == rows
    assert {r.table: r.batches for r in summary.results} == {
        table: -(-n // 50) for table, n in rows.items()
    }
    after = census(engine)
    assert {table: after[table] for table in TABLES} == {
        table: {DEFAULT_AGENCY_ID: n} for table, n in rows.items()
    }
    assert after["campaigns.client_id"] == {DEFAULT_CLIENT_ID: 4}
    assert timestamps(engine) == stamps  # a backfill is not an edit

    report = verify(sessions, manifest)
    assert report.ok, report.render()
    assert report.counts["agencies"] == 1

    again = run(sessions, batch_size=50)
    assert again.complete
    assert all(r.rows == 0 and r.batches == 0 for r in again.results)
    assert census(engine) == after
    assert timestamps(engine) == stamps


def test_the_cli_reports_progress_status_and_exit_codes(
    sessions: sessionmaker[Session], manifest: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    main = backfill_tenancy.main
    assert main(["--status"], session_factory=sessions) == 1
    status = capsys.readouterr().out
    assert re.search(r"campaigns\s+4 still missing", status)
    assert status.rstrip().endswith("NOT complete")

    assert main(["--batch-size", "100", "--table", "campaigns"], session_factory=sessions) == 0
    out = capsys.readouterr().out
    assert "campaigns: batch 1: 4 rows" in out and "people" not in out
    assert out.rstrip().endswith("complete")

    assert main(["--batch-size", "100"], session_factory=sessions) == 0
    out = capsys.readouterr().out
    assert re.search(r"campaigns\s+0\s+0\s+0", out)  # already done: 0 rows, 0 batches
    assert main(["--status"], session_factory=sessions) == 0
    assert capsys.readouterr().out.rstrip().endswith("complete")

    with pytest.raises(SystemExit):
        main(["--batch-size", "0"], session_factory=sessions)
    with pytest.raises(SystemExit):
        main(["--table", "agencies"], session_factory=sessions)


def test_an_interrupted_run_resumes_where_it_stopped(
    engine: Engine, sessions: sessionmaker[Session], manifest: dict[str, Any]
) -> None:
    first = run(sessions, batch_size=20, max_batches=3)
    assert first.stopped_early and not first.complete
    # campaigns (4 rows: one batch), then two batches of applications.
    assert [(r.table, r.rows) for r in first.results[:3]] == [
        ("campaigns", 4),
        ("applications", 40),
        ("people", 0),
    ]
    assert backfill_tenancy.status(sessions)["applications"] > 0

    second = run(sessions, batch_size=20)

    assert second.complete
    total = {r.table: r.rows for r in first.results}
    for r in second.results:
        total[r.table] += r.rows
    with engine.connect() as conn:
        assert total == {
            table: conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            for table in TABLES
        }  # every row filled exactly once over the two runs
    assert verify(sessions, manifest).ok


# ── Live writes ───────────────────────────────────────────────
# (Until E2-04, uploads during the backfill also matched returning candidates to People not
# backfilled yet: tenant_filter's `agency_id IS NULL` branch for the default agency. E2-04
# removed that branch, dead since the contract, and the test of it.)
def test_concurrent_uploads_during_the_backfill(
    engine: Engine, sessions: sessionmaker[Session], manifest: dict[str, Any]
) -> None:
    """Uploads (new candidates) in a second thread while the backfill runs in small batches: no
    error on either side, and the result verifies."""
    live = make_campaign(sessions, "Live uploads")
    errors: list[BaseException] = []
    uploaded: list[uuid.UUID] = []

    def upload() -> None:
        try:
            for i in range(30):
                email = f"live.upload{i}@example.com"
                uploaded.append(
                    make_application(sessions, live, email=email, name=f"Live {i}", score=i)
                )
        except BaseException as exc:  # reported below
            errors.append(exc)

    uploader = threading.Thread(target=upload)
    uploader.start()
    try:
        summary = run(sessions, batch_size=5)
    finally:
        uploader.join(timeout=60)
    assert not errors, errors
    assert summary.complete, summary.render()

    report = verify(sessions, manifest)
    assert report.ok, report.render()
    with sessions() as db:
        agencies = db.execute(
            text("SELECT DISTINCT agency_id FROM applications WHERE id = ANY(:ids)"),
            {"ids": uploaded},
        ).scalars()
        assert set(agencies) == {DEFAULT_AGENCY_ID}


def test_a_batch_blocked_by_a_live_row_lock_is_retried(
    engine: Engine,
    sessions: sessionmaker[Session],
    manifest: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backfill_tenancy, "LOCK_TIMEOUT", "200ms")
    monkeypatch.setattr(backfill_tenancy, "RETRY_PAUSE_S", 0.3)
    with engine.connect() as holder:
        holder.execute(text("SELECT id FROM applications ORDER BY id LIMIT 1 FOR UPDATE"))
        release = threading.Timer(0.35, holder.rollback)
        release.start()
        try:
            summary = run(sessions, tables=["applications"])
        finally:
            release.join()

    [result] = summary.results
    assert result.retries >= 1
    assert summary.complete


def test_a_batch_that_stays_blocked_fails_the_run_and_keeps_what_was_committed(
    engine: Engine,
    sessions: sessionmaker[Session],
    manifest: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(backfill_tenancy, "LOCK_TIMEOUT", "100ms")
    monkeypatch.setattr(backfill_tenancy, "RETRY_PAUSE_S", 0.01)
    with engine.connect() as holder:
        holder.execute(text("SELECT id FROM people ORDER BY id LIMIT 1 FOR UPDATE"))
        started = time.perf_counter()
        code = backfill_tenancy.main([], session_factory=sessions)
        assert time.perf_counter() - started < 10
        holder.rollback()

    assert code == 1
    assert "a batch failed" in capsys.readouterr().out
    status = backfill_tenancy.status(sessions)
    assert status["campaigns"] == status["applications"] == 0  # committed before the failure
    assert status["people"] > 0
    assert run(sessions).complete  # and a new run finishes the job


# ── What the backfill must not fill ───────────────────────────
def test_a_campaign_of_another_agency_is_not_given_the_default_client(
    engine: Engine,
    sessions: sessionmaker[Session],
    manifest: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO agencies (id, name, slug, status) VALUES (:id, 'B', 'b', 'active')"),
            {"id": OTHER_AGENCY},
        )
        campaign = conn.execute(
            text(
                "UPDATE campaigns SET agency_id = :a "
                "WHERE id = (SELECT id FROM campaigns ORDER BY id LIMIT 1) RETURNING id"
            ),
            {"a": OTHER_AGENCY},
        ).scalar_one()

    assert backfill_tenancy.main([], session_factory=sessions) == 1
    assert "rows are still missing their agency" in capsys.readouterr().out
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT agency_id, client_id FROM campaigns WHERE id = :id"), {"id": campaign}
        ).one()
    assert tuple(row) == (OTHER_AGENCY, None)
    assert backfill_tenancy.status(sessions)["campaigns"] == 1

    report = verify(sessions)
    assert "clients_same_agency" in failed_checks(report)
    assert f"campaign {campaign} has no client" in report.render()
    # Its applications and resumes were given the default agency: the cross-module check says so.
    assert "cross_module_same_agency" in failed_checks(report)
