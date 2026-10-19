"""E0-19: the read switch, rehearsed on the seed with real commits.

The legacy candidate endpoints now read ``applications`` and the resumes' extraction snapshots
(spec §7). These tests prove it on data the way it reaches the switch: legacy candidates seeded
by ``app.devtools.seed``, backfilled (``backfill``), the switch migration's timestamp alignment
applied (``e19b7f3a9c42``), then served by the switched API.

Like ``test_backfill.py`` this needs commits on several connections, so the module gets its own
throwaway database at ``head``; the HTTP API runs against it through an async engine of its
own (``get_db`` overridden), so every request commits for real, exactly as in production.

Covered:

- **Equivalence:** every seeded candidate's ``GET /candidates/{id}`` equals the legacy response
  built from its ``candidates`` row, byte for byte (as JSON), unless it was merged into an
  application with other candidates; then it answers with that application. The same for each
  campaign's list (all pages) and CSV export.
- **Merged legacy ids** resolve through ``legacy_candidate_map`` for ``GET``, ``PATCH``,
  ``PUT``, bulk pipeline and ``DELETE``, and the response carries the application's id.
- **Writes after the switch** go to applications only (``candidates`` is never written), a
  delete removes the application, all its resumes and (by the foreign key) their candidates,
  and ``verify_backfill`` stays green throughout.
- **A post-switch upload** writes no candidate, its application gets a fresh id and
  ``verify_backfill`` counts it as an application without a legacy candidate.
- **The list query's plan** on ~1,200 applications (a campaign with 100): index scans, printed
  with ``EXPLAIN ANALYZE`` (run with ``-s`` to see them).
"""

from __future__ import annotations

import importlib.util
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, event, func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.modules.ai.fake import FakeAIService
from app.modules.hiring import service as hiring_service
from app.modules.hiring.models import Application, Candidate, LegacyCandidateMap
from app.modules.hiring.schemas import CandidateDetail, CandidateListItem
from app.modules.ingestion import tasks, verify_backfill
from app.modules.ingestion.models import Resume
from tests import postgres
from tests.ingestion.test_backfill import (
    REGION,
    T0,
    make_campaign,
    make_legacy,
    run,
    seed_db,
)

API = "/api/v1"
ALIGN_MIGRATION = (
    postgres.BACKEND_DIR / "alembic" / "versions" / "e19b7f3a9c42_align_application_timestamps.py"
)


def _align_sql() -> str:
    spec = importlib.util.spec_from_file_location("e19b7f3a9c42", ALIGN_MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.ALIGN_TIMESTAMPS)


ALIGN_TIMESTAMPS = _align_sql()


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    with postgres.throwaway_database("e0_19", pool_size=5, max_overflow=0) as eng:
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
def verify(
    sessions: sessionmaker[Session], manifest: dict[str, Any] | None = None
) -> verify_backfill.Report:
    with sessions() as db:
        return verify_backfill.verify(db, manifest=manifest, default_region=REGION)


def align_timestamps(sessions: sessionmaker[Session]) -> int:
    """The switch migration's data step (``e19b7f3a9c42``); returns the rows it changed."""
    with sessions() as db:
        changed = db.execute(text(ALIGN_TIMESTAMPS)).rowcount
        db.commit()
    return int(changed)


def switch(sessions: sessionmaker[Session], manifest: dict[str, Any] | None = None) -> None:
    """Backfill, verify (the gate), then the switch migration's data step."""
    run(sessions)
    report = verify(sessions, manifest)
    assert report.ok, report.render()
    align_timestamps(sessions)


def legacy_rows(sessions: sessionmaker[Session]) -> list[Candidate]:
    with sessions() as db:
        rows = list(db.scalars(select(Candidate)))
        db.expunge_all()
    return rows


def groups(sessions: sessionmaker[Session]) -> dict[uuid.UUID, list[Candidate]]:
    """The legacy candidates that lead to each application."""
    with sessions() as db:
        redirect = dict(
            db.execute(select(LegacyCandidateMap.candidate_id, LegacyCandidateMap.application_id))
            .tuples()
            .all()
        )
    grouped: dict[uuid.UUID, list[Candidate]] = {}
    for c in legacy_rows(sessions):
        grouped.setdefault(redirect.get(c.id, c.id), []).append(c)
    return grouped


def legacy_detail(candidate: Candidate) -> dict[str, Any]:
    """The response the pre-switch endpoint built from a candidate row."""
    return CandidateDetail.model_validate(candidate).model_dump(mode="json")


def legacy_list_item(candidate: Candidate) -> dict[str, Any]:
    return CandidateListItem.model_validate(candidate).model_dump(mode="json")


async def all_pages(api: AsyncClient, campaign_id: uuid.UUID) -> list[dict[str, Any]]:
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


