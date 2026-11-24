"""talent data access: the SQL behind ``talent.service``. Private to the talent module.

``resolve_person`` and the verification reads use a synchronous ``Session``: they run in Celery
workers, the seed generator and a command, which have no event loop (see
``app.modules.ingestion.tasks``). The ``/api/v2`` people reads at the end (E1-02) take the
request's ``AsyncSession``.

**Tenancy (E2-03, E2-02).** Every query of ``resolve_person`` and the people reads is filtered to
the agency the session is bound to (``app.platform.tenancy.tenant_filter``): the identifier
lookup (:func:`find_identifiers`, per agency like ``UNIQUE(agency_id, kind, value_normalized)``),
the Person it locks and the stored resolution. The advisory-lock keys are per agency too (the
service builds them). The verification reads and the tenancy backfill read **every agency** on
purpose (operator commands, unbound sessions).
"""

import uuid
from collections.abc import Iterable, Sequence
from typing import cast

from sqlalchemy import ColumnElement, Table, any_, exists, func, or_, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.modules.talent.models import IdentityConflict, Person, PersonIdentifier, PersonResume
from app.platform import backfill
from app.platform.sql import LIKE_ESCAPE, escape_like, uuid_array
from app.platform.tenancy import AnySession, tenant_filter


def _people_of(db: AnySession) -> ColumnElement[bool]:
    return tenant_filter(db, Person.agency_id)


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
        .where(PersonResume.resume_id == resume_id, tenant_filter(session, PersonResume.agency_id))
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def find_identifiers(
    session: Session, kind_values: Sequence[tuple[str, str]]
) -> dict[tuple[str, str], PersonIdentifier]:
    """The stored identifiers of the session's agency among ``(kind, value_normalized)`` pairs,
    keyed by that pair (``UNIQUE(agency_id, kind, value_normalized)``: at most one each)."""
    if not kind_values:
        return {}
    rows = session.execute(
        select(PersonIdentifier)
        .where(
            tenant_filter(session, PersonIdentifier.agency_id),
            tuple_(PersonIdentifier.kind, PersonIdentifier.value_normalized).in_(kind_values),
        )
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
        .where(Person.id == person_id, _people_of(session))
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    ).scalar_one()


# ── Reads for the verification (spec §6.3): every agency, unbound session ──
def table_counts(session: Session) -> dict[str, int]:
    def count(model: type[Person | PersonIdentifier | IdentityConflict | PersonResume]) -> int:
        return session.execute(select(func.count()).select_from(model)).scalar_one()

    return {
        "people": count(Person),
        "identifiers": count(PersonIdentifier),
        "identity_conflicts": count(IdentityConflict),
        "resolved_resumes": count(PersonResume),
    }


def person_ids(session: Session) -> set[uuid.UUID]:
    return set(session.execute(select(Person.id)).scalars())


def resolutions(session: Session) -> dict[uuid.UUID, uuid.UUID]:
    rows = session.execute(select(PersonResume.resume_id, PersonResume.person_id)).tuples()
    return {resume_id: person_id for resume_id, person_id in rows}


def duplicate_identifiers(session: Session) -> list[tuple[uuid.UUID | None, str, str, int]]:
    columns = (PersonIdentifier.agency_id, PersonIdentifier.kind, PersonIdentifier.value_normalized)
    rows = session.execute(
        select(*columns, func.count()).group_by(*columns).having(func.count() > 1)
    ).tuples()
    return [(agency_id, kind, value, n) for agency_id, kind, value, n in rows]


def person_agencies(session: Session) -> dict[uuid.UUID, uuid.UUID | None]:
    rows = session.execute(select(Person.id, Person.agency_id)).tuples()
    return {person_id: agency_id for person_id, agency_id in rows}


def people_without_identifiers(session: Session) -> dict[uuid.UUID, list[uuid.UUID]]:
    has_identifier = select(PersonIdentifier.id).where(PersonIdentifier.person_id == Person.id)
    rows = session.execute(
        select(Person.id, PersonResume.resume_id)
        .outerjoin(PersonResume, PersonResume.person_id == Person.id)
        .where(~has_identifier.exists())
    ).tuples()
    people: dict[uuid.UUID, list[uuid.UUID]] = {}
    for person_id, resume_id in rows:
        resumes = people.setdefault(person_id, [])
        if resume_id is not None:
            resumes.append(resume_id)
    return people


def identity_conflict_resume_ids(session: Session) -> list[uuid.UUID]:
    return list(session.execute(select(IdentityConflict.resume_id)).scalars())


