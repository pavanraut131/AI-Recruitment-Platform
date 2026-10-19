"""E0-18: the legacy recruiter endpoints and deletes keep the candidates' applications right.

Through the HTTP API (``client``), in the test's rolled-back transaction. Every application here
is built the way the backfill / E0-17 pipeline leaves it: it reuses the id of one candidate, the
others are redirected by ``legacy_candidate_map``, the scoring is the newest resume's.

The rules (``app.modules.hiring.service``, "Mirroring the legacy writes"):

- ``pipeline_stage`` / ``notes``: newest non-``NULL`` value over the application's candidates,
  by resume upload time. Recomputed from all candidates on every write, never copied.
- ``category``: the candidate of the application's current resume; a manual category on an
  older, merged candidate is not mirrored.
- Delete: the application goes with its last candidate; otherwise it keeps its id, loses only
  the deleted candidates' map rows, takes the newest remaining resume's scoring if its current
  resume was deleted, and recomputes the recruiter fields.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.hiring.models import Application, Campaign, Candidate, LegacyCandidateMap
from app.modules.ingestion.models import Resume
from tests.factories import at, make_campaign, make_candidate, make_resume

API = "/api/v1"


@dataclass
class Merged:
    """One person's application with three candidates, uploaded B (oldest), C, A (newest).
    It reuses C's id (C was processed first) and holds A's scoring (the newest resume)."""

    campaign: Campaign
    application: Application
    a: Candidate  # newest resume: the application's current one
    b: Candidate  # oldest resume
    c: Candidate  # middle resume

    @property
    def all(self) -> list[Candidate]:
        return [self.a, self.b, self.c]


async def _candidate(
    db: AsyncSession, campaign: Campaign, uploaded: int, created: int, **fields: Any
) -> Candidate:
    resume = await make_resume(
        db, campaign, uploaded_at=at(uploaded), original_filename=f"cv_{uploaded}.pdf"
    )
    return await make_candidate(db, resume=resume, created_at=at(created), **fields)


async def _application(
    db: AsyncSession,
    owner: Candidate,
    current: Candidate,
    others: list[Candidate],
    **fields: Any,
) -> Application:
    """The application as the backfill writes it: ``owner``'s id, ``current``'s scoring, map
    rows for ``others``."""
    resume = await db.get_one(Resume, current.resume_id)
    application = Application(
        id=owner.id,
        campaign_id=owner.campaign_id,
        person_id=uuid.uuid4(),
        resume_id=current.resume_id,
        resume_uploaded_at=resume.uploaded_at,
        score=current.score,
        score_breakdown=current.score_breakdown,
        category=current.category,
        summary=current.summary,
        strengths=current.strengths,
        weaknesses=current.weaknesses,
        missing_skills=current.missing_skills,
        recommendation=current.recommendation,
        **fields,
    )
    db.add(application)
    await db.flush()
    for other in others:
        db.add(LegacyCandidateMap(candidate_id=other.id, application_id=application.id))
    await db.flush()
    return application


@pytest.fixture
async def merged(db_session: AsyncSession) -> Merged:
    """Processed (created) in the order C, A, B; uploaded B < C < A. C was processed first, so
    the application has C's id; A's resume is the newest, so it holds A's scoring."""
    campaign = await make_campaign(db_session)
    c = await _candidate(db_session, campaign, uploaded=10, created=100, score=60.0)
    a = await _candidate(
        db_session, campaign, uploaded=20, created=101, score=90.0, category="strong_match"
    )
    b = await _candidate(
        db_session, campaign, uploaded=0, created=102, score=30.0, category="weak_match"
    )
    application = await _application(db_session, owner=c, current=a, others=[a, b])
    return Merged(campaign, application, a=a, b=b, c=c)