# ── Equivalence on the seed ───────────────────────────────────
async def test_every_seeded_candidate_answers_as_before_unless_it_was_merged(
    sessions: sessionmaker[Session], api: AsyncClient
) -> None:
    manifest = seed_db(sessions)
    switch(sessions, manifest)
    grouped = groups(sessions)
    merged_ids = {c.id for cs in grouped.values() if len(cs) > 1 for c in cs}
    assert merged_ids, "the seed must contain merged candidates"

    same = redirected = 0
    for app_id, candidates in grouped.items():
        for candidate in candidates:
            resp = await api.get(f"{API}/candidates/{candidate.id}")
            assert resp.status_code == 200
            if len(candidates) == 1:
                assert resp.json() == legacy_detail(candidate)
                same += 1
            else:
                # Merged: the surviving application, under its own id.
                assert resp.json()["id"] == str(app_id)
                redirected += 1
    assert same == len(grouped) - sum(1 for cs in grouped.values() if len(cs) > 1)
    assert redirected == len(merged_ids)

    # The lists and the CSV export, campaign by campaign.
    for campaign_id in {c.campaign_id for cs in grouped.values() for c in cs}:
        in_campaign = {a: cs for a, cs in grouped.items() if cs[0].campaign_id == campaign_id}
        rows = await all_pages(api, campaign_id)
        assert sorted(r["id"] for r in rows) == sorted(str(a) for a in in_campaign)
        scores = [r["score"] for r in rows]
        assert scores == sorted(scores, reverse=True)  # every seeded candidate is scored
        for row in rows:
            candidates = in_campaign[uuid.UUID(row["id"])]
            if len(candidates) == 1:
                assert row == legacy_list_item(candidates[0])

        resp = await api.get(f"{API}/campaigns/{campaign_id}/candidates/export")
        lines = resp.text.split("\r\n")
        assert lines[0] == ",".join(hiring_service.CSV_FIELDS)
        assert len(lines) == len(rows) + 2  # header, one per application, trailing ""
        exported = set(lines[1:-1])
        for candidates in in_campaign.values():
            if len(candidates) == 1:
                legacy = hiring_service.render_candidates_csv(
                    [CandidateDetail.model_validate(candidates[0])]
                )
                assert legacy.split("\r\n")[1] in exported


async def test_the_list_filters_match_the_legacy_semantics_on_the_seed(
    sessions: sessionmaker[Session], api: AsyncClient
) -> None:
    manifest = seed_db(sessions)
    switch(sessions, manifest)
    grouped = groups(sessions)
    # One row per application, as the legacy row of its current resume (merged groups too: the
    # filters read the current resume's extraction and the application's scoring).
    with sessions() as db:
        current = {a.id: a.resume_id for a in db.scalars(select(Application))}
    row_of = {
        app_id: next(c for c in cs if c.resume_id == current[app_id])
        for app_id, cs in grouped.items()
    }
    campaign_id = uuid.UUID(manifest["campaign_ids"][0])
    rows = {a: c for a, c in row_of.items() if c.campaign_id == campaign_id}
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
        ({"search": "@"}, lambda c: "@" in (c.name or "") or "@" in (c.email or "")),
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
        assert got == {str(a) for a, c in rows.items() if keep(c)}, params


# ── Merged legacy ids ─────────────────────────────────────────
async def test_a_merged_legacy_id_answers_and_writes_as_its_surviving_application(
    sessions: sessionmaker[Session], api: AsyncClient
) -> None:
    campaign = make_campaign(sessions, "Trio")
    oldest, middle, newest = make_trio(sessions, campaign)
    switch(sessions)
    app_id = str(middle.id)  # the first one backfilled keeps its id

    for candidate in (oldest, middle, newest):
        body = (await api.get(f"{API}/candidates/{candidate.id}")).json()
        assert (body["id"], body["resume_id"]) == (app_id, str(newest.resume_id))
    for candidate in (oldest, middle, newest):
        body = (await api.get(f"{API}/resumes/{candidate.resume_id}/candidate")).json()
        assert body["id"] == app_id
    rows = await all_pages(api, campaign)
    assert [r["id"] for r in rows] == [app_id]

    resp = await api.patch(f"{API}/candidates/{oldest.id}/pipeline", json={"stage": "offer"})
    assert (resp.status_code, resp.json()["id"], resp.json()["pipeline_stage"]) == (
        200,
        app_id,
        "offer",
    )
    resp = await api.patch(f"{API}/candidates/{newest.id}/notes", json={"notes": "strong"})
    assert (resp.json()["id"], resp.json()["notes"]) == (app_id, "strong")
    resp = await api.put(f"{API}/candidates/{oldest.id}/category", json={"category": "rejected"})
    assert (resp.json()["id"], resp.json()["category"]) == (app_id, "rejected")
    resp = await api.patch(
        f"{API}/campaigns/{campaign}/candidates/bulk-pipeline",
        json={"candidate_ids": [str(oldest.id), str(newest.id)], "stage": "hired"},
    )
    assert resp.json() == {"updated": 1}  # one application, named twice

    with sessions() as db:
        app = db.get_one(Application, middle.id)
        assert (app.pipeline_stage, app.notes, app.category) == ("hired", "strong", "rejected")
        # The legacy rows are frozen: nothing writes candidates any more.
        frozen = {c.id: (c.pipeline_stage, c.notes, c.category) for c in (oldest, middle, newest)}
        now = {
            c.id: (c.pipeline_stage, c.notes, c.category)
            for c in db.scalars(select(Candidate).where(Candidate.campaign_id == campaign))
        }
        assert now == frozen
    report = verify(sessions)
    assert report.ok, report.render()

    # Deleting by a merged id deletes the application, every resume of it and (through the
    # foreign key) every legacy candidate; all three ids are gone.
    assert (await api.delete(f"{API}/candidates/{oldest.id}")).status_code == 204
    for candidate in (oldest, middle, newest):
        assert (await api.get(f"{API}/candidates/{candidate.id}")).status_code == 404
    with sessions() as db:
        assert db.get(Application, middle.id) is None
        assert db.scalar(select(func.count()).select_from(LegacyCandidateMap)) == 0
        assert (
            db.scalar(
                select(func.count()).select_from(Resume).where(Resume.campaign_id == campaign)
            )
            == 0
        )
        assert (
            db.scalar(
                select(func.count()).select_from(Candidate).where(Candidate.campaign_id == campaign)
            )
            == 0
        )
    report = verify(sessions)
    assert report.ok, report.render()


