"""Upload parsing cost: the B4 regression guard (python-multipart parsing DoS).

python-multipart 0.0.9 stepped through a file part's data one byte at a time, in Python, wherever
the bytes could be the start of the multipart delimiter (``\\r``, ``\\n``, ``-``, boundary
characters). Through this endpoint (Starlette's parser callbacks on top) one 10 MB upload made of
such bytes took almost 10 minutes, all of it CPU, on a developer laptop: a handful of requests
would tie up every worker. Current releases skip through part data with ``bytes.find``, and the same upload
takes a fraction of a second.

The test goes through the real endpoint (FastAPI, Starlette's form parser, python-multipart) with
a file at the upload size limit, so it is accepted and stored, and checks the stored bytes are the
uploaded ones: the fast path must still parse correctly.

**Timing.** The bound is on the CPU time of the test process (``time.process_time``, all
threads), not wall-clock time: the attack costs CPU, and CPU time doesn't grow when other jobs
load the machine. A first, wall-clock version of this test was flaky locally (2.8 s once, while
other test suites ran, including the first request's lazy imports). A small warm-up upload now
takes those imports out of the measurement, and the 10 MB upload measures about 0.05 s of CPU.
So 2 s has a wide margin for slow CI runners and is still four orders of magnitude below the
regression; the test runs in CI too, it is not skipped.
"""

from __future__ import annotations

import time

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.config import settings
from tests.factories import make_campaign
from tests.fakes import CeleryRecorder, InMemoryStorage

LIMIT_BYTES = settings.MAX_FILE_SIZE_MB * 1024 * 1024
MAX_CPU_SECONDS = 2.0

# The start of every multipart delimiter (``\r\n--`` + boundary), over and over. Each
# occurrence could be a delimiter, so the old parser stepped through it byte by byte; it never
# is one, because no boundary follows.
BOUNDARY_LIKE = b"\r\n--"


def boundary_like_pdf(size: int) -> bytes:
    """A ``size``-byte file that passes the PDF checks and is otherwise all boundary-like bytes."""
    header = b"%PDF-1.4\n"
    filler = BOUNDARY_LIKE * (size // len(BOUNDARY_LIKE) + 1)
    return header + filler[: size - len(header)]


async def test_boundary_like_upload_at_the_size_limit_parses_quickly(
    client: AsyncClient,
    db_session: AsyncSession,
    celery_calls: CeleryRecorder,
    fake_storage: InMemoryStorage,
) -> None:
    campaign = await make_campaign(db_session)
    url = f"/api/v2/campaigns/{campaign.id}/uploads"
    warm_up = await client.post(
        url, files=[("files", ("warm_up.pdf", b"%PDF-1.4\n", "application/pdf"))]
    )
    assert warm_up.status_code == 202, warm_up.text
    fake_storage.objects.clear()
    celery_calls.calls.clear()

    content = boundary_like_pdf(LIMIT_BYTES)
    upload = [("files", ("boundary_like.pdf", content, "application/pdf"))]
    started = time.process_time()
    resp = await client.post(url, files=upload)
    cpu_seconds = time.process_time() - started

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["rejected"] == []
    assert len(body["accepted"]) == 1
    [stored] = fake_storage.objects.values()
    assert stored.body == content
    assert len(celery_calls.calls) == 1
    assert cpu_seconds < MAX_CPU_SECONDS, f"10 MB boundary-like upload: {cpu_seconds:.2f} s CPU"
