"""talent ORM models: ``Person``, ``PersonIdentifier`` and ``IdentityConflict``.

Tables ``people``, ``person_identifiers`` and ``identity_conflicts`` (EPIC-00 spec §4, ADR 0002,
ADR 0004). Private to the talent module: other modules use ``app.modules.talent.service``.

Foreign keys exist only *within* talent (identifiers and conflicts -> ``people``). References to
other modules' tables are plain UUID columns with no foreign key and no ORM relationship
(ADR 0003): ``people.latest_resume_id``, ``person_identifiers.source_resume_id`` and
``identity_conflicts.resume_id`` all point at ingestion's ``resumes`` table.

Created by the E0-14 expand migration. Nothing writes these tables yet; ``resolve_person``
(E0-15) is the only intended writer.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.database import Base


class Person(Base):
    """One record per human, across campaigns. The profile comes from the most recently
    *uploaded* resume; fields a newer extraction lacks keep their previous value (spec §5.3)."""

    __tablename__ = "people"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    github_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Profile, same shape as the legacy candidates columns.
    skills: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    education: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    work_experience: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    certifications: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    projects: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    years_of_experience: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Ingestion-owned resume the profile was taken from (no cross-module foreign key, ADR 0003).
    latest_resume_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PersonIdentifier(Base):
    """A normalised email or phone that identifies a Person (ADR 0004).

    ``UNIQUE(kind, value_normalized)`` guarantees one Person per identifier; it becomes
    ``(agency_id, kind, value_normalized)`` in E2. ``inferred`` (sprint 3 decision, option A)
    marks a phone whose country was only inferred from ``PHONE_DEFAULT_REGION`` and that is not a
    mobile number: it is stored but never auto-matches on its own.
    """

    __tablename__ = "person_identifiers"
    __table_args__ = (
        UniqueConstraint(
            "kind", "value_normalized", name="uq_person_identifiers_kind_value_normalized"
        ),
        CheckConstraint("kind IN ('email', 'phone')", name="ck_person_identifiers_kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    value_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    raw_value: Mapped[str] = mapped_column(String(320), nullable=False)
    inferred: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    # Ingestion-owned resume the identifier was first seen on (no foreign key, ADR 0003).
    source_resume_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdentityConflict(Base):
    """A resume whose email matched one Person and phone another (spec §5.3).

    The resume is attached to ``chosen_person_id`` (the email match); nothing is merged. Written
    in E0, reviewed by the E6 queue, which sets ``resolved_at``.
    """

    __tablename__ = "identity_conflicts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Ingestion-owned resume (no foreign key, ADR 0003).
    resume_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Indexed: Postgres does not index FK columns itself, and deleting a person (cascade) and the
    # E6 queue's "conflicts involving this person" both look rows up by these columns.
    chosen_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conflicting_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
