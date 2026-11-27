"""ingestion service: the public interface of the ingestion module.

Uploading resumes (validate, de-duplicate, store in object storage, queue extraction), listing
them, retrying failed ones, and the resume data other parts of the app need: status counts for
the stats endpoints, the extraction snapshots the candidate endpoints show (E0-19), and
deleting the resumes of deleted applications and campaigns with their stored PDFs (E0-20:
first the rows, in the caller's transaction, then the PDFs once it has committed; see
:func:`delete_stored_files`). The router in
``router.py`` only translates HTTP to these calls; ``app.api`` composes these functions with
``app.modules.hiring.service`` for the endpoints that span both modules.

Ingestion may call the ``hiring``, ``talent`` and ``ai`` services (never their internals); no
module may call ingestion (it is the top layer). Errors are :class:`IngestionError` subclasses
carrying the user-facing ``detail``; the router maps them to status codes.

**Tenancy (E2-03).** The session is bound to an agency (``app.platform.tenancy``): the
repository filters every query to it and new resumes get its ``agency_id``. The use cases that
list or fetch a campaign's data take the caller's ``AccessScope`` and have hiring check it:
uploads, a campaign's resumes, retries and the progress stream go through the campaign
(``hiring.service.campaign_exists``: 404 outside the scope), so they follow its client (spec
§5.3, E2-06); the lists across campaigns (Home's resumes in progress, the stats' tally) keep to
the campaigns hiring says the scope covers (``campaign_ids_in_scope``). The ``/api/v1`` use
cases take only a scope of every client (``check_legacy_scope``). The agency travels on: every queued task
carries it (``tasks.enqueue_*``), the progress stream subscribes to the agency's channel of the
campaign (``events.campaign_channel``), and a new upload is stored under the agency's prefix
(:func:`resume_storage_key`).
"""

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.modules.hiring import service as hiring_service
from app.modules.ingestion import events, repository, tasks
from app.modules.ingestion import schemas_v2 as v2
from app.modules.ingestion.models import Resume
from app.modules.ingestion.schemas import UploadBatchResponse
from app.platform import storage
from app.platform.config import settings
from app.platform.tenancy import AccessScope, agency_id_of

logger = structlog.get_logger()

MAX_FILE_SIZE = settings.MAX_FILE_SIZE_MB * 1024 * 1024
IN_PROGRESS_STATUSES = ("pending", "extracting", "processing")
# Uploaded PDFs are stored under the agency's prefix (spec §6.5, E2-03):
# ``agencies/<agency>/campaigns/<campaign>/resumes/<resume>.pdf``. The file name stays in
# ``resumes.original_filename``, never in the key, so a crafted name cannot shape a path.
RESUME_STORAGE_PREFIX = "agencies/"
# Uploads before E2-03 were stored as ``campaigns/<campaign>/resumes/<resume>/<file name>``. Those
# objects keep their keys (``resumes.storage_path`` is stored per row, so reads and deletes keep
# working); moving them is an optional devtool in the backlog.
LEGACY_RESUME_STORAGE_PREFIX = "campaigns/"
# Every prefix a stored resume can live under (``app.devtools.storage_orphans`` scans them all).
RESUME_STORAGE_PREFIXES = (RESUME_STORAGE_PREFIX, LEGACY_RESUME_STORAGE_PREFIX)


