"""Contract: the tenant key is required, per-agency uniques and composite foreign keys (E2-02)

Phase 4 of the tenancy migration (EPIC-02 spec §7.1). Runs after the backfill
(``python -m app.modules.ingestion.backfill_tenancy``) and the verification
(``verify_people_applications``); runbook ``docs/runbooks/tenancy-backfill.md``. It **refuses
to run** while any tenant row has no ``agency_id`` (or a campaign no ``client_id``), naming the
tables, before it changes anything.

What it does
    - ``agency_id`` ``NOT NULL`` on ``campaigns``, ``applications``, ``people``,
      ``person_identifiers``, ``identity_conflicts``, ``person_resumes`` and ``resumes``, and
      ``campaigns.client_id`` ``NOT NULL``.
    - ``UNIQUE(agency_id, id)`` on ``campaigns`` and ``people``: the targets of the composite
      foreign keys (``clients`` has had its own since E2-01).
    - Composite, same-module foreign keys, so a child always has its parent's agency:
      ``campaigns (agency_id, client_id) -> clients``; ``applications (agency_id, campaign_id)
      -> campaigns`` (``ON DELETE CASCADE``); ``(agency_id, person_id) -> people`` from
      ``person_identifiers`` and ``person_resumes``, and ``(agency_id, chosen_person_id)`` /
      ``(agency_id, conflicting_person_id)`` from ``identity_conflicts`` (all ``ON DELETE
      CASCADE``). Each **replaces** the single-column key it extends.
    - ``person_identifiers``: ``UNIQUE(agency_id, kind, value_normalized)`` replaces ``UNIQUE(kind,
      value_normalized)``: matching is per agency (spec §3.4).
    - Indexes: ``campaigns (agency_id, status, updated_at)``, ``people (agency_id, updated_at)``
      and ``resumes (agency_id, campaign_id)`` (the last closes the backlog item "Index on
      ``resumes.campaign_id``").

How, without long locks (Postgres 16)
    The statements that need an ``ACCESS EXCLUSIVE`` lock change only the catalog, and run in
    short transactions with ``lock_timeout`` 5 s, so the migration fails fast (and can simply
    be run again) instead of queueing every query behind a long-running one. Everything that
    reads a whole table runs in ``autocommit_block()``s, one statement per transaction, under
    ``SHARE UPDATE EXCLUSIVE`` locks that let reads and writes go on:

    1. *Transaction:* the backfill check; ``ADD CONSTRAINT ck_<table>_<column>_not_null CHECK
       (<column> IS NOT NULL) NOT VALID`` (catalog only).
    2. *Autocommit:* ``VALIDATE CONSTRAINT`` for each check (scans the table, writes go on);
       ``CREATE [UNIQUE] INDEX CONCURRENTLY`` for the three unique targets and the three
       indexes.
    3. *Transaction:* ``SET NOT NULL`` (no scan: Postgres uses the validated check as proof),
       then the checks are dropped; the unique indexes become constraints with ``ADD CONSTRAINT
       ... UNIQUE USING INDEX``; the old ``(kind, value_normalized)`` unique is dropped; the
       composite foreign keys are added ``NOT VALID`` (they hold for every new row at once).
    4. *Autocommit:* ``VALIDATE CONSTRAINT`` for each foreign key (``SHARE UPDATE EXCLUSIVE`` on
       the child, ``ROW SHARE`` on the parent).
    5. *Transaction:* the single-column foreign keys they replace are dropped.

    Each step checks the catalog first, so after a failure (a lock timeout, a cancelled
    ``CONCURRENTLY`` build, which leaves an invalid index behind) running ``alembic upgrade
    head`` again finishes the job: an invalid index is dropped and rebuilt, finished steps are
    skipped. Because of the autocommit blocks, steps before the failure stay applied; that is
    harmless (they are what the next run would do first), and the revision only moves when
    everything succeeded.

Downgrade
    One transaction (lock_timeout 5 s): the single-column foreign keys and ``UNIQUE(kind,
    value_normalized)`` come back, the composite keys, per-agency uniques and indexes go, and
    the columns become nullable again; the values stay. It re-validates the restored keys and
    builds the old unique index under lock, so it is for a rollback, not for routine use. It
    refuses (naming one value) when two agencies store the same identifier, which the old
    global unique cannot hold.

Revision ID: e2b02c7a4d19
Revises: e2a01d5c8f36
Create Date: 2026-11-24 09:00:00.000000
"""

from collections.abc import Sequence
from typing import NamedTuple

import sqlalchemy as sa
from alembic import op

revision: str = "e2b02c7a4d19"
down_revision: str | None = "e2a01d5c8f36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"

TENANT_TABLES = (
    "campaigns",
    "applications",
    "people",
    "person_identifiers",
    "identity_conflicts",
    "person_resumes",
    "resumes",
)
# (table, column) made NOT NULL.
REQUIRED = (*((table, "agency_id") for table in TENANT_TABLES), ("campaigns", "client_id"))


