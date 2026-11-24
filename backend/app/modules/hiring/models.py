"""hiring ORM models: ``Client``, ``Campaign``, ``Application`` and ``LegacyCandidateMap``.

Tables ``clients`` (E2-01), ``campaigns``, ``applications`` and ``legacy_candidate_map`` (the last
two created by the E0-14 expand migration). The upload pipeline writes ``applications`` through
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

**Tenancy (EPIC-02, expand phase E2-01).** ``clients``, ``campaigns`` and ``applications`` carry
``agency_id``: identity's agency, a plain UUID with no foreign key (ADR 0003). On ``campaigns``
and ``applications`` it is nullable until the E2-02 backfill and contract (``NOT NULL``, and
composite foreign keys ``(agency_id, client_id) -> clients`` and ``(agency_id, campaign_id) ->
campaigns``); every insert sets it already (``app.platform.tenancy.DEFAULT_AGENCY_ID``).
``campaigns.client_id`` (nullable until E2-02 too) is the client the campaign is run for; new
campaigns get ``DEFAULT_CLIENT_ID``. ``campaigns.created_by_user_id`` refers to identity's
users (E2-05), also without a foreign key.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.database import Base


class CampaignStatus(str, enum.Enum):
    active = "active"
    closed = "closed"
    archived = "archived"


class ClientStatus(str, enum.Enum):
    active = "active"
    # Archived, never deleted: campaigns refer to their client by id (spec §3.1).
    archived = "archived"


class Client(Base):
    """An agency's customer; every campaign is run for one client (spec §3.1, §3.3).

    Created by the E2-01 expand migration with the default agency's default client ("General",
    ``app.platform.tenancy.DEFAULT_CLIENT_ID``). A new table, so ``agency_id`` is ``NOT NULL``
    and its unique constraints include it from the start: a client name is unique per agency
    (case-insensitively), and ``(agency_id, id)`` is the target of the composite foreign key
    from ``campaigns`` that E2-02 adds.
    """

    __tablename__ = "clients"
    __table_args__ = (
        UniqueConstraint("agency_id", "id", name="uq_clients_agency_id_id"),
        Index("uq_clients_agency_id_lower_name", "agency_id", text("lower(name)"), unique=True),
        CheckConstraint("status IN ('active', 'archived')", name="ck_clients_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # identity's agency (no foreign key, ADR 0003).
    agency_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ClientStatus.active.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


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

    # Tenancy (E2-01 expand): nullable until the E2-02 contract; see the module docstring.
    agency_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    client_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # identity's user who created the campaign (no foreign key, ADR 0003); NULL for campaigns
    # created before sign-in exists (E2-05).
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


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

    # Tenancy (E2-01 expand): nullable until the E2-02 contract; see the module docstring.
    agency_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


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
