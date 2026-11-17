"""Test data and helpers shared by the ``/api/v2`` contract tests."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, event
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.hiring.models import Application, Campaign
from app.modules.talent.models import IdentityConflict, Person, PersonIdentifier
from tests.factories import at, make_campaign, make_candidate, make_resume

V2 = "/api/v2"
UNKNOWN_ID = "00000000-0000-0000-0000-000000000000"


def pdf(label: str) -> bytes:
    """A minimal byte string that passes the upload's PDF checks (``%PDF`` magic bytes)."""
    return f"%PDF-1.4\n% {label}\n".encode()


def files(*items: tuple[str, bytes]) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("files", (name, content, "application/pdf")) for name, content in items]


async def applicant(
    db: AsyncSession, campaign: Campaign, i: int, **overrides: Any
) -> Application:
    """The i-th applicant of a campaign: its own resume (uploaded at ``at(i)``) and Person."""
    resume = await make_resume(
        db, campaign, original_filename=f"cv_{campaign.title}_{i}.pdf", uploaded_at=at(i)
    )
    fields: dict[str, Any] = {
        "name": f"Applicant {i}",
        "email": f"applicant{i}@example.com",
        "created_at": at(i),
        "updated_at": at(i),
    }
    fields.update(overrides)
    return await make_candidate(db, resume=resume, **fields)


async def ranked_campaign(db: AsyncSession) -> Campaign:
    """A campaign with five applications covering every list filter and sort:

    ======  ====================  =====  ==============  ======  ============  ==========
    i       name                  score  category        stage   skills        experience
    ======  ====================  =====  ==============  ======  ============  ==========
    0       Zoe Adams             91     strong_match    -       Python, Go    7
    1       amy brown             74     moderate_match  screen  Python, SQL   3
    2       Carl Diaz             45     weak_match      -       Java          1
    3       (no name)             null   null            -       []            null
    4       Ben Evans             91     strong_match    offer   Rust, Python  10
    ======  ====================  =====  ==============  ======  ============  ==========

    Application ids are ``UUID(int=i + 1)``; updated in order of ``i``.
    """
    campaign = await make_campaign(db, title="Ranked")
    rows: list[dict[str, Any]] = [
        dict(name="Zoe Adams", score=91.0, category="strong_match", skills=["Python", "Go"],
             years_of_experience=7.0),
        dict(name="amy brown", score=74.0, category="moderate_match", pipeline_stage="screened",
             skills=["Python", "SQL"], years_of_experience=3.0),
        dict(name="Carl Diaz", score=45.0, category="weak_match", skills=["Java"],
             years_of_experience=1.0),
        dict(name=None, email=None, score=None, category=None, skills=[],
             years_of_experience=None, score_breakdown=None, summary=None,
             recommendation=None),
        dict(name="Ben Evans", score=91.0, category="strong_match", pipeline_stage="offer",
             skills=["Rust", "Python"], years_of_experience=10.0),
    ]  # fmt: skip
    for i, row in enumerate(rows):
        # Fixed ids, so the tie on 91 (Zoe before Ben: ties by id) is the same on every run.
        await applicant(db, campaign, i, id=uuid.UUID(int=i + 1), **row)
    return campaign


async def add_identifier(
    db: AsyncSession, person: Person | uuid.UUID, kind: str, raw: str, *, inferred: bool = False
) -> None:
    person_id = person if isinstance(person, uuid.UUID) else person.id
    db.add(
        PersonIdentifier(
            person_id=person_id,
            kind=kind,
            value_normalized=raw.strip().lower(),
            raw_value=raw,
            inferred=inferred,
        )
    )
    await db.flush()


async def add_conflict(
    db: AsyncSession, chosen: uuid.UUID, conflicting: uuid.UUID, resume_id: uuid.UUID
) -> None:
    db.add(
        IdentityConflict(
            resume_id=resume_id,
            chosen_person_id=chosen,
            conflicting_person_id=conflicting,
            reason="email matched one person and phone matched another; "
            "attached to the email match (ADR 0004)",
            created_at=at(100),
        )
    )
    await db.flush()


@contextmanager
def counting_statements(db: AsyncSession) -> Iterator[list[str]]:
    """Every SQL statement run on the test's connection while the block runs."""
    statements: list[str] = []
    engine: Engine = db.bind.engine.sync_engine  # type: ignore[union-attr]

    def record(conn: Any, cursor: Any, statement: str, *_: Any) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)
