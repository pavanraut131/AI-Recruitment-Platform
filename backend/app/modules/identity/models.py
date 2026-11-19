"""identity ORM models: ``Agency`` (the ``agencies`` table).

Created by the E2-01 expand migration (``e2a01d5c8f36``), which also inserts the default agency
(:data:`app.platform.tenancy.DEFAULT_AGENCY_ID`, "Default agency"). Private to the identity
module: other modules use ``app.modules.identity.service``.

``agencies`` is referenced by ``agency_id`` on every tenant table of ``hiring``, ``talent`` and
``ingestion``, always **without** a foreign key (ADR 0003); RLS (E2-04) and the verification
(E2-02) keep those ids consistent instead.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.database import Base


class AgencyStatus(str, enum.Enum):
    active = "active"
    # Members of a suspended agency cannot sign in (checked per request from E2-05).
    suspended = "suspended"


class Agency(Base):
    """A tenant: the unit of isolation and data ownership (spec §3.1)."""

    __tablename__ = "agencies"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_agencies_slug"),
        CheckConstraint("status IN ('active', 'suspended')", name="ck_agencies_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Stable, URL-safe handle; read-only in the API (spec §8.2).
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=AgencyStatus.active.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
