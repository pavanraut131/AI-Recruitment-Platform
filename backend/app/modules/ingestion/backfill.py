"""Backfill People and Applications from the legacy candidates (EPIC-00 spec §6.1 phase 2, §6.2).

Usage (inside the backend container, or from ``backend/``)::

    python -m app.modules.ingestion.backfill [--batch-size 500] [--max-batches N]
        [--checkpoint-name person_application_v1] [--reset-checkpoint]
    python -m app.modules.ingestion.backfill --resync-recruiter-fields [--batch-size 500]

Runbook: ``docs/runbooks/person-application-backfill.md``. Check the result with
``python -m app.modules.ingestion.verify_backfill``.

Why ingestion owns it: it coordinates three modules. Candidates are read and applications
written through ``app.modules.hiring.service``, People resolved through
``app.modules.talent.service``, and ingestion writes its own ``resumes``. No module boundary is
crossed (spec §6.1).

Algorithm
    Candidates are read in pages of ``--batch-size`` in ``(created_at, id)`` order with keyset
    pagination (each page starts strictly after the last row of the previous one; never
    ``OFFSET``). For every candidate whose resume is ``done``:

    1. ``resumes.extracted``: set from the candidate's stored fields if it is ``NULL`` (the same
       shape the pipeline stores, ``ExtractionResult``).
    2. ``talent.resolve_person`` with the candidate's email, phone and profile, mapped exactly as
       the pipeline maps an extraction (``tasks.person_profile``), and the resume's
       ``uploaded_at``; stored as ``resumes.person_id``. **Skipped** when ``resumes.person_id`` is
       already set (the resume went through the dual-write pipeline): the stored resolution is
       used (``talent.get_resolution``).
    3. ``hiring.upsert_application`` with the candidate's stored scoring, ``application_id`` =
       the candidate's id and ``resume_uploaded_at`` = the resume's ``uploaded_at``. The first
       candidate of a ``(campaign, person)`` pair keeps its id; the newer resume's scoring wins
       whatever the processing order.
    4. ``hiring.link_legacy_candidate``: when the application already existed under another id,
       a ``legacy_candidate_map`` row redirects the candidate's id to it.
    5. ``hiring.carry_recruiter_fields``: ``pipeline_stage`` and ``notes`` per field from the
       newest candidate of that application that has a value, and the category of the
       candidate of its current resume (``hiring.expected_recruiter_fields``).

    Candidates are skipped (logged and counted, never fatal) when their resume is missing, is not
    ``done`` (still processing, failed: the pipeline links those itself when they finish), or the
    candidate is already backfilled (``resumes.person_id`` and ``extracted`` set and the candidate
    already leads to an application). Order does not change the outcome: People's profiles and
    applications' scoring follow upload time (``uploaded_at``), not processing order.

Transactions, locks and the checkpoint
    **One transaction per candidate**, committed at once; the checkpoint
    (``backfill_checkpoints``, the ``(created_at, id)`` of the page's last candidate) is committed
    after each page. Not one transaction per page, because:

    * ``resolve_person`` holds transaction-level advisory locks (resume, email, phone) until the
      commit. A 500-candidate transaction would hold up to 1,500 of them, about a quarter of the
      shared lock table at Postgres' defaults (``max_locks_per_transaction`` 64 x
      ``max_connections`` 100), and every live upload of any of those 500 people would wait for
      the whole page.
    * Spec §5.4's deadlock-freedom rests on each transaction taking all of its advisory locks in
      one sorted call. A page making 500 calls in one transaction breaks that: it can hold a
      lock that a live upload needs while waiting for one that upload holds.

    With one ``resolve_person`` per transaction the backfill behaves exactly like one more
    pipeline worker: at most 3 advisory locks, held for milliseconds. ``tests/ingestion/
    test_backfill.py`` measures the maximum. Stopping the process at any point loses at most the
    candidate in flight; a restart resumes after the last checkpoint and re-reads at most one
    page, whose finished candidates are skipped as ``already_backfilled``.

Idempotent and resumable
    Running it again after it finished changes nothing (every candidate is
    ``already_backfilled``), and so does ``--reset-checkpoint``, which only restarts from the
    first candidate. Candidates created after the run (live uploads) are linked by the pipeline
    itself; a later run would also pick them up, as they sort after the checkpoint.

Re-sync of the recruiter fields (``--resync-recruiter-fields``, E0-18)
    Before E0-18 the legacy recruiter endpoints wrote only ``candidates``: a stage, note or
    category changed after a candidate was backfilled is not on its application, and the
    backfill does not revisit ``already_backfilled`` candidates. The re-sync recomputes the
    mirrored fields of **every** application from its candidates with the same rule
    (``hiring.carry_recruiter_fields``), in pages of ``--batch-size`` applications (keyset on
    the id), one transaction per page, and reports how many applications changed. Idempotent:
    a second run changes nothing. It needs no checkpoint: it is cheap and a re-run is the
    resume. Run it after E0-18 is deployed (from then on the API mirrors every write), right
    before ``verify_backfill``; runbook §8.

Concurrency with live uploads
    The pipeline and the backfill share ``resolve_person``'s advisory locks and the
    ``ON CONFLICT`` upsert, so a backfilled resume and a live upload of the same person are
    serialised and converge on one Person and one application per campaign. A pipeline task
    never touches a ``done`` resume, and the backfill touches nothing else.
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

import structlog
from sqlalchemy.orm import Session

from app.modules.hiring import service as hiring_service
from app.modules.ingestion import repository, tasks
from app.modules.talent import service as talent_service
from app.modules.talent.schemas import ResolveResult
from app.platform.config import settings

logger = structlog.get_logger()

DEFAULT_BATCH_SIZE = 500
DEFAULT_CHECKPOINT = "person_application_v1"

SKIP_RESUME_MISSING = "resume_missing"
SKIP_RESUME_NOT_DONE = "resume_not_done"
SKIP_ALREADY_BACKFILLED = "already_backfilled"

Position = tuple[datetime, uuid.UUID]
SessionFactory = Callable[[], Session]


@dataclass
class Outcome:
    """What backfilling one candidate did."""

    skipped: str | None = None
    person_created: bool = False
    resolution_reused: bool = False
    conflict: bool = False
    application_created: bool = False
    map_row: bool = False
    extracted_filled: bool = False
    recruiter_fields: bool = False


@dataclass
class BackfillSummary:
    started_after: Position | None = None
    checkpoint: Position | None = None
    batches: int = 0
    candidates_read: int = 0
    processed: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    people_created: int = 0
    people_matched: int = 0
    resolutions_reused: int = 0
    identity_conflicts: int = 0
    applications_created: int = 0
    applications_merged: int = 0
    map_rows: int = 0
    extracted_filled: int = 0
    recruiter_fields_carried: int = 0
    reached_end: bool = False
    duration_s: float = 0.0

    def add(self, outcome: Outcome) -> None:
        self.candidates_read += 1
        if outcome.skipped is not None:
            self.skipped[outcome.skipped] += 1
            return
        self.processed += 1
        if outcome.resolution_reused:
            self.resolutions_reused += 1
        elif outcome.person_created:
            self.people_created += 1
        else:
            self.people_matched += 1
        self.identity_conflicts += outcome.conflict
        if outcome.application_created:
            self.applications_created += 1
        else:
            self.applications_merged += 1
        self.map_rows += outcome.map_row
        self.extracted_filled += outcome.extracted_filled
        self.recruiter_fields_carried += outcome.recruiter_fields

    def render(self) -> str:
        skipped = ", ".join(f"{reason} {n}" for reason, n in sorted(self.skipped.items()))
        lines = [
            f"backfill: {'finished' if self.reached_end else 'stopped'} after "
            f"{self.batches} batch(es) in {self.duration_s:.1f}s",
            f"  started after:          {_fmt(self.started_after)}",
            f"  checkpoint:             {_fmt(self.checkpoint)}",
            f"  candidates read:        {self.candidates_read}",
            f"  processed:              {self.processed}",
            f"  skipped:                {sum(self.skipped.values())}"
            + (f" ({skipped})" if skipped else ""),
            f"  people created:         {self.people_created}",
            f"  people matched:         {self.people_matched}",
            f"  resolutions reused:     {self.resolutions_reused} (resolved by the dual write)",
            f"  identity conflicts:     {self.identity_conflicts}",
            f"  applications created:   {self.applications_created}",
            f"  applications merged:    {self.applications_merged} (candidate joined an existing one)",
            f"  legacy map rows:        {self.map_rows}",
            f"  extractions stored:     {self.extracted_filled}",
            f"  recruiter fields moved: {self.recruiter_fields_carried} application(s)",
        ]
        return "\n".join(lines)


def _fmt(position: Position | None) -> str:
    if position is None:
        return "(start)"
    return f"({position[0].isoformat()}, {position[1]})"


# ── One candidate ─────────────────────────────────────────────
def backfill_candidate(
    db: Session, candidate: hiring_service.LegacyCandidate, *, default_region: str
) -> Outcome:
    """Backfill one legacy candidate in ``db``'s transaction (the caller commits)."""
    log = logger.bind(candidate_id=str(candidate.id), resume_id=str(candidate.resume_id))
    resume = repository.get_sync(db, candidate.resume_id)
    if resume is None:
        log.warning("backfill_candidate_skipped", reason=SKIP_RESUME_MISSING)
        return Outcome(skipped=SKIP_RESUME_MISSING)
    if resume.status != "done":
        log.info("backfill_candidate_skipped", reason=SKIP_RESUME_NOT_DONE, status=resume.status)
        return Outcome(skipped=SKIP_RESUME_NOT_DONE)
    linked = candidate.id in hiring_service.legacy_links(db, [candidate.id])
    if linked and resume.person_id is not None and resume.extracted is not None:
        return Outcome(skipped=SKIP_ALREADY_BACKFILLED)

    outcome = Outcome()
    extraction = candidate.extraction()
    if resume.extracted is None:
        resume.extracted = extraction.model_dump(mode="json")
        outcome.extracted_filled = True

    resolution: ResolveResult | None
    if resume.person_id is None:
        resolution = talent_service.resolve_person(
            db,
            email=extraction.email,
            phone=extraction.phone,
            profile=tasks.person_profile(extraction),
            resume_id=resume.id,
            uploaded_at=resume.uploaded_at,
            default_region=default_region,
        )
        resume.person_id = resolution.person_id
        outcome.person_created = not resolution.matched_existing
    else:
        # Resolved by the dual-write pipeline: never resolve twice (spec §5.1 step 3).
        resolution = talent_service.get_resolution(db, resume.id) or ResolveResult(
            person_id=resume.person_id, matched_existing=True
        )
        outcome.resolution_reused = True
    outcome.conflict = resolution.conflict_id is not None and not outcome.resolution_reused

    application = hiring_service.upsert_application(
        db,
        campaign_id=candidate.campaign_id,
        person_id=resolution.person_id,
        resume_id=resume.id,
        resume_uploaded_at=resume.uploaded_at,
        scoring=candidate.scoring(),
        application_id=candidate.id,
    )
    outcome.application_created = application.id == candidate.id
    outcome.map_row = hiring_service.link_legacy_candidate(
        db, candidate_id=candidate.id, application_id=application.id
    )
    outcome.recruiter_fields = bool(carry_recruiter_fields(db, [application.id]))
    return outcome