def resume_storage_key(agency_id: uuid.UUID, campaign_id: uuid.UUID, resume_id: uuid.UUID) -> str:
    """The object key of a newly uploaded resume's PDF."""
    return f"{RESUME_STORAGE_PREFIX}{agency_id}/campaigns/{campaign_id}/resumes/{resume_id}.pdf"


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
    db: AsyncSession, scope: AccessScope, campaign_id: uuid.UUID, files: Sequence[UploadedFile]
) -> UploadBatchResponse:
    """Accept a batch of PDFs for a campaign and queue each accepted one for extraction.

    Per file: only ``.pdf`` names, at most ``MAX_FILE_SIZE_MB``, ``%PDF`` magic bytes, and not
    a duplicate (same SHA-256 in this campaign) of a resume that is done or still in progress;
    a duplicate of a failed or quota-exceeded resume is accepted again as a new row. Rejected
    files are reported, not raised.
    """
    accepted_ids, rejected = await _accept_uploads(db, scope, campaign_id, files)
    return UploadBatchResponse(
        batch_id=str(uuid.uuid4()),
        campaign_id=campaign_id,
        total_files=len(files),
        accepted_files=len(accepted_ids),
        rejected_files=rejected,
        resume_ids=accepted_ids,
        message=f"Processing {len(accepted_ids)} resumes asynchronously",
    )


async def _accept_uploads(
    db: AsyncSession,
    scope: AccessScope,
    campaign_id: uuid.UUID,
    files: Sequence[UploadedFile],
    uploaded_by: uuid.UUID | None = None,
) -> tuple[list[uuid.UUID], list[dict[str, str]]]:
    """The upload itself (see :func:`upload_resumes`): stores and queues the accepted files,
    recording who uploaded them (``None``: unknown, v1 and the development fallback); returns
    their resume ids and the rejected files with the reason."""
    if not await hiring_service.campaign_exists(db, scope, campaign_id):
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
        object_key = resume_storage_key(agency_id_of(db), campaign_id, resume_id)
        storage.upload_file(content, object_key)

        repository.add(  # in the session's agency
            db,
            Resume(
                id=resume_id,
                campaign_id=campaign_id,
                original_filename=filename,
                storage_path=object_key,
                file_hash=file_hash,
                file_size_bytes=len(content),
                status="pending",
                uploaded_by_user_id=uploaded_by,
            ),
        )
        accepted_ids.append(resume_id)

    await repository.commit(db)

    # Enqueue extraction tasks
    agency_id = agency_id_of(db)
    for rid in accepted_ids:
        tasks.enqueue_extraction(rid, agency_id)

    logger.info(
        "bulk_upload_complete",
        campaign_id=str(campaign_id),
        accepted=len(accepted_ids),
        rejected=len(rejected),
    )
    return accepted_ids, rejected


# ── Resumes ───────────────────────────────────────────────────
async def list_resumes(
    db: AsyncSession, scope: AccessScope, campaign_id: uuid.UUID
) -> Sequence[Resume]:
    """The campaign's resumes, newest first. An unknown campaign has none (not a 404)."""
    hiring_service.check_legacy_scope(db, scope)
    return await repository.list_for_campaign(db, campaign_id)


async def get_resume(db: AsyncSession, scope: AccessScope, resume_id: uuid.UUID) -> Resume:
    hiring_service.check_legacy_scope(db, scope)
    resume = await repository.get(db, resume_id)
    if not resume:
        raise NotFoundError("Resume not found")
    return resume


async def retry_failed(db: AsyncSession, scope: AccessScope, campaign_id: uuid.UUID) -> int:
    """Re-queue the campaign's ``failed`` and ``quota_exceeded`` resumes; returns how many.

    Each resumes from the step it stopped at (spec §5.5): one that failed during AI analysis
    (``processing_step`` is an AI step, so its text is already stored) goes straight back to
    the AI task, which skips the steps already done; any other (failed text extraction, or
    processed before ``processing_step`` existed) starts again with text extraction.
    """
    hiring_service.check_legacy_scope(db, scope)
    return len(await _retry_failed(db, campaign_id))


async def _retry_failed(db: AsyncSession, campaign_id: uuid.UUID) -> Sequence[Resume]:
    """The retry itself (see :func:`retry_failed`); returns the resumes queued again."""
    resumes = await repository.failed_in_campaign(db, campaign_id)
    if not resumes:
        return []

    for resume in resumes:
        resume.status = "pending"
        resume.retry_count = 0
        resume.error_message = None

    await repository.commit(db)

    agency_id = agency_id_of(db)
    for resume in resumes:
        if resume.processing_step in tasks.AI_STEPS:
            tasks.enqueue_analysis(resume.id, agency_id)
        else:
            tasks.enqueue_extraction(resume.id, agency_id)

    return resumes


