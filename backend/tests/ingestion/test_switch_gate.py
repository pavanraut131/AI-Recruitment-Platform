"""E0-18: the switch gate. After the backfill, the legacy API keeps People + Applications right,
the re-sync repairs what was edited before E0-18, and ``verify_backfill`` proves both.

Like ``test_backfill.py`` this needs commits on several connections, so the module gets its own
throwaway database at ``head``; the HTTP API runs against it through an async engine of its
own (``get_db`` overridden), so every request commits for real, exactly as in production.

Covered:

- recruiter edits (stage, notes, category, bulk stage) and deletes (sole candidate, a merged
  candidate, the candidate whose id the application reuses, bulk) through the API after a
  backfill: ``verify_backfill`` stays green, ``applications_have_resumes`` included;
- edits made straight in ``candidates`` after the backfill (what the pre-E0-18 endpoints did):
  ``recruiter_fields_consistent`` fails, ``--resync-recruiter-fields`` fixes it, a second run
  changes nothing;
- ``verify_backfill`` detects an application out of step with its candidates;
- the pipeline recomputes the fields when it links a candidate the recruiter edited while its
  resume was processing.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.modules.ai.fake import FakeAIService
from app.modules.hiring.models import Application, Candidate, LegacyCandidateMap
from app.modules.ingestion import backfill, tasks, verify_backfill
from app.modules.ingestion.models import Resume
from tests import postgres
from tests.ingestion.test_backfill import (
    REGION,
    T0,
    checksum,
    make_campaign,
    make_legacy,
    run,
    seed_db,
)

API = "/api/v1"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    with postgres.throwaway_database("e0_18", pool_size=5, max_overflow=0) as eng:
        yield eng


@pytest.fixture(autouse=True)
def empty_tables(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE campaigns, resumes, candidates, people, person_identifiers, "
                "identity_conflicts, person_resumes, applications, legacy_candidate_map, "
                "backfill_checkpoints CASCADE"
            )
        )


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine)


@pytest_asyncio.fixture
async def api(app: FastAPI, engine: Engine) -> AsyncIterator[AsyncClient]:
    """The HTTP API on the throwaway database; each request's session commits for real."""
    from app.platform.database import get_db

    url = engine.url.set(drivername="postgresql+asyncpg")
    async_engine = create_async_engine(url, poolclass=NullPool)
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_db, None)
        await async_engine.dispose()


def verify(
    sessions: sessionmaker[Session], manifest: dict[str, Any] | None = None
) -> verify_backfill.Report:
    with sessions() as db:
        return verify_backfill.verify(db, manifest=manifest, default_region=REGION)


def failed_checks(report: verify_backfill.Report) -> set[str]:
    return {check.name for check in report.checks if not check.ok}


def make_trio(sessions: sessionmaker[Session], campaign: uuid.UUID) -> list[Candidate]:
    """Three resumes of one person to one campaign, uploaded oldest .. newest, backfilled in the
    order middle, oldest, newest (``created_at``): one application with the middle one's id."""
    return [
        make_legacy(
            sessions,
            campaign,
            uploaded_at=T0 + timedelta(days=i),
            created_at=T0 + timedelta(days=10 + (1, 0, 2)[i]),
            email="trio@example.com",
            name="Trio Person",
            score=float(10 * (i + 1)),
        )
        for i in range(3)
    ]


def application_of(db: Session, candidate_id: uuid.UUID) -> Application | None:
    redirected = db.get(LegacyCandidateMap, candidate_id)
    return db.get(Application, redirected.application_id if redirected else candidate_id)


