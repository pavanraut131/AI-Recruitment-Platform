"""Database sessions for the operator commands that span agencies (EPIC-02 spec §6.2, §6.3).

``reset_stuck``, ``storage_orphans``, ``verify_people_applications`` and ``backfill_tenancy``
read (and some write) the rows of **every** agency. Under row-level security the application
role sees only the transaction's agency, and an unbound session sees nothing, so these commands
connect as the owner role, ``hireflow_owner`` (``MIGRATION_DATABASE_URL``), whose
``maintenance`` policy covers every row. Using this module is how a command states that intent;
the API and the workers never import it.

The sessions are unbound (no ``app.agency_id``): the repository functions these commands call
say that they read every agency, and never call ``tenancy.agency_id_of``.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.platform.config import settings
from app.platform.tenancy import guard_in_development


@lru_cache
def owner_engine() -> Engine:
    """The owner role's engine (created on first use, then shared by the process)."""
    engine = create_engine(settings.migration_database_url, pool_pre_ping=True)
    guard_in_development(engine, settings.APP_ENV)
    return engine


def owner_session() -> Session:
    """A new, unbound session as the owner role: every agency's rows."""
    return Session(owner_engine())
