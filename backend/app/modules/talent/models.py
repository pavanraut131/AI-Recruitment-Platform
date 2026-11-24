"""talent ORM models: ``Person``, ``PersonIdentifier``, ``IdentityConflict`` and ``PersonResume``.

Tables ``people``, ``person_identifiers``, ``identity_conflicts`` (EPIC-00 spec §4, ADR 0002,
ADR 0004) and ``person_resumes`` (E0-15). Private to the talent module: other modules use ``app.modules.talent.service``.

Foreign keys exist only *within* talent (identifiers, conflicts and resolutions -> ``people``,
with the agency: see below). References to
other modules' tables are plain UUID columns with no foreign key and no ORM relationship
(ADR 0003): ``people.latest_resume_id``, ``person_identifiers.source_resume_id`` and
``identity_conflicts.resume_id`` and ``person_resumes.resume_id`` all point at ingestion's
``resumes`` table.

Created by the E0-14 expand migration (``person_resumes`` by the E0-15 one).
``app.modules.talent.service.resolve_person`` is the only writer.

**Tenancy (EPIC-02).** Every table has ``agency_id`` (``NOT NULL``), identity's agency (a plain
UUID, no foreign key, ADR 0003): the talent pool is per agency (spec §3.1). Added by the E2-01
expand migration, backfilled and contracted by E2-02 (``e2b02c7a4d19``); ``resolve_person``
writes the agency it is given on every insert. The references to a Person are **composite
foreign keys** ``(agency_id, person_id) -> people(agency_id, id)``, so an identifier, a conflict
or a resolution always has its Person's agency, and matching is per agency
(``UNIQUE(agency_id, kind, value_normalized)``).
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
    ForeignKeyConstraint,
    Index,
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
    __table_args__ = (
        # Target of the composite foreign keys from the other talent tables.
        UniqueConstraint("agency_id", "id", name="uq_people_agency_id_id"),
        # The agency's talent pool, most recently updated first (/people).
        Index("ix_people_agency_id_updated_at", "agency_id", "updated_at"),
    )

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

    # Tenancy: identity's agency (no foreign key); see the module docstring.
    agency_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


def _person_fk(column: str, name: str) -> ForeignKeyConstraint:
    """``(agency_id, <column>) -> people(agency_id, id)``, deleted with the Person."""
    return ForeignKeyConstraint(
        ["agency_id", column],
        ["people.agency_id", "people.id"],
        ondelete="CASCADE",
        name=name,
    )


class PersonIdentifier(Base):
    """A normalised email or phone that identifies a Person (ADR 0004).

    ``UNIQUE(agency_id, kind, value_normalized)`` guarantees one Person per identifier in an
    agency (E2-02; it was ``UNIQUE(kind, value_normalized)`` before). ``inferred`` (sprint 3 decision, option A)
    marks a phone whose country was only inferred from ``PHONE_DEFAULT_REGION`` and that is not a
    mobile number: it is stored but never auto-matches on its own.
    """

    __tablename__ = "person_identifiers"
    __table_args__ = (
        UniqueConstraint(
            "agency_id",
            "kind",
            "value_normalized",
            name="uq_person_identifiers_agency_id_kind_value_normalized",
        ),
        CheckConstraint("kind IN ('email', 'phone')", name="ck_person_identifiers_kind"),
        _person_fk("person_id", "fk_person_identifiers_agency_id_person_id_people"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    value_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    raw_value: Mapped[str] = mapped_column(String(320), nullable=False)
    inferred: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    # Ingestion-owned resume the identifier was first seen on (no foreign key, ADR 0003).
    source_resume_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Tenancy: identity's agency (no foreign key); see the module docstring.
    agency_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


class IdentityConflict(Base):
    """A resume whose email matched one Person and phone another (spec §5.3).

    The resume is attached to ``chosen_person_id`` (the email match); nothing is merged. Written
    in E0, reviewed by the E6 queue, which sets ``resolved_at``.
    """

    __tablename__ = "identity_conflicts"
    __table_args__ = (
        _person_fk("chosen_person_id", "fk_identity_conflicts_agency_id_chosen_person_id_people"),
        _person_fk(
            "conflicting_person_id", "fk_identity_conflicts_agency_id_conflicting_person_id_people"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Ingestion-owned resume (no foreign key, ADR 0003).
    resume_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Indexed: Postgres does not index FK columns itself, and deleting a person (cascade) and the
    # E6 queue's "conflicts involving this person" both look rows up by these columns.
    chosen_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    conflicting_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Tenancy: identity's agency (no foreign key); see the module docstring.
    agency_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


class PersonResume(Base):
    """Which Person a resume was resolved to: one row per resolved resume (E0-15).

    Talent's own record of every ``resolve_person`` decision. It makes resolution idempotent per
    resume (a retried pipeline step gets the same answer, even for a resume with no usable
    identifiers) and gives each resume's ``uploaded_at``, which the profile refresh compares
    against (spec §5.3). ``resumes.person_id`` holds the same link on the ingestion side, but
    talent may not read another module's table (spec §3).
    """

    __tablename__ = "person_resumes"
    __table_args__ = (_person_fk("person_id", "fk_person_resumes_agency_id_person_id_people"),)

    # Ingestion-owned resume (no foreign key, ADR 0003).
    resume_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    matched_existing: Mapped[bool] = mapped_column(Boolean, nullable=False)
    conflict_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identity_conflicts.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Tenancy: identity's agency (no foreign key); see the module docstring.
    agency_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
