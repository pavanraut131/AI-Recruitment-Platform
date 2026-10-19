"""``talent.service.resolve_person`` on real Postgres: spec §5.3 rules, profile refresh, the
phone policy (ADR 0004 amendment, option A), idempotency and the unique-violation fallback.

Each test runs in a rolled-back transaction (``sync_session``). Concurrency is covered in
``test_resolve_person_concurrency.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.modules.talent import repository, service
from app.modules.talent.models import IdentityConflict, Person, PersonIdentifier, PersonResume
from app.modules.talent.schemas import PersonProfile, ResolveResult

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)

# An Indian mobile, an Indian (Bengaluru) landline, and a US number.
IN_MOBILE = "+919876543210"
IN_LANDLINE = "+918022223333"
US_NUMBER = "+14155552671"
US_AS_INDIAN_LANDLINE = "+914155552671"  # "(415) 555-2671" read with region IN


def resolve(
    session: Session,
    *,
    email: str | None = None,
    phone: str | None = None,
    profile: PersonProfile | None = None,
    resume_id: uuid.UUID | None = None,
    uploaded_at: datetime = T0,
    region: str = "IN",
) -> ResolveResult:
    return service.resolve_person(
        session,
        email=email,
        phone=phone,
        profile=profile or PersonProfile(full_name="Asha Rao"),
        resume_id=resume_id or uuid.uuid4(),
        uploaded_at=uploaded_at,
        default_region=region,
    )


def count(session: Session, model: Any) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


def identifiers(session: Session) -> set[tuple[uuid.UUID, str, str, bool]]:
    rows = session.execute(select(PersonIdentifier)).scalars()
    return {(r.person_id, r.kind, r.value_normalized, r.inferred) for r in rows}


def person(session: Session, person_id: uuid.UUID) -> Person:
    session.expire_all()
    return session.get_one(Person, person_id)


class RecordingLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    warning = info

    def names(self) -> list[str]:
        return [name for name, _ in self.events]


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> RecordingLogger:
    recorder = RecordingLogger()
    monkeypatch.setattr(service, "logger", recorder)
    return recorder


# ── Spec §5.3: the four rows ──────────────────────────────────
def test_no_match_creates_a_person_with_its_identifiers(sync_session: Session) -> None:
    resume_id = uuid.uuid4()
    result = resolve(
        sync_session,
        email="  A.Sharma+jobs@Gmail.com ",
        phone="+91 98765 43210",
        resume_id=resume_id,
    )

    assert result.matched_existing is False
    assert result.conflict_id is None
    assert identifiers(sync_session) == {
        (result.person_id, "email", "asharma@gmail.com", False),
        (result.person_id, "phone", IN_MOBILE, False),
    }
    raw = sync_session.execute(
        select(PersonIdentifier.raw_value, PersonIdentifier.source_resume_id)
    ).all()
    assert set(raw) == {("A.Sharma+jobs@Gmail.com", resume_id), ("+91 98765 43210", resume_id)}
    p = person(sync_session, result.person_id)
    assert p.full_name == "Asha Rao"
    assert p.latest_resume_id == resume_id
    link = sync_session.get_one(PersonResume, resume_id)
    assert (link.person_id, link.uploaded_at, link.matched_existing) == (
        result.person_id,
        T0,
        False,
    )


def test_email_match_attaches_and_adds_the_new_phone(sync_session: Session) -> None:
    first = resolve(sync_session, email="asharma@gmail.com")
    second = resolve(sync_session, email="A.Sharma@gmail.com", phone="09876543210")

    assert second == ResolveResult(person_id=first.person_id, matched_existing=True)
    assert count(sync_session, Person) == 1
    assert identifiers(sync_session) == {
        (first.person_id, "email", "asharma@gmail.com", False),
        (first.person_id, "phone", IN_MOBILE, False),
    }


def test_phone_match_attaches_and_adds_the_new_email(sync_session: Session) -> None:
    first = resolve(sync_session, email="old@example.com", phone="+91 98765 43210")
    second = resolve(sync_session, email="new@example.com", phone="98765-43210")

    assert second.person_id == first.person_id
    assert second.matched_existing is True
    assert count(sync_session, Person) == 1
    assert {v for _, k, v, _ in identifiers(sync_session) if k == "email"} == {
        "old@example.com",
        "new@example.com",
    }


def test_email_and_phone_matching_the_same_person_attach_without_new_rows(
    sync_session: Session,
) -> None:
    first = resolve(sync_session, email="a@example.com", phone=IN_MOBILE)
    second = resolve(sync_session, email="A@Example.com", phone="+91-98765-43210")

    assert second == ResolveResult(person_id=first.person_id, matched_existing=True)
    assert len(identifiers(sync_session)) == 2
    assert count(sync_session, IdentityConflict) == 0


def test_email_and_phone_matching_different_people_go_to_the_email_match(
    sync_session: Session,
) -> None:
    a = resolve(sync_session, email="a@example.com")
    b = resolve(sync_session, email="b@example.com", phone=IN_MOBILE)
    resume_id = uuid.uuid4()

    result = resolve(sync_session, email="a@example.com", phone=IN_MOBILE, resume_id=resume_id)

    assert result.person_id == a.person_id
    assert result.matched_existing is True
    assert result.conflict_id is not None
    conflict = sync_session.get_one(IdentityConflict, result.conflict_id)
    assert (conflict.resume_id, conflict.chosen_person_id, conflict.conflicting_person_id) == (
        resume_id,
        a.person_id,
        b.person_id,
    )
    assert "email" in conflict.reason
    assert conflict.resolved_at is None
    # Never merged: both people stay, and the phone stays with B.
    assert count(sync_session, Person) == 2
    assert identifiers(sync_session) == {
        (a.person_id, "email", "a@example.com", False),
        (b.person_id, "email", "b@example.com", False),
        (b.person_id, "phone", IN_MOBILE, False),
    }
    assert sync_session.get_one(PersonResume, resume_id).conflict_id == result.conflict_id


@pytest.mark.parametrize(
    ("email", "phone"),
    [
        pytest.param(None, None, id="none"),
        pytest.param("not an email", "n/a", id="unusable"),
        pytest.param("", "12345", id="empty-and-too-short"),
    ],
)
def test_no_usable_identifiers_creates_a_new_person_each_time(
    sync_session: Session, email: str | None, phone: str | None
) -> None:
    first = resolve(sync_session, email=email, phone=phone)
    second = resolve(sync_session, email=email, phone=phone)

    assert first.person_id != second.person_id
    assert not first.matched_existing and not second.matched_existing
    assert count(sync_session, Person) == 2
    assert count(sync_session, PersonIdentifier) == 0


def test_an_overlong_email_is_discarded(sync_session: Session, log: RecordingLogger) -> None:
    result = resolve(sync_session, email=f"{'a' * 320}@example.com", phone=IN_MOBILE)

    assert identifiers(sync_session) == {(result.person_id, "phone", IN_MOBILE, False)}
    assert "identifier_too_long_discarded" in log.names()


# ── Profile refresh ───────────────────────────────────────────
FULL_PROFILE = PersonProfile(
    full_name="Asha Rao",
    github_url="https://github.com/asha",
    linkedin_url="https://linkedin.com/in/asha",
    skills=["Python"],
    education=[{"degree": "BTech"}],
    work_experience=[{"company": "Acme"}],
    certifications=["AWS"],
    projects=[{"name": "Parser"}],
    years_of_experience=3.0,
)


def test_a_newer_resume_refreshes_the_profile_and_keeps_missing_fields(
    sync_session: Session,
) -> None:
    first = resolve(sync_session, email="a@example.com", profile=FULL_PROFILE, uploaded_at=T0)
    newer_id = uuid.uuid4()
    resolve(
        sync_session,
        email="a@example.com",
        profile=PersonProfile(full_name="Asha R. Rao", skills=["Python", "Go"], github_url=""),
        resume_id=newer_id,
        uploaded_at=T0 + timedelta(days=1),
    )

    p = person(sync_session, first.person_id)
    assert p.latest_resume_id == newer_id
    assert p.full_name == "Asha R. Rao"
    assert p.skills == ["Python", "Go"]
    # Missing in the newer extraction (None, "" or []): previous values kept.
    assert p.github_url == "https://github.com/asha"
    assert p.linkedin_url == FULL_PROFILE.linkedin_url
    assert p.education == [{"degree": "BTech"}]
    assert p.work_experience == [{"company": "Acme"}]
    assert p.certifications == ["AWS"]
    assert p.projects == [{"name": "Parser"}]
    assert p.years_of_experience == 3.0


def test_an_older_resume_processed_late_never_overwrites_a_newer_profile(
    sync_session: Session,
) -> None:
    newer_id = uuid.uuid4()
    newer = resolve(
        sync_session,
        email="a@example.com",
        profile=PersonProfile(full_name="Asha R. Rao", skills=["Go"]),
        resume_id=newer_id,
        uploaded_at=T0 + timedelta(days=5),
    )
    older = resolve(
        sync_session,
        email="a@example.com",
        profile=FULL_PROFILE,
        uploaded_at=T0,
    )

    assert older.person_id == newer.person_id
    p = person(sync_session, newer.person_id)
    assert p.latest_resume_id == newer_id
    assert p.full_name == "Asha R. Rao"
    assert p.skills == ["Go"]
    # Fields the newer resume lacked are filled from the older one, as if processed in order.
    assert p.github_url == FULL_PROFILE.github_url
    assert p.years_of_experience == 3.0
    assert p.education == [{"degree": "BTech"}]


def test_profile_order_is_by_upload_time_then_resume_id(sync_session: Session) -> None:
    low, high = sorted([uuid.uuid4(), uuid.uuid4()])
    resolve(
        sync_session,
        email="a@example.com",
        profile=PersonProfile(full_name="High"),
        resume_id=high,
    )
    result = resolve(
        sync_session,
        email="a@example.com",
        profile=PersonProfile(full_name="Low"),
        resume_id=low,
    )

    p = person(sync_session, result.person_id)
    assert (p.full_name, p.latest_resume_id) == ("High", high)


def test_profile_strings_are_fitted_to_their_columns(sync_session: Session) -> None:
    result = resolve(
        sync_session,
        profile=PersonProfile(full_name="N" * 300, github_url="https://g/" + "x" * 600),
    )

    p = person(sync_session, result.person_id)
    assert p.full_name == "N" * 255
    assert p.github_url is None
    assert p.skills == []


@pytest.mark.parametrize("source", [None, uuid.uuid4()], ids=["no-source", "unrecorded-source"])
def test_a_person_whose_profile_source_is_unknown_takes_the_next_resume(
    sync_session: Session, source: uuid.UUID | None
) -> None:
    # A Person written outside resolve_person (no person_resumes row for its source).
    existing = Person(full_name="Old", latest_resume_id=source)
    sync_session.add(existing)
    sync_session.flush()
    sync_session.add(
        PersonIdentifier(
            person_id=existing.id, kind="email", value_normalized="a@example.com", raw_value="a"
        )
    )
    sync_session.flush()

    result = resolve(sync_session, email="a@example.com", profile=PersonProfile(full_name="New"))

    assert result.person_id == existing.id
    assert person(sync_session, existing.id).full_name == "New"


# ── Phone policy (ADR 0004 amendment, option A) ───────────────
def test_explicit_us_number_matches(sync_session: Session) -> None:
    first = resolve(sync_session, phone="+1 (415) 555-2671")
    second = resolve(sync_session, phone="+1 415 555 2671")

    assert second == ResolveResult(person_id=first.person_id, matched_existing=True)
    assert identifiers(sync_session) == {(first.person_id, "phone", US_NUMBER, False)}


def test_us_number_without_country_code_is_inferred_and_does_not_match(
    sync_session: Session, log: RecordingLogger
) -> None:
    # Someone in India whose landline is genuinely +91 415 555 2671.
    indian = resolve(sync_session, email="in@example.com", phone="+91 4155552671")

    us = resolve(sync_session, email="us@example.com", phone="(415) 555-2671")

    assert us.person_id != indian.person_id
    assert us.matched_existing is False
    assert us.conflict_id is None
    # The value has one owner; the US resume's inferred copy is not stored, and that is no error.
    assert identifiers(sync_session) == {
        (indian.person_id, "email", "in@example.com", False),
        (indian.person_id, "phone", US_AS_INDIAN_LANDLINE, False),
        (us.person_id, "email", "us@example.com", False),
    }
    assert ("identifier_owned_by_other_person", "phone") in [
        (name, kw.get("kind")) for name, kw in log.events
    ]


def test_us_number_without_country_code_is_stored_inferred(sync_session: Session) -> None:
    result = resolve(sync_session, email="us@example.com", phone="(415) 555-2671")

    assert (result.person_id, "phone", US_AS_INDIAN_LANDLINE, True) in identifiers(sync_session)


def test_explicit_number_does_not_match_someone_elses_inferred_copy(
    sync_session: Session,
) -> None:
    us = resolve(sync_session, email="us@example.com", phone="(415) 555-2671")
    indian = resolve(sync_session, email="in@example.com", phone="+91 4155552671")

    assert indian.person_id != us.person_id
    assert indian.matched_existing is False
    assert indian.conflict_id is None
    # The inferred row stays with its owner and stays inferred: it is not "upgraded" for them.
    assert (us.person_id, "phone", US_AS_INDIAN_LANDLINE, True) in identifiers(sync_session)


def test_indian_mobile_without_country_code_matches(sync_session: Session) -> None:
    first = resolve(sync_session, email="a@example.com", phone="+91 98765 43210")
    second = resolve(sync_session, phone="9876543210")

    assert second == ResolveResult(person_id=first.person_id, matched_existing=True)


def test_indian_landline_without_country_code_is_inferred_and_never_matches(
    sync_session: Session,
) -> None:
    first = resolve(sync_session, email="a@example.com", phone="080 2222 3333")
    second = resolve(sync_session, phone="080-2222-3333")

    assert second.person_id != first.person_id
    assert second.matched_existing is False
    assert identifiers(sync_session) == {
        (first.person_id, "email", "a@example.com", False),
        (first.person_id, "phone", IN_LANDLINE, True),
    }


def test_inferred_phone_cannot_cause_a_conflict(sync_session: Session) -> None:
    a = resolve(sync_session, email="a@example.com")
    resolve(sync_session, email="b@example.com", phone="+91 80 2222 3333")

    result = resolve(sync_session, email="a@example.com", phone="080 2222 3333")

    assert result == ResolveResult(person_id=a.person_id, matched_existing=True)
    assert count(sync_session, IdentityConflict) == 0


def test_inferred_phone_is_confirmed_when_its_owner_writes_the_country_code(
    sync_session: Session,
) -> None:
    first = resolve(sync_session, email="a@example.com", phone="080 2222 3333")
    # Same person (email match), now with +91: the number is theirs.
    resolve(sync_session, email="a@example.com", phone="+91 80 2222 3333")
    # From now on the landline matches on its own.
    third = resolve(sync_session, phone="+91 80 2222 3333")

    assert (first.person_id, "phone", IN_LANDLINE, False) in identifiers(sync_session)
    assert third == ResolveResult(person_id=first.person_id, matched_existing=True)


def test_inferred_phone_repeated_by_its_owner_changes_nothing(sync_session: Session) -> None:
    first = resolve(sync_session, email="a@example.com", phone="080 2222 3333")
    resolve(sync_session, email="a@example.com", phone="08022223333")

    assert identifiers(sync_session) == {
        (first.person_id, "email", "a@example.com", False),
        (first.person_id, "phone", IN_LANDLINE, True),
    }


# ── Idempotency per resume ────────────────────────────────────
@pytest.mark.parametrize(
    "case",
    ["new", "attach", "conflict", "no-identifiers", "inferred"],
)
def test_resolving_the_same_resume_twice_returns_the_same_answer(
    sync_session: Session, case: str
) -> None:
    resolve(sync_session, email="a@example.com")
    resolve(sync_session, email="b@example.com", phone=IN_MOBILE)
    kwargs: dict[str, Any] = {
        "new": {"email": "c@example.com", "phone": "+44 20 7183 8750"},
        "attach": {"email": "a@example.com", "phone": "+91 80 2222 3333"},
        "conflict": {"email": "a@example.com", "phone": IN_MOBILE},
        "no-identifiers": {},
        "inferred": {"phone": "080 2222 3333"},
    }[case]
    resume_id = uuid.uuid4()

    first = resolve(sync_session, resume_id=resume_id, **kwargs)
    before = {model: count(sync_session, model) for model in (Person, PersonIdentifier)}
    before_conflicts = count(sync_session, IdentityConflict)
    # A retry, even with a different extraction, gets the stored answer and writes nothing.
    again = resolve(
        sync_session,
        resume_id=resume_id,
        email="someone-else@example.com",
        profile=PersonProfile(full_name="Other"),
        uploaded_at=T0 + timedelta(days=9),
    )

    assert again == first
    assert {model: count(sync_session, model) for model in (Person, PersonIdentifier)} == before
    assert count(sync_session, IdentityConflict) == before_conflicts
    assert count(sync_session, PersonResume) == 3
    assert (first.conflict_id is not None) == (case == "conflict")


def test_resolve_person_does_not_commit(sync_engine: Any) -> None:
    resume_id = uuid.uuid4()
    with Session(sync_engine) as session:
        result = resolve(session, email=f"{resume_id}@example.com", resume_id=resume_id)
        session.rollback()
    with Session(sync_engine) as session:
        assert session.get(Person, result.person_id) is None
        assert session.get(PersonResume, resume_id) is None


# ── Concurrency mechanics (single connection) ────────────────
def test_unique_violation_rolls_back_to_the_savepoint_and_retries(
    sync_session: Session, monkeypatch: pytest.MonkeyPatch, log: RecordingLogger
) -> None:
    existing = resolve(sync_session, email="a@example.com")
    # Simulate a writer that bypassed the locks: the first read misses the stored identifier.
    real_find = repository.find_identifiers
    calls = {"n": 0}

    def stale_then_real(session: Session, kind_values: Any) -> Any:
        calls["n"] += 1
        return {} if calls["n"] == 1 else real_find(session, kind_values)

    monkeypatch.setattr(repository, "find_identifiers", stale_then_real)

    result = resolve(sync_session, email="a@example.com", phone=IN_MOBILE)

    assert calls["n"] == 2
    assert result == ResolveResult(person_id=existing.person_id, matched_existing=True)
    # The Person the first attempt created was rolled back with its savepoint.
    assert count(sync_session, Person) == 1
    assert count(sync_session, PersonResume) == 2
    assert "resolve_person_unique_violation_retry" in log.names()
    retry = dict(log.events)["resolve_person_unique_violation_retry"]
    assert retry["constraint"] == "uq_person_identifiers_kind_value_normalized"


def test_a_second_unique_violation_is_raised(
    sync_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.exc import IntegrityError

    resolve(sync_session, email="a@example.com")
    monkeypatch.setattr(repository, "find_identifiers", lambda session, kind_values: {})

    with pytest.raises(IntegrityError):
        resolve(sync_session, email="a@example.com")


def test_locks_are_taken_per_identifier_in_lock_id_order(sync_session: Session) -> None:
    resume_id = uuid.uuid4()
    resolve(sync_session, email="a@example.com", phone="080 2222 3333", resume_id=resume_id)

    expected = sorted(
        sync_session.execute(
            text("SELECT hashtext(k) FROM unnest(CAST(:keys AS text[])) AS k"),
            {"keys": [f"resume:{resume_id}", "email:a@example.com", f"phone:{IN_LANDLINE}"]},
        ).scalars()
    )
    held = sync_session.execute(
        text(
            # The bigint key of pg_advisory_xact_lock(bigint) is split into classid (high 32 bits)
            # and objid (low 32 bits), with objsubid = 1.
            "SELECT (classid::bigint << 32) | objid::bigint FROM pg_locks "
            "WHERE locktype = 'advisory' AND objsubid = 1 AND pid = pg_backend_pid() AND granted"
        )
    ).scalars()
    assert sorted(held) == expected
    assert repository.take_advisory_locks(sync_session, []) == []
    # Locking again in the same transaction is re-entrant and returns the sorted ids.
    assert repository.take_advisory_locks(sync_session, ["b", "a", "a"]) == sorted(
        sync_session.execute(text("SELECT hashtext('a') UNION SELECT hashtext('b')")).scalars()
    )


def test_uploaded_at_must_be_timezone_aware(sync_session: Session) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        resolve(sync_session, email="a@example.com", uploaded_at=datetime(2026, 10, 1))


def test_get_resolution_returns_the_stored_result(sync_session: Session) -> None:
    first = resolve(sync_session, email="asha@example.com", phone=IN_MOBILE)
    resume_id = uuid.uuid4()
    conflict = resolve(sync_session, email="ravi@example.com", phone="+919123456789")
    mixed = resolve(
        sync_session, email="asha@example.com", phone="+919123456789", resume_id=resume_id
    )
    assert mixed.conflict_id is not None and conflict.person_id != first.person_id

    assert service.get_resolution(sync_session, resume_id) == mixed
    assert service.get_resolution(sync_session, uuid.uuid4()) is None
