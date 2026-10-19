"""After the contract (E0-20): People + Applications on the seed, with real commits.

The legacy ``candidates`` table is gone; the candidate endpoints serve applications and the
resumes' extraction snapshots (spec §7), and a campaign delete is composed in ``app.api``. These
tests prove it on the seed (``app.devtools.seed``, which runs the pipeline's matching and
scoring services) with every request committing for real.

The module gets its own throwaway database at ``head``; the HTTP API runs against it through an
async engine of its own (``get_db`` overridden), exactly as in production.

Covered:

- **The seed is consistent:** ``verify_people_applications --manifest`` passes, and each
  campaign's list (all pages), filters and CSV export show one row per application, built from
  the application and its current resume's snapshot.
- **Campaign delete:** the campaign, its applications, its resumes and their stored PDFs are
  gone, nothing else is, ``verify_people_applications`` stays green and
  ``app.devtools.storage_orphans`` finds no orphan. A PDF whose delete fails stays as an orphan
  that the check reports; the request still succeeds.
- **An upload after the contract** is processed and verified like any other.
- **The list query's plan** on ~1,200 applications (a campaign with 100): index scans, printed
  with ``EXPLAIN ANALYZE`` (run with ``-s`` to see them).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, delete, event, func, select, text, update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.devtools import seed, storage_orphans
from app.modules.ai.fake import FakeAIService
from app.modules.ai.schemas import ExtractionResult, ScoringResult
from app.modules.hiring import service as hiring_service
from app.modules.hiring.models import Application, Campaign
from app.modules.hiring.schemas import CandidateDetail
from app.modules.ingestion import tasks, verify_people_applications
from app.modules.ingestion.models import Resume
from app.modules.talent import service as talent_service
from app.modules.talent.models import Person, PersonIdentifier
from tests import postgres
from tests.fakes import InMemoryStorage

API = "/api/v1"
REGION = "IN"
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    with postgres.throwaway_database("e0_20", pool_size=5, max_overflow=0) as eng:
        yield eng


@pytest.fixture(autouse=True)
def empty_tables(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE campaigns, resumes, people, person_identifiers, identity_conflicts, "
                "person_resumes, applications, legacy_candidate_map CASCADE"
            )
        )


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine)


@pytest_asyncio.fixture
async def async_engine(engine: Engine) -> AsyncIterator[AsyncEngine]:
    url = engine.url.set(drivername="postgresql+asyncpg")
    eng = create_async_engine(url, poolclass=NullPool)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def api(app: FastAPI, async_engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    """The HTTP API on the throwaway database; each request's session commits for real."""
    from app.platform.database import get_db

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


# ── Helpers ───────────────────────────────────────────────────
def seed_db(
    sessions: sessionmaker[Session], people: int = 60, campaigns: int = 4, seed_: int = 7
) -> dict[str, Any]:
    with sessions() as db:
        manifest = seed.generate(db, people=people, campaigns=campaigns, seed=seed_)
        db.commit()
    return manifest


def verify(
    sessions: sessionmaker[Session], manifest: dict[str, Any] | None = None
) -> verify_people_applications.Report:
    with sessions() as db:
        return verify_people_applications.verify(db, manifest=manifest, default_region=REGION)


def make_campaign(sessions: sessionmaker[Session], title: str = "Backend") -> uuid.UUID:
    with sessions() as db:
        campaign = Campaign(title=title, role="Backend Engineer", required_skills=["Python"])
        db.add(campaign)
        db.commit()
        return campaign.id


def make_application(
    sessions: sessionmaker[Session],
    campaign: uuid.UUID,
    *,
    email: str | None,
    name: str,
    score: float,
    uploaded_at: datetime = T0,
    phone: str | None = None,
) -> uuid.UUID:
    """A processed resume as the pipeline leaves it (snapshot, Person, Application), written
    through the same services; returns the application id."""
    extraction = ExtractionResult(name=name, email=email, phone=phone, skills=["Python"])
    with sessions() as db:
        resume = Resume(
            campaign_id=campaign,
            original_filename="cv.pdf",
            storage_path=f"campaigns/{campaign}/resumes/{uuid.uuid4()}/cv.pdf",
            file_size_bytes=1000,
            status="done",
            uploaded_at=uploaded_at,
            extracted=extraction.model_dump(mode="json"),
        )
        db.add(resume)
        db.flush()
        resolution = talent_service.resolve_person(
            db,
            email=email,
            phone=phone,
            profile=tasks.person_profile(extraction),
            resume_id=resume.id,
            uploaded_at=uploaded_at,
            default_region=REGION,
        )
        resume.person_id = resolution.person_id
        application = hiring_service.upsert_application(
            db,
            campaign_id=campaign,
            person_id=resolution.person_id,
            resume_id=resume.id,
            resume_uploaded_at=uploaded_at,
            scoring=ScoringResult(
                score=score, category="moderate_match", summary="s", recommendation="r"
            ),
        )
        db.commit()
        return application.id


