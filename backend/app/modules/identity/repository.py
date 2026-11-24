"""identity data access: every query against identity's tables (``agencies`` for now).

Private to the identity module (only ``service.py`` calls it).
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.modules.identity.models import Agency


async def get_agency(db: AsyncSession, agency_id: uuid.UUID) -> Agency | None:
    result = await db.execute(select(Agency).where(Agency.id == agency_id))
    return result.scalar_one_or_none()


def agency_ids_sync(db: Session) -> set[uuid.UUID]:
    return set(db.execute(select(Agency.id)).scalars())
