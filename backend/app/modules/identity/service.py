"""identity public interface (EPIC-02 spec §3.2).

E2-01 (the expand phase) adds only the ``agencies`` table and a read of it; E2-02 a synchronous
read of every agency id for ``app.modules.ingestion.verify_people_applications``. Users,
memberships, sessions, login and the audit log arrive with E2-05.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.modules.identity import repository
from app.modules.identity.schemas import AgencySummary


async def get_agency(db: AsyncSession, agency_id: uuid.UUID) -> AgencySummary | None:
    """The agency with this id, or ``None``."""
    agency = await repository.get_agency(db, agency_id)
    return None if agency is None else AgencySummary.model_validate(agency)


def agency_ids(db: Session) -> set[uuid.UUID]:
    """The id of every agency (synchronous: for the verification command)."""
    return repository.agency_ids_sync(db)
