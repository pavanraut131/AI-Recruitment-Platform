"""Keyset-paginated batch updates for online backfills (EPIC-02 spec §7.1 phase 2).

Each module fills its own tables with these helpers (a module never writes another's tables);
``app.modules.ingestion.backfill_tenancy`` drives them one batch, and one transaction, at a time.

A batch is the next ``limit`` rows, in primary-key order after the previous batch's last key, for
which ``missing`` holds; ``missing`` is repeated in the ``UPDATE``'s own ``WHERE``, so a row
another transaction filled in the meantime is left alone. A run therefore converges: rows already
filled are never selected again, and a new run (after a stop or a crash) starts at the first row
still missing its value, without a checkpoint table.
"""

from collections.abc import Mapping
from typing import Any

from sqlalchemy import ColumnElement, Table, func, select, update
from sqlalchemy.orm import Session


def _key(table: Table) -> Any:
    (key,) = table.primary_key.columns  # every table filled this way has a one-column key
    return key


def update_next_batch(
    db: Session,
    table: Table,
    *,
    missing: ColumnElement[bool],
    values: Mapping[str, Any],
    after: Any | None,
    limit: int,
) -> list[Any]:
    """Set ``values`` on the next ``limit`` rows of ``table`` (by primary key, after ``after``)
    for which ``missing`` holds. Returns the keys of the rows it updated, ascending: empty when
    there are none left after ``after``. Columns with an ``onupdate`` default (``updated_at``)
    are left as they are. Flushes nothing else and does not commit.

    One statement::

        UPDATE t SET ... WHERE <missing>
          AND id IN (SELECT id FROM t WHERE <missing> AND id > :after ORDER BY id LIMIT :limit)
        RETURNING id
    """
    key = _key(table)
    page = select(key).where(missing).order_by(key).limit(limit)
    if after is not None:
        page = page.where(key > after)
    # A backfill is not an edit: columns with an ``onupdate`` (``updated_at``) keep their value.
    # Without this, Core would set them to now() and reorder every "recently updated" list.
    keep = {c.name: c for c in table.columns if c.onupdate is not None and c.name not in values}
    stmt = (
        update(table)
        .where(key.in_(page.scalar_subquery()), missing)
        .values({**values, **keep})
        .returning(key)
        .execution_options(synchronize_session=False)
    )
    return sorted(db.execute(stmt).scalars())


def count_missing(db: Session, table: Table, missing: ColumnElement[bool]) -> int:
    """How many rows of ``table`` still match ``missing``."""
    return db.execute(select(func.count()).select_from(table).where(missing)).scalar_one()


def agency_census(db: Session, table: Table) -> dict[Any, int]:
    """``agency_id -> rows`` of ``table`` (``None`` counts the rows without one)."""
    column = table.c.agency_id
    rows = db.execute(select(column, func.count()).group_by(column)).tuples()
    return {agency_id: n for agency_id, n in rows}
