"""E0-17: the Person/Application backfill and its verification, rehearsed on the seed data.

The backfill commits once per candidate and once per batch (the checkpoint), and the interleaving
test runs it next to a pipeline task on another connection, so these tests cannot use the
rolled-back ``sync_db``: the module gets a dedicated, throwaway database at ``head``
(``tests.postgres.throwaway_database``, like the E0-15 concurrency tests) and every test starts
from empty tables. The session database is never touched.

Covered:

- **Rehearsal:** seed (60 people, 4 campaigns) -> backfill -> ``verify_backfill`` against the
  seed manifest, exactly.
- **Idempotent:** a second run, and a run after ``--reset-checkpoint``, change nothing (row
  counts and a checksum of every table the backfill writes, timestamps included).
- **Resumable:** ``--max-batches`` then resume, and a crash in the middle of a batch then resume,
  end in the state of an uninterrupted run.
- **Mixed database:** some resumes already went through the dual-write pipeline (the E0-17
  pipeline, and the E0-16 one that did not link candidates), plus a live upload elsewhere.
- **Recruiter fields:** newest non-null per field, in any processing order.
- **Order:** profile and scoring follow upload time, not processing order.
- **Skips:** resumes not ``done`` or missing are skipped and counted, never fatal.
- **Concurrency:** a pipeline task for the same person waits on the backfill's advisory lock
  and both converge; the backfill never holds more than one resolution's locks.
- **Verification** fails, with a readable report and exit code 1, on each broken invariant.
"""

from __future__ import annotations

import itertools
import json
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from app.devtools import seed
from app.modules.ai.fake import FakeAIService
from app.modules.hiring import service as hiring_service
from app.modules.hiring.models import Application, Campaign, Candidate, LegacyCandidateMap
from app.modules.ingestion import backfill, repository, tasks, verify_backfill
from app.modules.ingestion.models import BackfillCheckpoint, Resume
from app.modules.talent import service as talent_service
from app.modules.talent.models import IdentityConflict, Person, PersonIdentifier, PersonResume
from tests import postgres

PEOPLE, CAMPAIGNS, SEED = 60, 4, 7
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
REGION = "IN"

# Every table the backfill (or the pipeline) writes, in checksum order.
WRITTEN_TABLES = (
    "people",
    "person_identifiers",
    "identity_conflicts",
    "person_resumes",
    "applications",
    "legacy_candidate_map",
    "resumes",
    "candidates",
)


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    with postgres.throwaway_database("e0_17", pool_size=10, max_overflow=0) as eng:
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


# ── Helpers ───────────────────────────────────────────────────
def seed_db(
    sessions: sessionmaker[Session],
    people: int = PEOPLE,
    campaigns: int = CAMPAIGNS,
    seed_: int = SEED,
) -> dict[str, Any]:
    with sessions() as db:
        manifest = seed.generate(db, people=people, campaigns=campaigns, seed=seed_)
        db.commit()
    return manifest


def run(sessions: sessionmaker[Session], **kwargs: Any) -> backfill.BackfillSummary:
    kwargs.setdefault("batch_size", 25)
    return backfill.run_backfill(sessions, default_region=REGION, progress=lambda _: None, **kwargs)


def verify(
    sessions: sessionmaker[Session], manifest: dict[str, Any] | None = None
) -> verify_backfill.Report:
    with sessions() as db:
        return verify_backfill.verify(db, manifest=manifest, default_region=REGION)


def manifest_results(report: verify_backfill.Report) -> dict[str, tuple[int, int]]:
    return {key: (expected, actual) for key, expected, actual in report.manifest}


def checksum(sessions: sessionmaker[Session]) -> dict[str, tuple[int, str]]:
    """Row count and md5 of every row (all columns, timestamps included) of each written table,
    and the checkpoint position (not its ``updated_at``, which a run refreshes)."""
    out: dict[str, tuple[int, str]] = {}
    with sessions() as db:
        for table in WRITTEN_TABLES:
            out[table] = (
                db.execute(
                    text(
                        f"SELECT count(*), md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) "
                        f"FROM {table} t"
                    )
                )
                .one()
                ._tuple()
            )
        out["backfill_checkpoints"] = (
            db.execute(
                text(
                    "SELECT count(*), coalesce(string_agg(name || last_created_at || last_id, '|'), '') "
                    "FROM backfill_checkpoints"
                )
            )
            .one()
            ._tuple()
        )
    return out


