"""talent data access: the SQL behind ``talent.service``. Private to the talent module.

Synchronous ``Session`` only: ``resolve_person`` runs in Celery workers and the backfill, which
have no event loop (see ``app.modules.ingestion.tasks``).
"""

import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import select, text, tuple_
from sqlalchemy.orm import Session

from app.modules.talent.models import Person, PersonIdentifier, PersonResume


def take_advisory_locks(session: Session, keys: Iterable[str]) -> list[int]:
    """Take ``pg_advisory_xact_lock(hashtext(key))`` for every key, in ascending lock-id order.

    The locks are held until the caller's transaction ends (commit or rollback). They are taken
    in order of the **lock id** (the ``hashtext`` value), not of the key text: two different keys
    can hash to the same id, and only a single global order over the ids actually locked rules out
    a lock-order deadlock between two transactions (spec §5.4). Duplicate ids are locked once.
    Returns the ids, in the order they were locked.
    """
    key_list = sorted(set(keys))
    if not key_list:
        return []
    lock_ids = sorted(
        set(
            session.execute(
                text("SELECT hashtext(k) FROM unnest(CAST(:keys AS text[])) AS k"),
                {"keys": key_list},
            ).scalars()
        )
    )
    for lock_id in lock_ids:
        session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": lock_id})
    return lock_ids


def get_person_resume(session: Session, resume_id: uuid.UUID) -> PersonResume | None:
    return session.execute(
        select(PersonResume)
        .where(PersonResume.resume_id == resume_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def find_identifiers(
    session: Session, kind_values: Sequence[tuple[str, str]]
) -> dict[tuple[str, str], PersonIdentifier]:
    """The stored identifiers among ``(kind, value_normalized)`` pairs, keyed by that pair."""
    if not kind_values:
        return {}
    rows = session.execute(
        select(PersonIdentifier)
        .where(tuple_(PersonIdentifier.kind, PersonIdentifier.value_normalized).in_(kind_values))
        .execution_options(populate_existing=True)
    ).scalars()
    return {(row.kind, row.value_normalized): row for row in rows}


def lock_person(session: Session, person_id: uuid.UUID) -> Person:
    """Load a Person with ``FOR NO KEY UPDATE`` (fresh from the database).

    ``NO KEY UPDATE`` rather than ``UPDATE``: it still serialises two profile refreshes of the
    same Person, but does not block the ``FOR KEY SHARE`` lock that inserting a row with a
    foreign key to this Person takes (an identifier, a conflict naming it).
    """
    return session.execute(
        select(Person)
        .where(Person.id == person_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    ).scalar_one()
