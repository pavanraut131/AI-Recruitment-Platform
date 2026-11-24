"""Backfill the tenant key: every existing row joins the default agency (EPIC-02 spec §7.1 phase 2).

Usage (inside the backend container, or from ``backend/``)::

    python -m app.modules.ingestion.backfill_tenancy [--batch-size 1000] [--max-batches N]
        [--table TABLE ...] [--status]

Runbook: ``docs/runbooks/tenancy-backfill.md``. Then check the result with
``python -m app.modules.ingestion.verify_people_applications`` and apply the contract migration
(``alembic upgrade head``), which refuses to run while any row still has no agency.

What it does
    The E2-01 expand migration added a nullable ``agency_id`` to the seven tenant tables, and
    since then every insert sets it; rows written before that have ``NULL``. This command gives
    each of them the default agency (``app.platform.tenancy.DEFAULT_AGENCY_ID``), and every
    campaign without a client the default agency's client "General" (``DEFAULT_CLIENT_ID``), in
    this order: ``campaigns``, ``applications`` (hiring), ``people``, ``person_identifiers``,
    ``identity_conflicts``, ``person_resumes`` (talent), ``resumes`` (ingestion). It never
    changes a row that already has its values, nor any ``updated_at``.

Why ingestion owns it
    It is the top layer, like E0's backfill: ``identity`` is a leaf and may not write the other
    modules' tables. It writes none of them itself either: each module fills its own tables
    (``hiring.service.fill_tenancy_batch``, ``talent.service.fill_tenancy_batch``, and
    ingestion's repository for ``resumes``); this command only drives them.

Batches, transactions and locks
    Per table, in batches of ``--batch-size`` rows by keyset on the primary key: each batch is
    the next rows still missing their agency after the previous batch's last key (never
    ``OFFSET``), updated with one ``UPDATE ... WHERE id IN (...) AND agency_id IS NULL``, in **one
    transaction per batch**, committed at once. A batch takes row locks only (no table lock
    beyond the ``ROW EXCLUSIVE`` every ``UPDATE`` takes), on at most ``--batch-size`` rows of one
    table, for milliseconds; it runs with ``lock_timeout`` :data:`LOCK_TIMEOUT`, so it never
    holds those rows long while it waits for a live transaction. A batch that hits the lock
    timeout or a deadlock (a live transaction locking two rows of the same batch in the other
    order, e.g. a campaign delete cascading to applications) is rolled back and retried, up to
    :data:`MAX_ATTEMPTS` times.

Resumable and idempotent
    No checkpoint is needed: rows already filled are never selected again, so a new run (after
    Ctrl-C, a crash or ``--max-batches``) starts at the first row still missing its agency. A
    run on a finished database changes nothing and reports ``0`` rows for every table.
    ``--status`` only counts what is left.

Concurrency with live writes
    From E2-01 every insert sets ``agency_id``, so live uploads add no work; a re-scored
    application or a refreshed Person keeps its ``NULL`` until its batch fills it (the upsert
    sets the agency on insert only). Uploads matched to an existing Person during the backfill
    still find it: ``talent.resolve_person`` matches the default agency's identifiers *and*
    those without an agency until the contract.

Exit code: ``0`` when every selected table is complete (or ``--max-batches`` stopped the run
early), ``1`` when rows are still missing their agency at the end or a batch failed (run it
again; the summary says which tables), ``2`` for a bad command line.
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TextIO

import structlog
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.modules.hiring import service as hiring_service
from app.modules.ingestion import repository
from app.modules.talent import service as talent_service
from app.platform import maintenance
from app.platform.tenancy import DEFAULT_AGENCY_ID, DEFAULT_CLIENT_ID

logger = structlog.get_logger()

PROG = "backfill_tenancy"
DEFAULT_BATCH_SIZE = 1000
# How long a batch waits for a row lock before it gives up (rolls back and retries).
LOCK_TIMEOUT = "2s"
MAX_ATTEMPTS = 3
RETRY_PAUSE_S = 0.5
# deadlock_detected, lock_not_available (lock_timeout): a live transaction got in the way.
RETRYABLE_SQLSTATES = frozenset({"40P01", "55P03"})

SessionFactory = Callable[[], Session]
FillBatch = Callable[[Session, "uuid.UUID | None", int], list[uuid.UUID]]


@dataclass(frozen=True)
class Target:
    """One tenant table and the functions of its owning module that fill and count it."""

    table: str
    fill: FillBatch
    gaps: Callable[[Session], int]


def _hiring_target(table: str) -> Target:
    def fill(db: Session, after: uuid.UUID | None, limit: int) -> list[uuid.UUID]:
        return hiring_service.fill_tenancy_batch(
            db,
            table,
            agency_id=DEFAULT_AGENCY_ID,
            client_id=DEFAULT_CLIENT_ID,
            after=after,
            limit=limit,
        )

    return Target(table, fill, lambda db: hiring_service.count_tenancy_gaps(db, table))


def _talent_target(table: str) -> Target:
    def fill(db: Session, after: uuid.UUID | None, limit: int) -> list[uuid.UUID]:
        return talent_service.fill_tenancy_batch(
            db, table, agency_id=DEFAULT_AGENCY_ID, after=after, limit=limit
        )

    return Target(table, fill, lambda db: talent_service.count_tenancy_gaps(db, table))


def _resumes_target() -> Target:
    def fill(db: Session, after: uuid.UUID | None, limit: int) -> list[uuid.UUID]:
        return repository.fill_tenancy_batch_sync(
            db, agency_id=DEFAULT_AGENCY_ID, after=after, limit=limit
        )

    return Target("resumes", fill, repository.count_tenancy_gaps_sync)


def targets() -> list[Target]:
    """Every tenant table, in the order the backfill fills them."""
    return [
        *(_hiring_target(t) for t in hiring_service.TENANCY_TABLES),
        *(_talent_target(t) for t in talent_service.TENANCY_TABLES),
        _resumes_target(),
    ]


TABLES = tuple(t.table for t in targets())


@dataclass
class TableResult:
    table: str
    rows: int = 0
    batches: int = 0
    retries: int = 0
    seconds: float = 0.0
    remaining: int | None = None


@dataclass
class Summary:
    results: list[TableResult] = field(default_factory=list)
    stopped_early: bool = False  # --max-batches
    seconds: float = 0.0

    @property
    def complete(self) -> bool:
        return all(r.remaining == 0 for r in self.results)

    def render(self) -> str:
        lines = [
            f"{PROG}: {'stopped after --max-batches' if self.stopped_early else 'finished'} "
            f"in {self.seconds:.2f}s",
            f"  {'table':<20} {'rows filled':>11} {'batches':>8} {'retries':>8} "
            f"{'seconds':>8} {'still missing':>14}",
        ]
        for r in self.results:
            remaining = "-" if r.remaining is None else str(r.remaining)
            lines.append(
                f"  {r.table:<20} {r.rows:>11} {r.batches:>8} {r.retries:>8} "
                f"{r.seconds:>8.2f} {remaining:>14}"
            )
        return "\n".join(lines)


def _fill_one_batch(
    session_factory: SessionFactory,
    target: Target,
    after: uuid.UUID | None,
    batch_size: int,
    result: TableResult,
) -> list[uuid.UUID]:
    """One batch in its own transaction, retried when a live transaction got in the way."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with session_factory() as db:
                db.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
                keys = target.fill(db, after, batch_size)
                db.commit()
            return keys
        except DBAPIError as exc:
            sqlstate = getattr(exc.orig, "pgcode", None)
            if sqlstate not in RETRYABLE_SQLSTATES or attempt == MAX_ATTEMPTS:
                raise
            result.retries += 1
            logger.warning(
                "backfill_tenancy_batch_retry",
                table=target.table,
                after=str(after),
                attempt=attempt,
                sqlstate=sqlstate,
            )
            time.sleep(RETRY_PAUSE_S * attempt)
    raise AssertionError("unreachable")  # pragma: no cover


