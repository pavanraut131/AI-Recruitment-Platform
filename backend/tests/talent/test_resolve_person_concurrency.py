"""``resolve_person`` under real concurrency (spec §5.4): 20 threads, 20 connections, one barrier.

Every worker opens its **own** connection and transaction, calls ``resolve_person`` and commits,
exactly like 20 Celery workers would. That needs committed data, so these tests do not use the
rolled-back ``sync_session``: the module gets a dedicated, throwaway database in the test
container (migrated to ``head``, dropped afterwards) and every test empties the talent tables
first. The session database is never touched.

A deadlock would make Postgres abort one of the transactions (``deadlock_timeout`` is 1 s), and a
lost race would surface as a unique violation: both are exceptions in a worker, and the tests fail
on any worker exception.

Matching is per agency (EPIC-02 spec §3.4, E2-02): the last tests run the same identities in two
agencies at once (one Person per agency, never one shared) and prove that an agency never waits
on another agency's advisory locks.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.modules.talent import service
from app.modules.talent.models import IdentityConflict, Person, PersonIdentifier, PersonResume
from app.modules.talent.schemas import PersonProfile, ResolveResult
from app.platform.tenancy import DEFAULT_AGENCY_ID, bind
from tests import postgres

WORKERS = 20
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    """A throwaway database at ``head``, with a pool big enough for every worker at once."""
    from app.platform.config import settings

    name = f"e0_15_{uuid.uuid4().hex[:8]}"
    base_url = make_url(settings.SYNC_DATABASE_URL)
    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = base_url.set(database=name).render_as_string(hide_password=False)

    original = settings.SYNC_DATABASE_URL
    settings.SYNC_DATABASE_URL = url  # alembic/env.py reads it
    try:
        cfg = Config()  # no ini file: env.py would reconfigure logging
        cfg.set_main_option("script_location", str(postgres.BACKEND_DIR / "alembic"))
        command.upgrade(cfg, "head")
    finally:
        settings.SYNC_DATABASE_URL = original

    eng = create_engine(url, pool_size=WORKERS, max_overflow=0)
    try:
        yield eng
    finally:
        eng.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(autouse=True)
def empty_tables(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("TRUNCATE people, person_identifiers, identity_conflicts, person_resumes")
        )


@dataclass(frozen=True)
class Call:
    email: str | None
    phone: str | None
    resume_id: uuid.UUID
    uploaded_at: datetime
    name: str = "Asha Rao"
    agency_id: uuid.UUID = DEFAULT_AGENCY_ID


def run_concurrently(engine: Engine, calls: list[Call]) -> list[ResolveResult]:
    """Run every call in its own thread, connection and transaction, released together."""
    barrier = threading.Barrier(len(calls))
    results: list[ResolveResult | None] = [None] * len(calls)
    errors: list[BaseException] = []

    def worker(index: int, call: Call) -> None:
        try:
            with Session(engine) as session:
                bind(session, call.agency_id)
                session.connection()  # check out the connection before the start line
                barrier.wait(timeout=30)
                results[index] = service.resolve_person(
                    session,
                    agency_id=call.agency_id,
                    email=call.email,
                    phone=call.phone,
                    profile=PersonProfile(full_name=call.name),
                    resume_id=call.resume_id,
                    uploaded_at=call.uploaded_at,
                    default_region="IN",
                )
                session.commit()
        except BaseException as exc:  # reported by run_concurrently; abort frees the others
            errors.append(exc)
            barrier.abort()

    threads = [threading.Thread(target=worker, args=(i, c)) for i, c in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads), "a worker hung (lock wait?)"
    assert not errors, f"{len(errors)} worker(s) failed: {errors!r}"
    return [result for result in results if result is not None]


def calls(n: int, make: Callable[[int], tuple[str | None, str | None]]) -> list[Call]:
    out = []
    for i in range(n):
        email, phone = make(i)
        out.append(Call(email, phone, uuid.uuid4(), T0 + timedelta(minutes=i), name=f"v{i}"))
    return out


def snapshot(engine: Engine) -> dict[str, Any]:
    with Session(engine) as session:
        return {
            "people": session.execute(select(func.count()).select_from(Person)).scalar_one(),
            "identifiers": sorted(
                session.execute(
                    select(
                        PersonIdentifier.person_id,
                        PersonIdentifier.kind,
                        PersonIdentifier.value_normalized,
                        PersonIdentifier.inferred,
                    )
                ).all()
            ),
            "conflicts": sorted(
                session.execute(
                    select(
                        IdentityConflict.resume_id,
                        IdentityConflict.chosen_person_id,
                        IdentityConflict.conflicting_person_id,
                    )
                ).all()
            ),
            "links": dict(
                session.execute(select(PersonResume.resume_id, PersonResume.person_id)).all()
            ),
            "profiles": {
                p.id: (p.full_name, p.latest_resume_id)
                for p in session.execute(select(Person)).scalars()
            },
        }


# ── Same identity, 20 workers ─────────────────────────────────
EMAIL_FORMS = ["a.sharma@gmail.com", "ASharma+jobs@Gmail.com", "asharma@googlemail.com"]
PHONE_FORMS = ["+91 98765 43210", "09876543210", "98765-43210", "0091 98765 43210"]


def test_twenty_concurrent_resolutions_of_one_identity_create_one_person(engine: Engine) -> None:
    batch = calls(
        WORKERS, lambda i: (EMAIL_FORMS[i % len(EMAIL_FORMS)], PHONE_FORMS[i % len(PHONE_FORMS)])
    )

    results = run_concurrently(engine, batch)

    state = snapshot(engine)
    person_ids = {r.person_id for r in results}
    assert len(person_ids) == 1
    (person_id,) = person_ids
    assert state["people"] == 1
    assert state["identifiers"] == [
        (person_id, "email", "asharma@gmail.com", False),
        (person_id, "phone", "+919876543210", False),
    ]
    assert state["conflicts"] == []
    assert sorted(r.matched_existing for r in results) == [False] + [True] * (WORKERS - 1)
    assert state["links"] == {c.resume_id: person_id for c in batch}
    # Whatever the processing order, the latest upload's profile wins.
    assert state["profiles"][person_id] == ("v19", batch[-1].resume_id)


def test_twenty_concurrent_resolutions_of_one_email_create_one_person(engine: Engine) -> None:
    results = run_concurrently(engine, calls(WORKERS, lambda i: (EMAIL_FORMS[i % 3], None)))

    assert len({r.person_id for r in results}) == 1
    assert snapshot(engine)["people"] == 1


def test_concurrent_retries_of_one_resume_resolve_it_once(engine: Engine) -> None:
    resume_id = uuid.uuid4()
    batch = [Call("a@example.com", None, resume_id, T0) for _ in range(WORKERS)]

    results = run_concurrently(engine, batch)

    assert len(set(results)) == 1
    state = snapshot(engine)
    assert state["people"] == 1
    assert len(state["identifiers"]) == 1


# ── Cross-overs: no deadlock, deterministic final state ──────
def _seed(engine: Engine, email: str, phone: str) -> uuid.UUID:
    with Session(engine) as session:
        bind(session, DEFAULT_AGENCY_ID)
        result = service.resolve_person(
            session,
            agency_id=DEFAULT_AGENCY_ID,
            email=email,
            phone=phone,
            profile=PersonProfile(full_name="seed"),
            resume_id=uuid.uuid4(),
            uploaded_at=T0 - timedelta(days=1),
            default_region="IN",
        )
        session.commit()
    return result.person_id


def test_cross_over_identifiers_never_deadlock_and_end_in_one_state(engine: Engine) -> None:
    # A and B already exist; workers write their identifiers in every combination, crossed over
    # (A's email with B's phone and the other way round), alone, and alongside new identifiers.
    a = _seed(engine, "a@example.com", "+91 98765 43210")
    b = _seed(engine, "b@example.com", "+91 91234 56789")
    a_email, a_phone, b_email, b_phone = (
        "a@example.com",
        "+919876543210",
        "b@example.com",
        "+919123456789",
    )
    shapes: list[tuple[Callable[[int], tuple[str | None, str | None]], uuid.UUID, bool]] = [
        # (identifiers, expected person, expect a conflict)
        (lambda i: (a_email, b_phone), a, True),
        (lambda i: (b_email, a_phone), b, True),
        (lambda i: (a_email, a_phone), a, False),
        (lambda i: (b_email, "09123456789"), b, False),
        (lambda i: (a_email, None), a, False),
        (lambda i: (None, "91234-56789"), b, False),
        (lambda i: (f"new{i}@example.com", a_phone), a, False),
        (lambda i: (b_email, f"+44 20 7183 87{i:02d}"), b, False),
    ]
    batch: list[Call] = []
    expected: dict[uuid.UUID, tuple[uuid.UUID, bool]] = {}
    for i in range(WORKERS):
        make, person_id, conflict = shapes[i % len(shapes)]
        email, phone = make(i)
        call = Call(email, phone, uuid.uuid4(), T0 + timedelta(minutes=i), f"v{i}")
        batch.append(call)
        expected[call.resume_id] = (person_id, conflict)

    results = run_concurrently(engine, batch)

    for call, result in zip(batch, results, strict=True):
        person_id, conflict = expected[call.resume_id]
        assert result.person_id == person_id
        assert result.matched_existing is True
        assert (result.conflict_id is not None) is conflict

    state = snapshot(engine)
    assert state["people"] == 2
    new_emails = {
        (a, "email", f"new{i}@example.com", False) for i in range(6, WORKERS, len(shapes))
    }
    new_phones = {
        (b, "phone", f"+4420718387{i:02d}", False) for i in range(7, WORKERS, len(shapes))
    }
    assert set(state["identifiers"]) == {
        (a, "email", a_email, False),
        (a, "phone", a_phone, False),
        (b, "email", b_email, False),
        (b, "phone", b_phone, False),
        *new_emails,
        *new_phones,
    }
    assert state["conflicts"] == sorted(
        (c.resume_id, *((a, b) if c.email == a_email else (b, a)))
        for c in batch
        if expected[c.resume_id][1]
    )
    # Each person's profile comes from its latest upload, whatever order the workers ran in.
    for person_id in (a, b):
        latest = max(
            (c for c in batch if expected[c.resume_id][0] == person_id),
            key=lambda c: c.uploaded_at,
        )
        assert state["profiles"][person_id] == (latest.name, latest.resume_id)


def test_cross_over_without_existing_people_never_deadlocks(engine: Engine) -> None:
    # Nothing exists yet: email-only, phone-only and both-in-either-order workers race to create
    # the person. The outcome depends on who wins (that is inherent), but no worker may fail and
    # the invariants must hold: one owner per identifier, and at most two people.
    batch = calls(
        WORKERS,
        lambda i: [
            ("x@example.com", "+91 98765 43210"),
            ("X@example.com", "09876543210"),
            ("x@example.com", None),
            (None, "9876543210"),
        ][i % 4],
    )

    results = run_concurrently(engine, batch)

    state = snapshot(engine)
    assert 1 <= state["people"] <= 2
    assert sorted((k, v) for _, k, v, _ in state["identifiers"]) == [
        ("email", "x@example.com"),
        ("phone", "+919876543210"),
    ]
    assert len(state["links"]) == WORKERS
    assert {r.person_id for r in results} <= set(state["profiles"])


# ── Two agencies at once (E2-02) ──────────────────────────────
OTHER_AGENCY = uuid.UUID("00000000-0000-4000-8000-0000000000b2")


def test_the_same_identity_in_two_agencies_creates_one_person_per_agency(engine: Engine) -> None:
    # 20 workers, alternating agencies, every form of one email and phone.
    batch = [
        Call(
            EMAIL_FORMS[i % len(EMAIL_FORMS)],
            PHONE_FORMS[i % len(PHONE_FORMS)],
            uuid.uuid4(),
            T0 + timedelta(minutes=i),
            name=f"v{i}",
            agency_id=DEFAULT_AGENCY_ID if i % 2 == 0 else OTHER_AGENCY,
        )
        for i in range(WORKERS)
    ]

    results = run_concurrently(engine, batch)

    with Session(engine) as session:
        people = dict(session.execute(select(Person.id, Person.agency_id)).tuples().all())
        identifiers = sorted(
            session.execute(
                select(
                    PersonIdentifier.agency_id,
                    PersonIdentifier.person_id,
                    PersonIdentifier.kind,
                    PersonIdentifier.value_normalized,
                )
            ).tuples(),
            key=str,
        )
    assert sorted(people.values(), key=str) == sorted([DEFAULT_AGENCY_ID, OTHER_AGENCY], key=str)
    person_of = {agency: person for person, agency in people.items()}
    for call, result in zip(batch, results, strict=True):
        assert result.person_id == person_of[call.agency_id]
    assert identifiers == sorted(
        (
            (agency, person_of[agency], kind, value)
            for agency in (DEFAULT_AGENCY_ID, OTHER_AGENCY)
            for kind, value in (("email", "asharma@gmail.com"), ("phone", "+919876543210"))
        ),
        key=str,
    )
    # One "new person" per agency; every other call matched its own agency's Person.
    assert sorted(r.matched_existing for r in results) == [False] * 2 + [True] * (WORKERS - 2)
    assert snapshot(engine)["conflicts"] == []


def _resolve_holding_locks(session: Session, agency_id: uuid.UUID) -> None:
    service.resolve_person(
        session,
        agency_id=agency_id,
        email="asha@example.com",
        phone="+91 98765 43210",
        profile=PersonProfile(full_name="Asha"),
        resume_id=uuid.uuid4(),
        uploaded_at=T0,
        default_region="IN",
    )


def test_an_agency_never_waits_on_another_agencys_locks(engine: Engine) -> None:
    """Agency A's resolution holds its advisory locks (not committed). The same identifiers in
    agency B resolve at once; in agency A they would wait (the control)."""
    from sqlalchemy.exc import OperationalError

    with Session(engine) as holder:
        bind(holder, DEFAULT_AGENCY_ID)
        _resolve_holding_locks(holder, DEFAULT_AGENCY_ID)  # locks held until rollback

        with Session(engine) as other:
            bind(other, OTHER_AGENCY)
            other.execute(text("SET LOCAL lock_timeout = '500ms'"))
            _resolve_holding_locks(other, OTHER_AGENCY)  # would raise if it had to wait
            other.commit()

        with Session(engine) as same, pytest.raises(OperationalError, match="lock timeout"):
            bind(same, DEFAULT_AGENCY_ID)
            same.execute(text("SET LOCAL lock_timeout = '500ms'"))
            _resolve_holding_locks(same, DEFAULT_AGENCY_ID)
        holder.rollback()

    with Session(engine) as session:
        assert session.execute(select(Person.agency_id)).scalars().all() == [OTHER_AGENCY]
