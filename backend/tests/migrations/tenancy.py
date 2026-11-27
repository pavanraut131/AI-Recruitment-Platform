"""Helpers for migration tests that start below the tenancy contract (E2-02, ``e2b02c7a4d19``),
or check a revision below E2-12's (``e2f12a6c9d40``).

Rows written at a revision before the E2-01 expand have no ``agency_id``, and the contract
refuses to run until the tenancy backfill has given them one. These tests therefore reach
``head`` the way the runbook does: up to the expand, the backfill, then the contract.

Below E2-12, ``legacy_candidate_map`` still exists: it was on the row-level-security allowlist
(``app.platform.rls.GLOBAL_TABLES``) until E2-12 dropped it with ``/api/v1`` and took it off.
"""

from __future__ import annotations

import io

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine
from sqlalchemy.orm import sessionmaker

from app.modules.ingestion import backfill_tenancy
from app.platform import rls

EXPAND = "e2a01d5c8f36"
CONTRACT = "e2b02c7a4d19"
DROP_LEGACY_MAP = "e2f12a6c9d40"
# Tables of the earlier revisions that E2-12 dropped.
DROPPED_BY_E2_12 = frozenset({"legacy_candidate_map"})


def backfill(url: str) -> backfill_tenancy.Summary:
    """Run the tenancy backfill on the database at ``url`` (at the expand revision or later)."""
    engine = create_engine(url)
    try:
        return backfill_tenancy.run(sessionmaker(bind=engine), out=io.StringIO())
    finally:
        engine.dispose()


def upgrade_to_head_through_backfill(cfg: Config, url: str) -> None:
    """``alembic upgrade head`` with the tenancy backfill run between the expand and the
    contract (EPIC-02 spec §7.1: expand -> backfill -> contract)."""
    command.upgrade(cfg, EXPAND)
    assert backfill(url).complete
    command.upgrade(cfg, "head")


def catalogue_problems_before_e2_12(conn: Connection) -> list[str]:
    """``rls.catalogue_problems`` at a revision below E2-12's, where ``legacy_candidate_map``
    still exists: the global table it was until E2-12 (no policies, the app role's grants)."""
    legacy = "legacy_candidate_map"
    return [p for p in rls.catalogue_problems(conn) if not p.startswith(f"{legacy}: ")]