# ── For other use cases (composed in app.api) ─────────────────
async def resume_status_counts(
    db: AsyncSession, scope: AccessScope, campaign_id: uuid.UUID | None = None
) -> dict[str, int]:
    """Resume counts by status, for one campaign or (``campaign_id=None``) for all of them."""
    hiring_service.check_legacy_scope(db, scope)
    return await repository.status_counts(db, campaign_id)


@dataclass(frozen=True)
class StoredFiles:
    """The object-storage keys of deleted resumes: what :func:`delete_stored_files` removes once
    the transaction that deleted the rows has committed."""

    keys: tuple[str, ...] = ()


async def delete_application_resumes(
    db: AsyncSession, deleted: Sequence[hiring_service.DeletedApplication]
) -> StoredFiles:
    """Delete the resumes of deleted applications (E0-19): each application's current resume
    and every other resume of its Person in its campaign (the older resumes of a merged
    application). Flushes, does not commit: the caller commits together with the application
    delete, then passes the result to :func:`delete_stored_files`.
    """
    keys = await repository.delete_of_people(
        db,
        [d.resume_id for d in deleted],
        [(d.campaign_id, d.person_id) for d in deleted],
    )
    return StoredFiles(tuple(keys))


async def delete_campaign_resumes(db: AsyncSession, campaign_id: uuid.UUID) -> StoredFiles:
    """Delete every resume of a campaign that is being deleted (E0-20: no foreign key cascades
    from ``campaigns`` to ``resumes`` any more, ADR 0003). Flushes, does not commit: the caller
    commits together with the campaign delete, then passes the result to
    :func:`delete_stored_files`."""
    keys = await repository.delete_for_campaign(db, campaign_id)
    logger.info("campaign_resumes_deleted", campaign_id=str(campaign_id), count=len(keys))
    return StoredFiles(tuple(keys))


async def delete_stored_files(files: StoredFiles) -> list[str]:
    """Delete the stored PDFs of resumes whose delete **has committed**; returns the keys that
    could not be deleted.

    Call it only after the commit: if the transaction failed, the rows (and their PDFs) must
    stay. A failure here never raises, because the request it belongs to has already succeeded
    in the database: it is logged (``stored_file_delete_failed``) and the object is left as an
    orphan (a stored object with no resume row), which ``python -m app.devtools.storage_orphans``
    reports and can delete. The calls run in a worker thread (boto3 is blocking).
    """
    failed: list[str] = []
    for key in files.keys:
        try:
            await asyncio.to_thread(storage.delete_file, key)
        except Exception as exc:  # any storage error: log, keep going, never fail the request
            failed.append(key)
            logger.error("stored_file_delete_failed", key=key, error=str(exc))
    return failed


def stored_resume_paths(db: Session) -> set[str]:
    """The storage key of every resume row of **every agency** (sync, unbound owner session; for
    ``app.devtools.storage_orphans``)."""
    return repository.storage_paths_sync(db)


