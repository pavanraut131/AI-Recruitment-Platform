"""Tenancy shared by every module (EPIC-02 spec §3.2, §7).

For now (E2-01, the expand phase) this holds only the two **transition constants**: the ids of
the default agency and its default client, which the expand migration (``e2a01d5c8f36``) inserts
and every insert path writes until tenant context exists:

- :data:`DEFAULT_AGENCY_ID`: the agency (identity's ``agencies``) that every existing row belongs
  to, named **"Default agency"** (slug ``default``). Every insert into a tenant table sets
  ``agency_id`` to it.
- :data:`DEFAULT_CLIENT_ID`: the client (hiring's ``clients``) of the default agency that every
  existing campaign is run for, named **"General"** (spec §15, Q7). New campaigns get it.

The owner renames both later (E2-05, E2-10). They live here, not in ``identity`` or ``hiring``,
because every tenant module needs the agency id and none of them may import ``identity``
(it is a leaf, and module boundaries are enforced by import-linter); the client id is the same
kind of migration-only value and the E2-02 backfill (in ``identity``) needs it too.

What replaces them:

- **E2-03** adds tenant context here (``TenantContext``, ``AccessScope``, ``bind``,
  ``agency_id_of``) and the insert paths take the agency from the session instead of
  :data:`DEFAULT_AGENCY_ID` (``grep -rn DEFAULT_AGENCY_ID app`` lists every one).
- **E2-06** makes the client a required field of a new campaign (no more
  :data:`DEFAULT_CLIENT_ID` on create).
- **E2-12** deletes both constants; the rows stay as a normal agency and client.

The values are fixed, so the migration, the code and the runbooks agree without a lookup.
Never reuse them for anything else.
"""

import uuid
from typing import Final

DEFAULT_AGENCY_ID: Final = uuid.UUID("00000000-0000-4000-8000-00000000a001")
DEFAULT_CLIENT_ID: Final = uuid.UUID("00000000-0000-4000-8000-00000000c001")
