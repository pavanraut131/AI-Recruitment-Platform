import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, Float, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.core.database import Base


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False, unique=True
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
    skills: Mapped[list] = mapped_column(JSONB, default=list)
    education: Mapped[list] = mapped_column(JSONB, default=list)
    work_experience: Mapped[list] = mapped_column(JSONB, default=list)
    certifications: Mapped[list] = mapped_column(JSONB, default=list)
    projects: Mapped[list] = mapped_column(JSONB, default=list)
    years_of_experience: Mapped[float | None] = mapped_column(Float, nullable=True)

    # AI insights
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    strengths: Mapped[list] = mapped_column(JSONB, default=list)
    weaknesses: Mapped[list] = mapped_column(JSONB, default=list)
    missing_skills: Mapped[list] = mapped_column(JSONB, default=list)
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_breakdown: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    category: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Recruiter workflow fields
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    pipeline_stage: Mapped[str | None] = mapped_column(
        String(30), nullable=True
        # values: screened | phone_call | technical | offer | hired | rejected_manual
    )

    # Raw text
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    resume: Mapped["Resume"] = relationship("Resume", back_populates="candidate")
    campaign: Mapped["Campaign"] = relationship("Campaign", back_populates="candidates")
