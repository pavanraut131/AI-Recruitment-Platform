"""ingestion service: the public interface of the ingestion module.

Uploading resumes (validate, de-duplicate, store in object storage, queue extraction), listing
them, retrying failed ones, and the resume data other parts of the app need: status counts for
the stats endpoints and deleting the resumes of deleted candidates. The router in ``router.py``
only translates HTTP to these calls; ``app.api`` composes these functions with
``app.modules.hiring.service`` for the endpoints that span both modules.

Ingestion may call the ``hiring``, ``talent`` and ``ai`` services (never their internals); no
module may call ingestion (it is the top layer). Errors are :class:`IngestionError` subclasses
carrying the user-facing ``detail``; the router maps them to status codes.
"""

import hashlib
import uuid
from collections.abc import Sequence
from typing import Protocol

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.hiring import service as hiring_service
from app.modules.ingestion import repository, tasks
from app.modules.ingestion.models import Resume
from app.modules.ingestion.schemas import UploadBatchResponse
from app.platform import storage
from app.platform.config import settings

logger = structlog.get_logger()

MAX_FILE_SIZE = settings.MAX_FILE_SIZE_MB * 1024 * 1024
IN_PROGRESS_STATUSES = ("pending", "extracting", "processing")


# ── Errors ────────────────────────────────────────────────────
class IngestionError(Exception):
    """An ingestion operation failed; ``detail`` is the message returned to the client."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NotFoundError(IngestionError):
    """The campaign or resume does not exist."""


class InvalidInputError(IngestionError):
    """The request as a whole is invalid (e.g. too many files)."""


# ── Upload ────────────────────────────────────────────────────
class UploadedFile(Protocol):
    """What the upload needs from a file (FastAPI's ``UploadFile`` fits)."""

    @property
    def filename(self) -> str | None: ...

    async def read(self, size: int = -1) -> bytes: ...


def _rejection(content: bytes) -> str | None:
    """Why this file is rejected (size, magic bytes), or ``None``. The extension is checked
    before the content is read."""
    if len(content) > MAX_FILE_SIZE:
        return f"File exceeds {settings.MAX_FILE_SIZE_MB}MB limit"
    if content[:4] != b"%PDF":
        return "Invalid PDF file"
    return None


async def upload_resumes(
    db: AsyncSession, campaign_id: uuid.UUID, files: Sequence[UploadedFile]
) -> UploadBatchResponse:
    """Accept a batch of PDFs for a campaign and queue each accepted one for extraction.

    Per file: only ``.pdf`` names, at most ``MAX_FILE_SIZE_MB``, ``%PDF`` magic bytes, and not
    a duplicate (same SHA-256 in this campaign) of a resume that is done or still in progress;
    a duplicate of a failed or quota-exceeded resume is accepted again as a new row. Rejected
    files are reported, not raised.
    """
    if not await hiring_service.campaign_exists(db, campaign_id):
        raise NotFoundError("Campaign not found")

    if len(files) > settings.MAX_FILES_PER_BATCH:
        raise InvalidInputError(f"Maximum {settings.MAX_FILES_PER_BATCH} files per upload batch")

    accepted_ids: list[uuid.UUID] = []
    rejected: list[dict[str, str]] = []

    for file in files:
        filename = file.filename
        if filename is None:  # never for a multipart file part; same 500 as before
            raise ValueError("Uploaded file has no file name")

        # Validate file type
        if not filename.lower().endswith(".pdf"):
            rejected.append({"filename": filename, "reason": "Only PDF files are accepted"})
            continue

        content = await file.read()
        reason = _rejection(content)
        if reason:
            rejected.append({"filename": filename, "reason": reason})
            continue

        # Calculate hash for idempotency
        file_hash = hashlib.sha256(content).hexdigest()

        # Check if already processed or processing
        existing = await repository.latest_with_hash(db, campaign_id, file_hash)
        if existing:
            if existing.status == "done":
                rejected.append(
                    {
                        "filename": filename,
                        "reason": "Duplicate file (already processed successfully)",
                    }
                )
                continue
            elif existing.status in IN_PROGRESS_STATUSES:
                rejected.append(
                    {"filename": filename, "reason": "Duplicate file (currently in progress)"}
                )
                continue
            # If status is failed or quota_exceeded, we allow re-upload (creates new Resume row)

        # Upload to MinIO
        resume_id = uuid.uuid4()
        object_key = f"campaigns/{campaign_id}/resumes/{resume_id}/{filename}"
        storage.upload_file(content, object_key)

        repository.add(
            db,
            Resume(
                id=resume_id,
                campaign_id=campaign_id,
                original_filename=filename,
                storage_path=object_key,
                file_hash=file_hash,
                file_size_bytes=len(content),
                status="pending",
            ),
        )
        accepted_ids.append(resume_id)

    await repository.commit(db)

    # Enqueue extraction tasks
    for rid in accepted_ids:
        tasks.enqueue_extraction(rid)

    logger.info(
        "bulk_upload_complete",
        campaign_id=str(campaign_id),
        accepted=len(accepted_ids),
        rejected=len(rejected),
    )

    return UploadBatchResponse(
        batch_id=str(uuid.uuid4()),
        campaign_id=campaign_id,
        total_files=len(files),
        accepted_files=len(accepted_ids),
        rejected_files=rejected,
        resume_ids=accepted_ids,
        message=f"Processing {len(accepted_ids)} resumes asynchronously",
    )


# ── Resumes ───────────────────────────────────────────────────
async def list_resumes(db: AsyncSession, campaign_id: uuid.UUID) -> Sequence[Resume]:
    """The campaign's resumes, newest first. An unknown campaign has none (not a 404)."""
    return await repository.list_for_campaign(db, campaign_id)


async def get_resume(db: AsyncSession, resume_id: uuid.UUID) -> Resume:
    resume = await repository.get(db, resume_id)
    if not resume:
        raise NotFoundError("Resume not found")
    return resume


async def retry_failed(db: AsyncSession, campaign_id: uuid.UUID) -> int:
    """Re-queue the campaign's ``failed`` and ``quota_exceeded`` resumes; returns how many.

    Each resumes from the step it stopped at (spec §5.5): one that failed during AI analysis
    (``processing_step`` is an AI step, so its text is already stored) goes straight back to
    the AI task, which skips the steps already done; any other (failed text extraction, or
    processed before ``processing_step`` existed) starts again with text extraction.
    """
    resumes = await repository.failed_in_campaign(db, campaign_id)
    if not resumes:
        return 0

    for resume in resumes:
        resume.status = "pending"
        resume.retry_count = 0
        resume.error_message = None

    await repository.commit(db)

    for resume in resumes:
        if resume.processing_step in tasks.AI_STEPS:
            tasks.enqueue_analysis(resume.id)
        else:
            tasks.enqueue_extraction(resume.id)

    return len(resumes)


# ── For other use cases (composed in app.api) ─────────────────
async def resume_status_counts(
    db: AsyncSession, campaign_id: uuid.UUID | None = None
) -> dict[str, int]:
    """Resume counts by status, for one campaign or (``campaign_id=None``) for all of them."""
    return await repository.status_counts(db, campaign_id)


async def delete_resumes(db: AsyncSession, resume_ids: Sequence[uuid.UUID]) -> None:
    """Delete the resumes with these ids (unknown ids are ignored). Does not commit: the caller
    commits, together with whatever made the resumes obsolete (e.g. deleting candidates).

    The stored PDFs are left in object storage (as they always were).
    """
    await repository.delete_by_ids(db, resume_ids)