async def test_bulk_delete_of_seeded_candidates_merged_ones_included_keeps_verify_green(
    sessions: sessionmaker[Session], api: AsyncClient
) -> None:
    manifest = seed_db(sessions)
    switch(sessions, manifest)
    grouped = groups(sessions)
    campaign_id = uuid.UUID(manifest["campaign_ids"][0])
    merged = [a for a, cs in grouped.items() if len(cs) > 1 and cs[0].campaign_id == campaign_id]
    single = [a for a, cs in grouped.items() if len(cs) == 1 and cs[0].campaign_id == campaign_id]
    assert merged and single
    # Name the merged applications by a legacy id that is not theirs, the others by their own.
    named = [next(c.id for c in grouped[a] if c.id != a) for a in merged] + single[:3]

    resp = await api.request(
        "DELETE",
        f"{API}/campaigns/{campaign_id}/candidates/bulk",
        json={"candidate_ids": [str(i) for i in named]},
    )

    assert resp.json() == {"deleted": len(merged) + 3}
    with sessions() as db:
        assert not db.scalars(
            select(Application.id).where(Application.id.in_(merged + single[:3]))
        ).all()
    report = verify(sessions)
    assert report.ok, report.render()


# ── After the switch ──────────────────────────────────────────
async def test_a_post_switch_upload_writes_no_candidate_and_is_counted_as_new(
    sessions: sessionmaker[Session],
    api: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    fake_ai: FakeAIService,
) -> None:
    monkeypatch.setattr(tasks, "SyncSession", sessions)
    campaign = make_campaign(sessions)
    make_legacy(sessions, campaign, uploaded_at=T0)
    switch(sessions)
    with sessions() as db:  # as the E0-19 extract step leaves it: the text on the resume
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

    # In progress: no application yet, so no row (the legacy placeholder row is gone, E0-19).
    resp = await api.get(f"{API}/resumes/{rid}/candidate")
    assert (resp.status_code, resp.json()) == (404, {"detail": "Candidate not yet processed"})
    assert len(await all_pages(api, campaign)) == 1

    result = tasks.process_candidate_ai.apply(args=(str(rid),))
    assert result.successful(), result.result

    body = (await api.get(f"{API}/resumes/{rid}/candidate")).json()
    assert (body["name"], body["email"]) == ("Meera Iyer", "meera.iyer@example.com")
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Candidate)) == 1  # the seeded one
        assert db.get(LegacyCandidateMap, uuid.UUID(body["id"])) is None
    assert len(await all_pages(api, campaign)) == 2
    report = verify(sessions)
    assert report.ok, report.render()
    assert report.counts["  without a legacy candidate (new)"] == 1


# ── The list query's plan ─────────────────────────────────────
async def test_the_list_queries_use_the_indexes(
    sessions: sessionmaker[Session], api: AsyncClient, async_engine: AsyncEngine
) -> None:
    seed_db(sessions, people=800, campaigns=15, seed_=42)
    campaign = make_campaign(sessions, "A campaign with 100 applications")
    for i in range(100):
        make_legacy(
            sessions,
            campaign,
            uploaded_at=T0 + timedelta(minutes=i),
            email=f"applicant{i}@example.com",
            name=f"Applicant {i}",
            score=float(i % 97),
        )
    switch(sessions)
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
