"""hiring ORM models: ``Campaign``, ``Application`` and ``LegacyCandidateMap``.

Tables ``campaigns``, ``applications`` and ``legacy_candidate_map`` (the last two created by the
E0-14 expand migration). The upload pipeline writes ``applications`` through
``service.upsert_application``; the API reads and writes them since the read switch (E0-19).
The legacy ``candidates`` table was dropped by the contract migration (E0-20, spec §6.1 phase 5).

Private to the hiring module: other modules use ``app.modules.hiring.service`` instead.

Cross-module references are plain UUIDs, with no foreign key and no ORM relationship (ADR 0003):
``applications.person_id`` (talent) and ``applications.resume_id`` (ingestion). No foreign key
crosses a module boundary any more (E0-20 dropped the last one, ``resumes.campaign_id ->
campaigns.id``; ``tests/test_no_cross_module_foreign_keys.py`` checks the whole schema).
``applications.campaign_id`` is a same-module foreign key (``ON DELETE CASCADE``), which the
database applies on a campaign delete; the campaign's resumes are deleted by ingestion in the
same transaction (``app.api``, ``DELETE /campaigns/{id}``).
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.database import Base


class CampaignStatus(str, enum.Enum):
    active = "active"
    closed = "closed"
    archived = "archived"


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(5000), nullable=True)
    required_skills: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(
        String(20), default=CampaignStatus.active.value, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Application(Base):
    """A Person's application to one campaign (ADR 0002, spec §4).

    ``UNIQUE(campaign_id, person_id)``: a re-application to the same campaign updates the row.
    Applications backfilled from legacy candidates (E0-17) reuse the candidate's id. The
    scoring, insight and recruiter columns have the types and nullability the legacy
    ``candidates`` columns had, so the compatibility layer (spec §7) builds the legacy responses
    unchanged.
    """

    __tablename__ = "applications"
    __table_args__ = (
        # Also serves lookups by campaign_id (leading column), so no separate index for it.
        UniqueConstraint("campaign_id", "person_id", name="uq_applications_campaign_id_person_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    # Talent-owned Person (no foreign key, ADR 0003). Indexed for "applications of this person".
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # Ingestion-owned resume submitted for this application (no foreign key, ADR 0003). Indexed
    # for resume -> application lookups (dual write, deletes, the spec §6.3 verification).
    resume_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # When ``resume_id`` was uploaded: ingestion's ``resumes.uploaded_at``, copied here because
    # hiring may not read that table. It decides whether a later resume replaces the scoring
    # (``service.upsert_application``). Added by the E0-16 expand migration, hence nullable; a
    # row without it counts as older than any resume.
    resume_uploaded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # AI insights
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    strengths: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    weaknesses: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    missing_skills: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    category: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Recruiter workflow fields
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    pipeline_stage: Mapped[str | None] = mapped_column(String(30), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LegacyCandidateMap(Base):
    """A historical id map: the id of a legacy candidate that the backfill (E0-17) merged into
    another application, and that application.

    Old candidate ids (bookmarks, links) keep resolving through it to the surviving application
    (spec §7). ``candidate_id`` never had a foreign key, so the map outlived ``candidates``
    (dropped in E0-20); it refers to nothing any more and no new rows are written.
    ``application_id`` is a same-module foreign key with ``ON DELETE CASCADE``, so deleting an
    application drops its redirects; it is indexed because Postgres does not index FK columns and
    the cascade looks rows up by it.
    """

    __tablename__ = "legacy_candidate_map"

    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
