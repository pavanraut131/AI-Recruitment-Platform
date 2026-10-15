"""hiring ORM models: ``Campaign``, ``Candidate``, ``Application`` and ``LegacyCandidateMap``.

Tables ``campaigns``, ``candidates`` (legacy, dropped in phase 5 of spec §6.1), ``applications``
and ``legacy_candidate_map`` (both created by the E0-14 expand migration; nothing writes them
until E0-16/E0-17).

Private to the hiring module: other modules use ``app.modules.hiring.service`` instead.

Cross-module references: ``candidates.resume_id`` points at the ingestion-owned ``resumes``
table. It is a plain column here, with no ORM relationship to ``Resume`` and no import of it
(``ingestion`` sits above ``hiring`` in the layer contract; ADR 0003 wants cross-module links to
be plain ids). The database still has the legacy foreign keys ``candidates.resume_id ->
resumes.id`` and ``resumes.campaign_id -> campaigns.id``, both ``ON DELETE CASCADE``; deleting a
campaign removes its resumes through the latter (E0-10 dropped the ORM cascade that did the same).
The Person/Application split removes those foreign keys (ADR 0003); from then on a campaign
delete must also call ingestion explicitly (see ``app.api``).

``applications.person_id`` (talent) and ``applications.resume_id`` (ingestion) are plain UUIDs
from the start: no foreign key, no ORM relationship (ADR 0003). ``applications.campaign_id`` is a
same-module foreign key (``ON DELETE CASCADE``), which the database applies on a campaign delete
without an ORM relationship.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

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

    # Relationships
    candidates: Mapped[list["Candidate"]] = relationship(
        "Candidate", back_populates="campaign", cascade="all, delete-orphan"
    )


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Cross-module foreign key to the ingestion-owned resumes table (no ORM relationship). Kept
    # as is until the Person/Application split replaces this table (ADR 0003).
    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resumes.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )

    # Personal info
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    github_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Structured extracted data
    skills: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    education: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    work_experience: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    certifications: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    projects: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    years_of_experience: Mapped[float | None] = mapped_column(Float, nullable=True)

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
    pipeline_stage: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
        # values: screened | phone_call | technical | offer | hired | rejected_manual
    )

    # Raw text
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    campaign: Mapped["Campaign"] = relationship("Campaign", back_populates="candidates")


class Application(Base):
    """A Person's application to one campaign (ADR 0002, spec §4).

    ``UNIQUE(campaign_id, person_id)``: a re-application to the same campaign updates the row.
    During the backfill ``id`` reuses the legacy ``candidates.id``. The scoring, insight and
    recruiter columns mirror ``candidates`` exactly (types and nullability), so the compatibility
    layer (spec §7) can rebuild the legacy responses unchanged.
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

    # AI insights (as in candidates)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    strengths: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    weaknesses: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    missing_skills: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    category: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Recruiter workflow fields (as in candidates)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    pipeline_stage: Mapped[str | None] = mapped_column(String(30), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LegacyCandidateMap(Base):
    """Redirects the id of a legacy candidate that the backfill merged into another application.

    ``candidate_id`` deliberately has no foreign key to ``candidates``: the map must outlive that
    table (dropped in phase 5). ``application_id`` is a same-module foreign key with ``ON DELETE
    CASCADE``, so deleting an application drops its redirects; it is indexed because Postgres does
    not index FK columns and the cascade looks rows up by it.
    """

    __tablename__ = "legacy_candidate_map"

    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