def run(
    session_factory: SessionFactory,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int | None = None,
    tables: Sequence[str] | None = None,
    out: TextIO | None = None,
) -> Summary:
    """Fill ``tables`` (default: all, in :data:`TABLES` order). ``max_batches`` caps the batches
    of the whole run. Prints one line per batch to ``out``. Raises when a batch fails for a
    reason other than a lock timeout or deadlock (after its retries); the batches committed
    before it are kept."""
    stream = out or sys.stdout
    selected = [t for t in targets() if tables is None or t.table in tables]
    summary = Summary(results=[TableResult(t.table) for t in selected])
    started = time.perf_counter()
    batches = 0
    print(
        f"{PROG}: agency {DEFAULT_AGENCY_ID}, client {DEFAULT_CLIENT_ID}, batch size {batch_size}",
        file=stream,
    )
    for target, result in zip(selected, summary.results, strict=True):
        table_started = time.perf_counter()
        after: uuid.UUID | None = None
        while True:
            if max_batches is not None and batches >= max_batches:
                summary.stopped_early = True
                break
            keys = _fill_one_batch(session_factory, target, after, batch_size, result)
            if not keys:
                break
            batches += 1
            result.batches += 1
            result.rows += len(keys)
            after = keys[-1]
            print(
                f"{PROG}: {target.table}: batch {result.batches}: {len(keys)} rows (up to {after})",
                file=stream,
            )
        result.seconds = time.perf_counter() - table_started
        if summary.stopped_early:
            break
    with session_factory() as db:
        for target, result in zip(selected, summary.results, strict=True):
            result.remaining = target.gaps(db)
    summary.seconds = time.perf_counter() - started
    return summary


