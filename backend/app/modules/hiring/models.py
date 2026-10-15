"""hiring ORM models: ``Campaign`` and ``Candidate`` (the ``campaigns`` / ``candidates`` tables).

Private to the hiring module: other modules use ``app.modules.hiring.service`` instead.

Cross-module references: ``candidates.resume_id`` points at the ingestion-owned ``resumes``
table. It is a plain column here, with no ORM relationship to ``Resume`` and no import of it
(``ingestion`` sits above ``hiring`` in the layer contract; ADR 0003 wants cross-module links to
be plain ids). The database still has the legacy foreign keys ``candidates.resume_id ->
resumes.id`` and ``resumes.campaign_id -> campaigns.id``, both ``ON DELETE CASCADE``; deleting a
campaign removes its resumes through the latter (E0-10 dropped the ORM cascade that did the same).
The Person/Application split removes those foreign keys (ADR 0003); from then on a campaign
delete must also call ingestion explicitly (see ``app.api``).
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, func
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