def canonical(sessions: sessionmaker[Session], *, with_ids: bool = True) -> dict[str, Any]:
    """Everything the backfill writes, independent of the random ids of new People: a Person is
    named by its sorted identifiers, or (no identifiers) by its sorted resume ids.
    ``with_ids=False`` also drops what depends on processing order by design: which candidate
    id an application keeps and the map rows."""
    with sessions() as db:
        idents: dict[uuid.UUID, list[str]] = {}
        for pid, kind, value in db.execute(
            select(
                PersonIdentifier.person_id, PersonIdentifier.kind, PersonIdentifier.value_normalized
            )
        ):
            idents.setdefault(pid, []).append(f"{kind}:{value}")
        resumes_of: dict[uuid.UUID, list[str]] = {}
        for rid, pid in db.execute(select(PersonResume.resume_id, PersonResume.person_id)):
            resumes_of.setdefault(pid, []).append(str(rid))

        def key(pid: uuid.UUID | None) -> str:
            if pid is None:
                return "-"
            return "|".join(sorted(idents.get(pid) or ["resumes"] + resumes_of.get(pid, [])))

        people = sorted(
            (
                key(p.id),
                p.full_name,
                json.dumps(p.skills),
                p.years_of_experience,
                str(p.latest_resume_id),
                p.github_url,
            )
            for p in db.scalars(select(Person))
        )
        apps = sorted(
            (
                str(a.id) if with_ids else "",
                str(a.campaign_id),
                key(a.person_id),
                str(a.resume_id),
                a.resume_uploaded_at.isoformat() if a.resume_uploaded_at else None,
                a.score,
                a.category,
                a.summary,
                a.pipeline_stage,
                a.notes,
            )
            for a in db.scalars(select(Application))
        )
        conflicts = sorted(
            (str(c.resume_id), key(c.chosen_person_id), key(c.conflicting_person_id))
            for c in db.scalars(select(IdentityConflict))
        )
        resumes = sorted(
            (str(r.id), key(r.person_id), json.dumps(r.extracted, sort_keys=True))
            for r in db.scalars(select(Resume))
        )
        legacy_map = sorted(
            (str(m.candidate_id), str(m.application_id))
            for m in db.scalars(select(LegacyCandidateMap))
        )
    state: dict[str, Any] = {
        "people": people,
        "applications": apps,
        "conflicts": conflicts,
        "resumes": resumes,
    }
    if with_ids:
        state["legacy_map"] = legacy_map
    return state


def forget_backfill(sessions: sessionmaker[Session]) -> None:
    """Back to the legacy data only (as right after seeding)."""
    with sessions() as db:
        db.execute(
            text(
                "TRUNCATE people, person_identifiers, identity_conflicts, person_resumes, "
                "applications, legacy_candidate_map, backfill_checkpoints CASCADE"
            )
        )
        db.execute(update(Resume).values(person_id=None, extracted=None))
        db.commit()


def legacy_candidates(sessions: sessionmaker[Session]) -> list[hiring_service.LegacyCandidate]:
    with sessions() as db:
        return hiring_service.iter_legacy_candidates_after(db, None, 100_000)


def make_campaign(sessions: sessionmaker[Session], title: str = "Backend") -> uuid.UUID:
    with sessions() as db:
        campaign = Campaign(title=title, role="Backend Engineer", required_skills=["Python"])
        db.add(campaign)
        db.commit()
        return campaign.id


def make_legacy(
    sessions: sessionmaker[Session],
    campaign_id: uuid.UUID,
    *,
    uploaded_at: datetime,
    created_at: datetime | None = None,
    email: str | None = "asha.rao@example.com",
    phone: str | None = None,
    name: str = "Asha Rao",
    score: float = 50.0,
    category: str = "moderate_match",
    skills: list[str] | None = None,
    pipeline_stage: str | None = None,
    notes: str | None = None,
    status: str = "done",
) -> Candidate:
    """A legacy candidate and its resume, as the pre-split pipeline left them."""
    with sessions() as db:
        resume = Resume(
            id=uuid.uuid4(),
            campaign_id=campaign_id,
            original_filename="cv.pdf",
            storage_path=f"campaigns/{campaign_id}/{uuid.uuid4()}/cv.pdf",
            file_size_bytes=1000,
            status=status,
            uploaded_at=uploaded_at,
        )
        db.add(resume)
        db.flush()
        candidate = Candidate(
            id=uuid.uuid4(),
            resume_id=resume.id,
            campaign_id=campaign_id,
            name=name,
            email=email,
            phone=phone,
            skills=skills or ["Python"],
            education=[],
            work_experience=[],
            certifications=[],
            projects=[],
            years_of_experience=3.0,
            summary=f"summary {score}",
            strengths=[],
            weaknesses=[],
            missing_skills=[],
            recommendation="Phone screen first",
            score=score,
            score_breakdown={"skills_match": score},
            category=category,
            pipeline_stage=pipeline_stage,
            notes=notes,
            raw_text=f"{name}\n{email or ''}",
            created_at=created_at or uploaded_at + timedelta(minutes=1),
        )
        db.add(candidate)
        db.commit()
        db.refresh(candidate)
        db.expunge(candidate)
        return candidate


