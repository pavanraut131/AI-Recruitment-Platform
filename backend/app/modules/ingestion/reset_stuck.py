"""Reset stuck/failed resumes and re-enqueue them.

Operator script for a running stack: every resume that is ``extracting``, ``processing`` or
``failed`` goes back to ``pending`` and is queued for extraction again (``quota_exceeded`` ones
are left alone; the retry-failed endpoint covers those per campaign). Run inside the backend
container::

    docker compose exec backend python -m app.modules.ingestion.reset_stuck

Moved from ``backend/reset_stuck.py`` (E0-10).

**Every agency** (E2-03): it resets the stuck resumes of all agencies through an unbound session
(from E2-04 as the owner role, spec §6.2), and re-queues each one with **its own row's**
``agency_id``, so its task runs for the agency it belongs to. A row not backfilled yet (``NULL``,
before E2-02) belongs to the default agency.
"""

from app.modules.ingestion import repository, tasks
from app.platform.tenancy import DEFAULT_AGENCY_ID


def main() -> None:
    with tasks.SyncSession() as db:  # unbound: every agency, on purpose
        rows = repository.reset_stuck_sync(db)
        db.commit()
    print(f"Reset {len(rows)} stuck resumes:")
    for resume_id, agency_id, filename in rows:
        print(f"  {resume_id} - {filename}")
        tasks.enqueue_extraction(resume_id, agency_id or DEFAULT_AGENCY_ID)
        print(f"  Re-queued: {filename}")
    print("Done! Watch the worker logs for progress.")


if __name__ == "__main__":
    main()