async def _reload(db: AsyncSession, application_id: uuid.UUID) -> Application | None:
    result = await db.execute(
        select(Application)
        .where(Application.id == application_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def _recruiter(db: AsyncSession, application_id: uuid.UUID) -> tuple[Any, ...]:
    app = await _reload(db, application_id)
    assert app is not None
    return (app.pipeline_stage, app.notes, app.category)


async def _map_rows(db: AsyncSession) -> dict[uuid.UUID, uuid.UUID]:
    result = await db.execute(
        select(LegacyCandidateMap.candidate_id, LegacyCandidateMap.application_id)
    )
    return dict(result.tuples().all())


async def _stage(client: AsyncClient, candidate: Candidate, stage: str) -> None:
    resp = await client.patch(f"{API}/candidates/{candidate.id}/pipeline", json={"stage": stage})
    assert resp.status_code == 200, resp.text
    assert resp.json()["pipeline_stage"] == stage


# ── Pipeline stage ────────────────────────────────────────────
async def test_pipeline_stage_is_recomputed_over_all_candidates_not_copied(
    client: AsyncClient, db_session: AsyncSession, merged: Merged
) -> None:
    app_id = merged.application.id

    await _stage(client, merged.b, "technical")  # oldest resume, the only value
    assert (await _recruiter(db_session, app_id))[0] == "technical"

    await _stage(client, merged.c, "offer")  # newer than B: wins
    assert (await _recruiter(db_session, app_id))[0] == "offer"

    # Editing the oldest candidate again changes nothing: C's newer value still wins. ("Copy
    # the new value" would have written "hired" here.)
    await _stage(client, merged.b, "hired")
    assert (await _recruiter(db_session, app_id))[0] == "offer"

    await _stage(client, merged.a, "phone_call")  # newest resume
    assert (await _recruiter(db_session, app_id))[0] == "phone_call"

    # The candidates themselves were written exactly as before.
    for candidate, stage in ((merged.a, "phone_call"), (merged.b, "hired"), (merged.c, "offer")):
        await db_session.refresh(candidate)
        assert candidate.pipeline_stage == stage


async def test_the_id_owner_is_resolved_directly_and_the_others_through_the_map(
    client: AsyncClient, db_session: AsyncSession, merged: Merged
) -> None:
    """C *is* the application (same id); A and B lead to it through legacy_candidate_map."""
    await _stage(client, merged.c, "screened")
    assert (await _recruiter(db_session, merged.application.id))[0] == "screened"
    await _stage(client, merged.a, "rejected_manual")
    assert (await _recruiter(db_session, merged.application.id))[0] == "rejected_manual"


# ── Notes ─────────────────────────────────────────────────────
async def test_notes_follow_the_same_rule_and_an_empty_note_is_a_value(
    client: AsyncClient, db_session: AsyncSession, merged: Merged
) -> None:
    app_id = merged.application.id

    async def notes(candidate: Candidate, text: str) -> None:
        resp = await client.patch(f"{API}/candidates/{candidate.id}/notes", json={"notes": text})
        assert resp.status_code == 200, resp.text
        assert resp.json()["notes"] == text

    await notes(merged.c, "met at the meetup")
    assert (await _recruiter(db_session, app_id))[1] == "met at the meetup"
    await notes(merged.b, "older note")  # older than C: C's note stays
    assert (await _recruiter(db_session, app_id))[1] == "met at the meetup"
    await notes(merged.a, "")  # the newest candidate's note was cleared: that is its value
    assert (await _recruiter(db_session, app_id))[1] == ""


# ── Category ──────────────────────────────────────────────────
async def test_category_is_mirrored_only_from_the_candidate_of_the_current_resume(
    client: AsyncClient, db_session: AsyncSession, merged: Merged
) -> None:
    app_id = merged.application.id

    resp = await client.put(
        f"{API}/candidates/{merged.b.id}/category", json={"category": "rejected"}
    )
    assert resp.status_code == 200
    assert resp.json()["category"] == "rejected"
    # B's resume is not the application's: the category travels with the scoring (A's).
    assert (await _recruiter(db_session, app_id))[2] == "strong_match"

    resp = await client.put(
        f"{API}/candidates/{merged.a.id}/category", json={"category": "moderate_match"}
    )
    assert resp.status_code == 200
    assert (await _recruiter(db_session, app_id))[2] == "moderate_match"


# ── Bulk pipeline ─────────────────────────────────────────────
async def test_bulk_pipeline_updates_every_affected_application(
    client: AsyncClient, db_session: AsyncSession, merged: Merged
) -> None:
    single = await _candidate(
        db_session, merged.campaign, uploaded=5, created=103, email="solo@example.com"
    )
    solo_app = await _application(db_session, owner=single, current=single, others=[])
    no_app = await _candidate(db_session, merged.campaign, uploaded=6, created=104)

    resp = await client.patch(
        f"{API}/campaigns/{merged.campaign.id}/candidates/bulk-pipeline",
        json={
            "candidate_ids": [str(merged.b.id), str(single.id), str(no_app.id)],
            "stage": "offer",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"updated": 3}
    assert (await _recruiter(db_session, merged.application.id))[0] == "offer"
    assert (await _recruiter(db_session, solo_app.id))[0] == "offer"

    # B and C together, then A alone: A is the newest, so its stage wins at the end.
    resp = await client.patch(
        f"{API}/campaigns/{merged.campaign.id}/candidates/bulk-pipeline",
        json={"candidate_ids": [str(merged.b.id), str(merged.c.id)], "stage": "screened"},
    )
    assert resp.json() == {"updated": 2}
    assert (await _recruiter(db_session, merged.application.id))[0] == "screened"
    resp = await client.patch(
        f"{API}/campaigns/{merged.campaign.id}/candidates/bulk-pipeline",
        json={"candidate_ids": [str(merged.a.id)], "stage": "technical"},
    )
    assert (await _recruiter(db_session, merged.application.id))[0] == "technical"


# ── Candidates without an application ─────────────────────────
async def test_a_candidate_without_an_application_is_written_alone(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Its resume is still processing (or was never backfilled): no error, no application."""
    candidate = await make_candidate(db_session)

    assert (
        await client.patch(f"{API}/candidates/{candidate.id}/pipeline", json={"stage": "offer"})
    ).status_code == 200
    assert (
        await client.patch(f"{API}/candidates/{candidate.id}/notes", json={"notes": "n"})
    ).status_code == 200
    assert (
        await client.put(f"{API}/candidates/{candidate.id}/category", json={"category": "rejected"})
    ).status_code == 200
    await db_session.refresh(candidate)
    assert (candidate.pipeline_stage, candidate.notes, candidate.category) == (
        "offer",
        "n",
        "rejected",
    )
    assert await db_session.scalar(select(func.count()).select_from(Application)) == 0


async def test_errors_are_unchanged(client: AsyncClient, merged: Merged) -> None:
    missing = uuid.uuid4()
    resp = await client.patch(f"{API}/candidates/{missing}/pipeline", json={"stage": "offer"})
    assert (resp.status_code, resp.json()) == (404, {"detail": "Candidate not found"})
    resp = await client.patch(f"{API}/candidates/{merged.a.id}/pipeline", json={"stage": "nope"})
    assert resp.status_code == 400
    resp = await client.put(f"{API}/candidates/{merged.a.id}/category", json={"category": "nope"})
    assert resp.status_code == 400


# ── Deletes ───────────────────────────────────────────────────
async def test_deleting_the_only_candidate_deletes_its_application(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    campaign = await make_campaign(db_session)
    candidate = await _candidate(db_session, campaign, uploaded=0, created=1)
    application = await _application(db_session, owner=candidate, current=candidate, others=[])

    resp = await client.delete(f"{API}/candidates/{candidate.id}")
    assert resp.status_code == 204
    assert await _reload(db_session, application.id) is None
    assert await db_session.get(Resume, candidate.resume_id) is None


async def test_deleting_a_merged_candidate_keeps_the_application_and_drops_its_map_row(
    client: AsyncClient, db_session: AsyncSession, merged: Merged
) -> None:
    await _stage(client, merged.b, "hired")  # B's stage is the application's (the only one)
    assert (await _recruiter(db_session, merged.application.id))[0] == "hired"

    resp = await client.delete(f"{API}/candidates/{merged.b.id}")
    assert resp.status_code == 204

    app = await _reload(db_session, merged.application.id)
    assert app is not None
    assert app.id == merged.c.id
    assert app.resume_id == merged.a.resume_id  # B's resume was not the current one
    assert await _map_rows(db_session) == {merged.a.id: app.id}
    assert app.pipeline_stage is None  # recomputed: no remaining candidate has a stage


async def test_deleting_the_id_owner_keeps_the_application_id(
    client: AsyncClient, db_session: AsyncSession, merged: Merged
) -> None:
    """C's id is the application's: the application keeps it (its other candidates' map rows
    point at it), although no candidate has that id any more."""
    resp = await client.delete(f"{API}/candidates/{merged.c.id}")
    assert resp.status_code == 204

    app = await _reload(db_session, merged.c.id)
    assert app is not None
    assert app.resume_id == merged.a.resume_id
    assert await _map_rows(db_session) == {merged.a.id: app.id, merged.b.id: app.id}


async def test_deleting_the_candidate_of_the_current_resume_moves_the_scoring_to_the_newest_remaining(
    client: AsyncClient, db_session: AsyncSession, merged: Merged
) -> None:
    await _stage(client, merged.a, "offer")
    await _stage(client, merged.b, "screened")

    resp = await client.delete(f"{API}/candidates/{merged.a.id}")
    assert resp.status_code == 204

    app = await _reload(db_session, merged.application.id)
    assert app is not None
    c_resume = await db_session.get_one(Resume, merged.c.resume_id)
    # C is the newest remaining resume: the application now holds it and its scoring.
    assert (app.resume_id, app.resume_uploaded_at, app.score, app.category) == (
        merged.c.resume_id,
        c_resume.uploaded_at,
        merged.c.score,
        merged.c.category,
    )
    assert app.pipeline_stage == "screened"  # A's stage went with A
    assert await _map_rows(db_session) == {merged.b.id: app.id}


async def test_bulk_delete_applies_the_same_rule(
    client: AsyncClient, db_session: AsyncSession, merged: Merged
) -> None:
    solo = await _candidate(
        db_session, merged.campaign, uploaded=5, created=103, email="solo@example.com"
    )
    solo_app = await _application(db_session, owner=solo, current=solo, others=[])

    resp = await client.request(
        "DELETE",
        f"{API}/campaigns/{merged.campaign.id}/candidates/bulk",
        json={"candidate_ids": [str(merged.a.id), str(merged.c.id), str(solo.id)]},
    )
    assert resp.json() == {"deleted": 3}
    assert await _reload(db_session, solo_app.id) is None
    app = await _reload(db_session, merged.application.id)
    assert app is not None  # B is left
    assert app.resume_id == merged.b.resume_id
    assert await _map_rows(db_session) == {merged.b.id: app.id}

    resp = await client.request(
        "DELETE",
        f"{API}/campaigns/{merged.campaign.id}/candidates/bulk",
        json={"candidate_ids": [str(merged.b.id)]},
    )
    assert resp.json() == {"deleted": 1}
    assert await _reload(db_session, merged.application.id) is None
    assert await _map_rows(db_session) == {}