def expected_rows(sessions: sessionmaker[Session], campaign_id: uuid.UUID) -> dict[str, Any]:
    """Each application of the campaign as the read path must show it (spec §7.1)."""
    with sessions() as db:
        applications = db.scalars(
            select(Application).where(Application.campaign_id == campaign_id)
        ).all()
        snapshots = dict(
            db.execute(
                select(Resume.id, Resume.extracted).where(
                    Resume.id.in_([a.resume_id for a in applications])
                )
            )
            .tuples()
            .all()
        )
        return {
            str(a.id): hiring_service.candidate_detail(a, snapshots[a.resume_id])
            for a in applications
        }


async def all_pages(api: AsyncClient, campaign_id: uuid.UUID | str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        resp = await api.get(
            f"{API}/campaigns/{campaign_id}/candidates", params={"page": page, "page_size": 100}
        )
        assert resp.status_code == 200
        rows += resp.json()
        if len(resp.json()) < 100:
            return rows
        page += 1


def store_pdfs(sessions: sessionmaker[Session], storage: InMemoryStorage) -> None:
    """Put a PDF in (fake) object storage for every resume, as an upload would have."""
    with sessions() as db:
        for path in db.scalars(select(Resume.storage_path)):
            storage.upload_file(b"%PDF-1.4", path)


def run_storage_check(sessions: sessionmaker[Session], *argv: str) -> int:
    return storage_orphans.main(["--min-age-minutes", "0", *argv], session_factory=sessions)


# ── The seed, served ──────────────────────────────────────────
async def test_the_seed_verifies_and_every_campaign_lists_its_applications(
    sessions: sessionmaker[Session], api: AsyncClient
) -> None:
    manifest = seed_db(sessions)
    report = verify(sessions, manifest)
    assert report.ok, report.render()

    for campaign_id in manifest["campaign_ids"]:
        expected = expected_rows(sessions, uuid.UUID(campaign_id))
        rows = await all_pages(api, campaign_id)
        assert sorted(r["id"] for r in rows) == sorted(expected)
        scores = [r["score"] for r in rows]
        assert scores == sorted(scores, reverse=True)  # every seeded application is scored
        for row in rows:
            detail = expected[row["id"]]
            assert row["name"] == detail.name and row["email"] == detail.email
            assert (await api.get(f"{API}/candidates/{row['id']}")).json() == detail.model_dump(
                mode="json"
            )

        resp = await api.get(f"{API}/campaigns/{campaign_id}/candidates/export")
        lines = resp.text.split("\r\n")
        assert lines[0] == ",".join(hiring_service.CSV_FIELDS)
        assert len(lines) == len(rows) + 2  # header, one per application, trailing ""
        expected_lines = hiring_service.render_candidates_csv(expected.values()).split("\r\n")
        assert sorted(lines[1:-1]) == sorted(expected_lines[1:-1])


async def test_the_list_filters_on_the_seed(
    sessions: sessionmaker[Session], api: AsyncClient
) -> None:
    manifest = seed_db(sessions)
    campaign_id = uuid.UUID(manifest["campaign_ids"][0])
    rows: dict[str, CandidateDetail] = expected_rows(sessions, campaign_id)
    some = next(iter(rows.values()))
    probes: list[tuple[dict[str, Any], Any]] = [
        (
            {"skill": some.skills[0][1:3]},
            lambda c: any(some.skills[0][1:3].lower() in s.lower() for s in c.skills),
        ),
        (
            {"min_experience": 4},
            lambda c: c.years_of_experience is not None and c.years_of_experience >= 4,
        ),
        (
            {"search": (some.name or "x")[:3].upper()},
            lambda c: (
                (some.name or "x")[:3].lower() in (c.name or "").lower()
                or (some.name or "x")[:3].lower() in (c.email or "").lower()
            ),
        ),
        (
            {"category": "strong_match", "min_score": 40},
            lambda c: c.category == "strong_match" and (c.score or 0) >= 40,
        ),
    ]
    for params, keep in probes:
        resp = await api.get(
            f"{API}/campaigns/{campaign_id}/candidates", params={**params, "page_size": 100}
        )
        got = {r["id"] for r in resp.json()}
        assert got == {i for i, c in rows.items() if keep(c)}, params


# ── Campaign delete (E0-20) ───────────────────────────────────
def _campaign_counts(sessions: sessionmaker[Session]) -> dict[uuid.UUID, tuple[int, int]]:
    """``campaign_id -> (applications, resumes)``."""
    with sessions() as db:
        apps = dict(
            db.execute(
                select(Application.campaign_id, func.count()).group_by(Application.campaign_id)
            ).all()
        )
        resumes = dict(
            db.execute(select(Resume.campaign_id, func.count()).group_by(Resume.campaign_id)).all()
        )
    return {c: (apps.get(c, 0), resumes.get(c, 0)) for c in set(apps) | set(resumes)}


async def test_deleting_a_seeded_campaign_removes_its_resumes_and_pdfs_only(
    sessions: sessionmaker[Session], api: AsyncClient, fake_storage: InMemoryStorage
) -> None:
    manifest = seed_db(sessions)
    store_pdfs(sessions, fake_storage)
    doomed, *kept = (uuid.UUID(c) for c in manifest["campaign_ids"])
    before = _campaign_counts(sessions)
    with sessions() as db:
        doomed_paths = set(
            db.scalars(select(Resume.storage_path).where(Resume.campaign_id == doomed))
        )
    assert before[doomed][1] > 0 and doomed_paths

    resp = await api.delete(f"{API}/campaigns/{doomed}")

    assert (resp.status_code, resp.text) == (204, "")
    after = _campaign_counts(sessions)
    assert doomed not in after
    assert {c: after[c] for c in kept} == {c: before[c] for c in kept}
    assert not doomed_paths & set(fake_storage.objects)
    assert len(fake_storage.objects) == sum(before[c][1] for c in kept)
    assert (await api.get(f"{API}/campaigns/{doomed}")).status_code == 404
    report = verify(sessions)
    assert report.ok, report.render()
    assert run_storage_check(sessions) == 0  # no orphan


async def test_a_failed_pdf_delete_still_deletes_the_campaign_and_leaves_a_reported_orphan(
    sessions: sessionmaker[Session],
    api: AsyncClient,
    fake_storage: InMemoryStorage,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    campaign = make_campaign(sessions)
    make_application(sessions, campaign, email="a@example.com", name="A", score=50.0)
    store_pdfs(sessions, fake_storage)
    (key,) = fake_storage.objects

    def unavailable(object_key: str) -> None:
        raise ConnectionError("storage is down")

    from app.platform import storage

    monkeypatch.setattr(storage, "delete_file", unavailable)
    resp = await api.delete(f"{API}/campaigns/{campaign}")
    assert resp.status_code == 204  # committed: a storage failure never fails the request
    assert _campaign_counts(sessions) == {}
    assert list(fake_storage.objects) == [key]

    monkeypatch.setattr(storage, "delete_file", fake_storage.delete_file)
    capsys.readouterr()
    assert run_storage_check(sessions) == 1
    assert f"orphan {key}" in capsys.readouterr().out
    assert run_storage_check(sessions, "--delete") == 0
    assert fake_storage.objects == {}
    assert run_storage_check(sessions) == 0


async def test_deleting_a_candidate_removes_its_pdfs_after_the_commit(
    sessions: sessionmaker[Session], api: AsyncClient, fake_storage: InMemoryStorage
) -> None:
    campaign = make_campaign(sessions)
    # Two resumes of one person: one application holding the newer one.
    first = make_application(sessions, campaign, email="p@example.com", name="P", score=10.0)
    second = make_application(
        sessions,
        campaign,
        email="p@example.com",
        name="P",
        score=20.0,
        uploaded_at=T0 + timedelta(days=1),
    )
    assert first == second
    kept = make_application(sessions, campaign, email="k@example.com", name="K", score=30.0)
    store_pdfs(sessions, fake_storage)
    assert len(fake_storage.objects) == 3

    assert (await api.delete(f"{API}/candidates/{first}")).status_code == 204

    with sessions() as db:
        (kept_path,) = db.scalars(select(Resume.storage_path))
        assert db.get(Application, kept) is not None
    assert list(fake_storage.objects) == [kept_path]
    assert run_storage_check(sessions) == 0


# ── After the contract ────────────────────────────────────────
async def test_an_upload_after_the_contract_is_processed_and_verified(
    sessions: sessionmaker[Session],
    api: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    fake_ai: FakeAIService,
) -> None:
    monkeypatch.setattr(tasks, "SyncSession", sessions)
    campaign = make_campaign(sessions)
    with sessions() as db:  # as the extract step leaves it: the text on the resume
        resume = Resume(
            id=uuid.uuid4(),
            campaign_id=campaign,
            original_filename="new.pdf",
            storage_path=f"campaigns/{campaign}/new.pdf",
            file_size_bytes=1000,
            status="processing",
            uploaded_at=datetime.now(UTC),
            raw_text="Meera Iyer\nmeera.iyer@example.com\nBackend Engineer, 4 years\nSkills: Go",
        )
        db.add(resume)
        db.commit()
        rid = resume.id

    resp = await api.get(f"{API}/resumes/{rid}/candidate")
    assert (resp.status_code, resp.json()) == (404, {"detail": "Candidate not yet processed"})

    result = tasks.process_candidate_ai.apply(args=(str(rid),))
    assert result.successful(), result.result

    body = (await api.get(f"{API}/resumes/{rid}/candidate")).json()
    assert (body["name"], body["email"]) == ("Meera Iyer", "meera.iyer@example.com")
    assert [r["id"] for r in await all_pages(api, campaign)] == [body["id"]]
    report = verify(sessions)
    assert report.ok, report.render()
    assert report.counts["applications"] == 1


# ── The list query's plan ─────────────────────────────────────
async def test_the_list_queries_use_the_indexes(
    sessions: sessionmaker[Session], api: AsyncClient, async_engine: AsyncEngine
) -> None:
    seed_db(sessions, people=800, campaigns=15, seed_=42)
    campaign = make_campaign(sessions, "A campaign with 100 applications")
    for i in range(100):
        make_application(
            sessions,
            campaign,
            uploaded_at=T0 + timedelta(minutes=i),
            email=f"applicant{i}@example.com",
            name=f"Applicant {i}",
            score=float(i % 97),
        )
    with sessions() as db:
        db.connection().exec_driver_sql("ANALYZE")
        db.commit()

    statements: list[tuple[str, Any]] = []

    def record(conn: Any, cursor: Any, statement: str, parameters: Any, *_: Any) -> None:
        statements.append((statement, parameters))

    for params, expected in (
        ({}, 2),
        ({"search": "applicant 4", "min_score": 10}, 4),
    ):
        statements.clear()
        event.listen(async_engine.sync_engine, "before_cursor_execute", record)
        try:
            resp = await api.get(f"{API}/campaigns/{campaign}/candidates", params=params)
        finally:
            event.remove(async_engine.sync_engine, "before_cursor_execute", record)
        assert resp.status_code == 200 and resp.json()
        reads = [(s, p) for s, p in statements if s.lstrip().upper().startswith("SELECT")]
        assert len(reads) == expected, [s for s, _ in reads]

        plans: list[str] = []
        async with async_engine.connect() as conn:
            raw = await conn.get_raw_connection()
            driver = raw.driver_connection
            assert driver is not None
            for statement, parameters in reads:
                rows = await driver.fetch(f"EXPLAIN (ANALYZE, BUFFERS) {statement}", *parameters)
                plans.append("\n".join(r[0] for r in rows))
        print(f"\n### GET /campaigns/{{id}}/candidates {params or '(no filters)'}")
        for (statement, _), plan in zip(reads, plans, strict=True):
            print(f"\n{statement}\n--\n{plan}")
        # The page of applications: by campaign (the unique index's leading column).
        assert "uq_applications_campaign_id_person_id" in plans[-2], plans[-2]
        # The page's extraction snapshots: by primary key, one query for the whole page.
        assert "resumes_pkey" in plans[-1], plans[-1]


# ── verify_people_applications catches what the services must prevent ──
@pytest.fixture
def seeded(sessions: sessionmaker[Session]) -> dict[str, Any]:
    manifest = seed_db(sessions)
    assert verify(sessions, manifest).ok
    return manifest


def failed_checks(report: verify_people_applications.Report) -> set[str]:
    return {check.name for check in report.checks if not check.ok}


def test_verify_fails_when_a_done_resume_has_no_person(
    sessions: sessionmaker[Session], seeded: dict[str, Any]
) -> None:
    with sessions() as db:
        rid = db.scalars(select(Resume.id).limit(1)).one()
        db.execute(update(Resume).where(Resume.id == rid).values(person_id=None))
        db.commit()
    assert "done_resumes_have_people" in failed_checks(verify(sessions))


def test_verify_fails_on_an_application_without_its_person(
    sessions: sessionmaker[Session], seeded: dict[str, Any]
) -> None:
    with sessions() as db:
        db.execute(
            update(Application)
            .where(Application.id == db.scalars(select(Application.id).limit(1)).one())
            .values(person_id=uuid.uuid4())
        )
        db.commit()
    failed = failed_checks(verify(sessions))
    assert {"applications_have_people", "applications_have_resumes"} <= failed


def test_verify_fails_on_an_application_whose_resume_is_gone(
    sessions: sessionmaker[Session], seeded: dict[str, Any]
) -> None:
    # No foreign key keeps them together (ADR 0003): only the services and this check do.
    with sessions() as db:
        rid = db.scalars(select(Application.resume_id).limit(1)).one()
        db.execute(delete(Resume).where(Resume.id == rid))
        db.commit()
    report = verify(sessions)
    assert "applications_have_resumes" in failed_checks(report)
    assert f"resume {rid} does not exist" in report.render()


def test_verify_fails_on_an_application_whose_resume_has_no_extraction(
    sessions: sessionmaker[Session], seeded: dict[str, Any]
) -> None:
    with sessions() as db:
        rid = db.scalars(select(Application.resume_id).limit(1)).one()
        db.execute(update(Resume).where(Resume.id == rid).values(extracted=None))
        db.commit()
    report = verify(sessions)
    assert "applications_have_extractions" in failed_checks(report)
    assert f"resume {rid} has no extraction" in report.render()


def test_verify_fails_on_a_person_whose_usable_identifiers_are_missing(
    sessions: sessionmaker[Session], seeded: dict[str, Any]
) -> None:
    with sessions() as db:
        pid = db.scalars(select(PersonIdentifier.person_id).limit(1)).one()
        db.execute(text("DELETE FROM person_identifiers WHERE person_id = :p"), {"p": pid})
        db.commit()
    report = verify(sessions)
    assert failed_checks(report) == {"people_without_identifiers_justified"}
    assert "has a usable one" in report.render()


def test_verify_fails_on_a_person_resolved_from_no_resume(
    sessions: sessionmaker[Session], seeded: dict[str, Any]
) -> None:
    with sessions() as db:
        db.add(Person(full_name="Nobody"))
        db.commit()
    assert failed_checks(verify(sessions)) == {"people_have_resumes"}


def test_people_without_identifiers_are_fine_when_their_resumes_had_none_usable(
    sessions: sessionmaker[Session],
) -> None:
    """No contact details, or only a region-inferred landline (option A) that another Person
    already holds: no identifier is stored, and that is correct."""
    campaign = make_campaign(sessions)
    make_application(sessions, campaign, email=None, name="A", score=1.0)
    make_application(
        sessions, campaign, email="a@example.com", name="B", score=1.0, phone="(415) 555-2671"
    )
    make_application(
        sessions,
        campaign,
        email=None,
        name="Other Person",
        score=1.0,
        phone="(415) 555-2671",
        uploaded_at=T0 + timedelta(hours=1),
    )

    report = verify(sessions)
    assert report.ok, report.render()
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Person)) == 3
        assert len(talent_service.people_without_identifiers(db)) == 2


