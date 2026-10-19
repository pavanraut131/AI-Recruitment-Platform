"""ingestion ORM models: ``Resume`` (the ``resumes`` table).

Private to the ingestion module: other modules and ``app.api`` use
``app.modules.ingestion.service`` instead.

``resumes.campaign_id`` refers to hiring's ``campaigns`` table. It is a plain UUID, with no ORM
relationship to ``Campaign`` and, since the contract migration (E0-20), no foreign key either
(ADR 0003: cross-module links are ids). Deleting a campaign therefore does not delete its resumes
in the database: ``app.api`` has ingestion delete them, in the same transaction
(``service.delete_campaign_resumes``), and their stored PDFs after the commit.

``resumes.person_id`` refers to talent's ``people`` table: a plain UUID with no foreign key
(ADR 0003). It, ``extracted`` and ``processing_step`` were added by the E0-14 expand migration as
nullable columns (a metadata-only ``ADD COLUMN`` in Postgres, no table rewrite). The upload
pipeline (``tasks.process_candidate_ai``) writes them since E0-16; the backfill (E0-17) filled
them for the resumes processed before. Since the read switch (E0-19) ``extracted`` is also what
the legacy candidate endpoints show as the candidate's name, contacts and profile.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.database import Base


class ResumeStatus(str):
    pending = "pending"
    extracting = "extracting"
    processing = "processing"
    done = "done"
    failed = "failed"


class Resume(Base):
    __tablename__ = "resumes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Hiring-owned campaign (no foreign key, ADR 0003).
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Person/Application split (spec §4, §5.1). All nullable, so existing rows are left as they are.
    # Talent-owned Person this resume resolved to (no foreign key, ADR 0003). Indexed for "resumes
    # of this person" and the spec §6.3 verification.
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    # Snapshot of the AI extraction for this file; when set, extraction is skipped on retry.
    extracted: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # extract_text | extract_profile | resolve_person | score | save: the current step while
    # processing, the failed step after a failure. No CHECK constraint: adding one to an existing
    # table scans it under an exclusive lock, and the values are owned by the pipeline code.
    processing_step: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # The text extracted from the PDF (step ``extract_text``), read by the AI step. Added by the
    # E0-19 expand migration: until the read switch it lived on the legacy candidate
    # (``candidates.raw_text``; the contract migration copied what was left there onto the
    # resume before dropping the table). Never part of an API response.
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
