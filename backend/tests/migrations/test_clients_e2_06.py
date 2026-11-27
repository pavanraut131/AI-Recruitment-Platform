"""E2-06 migration (``e2d06b8c4f17``): every agency without a client gets one named "General",
and nothing else changes; upgrade -> downgrade -> upgrade on a throwaway database (as the owner
role, like the CI ``migrations`` job), then ``alembic check``.
"""

from __future__ import annotations

import uuid

from alembic import command
from sqlalchemy import Connection, create_engine, text

from app.platform.tenancy import DEFAULT_AGENCY_ID, DEFAULT_CLIENT_ID
from tests.migrations.test_expand_e0_14 import (  # noqa: F401 (scratch_db is a fixture)
    _alembic_config,
    scratch_db,
)

BEFORE = "e2c04a7f3b18"
REVISION = "e2d06b8c4f17"

EMPTY = uuid.UUID("aaaaaaaa-0000-4000-8000-0000000000e1")  # an agency with no client
SERVED = uuid.UUID("aaaaaaaa-0000-4000-8000-0000000000e2")  # one with an archived client


def _agencies(conn: Connection) -> None:
    for agency_id, slug in ((EMPTY, "empty"), (SERVED, "served")):
        conn.execute(
            text(
                "INSERT INTO agencies (id, name, slug, status) VALUES (:id, :slug, :slug, 'active')"
            ),
            {"id": agency_id, "slug": slug},
        )
    conn.execute(
        text(
            "INSERT INTO clients (id, agency_id, name, status) "
            "VALUES (gen_random_uuid(), :agency, 'Old client', 'archived')"
        ),
        {"agency": SERVED},
    )


def _clients(conn: Connection) -> dict[uuid.UUID, list[tuple[str, str]]]:
    rows = conn.execute(
        text("SELECT agency_id, name, status FROM clients ORDER BY agency_id, name")
    ).all()
    found: dict[uuid.UUID, list[tuple[str, str]]] = {}
    for agency_id, name, status in rows:
        found.setdefault(agency_id, []).append((name, status))
    return found


def test_upgrade_downgrade_upgrade(scratch_db: str) -> None:  # noqa: F811
    cfg = _alembic_config()
    engine = create_engine(scratch_db)
    try:
        command.upgrade(cfg, BEFORE)
        with engine.begin() as conn:
            _agencies(conn)
            general = conn.execute(
                text("SELECT id FROM clients WHERE agency_id = :a"), {"a": DEFAULT_AGENCY_ID}
            ).scalar_one()
            assert general == DEFAULT_CLIENT_ID

        command.upgrade(cfg, REVISION)
        with engine.connect() as conn:
            after = _clients(conn)
        assert after == {
            DEFAULT_AGENCY_ID: [("General", "active")],  # had one: unchanged
            EMPTY: [("General", "active")],  # had none: given one
            SERVED: [("Old client", "archived")],  # had one, even archived: unchanged
        }

        command.downgrade(cfg, BEFORE)  # keeps the clients (campaigns may refer to them)
        with engine.connect() as conn:
            assert _clients(conn) == after
        command.upgrade(cfg, "head")  # idempotent: nothing new
        with engine.connect() as conn:
            assert _clients(conn) == after
        command.check(cfg)
    finally:
        engine.dispose()
