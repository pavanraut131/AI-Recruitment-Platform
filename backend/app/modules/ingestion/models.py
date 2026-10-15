"""ingestion ORM models: ``Resume`` (the ``resumes`` table) and ``BackfillCheckpoint``.

Private to the ingestion module: other modules and ``app.api`` use
``app.modules.ingestion.service`` instead.

``resumes.campaign_id`` refers to hiring's ``campaigns`` table. It is a plain column, with no ORM
relationship to ``Campaign`` (ADR 0003: cross-module links are ids). The legacy database foreign
key ``ON DELETE CASCADE`` stays until the Person/Application split, so deleting a campaign still
deletes its resumes in the database.

``resumes.person_id`` refers to talent's ``people`` table: a plain UUID with no foreign key
(ADR 0003). It, ``extracted`` and ``processing_step`` were added by the E0-14 expand migration as
nullable columns (a metadata-only ``ADD COLUMN`` in Postgres, no table rewrite); nothing writes
them until E0-16/E0-17.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
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
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
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


class BackfillCheckpoint(Base):
    """Progress of a resumable batch job, one row per job (spec §6.2).

    Deliberately minimal: ``name`` identifies the job (e.g. ``"person_application"``) and
    ``(last_created_at, last_id)`` is the keyset position of the last committed batch, so a
    restart resumes right after it. Both are null before the first batch. Meant to be written in
    the same transaction as the batch it records.
    """

    __tablename__ = "backfill_checkpoints"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    last_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