def test_verify_fails_on_a_manifest_mismatch(
    sessions: sessionmaker[Session], seeded: dict[str, Any]
) -> None:
    wrong = json.loads(json.dumps(seeded))
    wrong["expected"]["people"] += 1
    report = verify(sessions, wrong)
    assert all(check.ok for check in report.checks)
    assert not report.ok
    assert "FAIL expected.people" in report.render()


def test_the_manifest_counts_only_its_campaigns(
    sessions: sessionmaker[Session], seeded: dict[str, Any]
) -> None:
    other = make_campaign(sessions, "Live uploads")
    make_application(sessions, other, email="new@example.com", name="New", score=5.0)
    report = verify(sessions, seeded)
    assert report.ok, report.render()
    assert report.manifest_scope == "rows in the manifest's 4 campaigns"
    unscoped = {k: v for k, v in seeded.items() if k != "campaign_ids"}
    assert not verify(sessions, unscoped).ok  # the extra application counts without a scope


def test_verify_cli_exit_codes_and_report(
    sessions: sessionmaker[Session],
    seeded: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "seed-manifest.json"
    seed.write_manifest(seeded, path)
    main = verify_people_applications.main
    assert main(["--manifest", str(path)], session_factory=sessions) == 0
    out = capsys.readouterr().out
    assert "PASS expected.people" in out and out.rstrip().endswith("PASSED")
    assert "legacy_candidate_map rows" in out

    wrong = json.loads(json.dumps(seeded))
    wrong["expected"]["applications"] -= 1
    seed.write_manifest(wrong, path)
    assert main(["--manifest", str(path)], session_factory=sessions) == 1
    assert capsys.readouterr().out.rstrip().endswith("FAILED")

    path.write_text("{not json", encoding="utf-8")
    assert main(["--manifest", str(path)], session_factory=sessions) == 2
    assert main([], session_factory=sessions) == 0
