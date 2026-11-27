"""Reset stuck/failed resumes and re-enqueue them.

Operator script for a running stack: every resume that is ``extracting``, ``processing`` or
``failed`` goes back to ``pending`` and is queued for extraction again (``quota_exceeded`` ones
are left alone; the retry-failed endpoint covers those per campaign). Run inside the backend
container::

    docker compose exec backend python -m app.modules.ingestion.reset_stuck

Moved from ``backend/reset_stuck.py`` (E0-10).

**Every agency** (E2-03, E2-04): it connects as the owner role (``MIGRATION_DATABASE_URL``,
``app.platform.maintenance``, spec §6.2), resets the stuck resumes of all agencies, and re-queues
each one with **its own row's** ``agency_id``, so its task runs for the agency it belongs to.
"""

from collections.abc import Callable

from sqlalchemy.orm import Session

from app.modules.ingestion import repository, tasks
from app.platform import maintenance


def main(session_factory: Callable[[], Session] | None = None) -> None:
    with (session_factory or maintenance.owner_session)() as db:  # every agency, on purpose
        rows = repository.reset_stuck_sync(db)
        db.commit()
    print(f"Reset {len(rows)} stuck resumes:")
    for resume_id, agency_id, filename in rows:
        print(f"  {resume_id} - {filename}")
        tasks.enqueue_extraction(resume_id, agency_id)
        print(f"  Re-queued: {filename}")
    print("Done! Watch the worker logs for progress.")


if __name__ == "__main__":
    main()
