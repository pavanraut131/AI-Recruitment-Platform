"""Find (and optionally delete) stored resume PDFs that no resume row refers to any more.

Usage (from ``backend/``, or inside the backend container)::

    python -m app.devtools.storage_orphans [--delete] [--min-age-minutes 10]

Why: the delete endpoints (campaign, candidate, bulk candidate) delete the resume rows in one
transaction and the stored PDFs only after it commits (``app.api``, E0-20). If a storage call
fails at that point the request still succeeds and the error is logged
(``stored_file_delete_failed``); the PDF stays behind as an **orphan**: an object under
``campaigns/`` with no ``resumes.storage_path`` pointing at it. Deletes before E0-20 left every
PDF behind (bug B6), so an older stack has many.

What it does
    Lists every object under ``campaigns/`` and every resume's ``storage_path``, and prints:

    * **orphans**: objects no resume refers to (each key, then the count). Objects modified less
      than ``--min-age-minutes`` ago are skipped: an upload stores the PDF before it commits its
      row, so a very young object may simply be an upload in flight.
    * **missing**: resumes whose PDF is not in storage (only the count). Expected for seeded
      data (``app.devtools.seed`` stores no files); on real data a sign of a lost object.

    With ``--delete`` it deletes the orphans it found.

Exit code ``0`` when there are no orphans (or ``--delete`` removed them all), ``1`` when orphans
remain, ``2`` when storage or the database cannot be read. Refuses ``--delete`` when
``APP_ENV=production``.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.ingestion import service as ingestion_service
from app.platform import storage
from app.platform.config import settings

PROG = "storage_orphans"


@dataclass(frozen=True)
class Findings:
    orphans: list[str]
    missing: int
    too_young: int
    objects: int
    resumes: int


def find(
    objects: dict[str, datetime], paths: set[str], *, now: datetime, min_age: timedelta
) -> Findings:
    """Compare the stored objects (key -> last modified) with the resumes' storage paths."""
    young = {key for key, modified in objects.items() if now - modified < min_age}
    orphans = sorted(key for key in objects if key not in paths and key not in young)
    return Findings(
        orphans=orphans,
        missing=sum(1 for path in paths if path not in objects),
        too_young=sum(1 for key in young if key not in paths),
        objects=len(objects),
        resumes=len(paths),
    )


def main(
    argv: Sequence[str] | None = None, session_factory: Callable[[], Session] | None = None
) -> int:
    parser = argparse.ArgumentParser(
        prog=f"python -m app.devtools.{PROG}",
        description="Report stored resume PDFs that no resume refers to (and delete them).",
    )
    parser.add_argument("--delete", action="store_true", help="delete the orphans found")
    parser.add_argument(
        "--min-age-minutes",
        type=float,
        default=10.0,
        help="skip objects modified more recently than this (uploads in flight); default 10",
    )
    args = parser.parse_args(argv)
    if args.delete and settings.APP_ENV.strip().lower() == "production":
        print(f"{PROG}: refusing --delete: APP_ENV=production", file=sys.stderr)
        return 2

    try:
        objects = storage.list_objects(ingestion_service.RESUME_STORAGE_PREFIX)
        if session_factory is None:
            engine = create_engine(settings.SYNC_DATABASE_URL)
            try:
                with Session(engine) as db:
                    paths = ingestion_service.stored_resume_paths(db)
            finally:
                engine.dispose()
        else:
            with session_factory() as db:
                paths = ingestion_service.stored_resume_paths(db)
    except Exception as exc:  # storage or database unreachable: report, don't trace back
        print(f"{PROG}: cannot read storage or the database: {exc}", file=sys.stderr)
        return 2

    found = find(
        objects, paths, now=datetime.now(UTC), min_age=timedelta(minutes=args.min_age_minutes)
    )
    print(f"{PROG}: {found.objects} stored objects, {found.resumes} resumes")
    for key in found.orphans:
        print(f"  orphan {key}")
    print(f"  orphans:                          {len(found.orphans)}")
    print(f"  skipped (younger than the limit): {found.too_young}")
    print(f"  resumes without a stored PDF:     {found.missing}")
    if not found.orphans:
        return 0
    if not args.delete:
        print(f"{PROG}: run with --delete to remove the orphans")
        return 1

    failed = 0
    for key in found.orphans:
        try:
            storage.delete_file(key)
        except Exception as exc:
            failed += 1
            print(f"  could not delete {key}: {exc}", file=sys.stderr)
    print(f"{PROG}: deleted {len(found.orphans) - failed} orphans, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