# ── The API after the backfill ────────────────────────────────
async def test_recruiter_edits_and_deletes_through_the_api_keep_verify_green(
    sessions: sessionmaker[Session], api: AsyncClient
) -> None:
    manifest = seed_db(sessions)
    campaign = make_campaign(sessions, "Trio")
    oldest, middle, newest = make_trio(sessions, campaign)
    run(sessions)
    assert verify(sessions, manifest).ok

    with sessions() as db:
        app = application_of(db, oldest.id)
        assert app is not None
        assert (app.id, app.resume_id) == (middle.id, newest.resume_id)
        app_id = app.id

    # Stage: oldest, then middle; the newer one's wins whatever the edit order.
    for candidate, stage in ((middle, "technical"), (oldest, "offer")):
        resp = await api.patch(f"{API}/candidates/{candidate.id}/pipeline", json={"stage": stage})
        assert resp.status_code == 200
    resp = await api.patch(f"{API}/candidates/{oldest.id}/notes", json={"notes": "strong systems"})
    assert resp.status_code == 200
    resp = await api.put(f"{API}/candidates/{newest.id}/category", json={"category": "rejected"})
    assert resp.status_code == 200
    resp = await api.put(
        f"{API}/candidates/{oldest.id}/category", json={"category": "strong_match"}
    )
    assert resp.status_code == 200
    with sessions() as db:
        app = db.get_one(Application, app_id)
        assert (app.pipeline_stage, app.notes, app.category) == (
            "technical",
            "strong systems",
            "rejected",
        )

    # Bulk stage over seeded candidates of several applications.
    with sessions() as db:
        seeded_campaign = uuid.UUID(manifest["campaign_ids"][0])
        seeded = list(
            db.scalars(
                select(Candidate.id).where(Candidate.campaign_id == seeded_campaign).limit(8)
            )
        )
    resp = await api.patch(
        f"{API}/campaigns/{seeded_campaign}/candidates/bulk-pipeline",
        json={"candidate_ids": [str(c) for c in seeded], "stage": "hired"},
    )
    assert resp.json() == {"updated": len(seeded)}
    report = verify(sessions, manifest)
    assert report.ok, report.render()

    # Delete the candidate whose id the application reuses (it does not hold the current
    # resume): the application stays, under that id.
    assert (await api.delete(f"{API}/candidates/{middle.id}")).status_code == 204
    report = verify(sessions)
    assert report.ok, report.render()
    with sessions() as db:
        app = db.get_one(Application, app_id)
        assert app.resume_id == newest.resume_id
        assert app.pipeline_stage == "offer"  # the middle one's stage went with it

    # Delete the candidate of the current resume: the scoring moves to the one that is left.
    assert (await api.delete(f"{API}/candidates/{newest.id}")).status_code == 204
    report = verify(sessions)
    assert report.ok, report.render()
    with sessions() as db:
        app = db.get_one(Application, app_id)
        assert (app.resume_id, app.score, app.category) == (oldest.resume_id, 10.0, "strong_match")
        assert db.get(LegacyCandidateMap, newest.id) is None

    # The last one: the application goes too.
    assert (await api.delete(f"{API}/candidates/{oldest.id}")).status_code == 204
    with sessions() as db:
        assert db.get(Application, app_id) is None
        assert (
            db.scalar(select(LegacyCandidateMap).where(LegacyCandidateMap.application_id == app_id))
            is None
        )

    # Bulk delete of seeded candidates, merged ones among them.
    with sessions() as db:
        merged_ids = list(db.scalars(select(LegacyCandidateMap.candidate_id).limit(3)))
        merged_campaigns = {db.get_one(Candidate, c).campaign_id for c in merged_ids}
    for campaign_id in merged_campaigns:
        resp = await api.request(
            "DELETE",
            f"{API}/campaigns/{campaign_id}/candidates/bulk",
            json={"candidate_ids": [str(c) for c in merged_ids]},
        )
        assert resp.status_code == 200
    report = verify(sessions)
    assert report.ok, report.render()
    assert not failed_checks(report)