def status(session_factory: SessionFactory, tables: Sequence[str] | None = None) -> dict[str, int]:
    """``table -> rows still missing their agency`` (campaigns: or their client). Read-only."""
    with session_factory() as db:
        return {t.table: t.gaps(db) for t in targets() if tables is None or t.table in tables}


# ── CLI ───────────────────────────────────────────────────────
def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def main(argv: Sequence[str] | None = None, session_factory: SessionFactory | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=f"python -m app.modules.ingestion.{PROG}",
        description="Give every row without an agency the default agency (and every campaign "
        "without a client the default client). Online, batched, resumable, idempotent.",
    )
    parser.add_argument(
        "--batch-size", type=_positive, default=DEFAULT_BATCH_SIZE, help="rows per batch"
    )
    parser.add_argument(
        "--max-batches", type=_positive, default=None, help="stop after N batches in all"
    )
    parser.add_argument(
        "--table",
        action="append",
        choices=TABLES,
        default=None,
        help="only this table (repeatable; default: every tenant table)",
    )
    parser.add_argument(
        "--status", action="store_true", help="only count the rows still missing (read-only)"
    )
    args = parser.parse_args(argv)
    factory = session_factory or maintenance.owner_session  # every agency (spec §6.2)

    if args.status:
        remaining = status(factory, args.table)
        for table, n in remaining.items():
            print(f"  {table:<20} {n:>8} still missing")
        done = not any(remaining.values())
        print(f"{PROG}: {'complete' if done else 'NOT complete'}")
        return 0 if done else 1

    try:
        summary = run(
            factory, batch_size=args.batch_size, max_batches=args.max_batches, tables=args.table
        )
    except DBAPIError as exc:
        logger.exception("backfill_tenancy_failed")
        print(f"{PROG}: a batch failed: {exc.orig!r}. Committed batches are kept; run again.")
        return 1
    print(summary.render())
    if summary.stopped_early:
        print(f"{PROG}: stopped early; run the same command again to continue")
        return 0
    if not summary.complete:
        print(
            f"{PROG}: rows are still missing their agency (written by pre-E2-01 code during the "
            "run, or a campaign of another agency without a client): run again, then see the "
            "runbook"
        )
        return 1
    print(f"{PROG}: complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