class Index(NamedTuple):
    table: str
    name: str
    columns: tuple[str, ...]
    unique: bool  # unique ones are attached as constraints of the same name


INDEXES = (
    Index("campaigns", "uq_campaigns_agency_id_id", ("agency_id", "id"), True),
    Index("people", "uq_people_agency_id_id", ("agency_id", "id"), True),
    Index(
        "person_identifiers",
        "uq_person_identifiers_agency_id_kind_value_normalized",
        ("agency_id", "kind", "value_normalized"),
        True,
    ),
    Index(
        "campaigns",
        "ix_campaigns_agency_id_status_updated_at",
        ("agency_id", "status", "updated_at"),
        False,
    ),
    Index("people", "ix_people_agency_id_updated_at", ("agency_id", "updated_at"), False),
    Index("resumes", "ix_resumes_agency_id_campaign_id", ("agency_id", "campaign_id"), False),
)

# The global unique that the per-agency one replaces.
OLD_UNIQUE = Index(
    "person_identifiers",
    "uq_person_identifiers_kind_value_normalized",
    ("kind", "value_normalized"),
    True,
)


class ForeignKey(NamedTuple):
    table: str
    name: str
    column: str  # the column besides agency_id
    parent: str
    on_delete: str | None
    replaces: str | None  # the single-column key (Postgres' default name) it replaces


FOREIGN_KEYS = (
    ForeignKey(
        "campaigns", "fk_campaigns_agency_id_client_id_clients", "client_id", "clients", None, None
    ),
    ForeignKey(
        "applications",
        "fk_applications_agency_id_campaign_id_campaigns",
        "campaign_id",
        "campaigns",
        "CASCADE",
        "applications_campaign_id_fkey",
    ),
    ForeignKey(
        "person_identifiers",
        "fk_person_identifiers_agency_id_person_id_people",
        "person_id",
        "people",
        "CASCADE",
        "person_identifiers_person_id_fkey",
    ),
    ForeignKey(
        "identity_conflicts",
        "fk_identity_conflicts_agency_id_chosen_person_id_people",
        "chosen_person_id",
        "people",
        "CASCADE",
        "identity_conflicts_chosen_person_id_fkey",
    ),
    ForeignKey(
        "identity_conflicts",
        "fk_identity_conflicts_agency_id_conflicting_person_id_people",
        "conflicting_person_id",
        "people",
        "CASCADE",
        "identity_conflicts_conflicting_person_id_fkey",
    ),
    ForeignKey(
        "person_resumes",
        "fk_person_resumes_agency_id_person_id_people",
        "person_id",
        "people",
        "CASCADE",
        "person_resumes_person_id_fkey",
    ),
)

BACKFILL_HINT = (
    "Run `python -m app.modules.ingestion.backfill_tenancy` until it reports `complete`, then "
    "`python -m app.modules.ingestion.verify_people_applications`, then upgrade again "
    "(docs/runbooks/tenancy-backfill.md)."
)


class BackfillIncompleteError(RuntimeError):
    """Tenant rows without an agency (or campaigns without a client): the backfill has not
    completed, so the contract cannot run."""


# ── Catalog helpers ───────────────────────────────────────────
def _scalar(sql: str, **params: object) -> object:
    return op.get_bind().execute(sa.text(sql), params).scalar()


def _validated(table: str, name: str) -> bool | None:
    """Whether the constraint ``name`` of ``table`` is validated; ``None`` when it does not
    exist."""
    validated = _scalar(
        "SELECT convalidated FROM pg_constraint "
        "WHERE conrelid = CAST(:table AS regclass) AND conname = :name",
        table=table,
        name=name,
    )
    return None if validated is None else bool(validated)


def _is_not_null(table: str, column: str) -> bool:
    return bool(
        _scalar(
            "SELECT attnotnull FROM pg_attribute "
            "WHERE attrelid = CAST(:table AS regclass) AND attname = :column",
            table=table,
            column=column,
        )
    )


def _index_valid(name: str) -> bool | None:
    """Whether the index ``name`` is valid; ``None`` when it does not exist."""
    valid = _scalar(
        "SELECT i.indisvalid FROM pg_index AS i JOIN pg_class AS c ON c.oid = i.indexrelid "
        "WHERE c.relname = :name AND c.relnamespace = 'public'::regnamespace",
        name=name,
    )
    return None if valid is None else bool(valid)


def _check_name(table: str, column: str) -> str:
    return f"ck_{table}_{column}_not_null"


def _lock_timeout() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")