class _ResumeProfiles:
    """:class:`hiring.service.ResumeProfiles` over this module's ``resumes`` table."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def extractions(
        self, resume_ids: Sequence[uuid.UUID]
    ) -> Mapping[uuid.UUID, Mapping[str, Any] | None]:
        return await repository.extractions(self._db, resume_ids)

    async def matching(
        self, resume_ids: Sequence[uuid.UUID], filters: hiring_service.ProfileFilters
    ) -> Collection[uuid.UUID]:
        return await repository.matching_extractions(
            self._db,
            resume_ids,
            search=filters.search,
            skill=filters.skill,
            min_experience=filters.min_experience,
        )

    async def skill_lists(self, resume_ids: Sequence[uuid.UUID]) -> Mapping[uuid.UUID, Any]:
        return await repository.skill_lists(self._db, resume_ids)

    async def owner(self, resume_id: uuid.UUID) -> hiring_service.ResumeOwner | None:
        found = await repository.owner(self._db, resume_id)
        if found is None:
            return None
        campaign_id, person_id = found
        return hiring_service.ResumeOwner(campaign_id=campaign_id, person_id=person_id)

    async def names(self, resume_ids: Sequence[uuid.UUID]) -> Mapping[uuid.UUID, str | None]:
        return await repository.names(self._db, resume_ids)


def resume_profiles(db: AsyncSession) -> hiring_service.ResumeProfiles:
    """The resumes' extraction snapshots, as the legacy candidate endpoints read them (E0-19):
    the per-resume name, contacts and profile, the list's profile filters, and a resume's
    campaign and Person. ``app.api`` passes it to ``hiring.service``."""
    return _ResumeProfiles(db)


# ══ /api/v2 (E1-02, EPIC-01 spec §6) ══════════════════════════
# Unlike the legacy endpoints, an unknown campaign is a 404 (B7) and a batch with too many files
# is reported as a validation error by the router (B10).
RECENT_UPLOADS = timedelta(hours=24)


async def _require_campaign(db: AsyncSession, scope: AccessScope, campaign_id: uuid.UUID) -> None:
    if not await hiring_service.campaign_exists(db, scope, campaign_id):
        raise NotFoundError("Campaign not found")


async def open_campaign_stream(
    db: AsyncSession, scope: AccessScope, campaign_id: uuid.UUID
) -> AsyncIterator[dict[str, str]]:
    """The campaign's typed progress stream (:func:`events.typed_campaign_event_stream`); 404 for
    an unknown campaign.

    The existence check's read transaction is ended before the stream is returned, so the
    request's session holds no database connection while the (long-lived) stream is open,
    whenever the framework closes the session. The stream subscribes to the campaign's channel
    **of the caller's agency** (spec §6.3).
    """
    await _require_campaign(db, scope, campaign_id)
    await repository.commit(db)
    return events.typed_campaign_event_stream(scope.agency_id, campaign_id)


def _resume_resource(resume: Resume, application_id: uuid.UUID | None) -> v2.Resume:
    return v2.Resume(
        id=resume.id,
        campaign_id=resume.campaign_id,
        filename=resume.original_filename,
        size_bytes=resume.file_size_bytes,
        status=v2.ResumeStatus(resume.status),
        processing_step=(
            v2.ProcessingStep(resume.processing_step) if resume.processing_step else None
        ),
        error_message=resume.error_message,
        retry_count=resume.retry_count or 0,
        application_id=application_id,
        uploaded_at=resume.uploaded_at,
        updated_at=resume.updated_at,
    )


async def resume_resources(
    db: AsyncSession, scope: AccessScope, resumes: Sequence[Resume]
) -> list[v2.Resume]:
    """The v2 ``Resume`` of each resume, with the application it belongs to (the application of
    its Person in its campaign, from hiring): one query for all of them."""
    owners = {
        r.id: hiring_service.ResumeOwner(campaign_id=r.campaign_id, person_id=r.person_id)
        for r in resumes
        if r.person_id is not None
    }
    applications = await hiring_service.application_ids_of(db, scope, list(owners.values()))
    return [
        _resume_resource(r, applications.get(owners[r.id]) if r.id in owners else None)
        for r in resumes
    ]


async def upload_resumes_v2(
    db: AsyncSession,
    scope: AccessScope,
    campaign_id: uuid.UUID,
    files: Sequence[UploadedFile],
    uploaded_by: uuid.UUID | None = None,
) -> v2.UploadResult:
    """:func:`upload_resumes` (404 for a campaign outside the scope), answering with the accepted
    resumes themselves."""
    accepted_ids, rejected = await _accept_uploads(db, scope, campaign_id, files, uploaded_by)
    stored = {r.id: r for r in await repository.by_ids(db, accepted_ids)}
    accepted = [stored[rid] for rid in accepted_ids]
    return v2.UploadResult(
        accepted=[_resume_resource(r, None) for r in accepted],
        rejected=[v2.RejectedFile(**item) for item in rejected],
    )


async def list_resumes_page(
    db: AsyncSession,
    scope: AccessScope,
    campaign_id: uuid.UUID,
    statuses: Sequence[str],
    offset: int,
    limit: int,
) -> tuple[list[v2.Resume], int]:
    """A page of the campaign's resumes, newest first (404 for an unknown campaign): four
    queries whatever the page size."""
    await _require_campaign(db, scope, campaign_id)
    resumes, total = await repository.page_for_campaign(
        db, campaign_id, statuses, offset=offset, limit=limit
    )
    return await resume_resources(db, scope, resumes), total


async def retry_failed_v2(
    db: AsyncSession, scope: AccessScope, campaign_id: uuid.UUID
) -> v2.RetryResult:
    """:func:`retry_failed` (404 for an unknown campaign), answering with the resumes queued
    again, oldest upload first."""
    await _require_campaign(db, scope, campaign_id)
    resumes = await _retry_failed(db, campaign_id)
    ordered = sorted(resumes, key=lambda r: (r.uploaded_at, r.id))
    return v2.RetryResult(retried=[r.id for r in ordered])


async def resumes_in_progress(
    db: AsyncSession, scope: AccessScope, limit: int
) -> tuple[list[v2.Resume], int]:
    """The newest resumes still being processed, across the campaigns in ``scope``, and how many
    there are."""
    campaign_ids = await hiring_service.campaign_ids_in_scope(db, scope)
    resumes, total = await repository.page_in_statuses(
        db, IN_PROGRESS_STATUSES, campaign_ids, limit=limit
    )
    return await resume_resources(db, scope, resumes), total


def _tally(rows: Sequence[repository.TallyRow]) -> hiring_service.ResumeTally:
    by_status: dict[str, int] = {}
    recent = 0
    latest: datetime | None = None
    for _, status, count, new, updated in rows:
        by_status[status] = by_status.get(status, 0) + count
        recent += new
        if updated is not None and (latest is None or updated > latest):
            latest = updated
    return hiring_service.ResumeTally(
        by_status=by_status, uploaded_last_24h=recent, last_updated_at=latest
    )


async def resume_tally(
    db: AsyncSession, scope: AccessScope, campaign_id: uuid.UUID | None = None
) -> hiring_service.ResumeTally:
    """The resume tally of one campaign, or (``None``) of every campaign in ``scope``: one query
    (two for a client-limited scope)."""
    if campaign_id is not None:
        hiring_service.check_scope(db, scope)
        ids: list[uuid.UUID] | None = [campaign_id]
    else:
        ids = await hiring_service.campaign_ids_in_scope(db, scope)
    return _tally(await repository.tallies(db, ids, RECENT_UPLOADS))


class _ResumeTallies:
    """:class:`hiring.service.ResumeTallies` over this module's ``resumes`` table."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def by_campaign(
        self, campaign_ids: Sequence[uuid.UUID]
    ) -> Mapping[uuid.UUID, hiring_service.ResumeTally]:
        rows = await repository.tallies(self._db, campaign_ids, RECENT_UPLOADS)
        grouped: dict[uuid.UUID, list[repository.TallyRow]] = {}
        for row in rows:
            grouped.setdefault(row[0], []).append(row)
        return {cid: _tally(campaign_rows) for cid, campaign_rows in grouped.items()}


def resume_tallies(db: AsyncSession) -> hiring_service.ResumeTallies:
    """The campaigns' resume tallies for hiring's v2 campaign resources; ``app.api`` passes it
    to ``hiring.service``."""
    return _ResumeTallies(db)
