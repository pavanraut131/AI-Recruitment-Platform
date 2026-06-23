"""Reset stuck/failed resumes and re-enqueue them."""
import sys
sys.path.insert(0, '/app')

from sqlalchemy import create_engine, text
from app.core.config import settings
from workers.extract_task import extract_resume

engine = create_engine(settings.SYNC_DATABASE_URL)
with engine.connect() as conn:
    result = conn.execute(text(
        "UPDATE resumes SET status='pending', retry_count=0, error_message=NULL "
        "WHERE status IN ('extracting', 'processing', 'failed') RETURNING id, original_filename"
    ))
    rows = result.fetchall()
    conn.commit()
    print(f"Reset {len(rows)} stuck resumes:")
    for r in rows:
        print(f"  {r[0]} - {r[1]}")
        extract_resume.apply_async(args=[str(r[0])], queue="extract_queue")
        print(f"  Re-queued: {r[1]}")
    print("Done! Watch the worker logs for progress.")