def dual_write(
    db: Session, candidate: hiring_service.LegacyCandidate, *, link: bool, carry: bool = False
) -> hiring_service.ApplicationRef:
    """What the pipeline's resolve + save steps write for an analysed resume, using the
    candidate's stored extraction and scoring: ``link=True`` is the E0-17 pipeline
    (application id = candidate id, map row when merged), ``link=False`` the E0-16 one, and
    ``carry=True`` adds what the E0-18 pipeline does after linking (recompute the recruiter
    fields from the candidates)."""
    resume = db.get_one(Resume, candidate.resume_id)
    extraction = candidate.extraction()
    resume.extracted = extraction.model_dump(mode="json")
    result = talent_service.resolve_person(
        db,
        email=extraction.email,
        phone=extraction.phone,
        profile=tasks.person_profile(extraction),
        resume_id=resume.id,
        uploaded_at=resume.uploaded_at,
        default_region=REGION,
    )
    resume.person_id = result.person_id
    app = hiring_service.upsert_application(
        db,
        campaign_id=candidate.campaign_id,
        person_id=result.person_id,
        resume_id=resume.id,
        resume_uploaded_at=resume.uploaded_at,
        scoring=candidate.scoring(),
        application_id=candidate.id if link else None,
    )
    if link:
        hiring_service.link_legacy_candidate(db, candidate_id=candidate.id, application_id=app.id)
    if carry:
        backfill.carry_recruiter_fields(db, [app.id])
    db.commit()
    return hiring_service.ApplicationRef(app.id, app.campaign_id, app.person_id, app.resume_id)