# ── Re-sync ───────────────────────────────────────────────────
def test_resync_repairs_edits_made_before_e0_18_and_is_idempotent(
    sessions: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = seed_db(sessions)
    campaign = make_campaign(sessions, "Trio")
    oldest, middle, newest = make_trio(sessions, campaign)
    run(sessions)
    assert verify(sessions, manifest).ok

    # What the pre-E0-18 endpoints did: write candidates only.
    with sessions() as db:
        db.execute(
            update(Candidate)
            .where(Candidate.id == oldest.id)
            .values(pipeline_stage="offer", notes="old")
        )
        db.execute(
            update(Candidate).where(Candidate.id == middle.id).values(pipeline_stage="hired")
        )
        db.execute(update(Candidate).where(Candidate.id == newest.id).values(category="rejected"))
        seeded = db.scalars(
            select(Candidate.id)
            .where(Candidate.campaign_id != campaign)
            .order_by(Candidate.id)
            .limit(5)
        ).all()
        db.execute(update(Candidate).where(Candidate.id.in_(seeded)).values(notes="called back"))
        db.commit()

    report = verify(sessions, manifest)
    assert failed_checks(report) == {"recruiter_fields_consistent"}
    assert "its candidates give" in report.render()

    summary = backfill.run_resync_recruiter_fields(sessions, batch_size=7, progress=lambda _: None)
    with sessions() as db:
        total = db.scalar(select(text("count(*)")).select_from(Application))
        app = db.get_one(Application, middle.id)
        assert (app.pipeline_stage, app.notes, app.category) == ("hired", "old", "rejected")
    assert summary.applications == total
    assert summary.batches == -(-total // 7)
    # The trio's application, plus each edited seeded candidate's (all 5 distinct or merged).
    assert 2 <= summary.changed <= 6
    assert summary.fields["category"] == 1
    report = verify(sessions, manifest)
    assert report.ok, report.render()

    before = checksum(sessions)
    again = backfill.run_resync_recruiter_fields(sessions, batch_size=7, progress=lambda _: None)
    assert again.changed == 0
    assert checksum(sessions) == before

    assert (
        backfill.main(["--resync-recruiter-fields", "--batch-size", "50"], session_factory=sessions)
        == 0
    )
    out = capsys.readouterr().out
    assert f"applications checked:   {total}" in out
    assert "applications changed:   0" in out


def test_verify_detects_an_application_out_of_step_with_its_candidates(
    sessions: sessionmaker[Session],
) -> None:
    campaign = make_campaign(sessions)
    oldest, middle, newest = make_trio(sessions, campaign)
    run(sessions)
    assert verify(sessions).ok

    with sessions() as db:
        db.execute(update(Application).values(pipeline_stage="hired", category="weak_match"))
        db.commit()
    report = verify(sessions)
    assert failed_checks(report) == {"recruiter_fields_consistent"}
    rendered = report.render()
    assert "pipeline_stage is 'hired', its candidates give None" in rendered
    assert "category is 'weak_match', its candidates give 'moderate_match'" in rendered


# ── Pipeline ──────────────────────────────────────────────────
async def test_an_edit_while_the_resume_was_processing_reaches_the_application_when_it_is_linked(
    sessions: sessionmaker[Session],
    api: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    fake_ai: FakeAIService,
) -> None:
    monkeypatch.setattr(tasks, "SyncSession", sessions)
    campaign = make_campaign(sessions)
    legacy = make_legacy(sessions, campaign, uploaded_at=T0, pipeline_stage="phone_call")
    run(sessions)
    upload = make_legacy(
        sessions,
        campaign,
        uploaded_at=T0 + timedelta(days=1),
        created_at=datetime.now(UTC),
        status="processing",
    )
    with sessions() as db:  # only its text exists yet
        db.execute(
            update(Candidate)
            .where(Candidate.id == upload.id)
            .values(
                score=None,
                category=None,
                raw_text="Asha Rao\nasha.rao@example.com\nBackend Engineer, 6 years\nSkills: Python, Go",
            )
        )
        db.commit()

    # No application leads from it yet: the edit is on the candidate only.
    resp = await api.patch(f"{API}/candidates/{upload.id}/pipeline", json={"stage": "technical"})
    assert resp.status_code == 200
    with sessions() as db:
        assert db.get_one(Application, legacy.id).pipeline_stage == "phone_call"

    result = tasks.process_candidate_ai.apply(args=(str(upload.resume_id),))
    assert result.successful(), result.result

    with sessions() as db:
        app = db.get_one(Application, legacy.id)
        assert db.get_one(LegacyCandidateMap, upload.id).application_id == app.id
        assert app.resume_id == upload.resume_id
        assert app.pipeline_stage == "technical"  # the newer resume's candidate's stage
        assert db.get_one(Resume, upload.resume_id).status == "done"
    report = verify(sessions)
    assert report.ok, report.render()
