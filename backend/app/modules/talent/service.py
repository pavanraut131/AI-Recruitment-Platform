"""talent public interface: decide which Person a resume belongs to (EPIC-00 spec §5.3–5.4).

Other modules call :func:`resolve_person` (and use the types in ``app.modules.talent.schemas``);
they never touch talent's tables. The callers are the upload pipeline (E0-16) and the backfill
(E0-17), both running a synchronous ``Session``.

Matching policy: ADR 0004 and its 2026-10-16 amendment (phones without a country code).

**Transactions.** :func:`resolve_person` works inside the caller's transaction and never commits:
the caller commits (or rolls back) together with its own writes, so a Person is never created for
a resume whose processing then fails. The advisory locks it takes are held until that commit, so
callers should commit promptly after it returns. It needs ``READ COMMITTED`` (Postgres' default):
each statement must see rows committed while it waited for a lock.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.talent import repository
from app.modules.talent.identity import classify_phone, normalize_email
from app.modules.talent.models import IdentityConflict, Person, PersonIdentifier, PersonResume
from app.modules.talent.schemas import PersonProfile, ResolveResult

logger = structlog.get_logger()

EMAIL = "email"
PHONE = "phone"

# person_identifiers.value_normalized / raw_value are varchar(320).
_MAX_IDENTIFIER_LENGTH = 320

_STRING_LIMITS = {"full_name": 255, "github_url": 500, "linkedin_url": 500}
_PROFILE_FIELDS = tuple(PersonProfile.model_fields)


@dataclass(frozen=True)
class _Identifier:
    kind: str
    value: str  # normalised
    raw: str
    # Option A (ADR 0004 amendment): stored, but never used to match.
    inferred: bool

    @property
    def key(self) -> tuple[str, str]:
        return (self.kind, self.value)

    @property
    def lock_key(self) -> str:
        return f"{self.kind}:{self.value}"


def resolve_person(
    session: Session,
    *,
    email: str | None,
    phone: str | None,
    profile: PersonProfile,
    resume_id: uuid.UUID,
    uploaded_at: datetime,
    default_region: str,
) -> ResolveResult:
    """Attach a resume to an existing Person or create one, and refresh the Person's profile.

    ``email`` and ``phone`` are the raw values from the resume; they are normalised here
    (``talent.identity``). ``default_region`` is normally ``settings.PHONE_DEFAULT_REGION``.
    ``uploaded_at`` (timezone-aware) is when the resume was uploaded: it decides which resume's
    profile wins, whatever order resumes are processed in.

    Rules (spec §5.3). Only *match-eligible* identifiers match: every valid email, and a phone
    written with its country code or that is a mobile number (``identity.classify_phone``). An
    *inferred* phone is stored for the Person but never matches anyone.

    - Nothing matches, or no usable identifiers: create a Person.
    - Every match is the same Person: attach to it and add the identifiers it lacks.
    - The email matches Person A and the phone Person B: attach to **A** and record an
      ``identity_conflicts`` row. People are never merged.

    An identifier already stored for a *different* Person (an inferred phone, or the phone of a
    conflict) is not added: ``UNIQUE(kind, value_normalized)`` allows one owner per value.

    Idempotent per ``resume_id``: once a resume is resolved (and committed), calling again
    returns the stored result and changes nothing, even with different arguments.

    Concurrency (spec §5.4): before reading anything it takes a transaction-level advisory lock
    on the resume and on every normalised identifier, in one global order, so concurrent calls
    that share any identifier run one after the other. Should an insert still hit the unique
    constraint, the attempt is rolled back to a savepoint, re-read and retried once.
    """
    if uploaded_at.tzinfo is None:
        raise ValueError("uploaded_at must be timezone-aware")

    identifiers = _normalise(email, phone, default_region)
    repository.take_advisory_locks(
        session, [f"resume:{resume_id}", *(ident.lock_key for ident in identifiers)]
    )
    args: dict[str, Any] = {
        "identifiers": identifiers,
        "profile": profile,
        "resume_id": resume_id,
        "uploaded_at": uploaded_at,
    }
    try:
        with session.begin_nested():
            return _resolve_locked(session, **args)
    except IntegrityError as exc:
        # The locks make this unreachable for writers that go through resolve_person; it guards
        # against one that does not. The savepoint is rolled back, so re-reading sees the row.
        logger.warning(
            "resolve_person_unique_violation_retry",
            resume_id=str(resume_id),
            constraint=getattr(getattr(exc.orig, "diag", None), "constraint_name", None),
        )
    with session.begin_nested():
        return _resolve_locked(session, **args)


def get_resolution(session: Session, resume_id: uuid.UUID) -> ResolveResult | None:
    """The stored result of resolving ``resume_id``, or ``None`` if it was never resolved.

    A plain read: no locks, no writes. For callers that already know the resume is resolved
    (the pipeline skips :func:`resolve_person` when ``resumes.person_id`` is set) and still
    need the result, e.g. ``matched_existing``.
    """
    done = repository.get_person_resume(session, resume_id)
    if done is None:
        return None
    return ResolveResult(
        person_id=done.person_id,
        matched_existing=done.matched_existing,
        conflict_id=done.conflict_id,
    )


# ── Steps ─────────────────────────────────────────────────────
def _normalise(email: str | None, phone: str | None, default_region: str) -> list[_Identifier]:
    identifiers: list[_Identifier] = []
    normalised_email = normalize_email(email)
    if email is not None and normalised_email is not None:
        identifiers.append(_Identifier(EMAIL, normalised_email, email.strip(), inferred=False))
    classified = classify_phone(phone, default_region)
    if phone is not None and classified is not None:
        identifiers.append(
            _Identifier(PHONE, classified.e164, phone.strip(), not classified.match_eligible)
        )
    usable = [ident for ident in identifiers if len(ident.value) <= _MAX_IDENTIFIER_LENGTH]
    if len(usable) < len(identifiers):
        logger.info("identifier_too_long_discarded")
    return usable


def _resolve_locked(
    session: Session,
    *,
    identifiers: Sequence[_Identifier],
    profile: PersonProfile,
    resume_id: uuid.UUID,
    uploaded_at: datetime,
) -> ResolveResult:
    done = repository.get_person_resume(session, resume_id)
    if done is not None:
        return ResolveResult(
            person_id=done.person_id,
            matched_existing=done.matched_existing,
            conflict_id=done.conflict_id,
        )

    stored = repository.find_identifiers(session, [ident.key for ident in identifiers])
    owners: dict[str, uuid.UUID] = {}
    for ident in identifiers:
        row = stored.get(ident.key)
        if row is not None and not ident.inferred and not row.inferred:
            owners[ident.kind] = row.person_id
    # The email wins (ADR 0004): it is the stronger identifier.
    chosen_id = owners.get(EMAIL, owners.get(PHONE))
    conflicting_id = owners.get(PHONE) if owners.get(PHONE) not in (None, chosen_id) else None

    if chosen_id is None:
        person = Person(id=uuid.uuid4())
        _refresh_profile(person, profile, resume_id, newer=True)
        session.add(person)
        session.flush()
    else:
        person = repository.lock_person(session, chosen_id)
        _refresh_profile(
            person, profile, resume_id, newer=_is_newer(session, person, resume_id, uploaded_at)
        )

    conflict_id: uuid.UUID | None = None
    if conflicting_id is not None:
        conflict_id = uuid.uuid4()
        session.add(
            IdentityConflict(
                id=conflict_id,
                resume_id=resume_id,
                chosen_person_id=person.id,
                conflicting_person_id=conflicting_id,
                reason="email matched one person and phone matched another; "
                "attached to the email match (ADR 0004)",
            )
        )
        logger.info(
            "identity_conflict_recorded",
            resume_id=str(resume_id),
            chosen_person_id=str(person.id),
            conflicting_person_id=str(conflicting_id),
        )

    for ident in identifiers:
        _attach(session, person.id, ident, stored.get(ident.key), resume_id, conflicting_id)

    session.flush()  # the conflict row before the reference to it
    session.add(
        PersonResume(
            resume_id=resume_id,
            person_id=person.id,
            uploaded_at=uploaded_at,
            matched_existing=chosen_id is not None,
            conflict_id=conflict_id,
        )
    )
    session.flush()
    return ResolveResult(
        person_id=person.id, matched_existing=chosen_id is not None, conflict_id=conflict_id
    )


def _attach(
    session: Session,
    person_id: uuid.UUID,
    ident: _Identifier,
    row: PersonIdentifier | None,
    resume_id: uuid.UUID,
    conflicting_id: uuid.UUID | None,
) -> None:
    """Store ``ident`` for the Person unless it is already stored."""
    if row is None:
        session.add(
            PersonIdentifier(
                person_id=person_id,
                kind=ident.kind,
                value_normalized=ident.value,
                raw_value=ident.raw[:_MAX_IDENTIFIER_LENGTH],
                inferred=ident.inferred,
                source_resume_id=resume_id,
            )
        )
    elif row.person_id == person_id:
        if row.inferred and not ident.inferred:
            # The Person was matched on another identifier, and this resume writes the same
            # number with its country code (or it now classifies as a mobile): the number is
            # theirs, so it may match from now on.
            row.inferred = False
            logger.info("inferred_phone_confirmed", person_id=str(person_id))
    elif row.person_id != conflicting_id:
        # Owned by someone else and not used to match (an inferred phone on either side):
        # one owner per value, so it is not stored for this Person. Not an error, not a conflict.
        logger.info(
            "identifier_owned_by_other_person",
            kind=ident.kind,
            inferred=ident.inferred or row.inferred,
            resume_id=str(resume_id),
            person_id=str(person_id),
            owner_person_id=str(row.person_id),
        )


def _is_newer(
    session: Session, person: Person, resume_id: uuid.UUID, uploaded_at: datetime
) -> bool:
    """Whether this resume was uploaded after the one the Person's profile came from.

    Ties on ``uploaded_at`` are broken by resume id, so the outcome never depends on processing
    order. A profile with no recorded source counts as older.
    """
    if person.latest_resume_id is None:
        return True
    source = repository.get_person_resume(session, person.latest_resume_id)
    if source is None:
        return True
    return (uploaded_at, resume_id) > (source.uploaded_at, source.resume_id)


def _refresh_profile(
    person: Person, profile: PersonProfile, resume_id: uuid.UUID, *, newer: bool
) -> None:
    """Spec §5.3 profile refresh.

    A newer resume sets every field it has and becomes ``latest_resume_id``; fields it lacks keep
    their value. An older resume (processed late) only fills fields the Person still lacks: it
    never overwrites a value from a newer resume.
    """
    for field in _PROFILE_FIELDS:
        value = _usable(field, getattr(profile, field))
        current = getattr(person, field)
        if value is not None and (newer or _missing(current)):
            setattr(person, field, value)
    if newer:
        person.latest_resume_id = resume_id


def _usable(field: str, value: Any) -> Any:
    if _missing(value):
        return None
    limit = _STRING_LIMITS.get(field)
    if limit is not None and len(value) > limit:
        # A clipped name is still a name; a clipped URL is a wrong URL.
        return value[:limit] if field == "full_name" else None
    return value


def _missing(value: Any) -> bool:
    return value is None or value == "" or value == []