# ── Upgrade ───────────────────────────────────────────────────
def _refuse_unless_backfilled() -> None:
    missing = []
    for table, column in REQUIRED:
        if _is_not_null(table, column):
            continue
        rows = _scalar(f"SELECT count(*) FROM {table} WHERE {column} IS NULL")
        if rows:
            missing.append(f"{table}: {rows} row(s) without {column}")
    if missing:
        raise BackfillIncompleteError(
            "The tenancy backfill has not completed, so the contract cannot run: "
            + "; ".join(missing)
            + ". "
            + BACKFILL_HINT
        )


def upgrade() -> None:
    # 1. Transaction: refuse without a finished backfill; checks NOT VALID (catalog only).
    _lock_timeout()
    _refuse_unless_backfilled()
    for table, column in REQUIRED:
        name = _check_name(table, column)
        if not _is_not_null(table, column) and _validated(table, name) is None:
            op.execute(
                f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({column} IS NOT NULL) NOT VALID"
            )

    # 2. Autocommit: validate the checks and build the indexes, reads and writes going on.
    with op.get_context().autocommit_block():
        for table, column in REQUIRED:
            name = _check_name(table, column)
            if _validated(table, name) is False:
                op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")
        for index in INDEXES:
            _build_concurrently(index)

    # 3. Transaction: NOT NULL, uniques from their indexes, composite keys NOT VALID.
    _lock_timeout()
    for table, column in REQUIRED:
        if not _is_not_null(table, column):
            op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} SET NOT NULL")
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {_check_name(table, column)}")
    for index in INDEXES:
        if index.unique and _validated(index.table, index.name) is None:
            op.execute(
                f"ALTER TABLE {index.table} ADD CONSTRAINT {index.name} UNIQUE USING INDEX "
                f"{index.name}"
            )
    op.execute(f"ALTER TABLE {OLD_UNIQUE.table} DROP CONSTRAINT IF EXISTS {OLD_UNIQUE.name}")
    for fk in FOREIGN_KEYS:
        if _validated(fk.table, fk.name) is None:
            on_delete = f" ON DELETE {fk.on_delete}" if fk.on_delete else ""
            op.execute(
                f"ALTER TABLE {fk.table} ADD CONSTRAINT {fk.name} "
                f"FOREIGN KEY (agency_id, {fk.column}) REFERENCES {fk.parent} (agency_id, id)"
                f"{on_delete} NOT VALID"
            )

    # 4. Autocommit: validate the composite keys against the existing rows.
    with op.get_context().autocommit_block():
        for fk in FOREIGN_KEYS:
            if _validated(fk.table, fk.name) is False:
                op.execute(f"ALTER TABLE {fk.table} VALIDATE CONSTRAINT {fk.name}")

    # 5. Transaction: drop the single-column keys the composite ones replace.
    _lock_timeout()
    for fk in FOREIGN_KEYS:
        if fk.replaces is not None:
            op.execute(f"ALTER TABLE {fk.table} DROP CONSTRAINT IF EXISTS {fk.replaces}")


def _build_concurrently(index: Index) -> None:
    """``CREATE [UNIQUE] INDEX CONCURRENTLY``, unless a valid one exists; an invalid one (left by
    a failed or cancelled build) is dropped first. Must run in an autocommit block."""
    valid = _index_valid(index.name)
    if valid:
        return
    if valid is False:
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index.name}")
    unique = "UNIQUE " if index.unique else ""
    op.execute(
        f"CREATE {unique}INDEX CONCURRENTLY {index.name} ON {index.table} "
        f"({', '.join(index.columns)})"
    )


# ── Downgrade ─────────────────────────────────────────────────
def downgrade() -> None:
    _lock_timeout()
    shared = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT kind, value_normalized FROM person_identifiers "
                "GROUP BY kind, value_normalized HAVING count(*) > 1 LIMIT 1"
            )
        )
        .first()
    )
    if shared is not None:
        raise RuntimeError(
            f"Cannot downgrade: the {shared[0]} {shared[1]!r} is stored in more than one agency, "
            "and the global UNIQUE(kind, value_normalized) it restores cannot hold that."
        )

    for fk in FOREIGN_KEYS:
        if fk.replaces is not None:
            on_delete = f" ON DELETE {fk.on_delete}" if fk.on_delete else ""
            op.execute(
                f"ALTER TABLE {fk.table} ADD CONSTRAINT {fk.replaces} FOREIGN KEY ({fk.column}) "
                f"REFERENCES {fk.parent} (id){on_delete}"
            )
        op.execute(f"ALTER TABLE {fk.table} DROP CONSTRAINT {fk.name}")
    op.execute(
        f"ALTER TABLE {OLD_UNIQUE.table} ADD CONSTRAINT {OLD_UNIQUE.name} "
        f"UNIQUE ({', '.join(OLD_UNIQUE.columns)})"
    )
    for index in reversed(INDEXES):
        if index.unique:
            op.execute(f"ALTER TABLE {index.table} DROP CONSTRAINT {index.name}")
        else:
            op.execute(f"DROP INDEX {index.name}")
    for table, column in REQUIRED:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} DROP NOT NULL")