def carry_recruiter_fields(
    db: Session, application_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    """Let hiring recompute these applications' recruiter fields from their candidates
    (``hiring.carry_recruiter_fields``), handing it the resumes' upload times (ingestion's
    data). Shared by the backfill, the re-sync and the upload pipeline."""
    return hiring_service.carry_recruiter_fields(
        db, application_ids, lambda resume_ids: repository.uploaded_at_sync(db, resume_ids)
    )


# ── The run ───────────────────────────────────────────────────
def run_backfill(
    session_factory: SessionFactory,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int | None = None,
    checkpoint_name: str = DEFAULT_CHECKPOINT,
    reset_checkpoint: bool = False,
    default_region: str | None = None,
    progress: Callable[[str], None] = print,
) -> BackfillSummary:
    """Backfill from the checkpoint until the last candidate (or ``max_batches`` pages).

    Every candidate is committed on its own, the checkpoint after every page (module docstring).
    An unexpected error stops the run and is raised; the checkpoint still points at the last
    complete page, so running again resumes there.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    region = default_region or settings.PHONE_DEFAULT_REGION
    started = time.perf_counter()
    summary = BackfillSummary()

    with session_factory() as db:
        if reset_checkpoint and repository.delete_checkpoint_sync(db, checkpoint_name):
            progress(f"backfill: checkpoint {checkpoint_name!r} reset")
        checkpoint = repository.get_checkpoint_sync(db, checkpoint_name)
        if checkpoint is not None and checkpoint.last_created_at and checkpoint.last_id:
            summary.started_after = (checkpoint.last_created_at, checkpoint.last_id)
        db.commit()
    after = summary.checkpoint = summary.started_after
    progress(f"backfill: {checkpoint_name!r} starting after {_fmt(after)}, batch size {batch_size}")

    try:
        while max_batches is None or summary.batches < max_batches:
            with session_factory() as db:
                page = hiring_service.iter_legacy_candidates_after(db, after, batch_size)
            if not page:
                summary.reached_end = True
                break
            before = (summary.processed, sum(summary.skipped.values()))
            for candidate in page:
                summary.add(_backfill_one(session_factory, candidate, region))
            after = page[-1].position
            with session_factory() as db:
                repository.save_checkpoint_sync(db, checkpoint_name, *after)
                db.commit()
            summary.checkpoint = after
            summary.batches += 1
            progress(
                f"backfill: batch {summary.batches}: {len(page)} candidates, "
                f"processed {summary.processed - before[0]}, "
                f"skipped {sum(summary.skipped.values()) - before[1]}; "
                f"checkpoint {_fmt(after)}"
            )
            if len(page) < batch_size:
                summary.reached_end = True
                break
    finally:
        summary.duration_s = time.perf_counter() - started
    return summary


def _backfill_one(
    session_factory: SessionFactory, candidate: hiring_service.LegacyCandidate, region: str
) -> Outcome:
    with session_factory() as db:
        try:
            outcome = backfill_candidate(db, candidate, default_region=region)
            db.commit()  # also releases resolve_person's advisory locks
        except Exception:
            db.rollback()
            logger.exception("backfill_candidate_failed", candidate_id=str(candidate.id))
            raise
    return outcome


# ── Re-sync of the recruiter fields (E0-18) ──────────────────
@dataclass
class ResyncSummary:
    batches: int = 0
    applications: int = 0
    changed: int = 0
    fields: Counter[str] = field(default_factory=Counter)
    duration_s: float = 0.0

    def render(self) -> str:
        fields = ", ".join(f"{name} {n}" for name, n in sorted(self.fields.items()))
        return "\n".join(
            [
                f"resync: finished after {self.batches} batch(es) in {self.duration_s:.1f}s",
                f"  applications checked:   {self.applications}",
                f"  applications changed:   {self.changed}" + (f" ({fields})" if fields else ""),
            ]
        )


def run_resync_recruiter_fields(
    session_factory: SessionFactory,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    progress: Callable[[str], None] = print,
) -> ResyncSummary:
    """Recompute the recruiter fields of every application from its candidates, one
    transaction per page of ``batch_size`` applications (module docstring). Idempotent."""
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    started = time.perf_counter()
    summary = ResyncSummary()
    after: uuid.UUID | None = None
    try:
        while True:
            with session_factory() as db:
                page = hiring_service.application_ids_after(db, after, batch_size)
                if not page:
                    break
                changed = carry_recruiter_fields(db, page)
                db.commit()
            summary.batches += 1
            summary.applications += len(page)
            summary.changed += len(changed)
            for names in changed.values():
                summary.fields.update(names)
            for application_id, names in changed.items():
                logger.info(
                    "resync_recruiter_fields_changed",
                    application_id=str(application_id),
                    fields=names,
                )
            progress(
                f"resync: batch {summary.batches}: {len(page)} applications, changed {len(changed)}"
            )
            after = page[-1]
            if len(page) < batch_size:
                break
    finally:
        summary.duration_s = time.perf_counter() - started
    return summary


# ── CLI ───────────────────────────────────────────────────────
def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def main(argv: Sequence[str] | None = None, session_factory: SessionFactory | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.modules.ingestion.backfill",
        description="Backfill People and Applications from legacy candidates (resumable).",
    )
    parser.add_argument("--batch-size", type=_positive, default=DEFAULT_BATCH_SIZE)
    parser.add_argument(
        "--max-batches", type=_positive, default=None, help="stop after this many batches"
    )
    parser.add_argument("--checkpoint-name", default=DEFAULT_CHECKPOINT)
    parser.add_argument(
        "--reset-checkpoint",
        action="store_true",
        help="start again from the first candidate (already backfilled ones are skipped)",
    )
    parser.add_argument(
        "--resync-recruiter-fields",
        action="store_true",
        help="instead of backfilling, recompute every application's pipeline_stage, notes and "
        "category from its candidates (E0-18; idempotent)",
    )
    args = parser.parse_args(argv)

    if args.resync_recruiter_fields:
        try:
            resync = run_resync_recruiter_fields(
                session_factory or tasks.SyncSession, batch_size=args.batch_size
            )
        except Exception as exc:
            print(f"resync: FAILED: {exc!r}", file=sys.stderr)
            print("resync: committed batches are kept; run again (it is idempotent)")
            return 1
        print(resync.render())
        return 0

    try:
        summary = run_backfill(
            session_factory or tasks.SyncSession,
            batch_size=args.batch_size,
            max_batches=args.max_batches,
            checkpoint_name=args.checkpoint_name,
            reset_checkpoint=args.reset_checkpoint,
        )
    except Exception as exc:
        print(f"backfill: FAILED: {exc!r}", file=sys.stderr)
        print("backfill: the checkpoint points at the last complete batch; run again to resume")
        return 1
    print(summary.render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
