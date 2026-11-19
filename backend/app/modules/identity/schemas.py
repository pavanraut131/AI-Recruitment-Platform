"""identity data contracts: what callers get back from ``identity.service``.

Public: the composition layer may import these (import-linter allows
``app.modules.identity.schemas``).
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AgencySummary(BaseModel):
    """An agency as other code sees it."""

    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    status: str
    created_at: datetime
    updated_at: datetime