# ── Rehearsal ─────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("people", "campaigns", "seed_", "batch_size"),
    [
        (PEOPLE, CAMPAIGNS, SEED, 25),
        # The Compose stack's seed (make seed): 808 / 1,101 / 4 / 24, in batches of 500.
        (800, 15, 42, backfill.DEFAULT_BATCH_SIZE),
    ],
    ids=["small", "compose_size"],
)
def test_rehearsal_on_the_seed_matches_the_manifest_exactly(
    sessions: sessionmaker[Session], people: int, campaigns: int, seed_: int, batch_size: int
) -> None:
    manifest = seed_db(sessions, people, campaigns, seed_)
    expected, counts = manifest["expected"], manifest["counts"]

    summary = run(sessions, batch_size=batch_size)

    report = verify(sessions, manifest)
    assert report.ok, report.render()
    assert manifest_results(report) == {
        "expected.people": (expected["people"], expected["people"]),
        "expected.applications": (expected["applications"], expected["applications"]),
        "expected.identity_conflicts": (
            expected["identity_conflicts"],
            expected["identity_conflicts"],
        ),
        "expected.legacy_candidate_map_rows": (
            expected["legacy_candidate_map_rows"],
            expected["legacy_candidate_map_rows"],
        ),
        "counts.legacy_candidates": (counts["legacy_candidates"], counts["legacy_candidates"]),
    }
    # The run's own summary tells the same story.
    n = counts["legacy_candidates"]
    assert summary.reached_end
    assert summary.batches == -(-n // batch_size)
    assert (summary.candidates_read, summary.processed, sum(summary.skipped.values())) == (n, n, 0)
    assert summary.people_created == expected["people"]
    assert summary.applications_created == expected["applications"]
    assert summary.applications_merged == summary.map_rows == expected["legacy_candidate_map_rows"]
    assert summary.identity_conflicts == expected["identity_conflicts"]
    assert summary.extracted_filled == n
    assert summary.resolutions_reused == 0
    # No phone was stored as inferred: every seeded phone is match-eligible (option A).
    with sessions() as db:
        assert db.scalar(select(func.count()).where(PersonIdentifier.inferred)) == 0


def test_the_same_seed_at_the_documented_size_gives_the_documented_numbers() -> None:
    """The numbers the rehearsal on the Compose stack must reproduce (sprint 3, E0-17)."""
    manifest = seed.build_manifest(seed.build_plan(800, 15, 42))
    assert manifest["counts"]["legacy_candidates"] == 1125
    assert manifest["expected"] == {
        "people": 808,
        "applications": 1101,
        "identity_conflicts": 4,
        "legacy_candidate_map_rows": 24,
    }


# ── Idempotent ────────────────────────────────────────────────
def test_a_second_run_and_a_reset_checkpoint_run_change_nothing(
    sessions: sessionmaker[Session],
) -> None:
    manifest = seed_db(sessions)
    run(sessions)
    before = checksum(sessions)

    again = run(sessions)
    assert (again.candidates_read, again.processed, again.batches) == (0, 0, 0)
    assert again.reached_end
    assert checksum(sessions) == before

    restarted = run(sessions, reset_checkpoint=True)
    n = manifest["counts"]["legacy_candidates"]
    assert restarted.candidates_read == n
    assert restarted.processed == 0
    assert restarted.skipped == {backfill.SKIP_ALREADY_BACKFILLED: n}
    assert checksum(sessions) == before
    assert verify(sessions, manifest).ok


# ── Resumable ─────────────────────────────────────────────────
def test_stopping_after_some_batches_then_resuming_gives_the_same_result(
    sessions: sessionmaker[Session],
) -> None:
    manifest = seed_db(sessions)
    run(sessions, batch_size=20)
    reference = canonical(sessions)
    forget_backfill(sessions)

    first = run(sessions, batch_size=20, max_batches=2)
    assert (first.batches, first.processed, first.reached_end) == (2, 40, False)
    with sessions() as db:
        checkpoint = db.get_one(BackfillCheckpoint, backfill.DEFAULT_CHECKPOINT)
        fortieth = legacy_candidates(sessions)[39]
        assert (checkpoint.last_created_at, checkpoint.last_id) == fortieth.position

    rest = run(sessions, batch_size=20)
    assert rest.started_after == fortieth.position
    assert rest.processed == manifest["counts"]["legacy_candidates"] - 40
    assert canonical(sessions) == reference
    assert verify(sessions, manifest).ok


class Crash(BaseException):
    """Stands in for the process being killed (not an ``Exception``: nothing may catch it)."""


def test_a_crash_in_the_middle_of_a_batch_loses_nothing_on_resume(
    sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = seed_db(sessions)
    run(sessions, batch_size=20)
    reference = canonical(sessions)
    forget_backfill(sessions)

    real = backfill.backfill_candidate
    calls = itertools.count(1)

    def crash_on_the_50th(*args: Any, **kwargs: Any) -> backfill.Outcome:
        if next(calls) == 50:  # the 10th candidate of the 3rd batch, mid-transaction
            raise Crash()
        return real(*args, **kwargs)

    monkeypatch.setattr(backfill, "backfill_candidate", crash_on_the_50th)
    with pytest.raises(Crash):
        run(sessions, batch_size=20)
    monkeypatch.setattr(backfill, "backfill_candidate", real)

    # 49 candidates committed, the checkpoint at the end of batch 2.
    with sessions() as db:
        assert db.scalar(select(func.count()).where(Resume.person_id.is_not(None))) == 49
        checkpoint = db.get_one(BackfillCheckpoint, backfill.DEFAULT_CHECKPOINT)
        assert (checkpoint.last_created_at, checkpoint.last_id) == legacy_candidates(sessions)[
            39
        ].position

    resumed = run(sessions, batch_size=20)
    assert resumed.skipped == {backfill.SKIP_ALREADY_BACKFILLED: 9}  # batch 3's committed ones
    assert canonical(sessions) == reference
    report = verify(sessions, manifest)
    assert report.ok, report.render()


# ── Mixed database: part of it already went through the dual-write pipeline ──
@pytest.mark.parametrize("pipeline", ["e0_18_carrying", "e0_17_linking", "e0_16_unlinked"])
def test_resumes_already_processed_by_the_dual_write_are_reused(
    sessions: sessionmaker[Session], pipeline: str
) -> None:
    manifest = seed_db(sessions)
    expected = manifest["expected"]
    candidates = legacy_candidates(sessions)
    # Every 3rd candidate, but not the late email/phone conflict resumes (emitted last): the
    # pipeline processes resumes in upload order, and so does this.
    conflicts = expected["identity_conflicts"]
    processed = candidates[: len(candidates) - conflicts : 3]
    with sessions() as db:
        linking = pipeline in ("e0_18_carrying", "e0_17_linking")
        written = [
            dual_write(db, c, link=linking, carry=pipeline == "e0_18_carrying") for c in processed
        ]

    summary = run(sessions)

    if linking:  # linked by the pipeline: nothing left to do
        assert summary.skipped == {backfill.SKIP_ALREADY_BACKFILLED: len(processed)}
        assert summary.processed == len(candidates) - len(processed)
        assert summary.resolutions_reused == 0
    else:  # resolved, but not linked: resolution reused, application linked now
        assert summary.processed == len(candidates)
        assert summary.resolutions_reused == len(processed)
    report = verify(sessions, manifest)
    if pipeline == "e0_17_linking":
        # The E0-17 pipeline did not carry the (seeded) recruiter fields of the candidates it
        # linked, and the backfill skips them as already_backfilled: exactly what the E0-18
        # re-sync is for.
        assert {c.name for c in report.checks if not c.ok} == {"recruiter_fields_consistent"}
        assert backfill.run_resync_recruiter_fields(sessions, progress=lambda _: None).changed
        report = verify(sessions, manifest)
    results = manifest_results(report)
    assert all(check.ok for check in report.checks), report.render()
    for key in ("people", "applications", "identity_conflicts"):
        assert results[f"expected.{key}"] == (expected[key], expected[key]), key
    if linking:
        assert report.ok, report.render()
    else:
        # The E0-16 pipeline gave new applications random ids, so each of their candidates
        # needs a map row as well: that many more rows than the seed's merges.
        unlinked = {app.id for app in written} - {c.id for c in candidates}
        assert results["expected.legacy_candidate_map_rows"] == (
            expected["legacy_candidate_map_rows"],
            expected["legacy_candidate_map_rows"] + len(unlinked),
        )


def test_a_live_upload_elsewhere_is_linked_by_the_pipeline_and_left_out_of_the_manifest_scope(
    sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch, fake_ai: FakeAIService
) -> None:
    manifest = seed_db(sessions)
    monkeypatch.setattr(tasks, "SyncSession", sessions)
    # An upload, to a campaign of its own, by a seeded person (same email): the real pipeline.
    seeded = next(c for c in legacy_candidates(sessions) if c.email)
    live = make_campaign(sessions, "Live uploads")
    upload = make_legacy(
        sessions,
        live,
        uploaded_at=datetime.now(UTC),
        email=seeded.email,
        name="Live Upload",
        status="processing",
    )
    with sessions() as db:
        db.execute(update(Candidate).where(Candidate.id == upload.id).values(score=None))
        db.commit()
    result = tasks.process_candidate_ai.apply(args=(str(upload.resume_id),))
    assert result.successful(), result.result
    with sessions() as db:
        assert db.get(Application, upload.id) is not None  # the pipeline reused the id

    summary = run(sessions)

    assert summary.skipped == {backfill.SKIP_ALREADY_BACKFILLED: 1}
    report = verify(sessions, manifest)
    assert report.ok, report.render()  # scoped to the seeded campaigns
    assert report.counts["applications"] == manifest["expected"]["applications"] + 1
    # Without the scope, the extra application is counted.
    unscoped = {k: v for k, v in manifest.items() if k != "campaign_ids"}
    assert not verify(sessions, unscoped).ok


# ── Recruiter fields ──────────────────────────────────────────
STAGES = ("screened", None, None)  # oldest .. newest resume
NOTES = ("first note", "second note", None)


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_recruiter_fields_come_from_the_newest_candidate_that_has_them(
    sessions: sessionmaker[Session], order: tuple[int, ...]
) -> None:
    """Three resumes of one person to one campaign, backfilled in every possible order."""
    campaign = make_campaign(sessions)
    made = [
        make_legacy(
            sessions,
            campaign,
            uploaded_at=T0 + timedelta(days=i),
            # The backfill's order is created_at: make it the permutation, not the upload order.
            created_at=T0 + timedelta(days=10 + order.index(i)),
            score=float(10 * (i + 1)),
            # The newest candidate's category was changed by hand: it travels with its scoring.
            category="rejected" if i == 2 else "moderate_match",
            pipeline_stage=STAGES[i],
            notes=NOTES[i],
        )
        for i in range(3)
    ]

    run(sessions)

    with sessions() as db:
        app = db.execute(select(Application)).scalar_one()
        first_processed = made[order[0]]
        assert app.id == first_processed.id
        assert (app.pipeline_stage, app.notes) == ("screened", "second note")
        assert (app.resume_id, app.score, app.category) == (made[2].resume_id, 30.0, "rejected")
        mapped = dict(
            db.execute(select(LegacyCandidateMap.candidate_id, LegacyCandidateMap.application_id))
            .tuples()
            .all()
        )
    assert mapped == {c.id: app.id for c in made if c.id != app.id}
    assert verify(sessions).ok


def test_recruiter_fields_of_a_single_candidate_are_copied_and_never_cleared(
    sessions: sessionmaker[Session],
) -> None:
    campaign = make_campaign(sessions)
    with_stage = make_legacy(sessions, campaign, uploaded_at=T0, pipeline_stage="offer", notes="n")
    without = make_legacy(
        sessions, campaign, uploaded_at=T0, email="ravi@example.com", name="Ravi Kumar"
    )

    run(sessions)

    with sessions() as db:
        assert (
            db.get_one(Application, with_stage.id).pipeline_stage,
            db.get_one(Application, with_stage.id).notes,
        ) == ("offer", "n")
        assert (
            db.get_one(Application, without.id).pipeline_stage,
            db.get_one(Application, without.id).notes,
        ) == (None, None)


# ── Processing order ──────────────────────────────────────────
def test_profile_and_scoring_follow_upload_time_not_processing_order(
    sessions: sessionmaker[Session],
) -> None:
    """One person, four resumes (two campaigns, one of them twice), backfilled oldest-first and
    newest-first: the same Person profile and the same applications' scoring. (Which candidate
    id an application keeps follows processing order, by design.)"""
    backend, platform = make_campaign(sessions, "Backend"), make_campaign(sessions, "Platform")

    def make_all(created: Callable[[int], datetime]) -> None:
        for i, campaign in enumerate((backend, platform, backend, platform)):
            make_legacy(
                sessions,
                campaign,
                uploaded_at=T0 + timedelta(days=i),
                created_at=created(i),
                phone="+91 98765 43210",
                score=float(10 + i),
                skills=[f"Skill{i}"],
                name=f"Asha Rao V{i}",
            )

    make_all(lambda i: T0 + timedelta(days=10 + i))
    run(sessions)
    oldest_first = canonical(sessions, with_ids=False)

    for table in ("applications", "candidates", "resumes"):
        with sessions() as db:
            db.execute(text(f"TRUNCATE {table} CASCADE"))
            db.commit()
    forget_backfill(sessions)
    make_all(lambda i: T0 + timedelta(days=20 - i))  # newest upload processed first
    run(sessions)
    newest_first = canonical(sessions, with_ids=False)

    # Resume ids differ between the two databases' rows: compare without them.
    def strip(state: dict[str, Any]) -> Any:
        return (
            [p[1:4] for p in state["people"]],
            sorted((a[1], a[4], a[5], a[6]) for a in state["applications"]),
        )

    assert strip(oldest_first) == strip(newest_first)
    assert [p[1] for p in newest_first["people"]] == ["Asha Rao V3"]
    assert sorted(a[5] for a in newest_first["applications"]) == [12.0, 13.0]


# ── Skips ─────────────────────────────────────────────────────
def test_candidates_whose_resume_is_not_done_are_skipped_and_counted(
    sessions: sessionmaker[Session],
) -> None:
    manifest = seed_db(sessions)
    candidates = legacy_candidates(sessions)
    not_done = {candidates[3].resume_id: "failed", candidates[30].resume_id: "processing"}
    with sessions() as db:
        for rid, status in not_done.items():
            db.execute(update(Resume).where(Resume.id == rid).values(status=status))
        db.commit()

    summary = run(sessions)

    assert summary.skipped == {backfill.SKIP_RESUME_NOT_DONE: 2}
    assert summary.processed == len(candidates) - 2
    report = verify(sessions)
    assert report.ok, report.render()
    assert report.counts["  with a resume not done (skipped)"] == 2

    # Once they are done, a restart from the beginning picks up exactly those two.
    with sessions() as db:
        db.execute(update(Resume).where(Resume.id.in_(not_done)).values(status="done"))
        db.commit()
    again = run(sessions, reset_checkpoint=True)
    assert again.processed == 2
    assert again.skipped == {backfill.SKIP_ALREADY_BACKFILLED: len(candidates) - 2}
    assert verify(sessions, manifest).ok


def test_a_candidate_whose_resume_is_missing_is_skipped(
    sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    # candidates.resume_id has a foreign key until phase 5, so simulate the missing row.
    campaign = make_campaign(sessions)
    gone = make_legacy(sessions, campaign, uploaded_at=T0)
    kept = make_legacy(sessions, campaign, uploaded_at=T0, email="ravi@example.com")
    real_get = repository.get_sync
    monkeypatch.setattr(
        repository,
        "get_sync",
        lambda db, rid: None if rid == gone.resume_id else real_get(db, rid),
    )

    summary = run(sessions)

    assert summary.skipped == {backfill.SKIP_RESUME_MISSING: 1}
    with sessions() as db:
        assert db.get(Application, kept.id) is not None
        assert db.get(Application, gone.id) is None


# ── Concurrency with live uploads ─────────────────────────────
def test_a_pipeline_task_for_the_same_person_waits_for_the_backfill_and_both_converge(
    sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch, fake_ai: FakeAIService
) -> None:
    """The backfill holds Asha's advisory locks (resolved, not yet committed) while the pipeline
    analyses Asha's newer resume to the same campaign: the pipeline must wait for the commit,
    then attach to the same Person and take over the application's scoring (newer resume)."""
    monkeypatch.setattr(tasks, "SyncSession", sessions)
    campaign = make_campaign(sessions)
    legacy = make_legacy(sessions, campaign, uploaded_at=T0, pipeline_stage="phone_call")
    upload = make_legacy(
        sessions,
        campaign,
        uploaded_at=T0 + timedelta(days=1),
        created_at=datetime.now(UTC),
        status="processing",
        name="Asha Rao",
    )
    with sessions() as db:  # the live upload is not analysed yet: only its text exists
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

    holding, release = threading.Event(), threading.Event()
    real_resolve = talent_service.resolve_person

    def resolve_then_hold(*args: Any, **kwargs: Any) -> Any:
        result = real_resolve(*args, **kwargs)
        if kwargs["resume_id"] == legacy.resume_id:  # the backfill's call, locks now held
            holding.set()
            assert release.wait(timeout=30)
        return result

    monkeypatch.setattr(talent_service, "resolve_person", resolve_then_hold)
    errors: list[BaseException] = []

    def in_thread(target: Callable[[], Any]) -> threading.Thread:
        def body() -> None:
            try:
                target()
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=body)
        thread.start()
        return thread

    backfill_thread = in_thread(lambda: run(sessions))
    assert holding.wait(timeout=30)
    pipeline_thread = in_thread(
        lambda: tasks.process_candidate_ai.apply(args=(str(upload.resume_id),)).get()
    )
    # The pipeline blocks on the advisory lock the backfill holds.
    deadline = time.monotonic() + 10
    with sessions() as db:
        while time.monotonic() < deadline:
            waiting = db.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE wait_event_type = 'Lock' AND wait_event = 'advisory'"
                )
            )
            if waiting:
                break
            db.rollback()  # pg_stat_activity is a per-transaction snapshot
            time.sleep(0.05)
    assert waiting == 1
    assert pipeline_thread.is_alive()
    release.set()
    backfill_thread.join(timeout=30)
    pipeline_thread.join(timeout=30)
    assert not errors, errors

    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Person)) == 1
        app = db.execute(select(Application)).scalar_one()
        # The backfill created the application with the legacy id; the newer upload then
        # replaced the scoring and was redirected to it. The recruiter's stage survived.
        assert app.id == legacy.id
        assert app.resume_id == upload.resume_id
        assert app.pipeline_stage == "phone_call"
        assert db.get_one(LegacyCandidateMap, upload.id).application_id == legacy.id
        assert db.get_one(Resume, upload.resume_id).status == "done"
    report = verify(sessions)
    assert report.ok, report.render()
    before = checksum(sessions)
    run(sessions, reset_checkpoint=True)
    assert checksum(sessions) == before


