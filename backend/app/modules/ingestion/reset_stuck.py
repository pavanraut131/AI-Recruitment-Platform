"""Reset stuck/failed resumes and re-enqueue them.

Operator script for a running stack: every resume that is ``extracting``, ``processing`` or
``failed`` goes back to ``pending`` and is queued for extraction again (``quota_exceeded`` ones
are left alone; the retry-failed endpoint covers those per campaign). Run inside the backend
container::

    docker compose exec backend python -m app.modules.ingestion.reset_stuck

Moved from ``backend/reset_stuck.py`` (E0-10).
"""

from app.modules.ingestion import repository, tasks


def main() -> None:
    with tasks.SyncSession() as db:
        rows = repository.reset_stuck_sync(db)
        db.commit()
    print(f"Reset {len(rows)} stuck resumes:")
    for resume_id, filename in rows:
        print(f"  {resume_id} - {filename}")
        tasks.enqueue_extraction(resume_id)
        print(f"  Re-queued: {filename}")
    print("Done! Watch the worker logs for progress.")


if __name__ == "__main__":
    main()