# ── Tenancy backfill (``app.modules.ingestion.backfill_tenancy``, E2-02): every agency ──
_TENANT_TABLES = {
    "people": cast(Table, Person.__table__),
    "person_identifiers": cast(Table, PersonIdentifier.__table__),
    "identity_conflicts": cast(Table, IdentityConflict.__table__),
    "person_resumes": cast(Table, PersonResume.__table__),
}
# talent's tenant tables, in the order the backfill fills them.
TENANCY_TABLES = tuple(_TENANT_TABLES)


def fill_tenancy_batch(
    session: Session, table: str, *, agency_id: uuid.UUID, after: uuid.UUID | None, limit: int
) -> list[uuid.UUID]:
    t = _TENANT_TABLES[table]
    return backfill.update_next_batch(
        session,
        t,
        missing=t.c.agency_id.is_(None),
        values={"agency_id": agency_id},
        after=after,
        limit=limit,
    )


def count_tenancy_gaps(session: Session, table: str) -> int:
    t = _TENANT_TABLES[table]
    return backfill.count_missing(session, t, t.c.agency_id.is_(None))


def agency_census(session: Session) -> dict[str, dict[uuid.UUID | None, int]]:
    return {
        table: backfill.agency_census(session, _TENANT_TABLES[table]) for table in TENANCY_TABLES
    }


# ── /api/v2 people (async, E1-02) ─────────────────────────────
def _search_clause(q: str) -> ColumnElement[bool]:
    """The name or any email identifier (as written or normalised) contains ``q``
    (case-insensitive, LIKE wildcards literal)."""
    pattern = f"%{escape_like(q)}%"
    email_matches = exists().where(
        PersonIdentifier.person_id == Person.id,
        PersonIdentifier.kind == "email",
        or_(
            PersonIdentifier.raw_value.ilike(pattern, escape=LIKE_ESCAPE),
            PersonIdentifier.value_normalized.ilike(pattern, escape=LIKE_ESCAPE),
        ),
    )
    return or_(Person.full_name.ilike(pattern, escape=LIKE_ESCAPE), email_matches)


async def search_people(
    db: AsyncSession, q: str | None, *, offset: int, limit: int
) -> tuple[Sequence[Person], int]:
    """A page of people (matching ``q`` if given), most recently updated first (ties by id), and
    how many match in all (two queries)."""
    clauses = [_people_of(db), *([_search_clause(q)] if q else [])]
    total = await db.scalar(select(func.count(Person.id)).where(*clauses))
    if not total:
        return [], 0
    result = await db.execute(
        select(Person)
        .where(*clauses)
        .order_by(Person.updated_at.desc(), Person.id)
        .offset(offset)
        .limit(limit)
    )
    return result.scalars().all(), total


async def first_emails(db: AsyncSession, person_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
    """``person_id -> raw value`` of each Person's first stored email (one query)."""
    if not person_ids:
        return {}
    result = await db.execute(
        select(PersonIdentifier.person_id, PersonIdentifier.raw_value)
        .where(
            PersonIdentifier.person_id == any_(uuid_array(person_ids)),
            PersonIdentifier.kind == "email",
            tenant_filter(db, PersonIdentifier.agency_id),
        )
        .order_by(PersonIdentifier.created_at, PersonIdentifier.id)
    )
    emails: dict[uuid.UUID, str] = {}
    for person_id, raw_value in result.tuples():
        emails.setdefault(person_id, raw_value)
    return emails


async def get_person(db: AsyncSession, person_id: uuid.UUID) -> Person | None:
    result = await db.execute(select(Person).where(Person.id == person_id, _people_of(db)))
    return result.scalar_one_or_none()


async def identifiers_of(db: AsyncSession, person_id: uuid.UUID) -> Sequence[PersonIdentifier]:
    """The Person's identifiers, emails first, then in the order they were stored."""
    result = await db.execute(
        select(PersonIdentifier)
        .where(
            PersonIdentifier.person_id == person_id,
            tenant_filter(db, PersonIdentifier.agency_id),
        )
        .order_by(PersonIdentifier.kind, PersonIdentifier.created_at, PersonIdentifier.id)
    )
    return result.scalars().all()


async def conflicts_of(db: AsyncSession, person_id: uuid.UUID) -> Sequence[IdentityConflict]:
    """The identity conflicts this Person is on either side of, oldest first."""
    result = await db.execute(
        select(IdentityConflict)
        .where(
            or_(
                IdentityConflict.chosen_person_id == person_id,
                IdentityConflict.conflicting_person_id == person_id,
            ),
            tenant_filter(db, IdentityConflict.agency_id),
        )
        .order_by(IdentityConflict.created_at, IdentityConflict.id)
    )
    return result.scalars().all()


async def count_people(db: AsyncSession) -> int:
    return await db.scalar(select(func.count(Person.id)).where(_people_of(db))) or 0