def test_each_transaction_holds_at_most_one_resolutions_advisory_locks(
    sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The measurement behind one transaction per candidate (module docstring of
    ``backfill``): per candidate at most 3 advisory locks (resume, email, phone); one
    transaction per 500-candidate page would have held them all at once."""
    seed_db(sessions)
    held: list[int] = []
    real_resolve = talent_service.resolve_person

    def resolve_and_count(db: Session, **kwargs: Any) -> Any:
        result = real_resolve(db, **kwargs)
        held.append(
            db.scalar(
                text(
                    "SELECT count(*) FROM pg_locks "
                    "WHERE locktype = 'advisory' AND pid = pg_backend_pid()"
                )
            )
            or 0
        )
        return result

    monkeypatch.setattr(talent_service, "resolve_person", resolve_and_count)
    run(sessions, batch_size=backfill.DEFAULT_BATCH_SIZE)
    assert held and max(held) <= 3

    # The alternative, one transaction per page, measured on the same data.
    forget_backfill(sessions)
    held.clear()
    with sessions() as db:
        for candidate in hiring_service.iter_legacy_candidates_after(db, None, 500):
            backfill.backfill_candidate(db, candidate, default_region=REGION)
        db.rollback()
    # Measured: 193 advisory locks for this seed's 86 candidates, held to the page's end.
    assert max(held) > 100


# ── Verification ──────────────────────────────────────────────
@pytest.fixture
def backfilled(sessions: sessionmaker[Session]) -> dict[str, Any]:
    manifest = seed_db(sessions)
    run(sessions)
    assert verify(sessions, manifest).ok
    return manifest


def failed_checks(report: verify_backfill.Report) -> set[str]:
    return {check.name for check in report.checks if not check.ok}


def test_verify_fails_when_a_candidate_leads_nowhere(
    sessions: sessionmaker[Session], backfilled: dict[str, Any]
) -> None:
    with sessions() as db:
        db.execute(
            text(
                "DELETE FROM legacy_candidate_map WHERE ctid IN (SELECT ctid FROM legacy_candidate_map LIMIT 1)"
            )
        )
        db.commit()
    report = verify(sessions, backfilled)
    assert failed_checks(report) == {"candidates_lead_to_one_application"}
    assert "leads to no application" in report.render()
    assert not report.ok


def test_verify_fails_when_a_done_resume_has_no_person(
    sessions: sessionmaker[Session], backfilled: dict[str, Any]
) -> None:
    with sessions() as db:
        rid = db.scalars(select(Resume.id).limit(1)).one()
        db.execute(update(Resume).where(Resume.id == rid).values(person_id=None))
        db.commit()
    failed = failed_checks(verify(sessions))
    assert "done_resumes_have_people" in failed


def test_verify_fails_on_an_application_without_its_person(
    sessions: sessionmaker[Session], backfilled: dict[str, Any]
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


def test_verify_fails_on_a_person_whose_usable_identifiers_are_missing(
    sessions: sessionmaker[Session], backfilled: dict[str, Any]
) -> None:
    with sessions() as db:
        pid = db.scalars(select(PersonIdentifier.person_id).limit(1)).one()
        db.execute(text("DELETE FROM person_identifiers WHERE person_id = :p"), {"p": pid})
        db.commit()
    report = verify(sessions)
    assert failed_checks(report) == {"people_without_identifiers_justified"}
    assert "has a usable one" in report.render()


def test_people_without_identifiers_are_fine_when_their_resumes_had_none_usable(
    sessions: sessionmaker[Session],
) -> None:
    """No contact details, or only a region-inferred landline (option A) that another Person
    already holds: no identifier is stored, and that is correct."""
    campaign = make_campaign(sessions)
    make_legacy(sessions, campaign, uploaded_at=T0, email=None, phone=None)
    make_legacy(sessions, campaign, uploaded_at=T0, email="a@example.com", phone="(415) 555-2671")
    make_legacy(
        sessions,
        campaign,
        uploaded_at=T0 + timedelta(hours=1),
        email=None,
        phone="(415) 555-2671",
        name="Other Person",
    )

    run(sessions)

    report = verify(sessions)
    assert report.ok, report.render()
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Person)) == 3
        assert len(talent_service.people_without_identifiers(db)) == 2


def test_verify_fails_on_a_manifest_mismatch(
    sessions: sessionmaker[Session], backfilled: dict[str, Any]
) -> None:
    wrong = json.loads(json.dumps(backfilled))
    wrong["expected"]["people"] += 1
    report = verify(sessions, wrong)
    assert all(check.ok for check in report.checks)
    assert not report.ok
    assert "FAIL expected.people" in report.render()


def test_verify_cli_exit_codes_and_report(
    sessions: sessionmaker[Session],
    backfilled: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "seed-manifest.json"
    seed.write_manifest(backfilled, path)
    assert verify_backfill.main(["--manifest", str(path)], session_factory=sessions) == 0
    out = capsys.readouterr().out
    assert "PASS expected.people" in out and out.rstrip().endswith("PASSED")

    wrong = json.loads(json.dumps(backfilled))
    wrong["expected"]["applications"] -= 1
    seed.write_manifest(wrong, path)
    assert verify_backfill.main(["--manifest", str(path)], session_factory=sessions) == 1
    assert capsys.readouterr().out.rstrip().endswith("FAILED")

    path.write_text("{not json", encoding="utf-8")
    assert verify_backfill.main(["--manifest", str(path)], session_factory=sessions) == 2


def test_backfill_cli_runs_resumes_and_reports(
    sessions: sessionmaker[Session], capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = seed_db(sessions)
    n = manifest["counts"]["legacy_candidates"]

    assert (
        backfill.main(["--batch-size", "30", "--max-batches", "1"], session_factory=sessions) == 0
    )
    out = capsys.readouterr().out
    assert "batch 1: 30 candidates, processed 30, skipped 0" in out
    assert "backfill: stopped after 1 batch(es)" in out

    assert backfill.main(["--batch-size", "30"], session_factory=sessions) == 0
    out = capsys.readouterr().out
    assert "backfill: finished" in out
    assert f"processed:              {n - 30}" in out
    assert verify(sessions, manifest).ok

    with pytest.raises(SystemExit):
        backfill.main(["--batch-size", "0"], session_factory=sessions)
