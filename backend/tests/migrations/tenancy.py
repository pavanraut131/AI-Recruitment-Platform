"""Helpers for migration tests that start below the tenancy contract (E2-02, ``e2b02c7a4d19``).

Rows written at a revision before the E2-01 expand have no ``agency_id``, and the contract
refuses to run until the tenancy backfill has given them one. These tests therefore reach
``head`` the way the runbook does: up to the expand, the backfill, then the contract.
"""

from __future__ import annotations

import io

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.modules.ingestion import backfill_tenancy

EXPAND = "e2a01d5c8f36"
CONTRACT = "e2b02c7a4d19"


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
