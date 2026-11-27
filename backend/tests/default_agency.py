"""The agency most tests put their data in: the "Default agency" and its client "General".

The E2-01 expand migration (``e2a01d5c8f36``) inserted them, with these fixed ids, for the data
that existed before E2 (EPIC-02 spec §7). Every test database is migrated from empty, so it has
them, and ``db_session`` is bound to this agency. Tests that need a second agency make one
(``tests/tenancy``).

The application has no constant for them since E2-12: to it this is an ordinary agency (slug
``default``), looked up by its slug where a command needs it (the seed, ``identity.cli
create-owner``, the tenancy backfill).
"""

from __future__ import annotations

import uuid
from typing import Final

DEFAULT_AGENCY_ID: Final = uuid.UUID("00000000-0000-4000-8000-00000000a001")
DEFAULT_CLIENT_ID: Final = uuid.UUID("00000000-0000-4000-8000-00000000c001")
DEFAULT_AGENCY_SLUG: Final = "default"
