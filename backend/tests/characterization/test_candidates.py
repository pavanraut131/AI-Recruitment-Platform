"""Characterization: candidate list/filter/export, detail, recruiter updates and bulk actions."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from syrupy.assertion import SnapshotAssertion

from app.models.campaign import Campaign
from app.models.candidate import Candidate
from app.models.resume import Resume
from tests.characterization.snapshot_utils import response_data
from tests.factories import at, make_campaign, make_candidate, make_resume
from tests.fakes import InMemoryStorage

API = "/api/v1"
UNKNOWN_ID = "00000000-0000-0000-0000-000000000000"


async def _seed_pool(db: AsyncSession) -> Campaign:
    """Five candidates covering every filter dimension, plus one in another campaign.

    | name           | email              | score | category       | exp | stage           |
    | Alice Anderson | alice@example.com  | 91    | strong_match   | 7   | technical       |
    | Bob Brown      | bob@sample.org     | 74    | moderate_match | 3   | screened        |
    | Chen, Carol    | carol@example.com  | 55    | weak_match     | 1.5 | -               |
    | Dan Diaz       | dan@sample.org     | 12    | rejected       | 0   | rejected_manual |
    | (placeholder)  | -                  | -     | -              | -   | -               |
    """
    campaign = await make_campaign(db)
    rows: list[dict[str, Any]] = [
        {
            "name": "Alice Anderson",
            "email": "alice@example.com",
            "score": 91.0,
            "category": "strong_match",
            "years_of_experience": 7.0,
            "pipeline_stage": "technical",
            "skills": ["Python", "FastAPI", "AWS"],
            "missing_skills": [],
            "strengths": ["Deep Python", "Cloud"],
        },
        {
            "name": "Bob Brown",
            "email": "bob@sample.org",
            "score": 74.0,
            "category": "moderate_match",
            "years_of_experience": 3.0,
            "pipeline_stage": "screened",
            "skills": ["JavaScript", "React", "Node.js"],
            "missing_skills": ["Python"],
            "strengths": ["Frontend"],
        },
        {
            "name": "Chen, Carol",
            "email": "carol@example.com",
            "score": 55.0,
            "category": "weak_match",
            "years_of_experience": 1.5,
            "pipeline_stage": None,
            "skills": ["Java", "Spring"],
            "missing_skills": ["Python", "FastAPI"],
            "strengths": ['Says "hello world" fluently'],
            "recommendation": "Maybe, for a junior role.",
        },
        {
            "name": "Dan Diaz",
            "email": "dan@sample.org",
            "score": 12.0,
            "category": "rejected",
            "years_of_experience": 0.0,
            "pipeline_stage": "rejected_manual",
            "skills": ["PHP"],
            "missing_skills": ["Python", "FastAPI", "PostgreSQL"],
            "strengths": [],
            "github_url": None,
            "linkedin_url": None,
            "phone": None,
        },
        {
            # Extraction done, AI scoring not yet: everything but raw_text is empty.
            "name": None,
            "email": None,
            "phone": None,
            "github_url": None,
            "linkedin_url": None,
            "score": None,
            "category": None,
            "years_of_experience": None,
            "pipeline_stage": None,
            "skills": [],
            "missing_skills": [],
            "strengths": [],
            "summary": None,
            "recommendation": None,
        },
    ]
    for i, fields in enumerate(rows):
        resume = await make_resume(
            db, campaign, original_filename=f"candidate_{i}.pdf", uploaded_at=at(i)
        )
        await make_candidate(db, resume=resume, created_at=at(i), **fields)

    other = await make_campaign(db, title="Other campaign")
    await make_candidate(db, other, name="Alice Elsewhere", email="alice@elsewhere.com")
    return campaign


def _csv_data(resp: Any) -> dict[str, Any]:
    """CSV export as plain data: lines split on the CRLF terminator (kept out of the snapshot
    file, which normalises newlines)."""
    return {
        "status_code": resp.status_code,
        "headers": {
            name: resp.headers.get(name) for name in ("content-type", "content-disposition")
        },
        "line_terminator_is_crlf": resp.text.endswith("\r\n"),
        "lines": resp.text.split("\r\n"),
    }


# ── GET /campaigns/{id}/candidates ────────────────────────────
@pytest.mark.parametrize(
    "query",
    [
        pytest.param("", id="default-score-desc-nulls-last"),
        pytest.param("?category=strong_match", id="category"),
        pytest.param("?category=not_a_category", id="category-unknown-value"),
        pytest.param("?min_score=55", id="min-score-inclusive"),
        pytest.param("?max_score=55", id="max-score-inclusive"),
        pytest.param("?min_score=50&max_score=80", id="score-range"),
        pytest.param("?min_experience=3", id="min-experience-inclusive"),
        pytest.param("?search=ALI", id="search-name-case-insensitive"),
        pytest.param("?search=sample.org", id="search-email"),
        # LIKE wildcards are not escaped: "_" matches any single character.
        pytest.param("?search=_", id="search-like-wildcard"),
        pytest.param("?pipeline_stage=screened", id="pipeline-stage"),
        pytest.param("?page_size=2", id="page-size"),
        pytest.param("?page=2&page_size=2", id="page-2"),
        pytest.param("?page=4&page_size=2", id="page-past-end"),
        pytest.param("?min_score=0&max_score=100&page_size=100", id="bounds"),
        pytest.param(
            "?category=strong_match&min_score=10&search=example&min_experience=1&pipeline_stage=technical",
            id="filters-combined",
        ),
    ],
)
async def test_list_candidates(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion, query: str
) -> None:
    campaign = await _seed_pool(db_session)
    resp = await client.get(f"{API}/campaigns/{campaign.id}/candidates{query}")
    assert response_data(resp) == snapshot


@pytest.mark.parametrize(
    "query",
    [
        pytest.param("?skill=python", id="skill"),
        pytest.param("?skill=", id="skill-empty-is-no-filter"),
        pytest.param("?skill=java&category=weak_match", id="skill-with-other-filters"),
    ],
)
async def test_list_candidates_skill_filter_is_a_500(
    raw_client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion, query: str
) -> None:
    # BUG (recorded): any non-empty `skill` raises AttributeError while building the query
    # (`Cast` has no `.astext`), so the skill filter always returns the generic 500.
    campaign = await _seed_pool(db_session)
    resp = await raw_client.get(f"{API}/campaigns/{campaign.id}/candidates{query}")
    assert response_data(resp) == snapshot


async def test_list_candidates_unknown_campaign(
    client: AsyncClient, snapshot: SnapshotAssertion
) -> None:
    # Not a 404: an empty list.
    resp = await client.get(f"{API}/campaigns/{UNKNOWN_ID}/candidates")
    assert response_data(resp) == snapshot


@pytest.mark.parametrize(
    "query",
    [
        "?min_score=101",
        "?min_score=-1",
        "?max_score=100.5",
        "?min_score=abc",
        "?min_experience=-1",
        "?page=0",
        "?page_size=0",
        "?page_size=101",
    ],
)
async def test_list_candidates_validation_error(
    client: AsyncClient, snapshot: SnapshotAssertion, query: str
) -> None:
    resp = await client.get(f"{API}/campaigns/{UNKNOWN_ID}/candidates{query}")
    assert response_data(resp) == snapshot


async def test_list_candidates_invalid_campaign_id(
    client: AsyncClient, snapshot: SnapshotAssertion
) -> None:
    resp = await client.get(f"{API}/campaigns/not-a-uuid/candidates")
    assert response_data(resp) == snapshot


# ── GET /campaigns/{id}/candidates/export ─────────────────────
@pytest.mark.parametrize(
    "query",
    [
        pytest.param("", id="all"),
        pytest.param("?category=weak_match", id="category"),
        pytest.param("?min_score=55", id="min-score"),
        pytest.param("?max_score=55", id="max-score"),
        pytest.param("?search=example.com", id="search"),
        pytest.param("?pipeline_stage=screened", id="pipeline-stage"),
        # The list endpoint's skill / min_experience filters are silently ignored by the export.
        pytest.param("?skill=PHP&min_experience=5", id="skill-and-experience-ignored"),
    ],
)
async def test_export_candidates_csv(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion, query: str
) -> None:
    campaign = await _seed_pool(db_session)
    resp = await client.get(f"{API}/campaigns/{campaign.id}/candidates/export{query}")
    assert _csv_data(resp) == snapshot


async def test_export_candidates_csv_empty_campaign(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    campaign = await make_campaign(db_session)
    resp = await client.get(f"{API}/campaigns/{campaign.id}/candidates/export")
    assert _csv_data(resp) == snapshot


@pytest.mark.parametrize("query", ["?min_score=101", "?max_score=-1"])
async def test_export_candidates_csv_validation_error(
    client: AsyncClient, snapshot: SnapshotAssertion, query: str
) -> None:
    resp = await client.get(f"{API}/campaigns/{UNKNOWN_ID}/candidates/export{query}")
    assert response_data(resp) == snapshot


# ── GET /candidates/{id} ──────────────────────────────────────
async def test_get_candidate(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    candidate = await make_candidate(db_session, notes="Great call", pipeline_stage="phone_call")
    resp = await client.get(f"{API}/candidates/{candidate.id}")
    assert response_data(resp) == snapshot


async def test_get_candidate_placeholder(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    # The row the extract worker creates before AI scoring: only ids, raw_text and defaults.
    resume = await make_resume(db_session, status="processing")
    candidate = Candidate(resume_id=resume.id, campaign_id=resume.campaign_id, raw_text="text")
    candidate.created_at = candidate.updated_at = at(0)
    db_session.add(candidate)
    await db_session.flush()
    resp = await client.get(f"{API}/candidates/{candidate.id}")
    assert response_data(resp) == snapshot


async def test_get_candidate_not_found(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.get(f"{API}/candidates/{UNKNOWN_ID}")
    assert response_data(resp) == snapshot


async def test_get_candidate_invalid_id(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.get(f"{API}/candidates/not-a-uuid")
    assert response_data(resp) == snapshot


# ── PUT /candidates/{id}/category ─────────────────────────────
@pytest.mark.parametrize("category", ["strong_match", "moderate_match", "weak_match", "rejected"])
async def test_update_category(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion, category: str
) -> None:
    candidate = await make_candidate(db_session, category="weak_match")
    resp = await client.put(
        f"{API}/candidates/{candidate.id}/category", json={"category": category}
    )
    assert response_data(resp) == snapshot


def _sorted_choice_list(detail: str) -> str:
    """The invalid-category message joins a Python ``set``, so its order depends on string hash
    randomisation (it differs between runs). Sort the listed values to keep the snapshot stable."""
    prefix, _, choices = detail.partition(": ")
    return f"{prefix}: {', '.join(sorted(choices.split(', ')))}"


@pytest.mark.parametrize(
    "candidate_exists", [pytest.param(True, id="known"), pytest.param(False, id="unknown-id")]
)
async def test_update_category_invalid_value(
    client: AsyncClient,
    db_session: AsyncSession,
    snapshot: SnapshotAssertion,
    candidate_exists: bool,
) -> None:
    # 400 (not 422), and checked before the candidate is looked up.
    candidate_id = (await make_candidate(db_session)).id if candidate_exists else UNKNOWN_ID
    resp = await client.put(
        f"{API}/candidates/{candidate_id}/category", json={"category": "superstar"}
    )
    data = response_data(resp)
    data["body"]["detail"] = _sorted_choice_list(data["body"]["detail"])
    assert data == snapshot


async def test_update_category_not_found(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.put(
        f"{API}/candidates/{UNKNOWN_ID}/category", json={"category": "rejected"}
    )
    assert response_data(resp) == snapshot


@pytest.mark.parametrize(
    "payload",
    [pytest.param({}, id="missing-category"), pytest.param({"category": None}, id="null")],
)
async def test_update_category_validation_error(
    client: AsyncClient, snapshot: SnapshotAssertion, payload: Any
) -> None:
    resp = await client.put(f"{API}/candidates/{UNKNOWN_ID}/category", json=payload)
    assert response_data(resp) == snapshot


# ── PATCH /candidates/{id}/notes ──────────────────────────────
@pytest.mark.parametrize("notes", ["Strong on system design; follow up re: visa.", ""])
async def test_update_notes(
    client: AsyncClient,
    db_session: AsyncSession,
    snapshot: SnapshotAssertion,
    notes: str,
) -> None:
    candidate = await make_candidate(db_session, notes="old note")
    resp = await client.patch(f"{API}/candidates/{candidate.id}/notes", json={"notes": notes})
    assert response_data(resp) == snapshot


async def test_update_notes_not_found(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.patch(f"{API}/candidates/{UNKNOWN_ID}/notes", json={"notes": "x"})
    assert response_data(resp) == snapshot


@pytest.mark.parametrize(
    "payload",
    [pytest.param({}, id="missing-notes"), pytest.param({"notes": None}, id="null-notes")],
)
async def test_update_notes_validation_error(
    client: AsyncClient, snapshot: SnapshotAssertion, payload: Any
) -> None:
    resp = await client.patch(f"{API}/candidates/{UNKNOWN_ID}/notes", json=payload)
    assert response_data(resp) == snapshot


# ── PATCH /candidates/{id}/pipeline ───────────────────────────
@pytest.mark.parametrize(
    "stage", ["screened", "phone_call", "technical", "offer", "hired", "rejected_manual"]
)
async def test_update_pipeline(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion, stage: str
) -> None:
    candidate = await make_candidate(db_session)
    resp = await client.patch(f"{API}/candidates/{candidate.id}/pipeline", json={"stage": stage})
    assert response_data(resp) == snapshot


@pytest.mark.parametrize(
    "candidate_exists", [pytest.param(True, id="known"), pytest.param(False, id="unknown-id")]
)
async def test_update_pipeline_invalid_stage(
    client: AsyncClient,
    db_session: AsyncSession,
    snapshot: SnapshotAssertion,
    candidate_exists: bool,
) -> None:
    candidate_id = (await make_candidate(db_session)).id if candidate_exists else UNKNOWN_ID
    resp = await client.patch(
        f"{API}/candidates/{candidate_id}/pipeline", json={"stage": "interviewing"}
    )
    assert response_data(resp) == snapshot


async def test_update_pipeline_not_found(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.patch(f"{API}/candidates/{UNKNOWN_ID}/pipeline", json={"stage": "offer"})
    assert response_data(resp) == snapshot


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="missing-stage"),
        pytest.param({"pipeline_stage": "offer"}, id="wrong-key"),
    ],
)
async def test_update_pipeline_validation_error(
    client: AsyncClient, snapshot: SnapshotAssertion, payload: Any
) -> None:
    resp = await client.patch(f"{API}/candidates/{UNKNOWN_ID}/pipeline", json=payload)
    assert response_data(resp) == snapshot


# ── GET /resumes/{id}/candidate ───────────────────────────────
async def test_get_candidate_by_resume(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    candidate = await make_candidate(db_session)
    resp = await client.get(f"{API}/resumes/{candidate.resume_id}/candidate")
    assert response_data(resp) == snapshot


async def test_get_candidate_by_resume_not_processed_yet(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    resume = await make_resume(db_session, status="pending")
    resp = await client.get(f"{API}/resumes/{resume.id}/candidate")
    assert response_data(resp) == snapshot


async def test_get_candidate_by_resume_unknown_resume(
    client: AsyncClient, snapshot: SnapshotAssertion
) -> None:
    # Same message as "not processed yet": an unknown resume id is indistinguishable.
    resp = await client.get(f"{API}/resumes/{UNKNOWN_ID}/candidate")
    assert response_data(resp) == snapshot


async def test_get_candidate_by_resume_invalid_id(
    client: AsyncClient, snapshot: SnapshotAssertion
) -> None:
    resp = await client.get(f"{API}/resumes/not-a-uuid/candidate")
    assert response_data(resp) == snapshot


# ── PATCH /campaigns/{id}/candidates/bulk-pipeline ────────────
async def _stages(db: AsyncSession) -> list[tuple[str | None, str | None]]:
    db.expire_all()
    rows = await db.execute(
        select(Candidate.name, Candidate.pipeline_stage).order_by(Candidate.name)
    )
    return [(name, stage) for name, stage in rows.all()]


async def test_bulk_update_pipeline(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    campaign = await make_campaign(db_session)
    other = await make_campaign(db_session, title="Other")
    a = await make_candidate(db_session, campaign, name="A", pipeline_stage=None)
    b = await make_candidate(db_session, campaign, name="B", pipeline_stage="screened")
    await make_candidate(db_session, campaign, name="C (not selected)", pipeline_stage=None)
    elsewhere = await make_candidate(db_session, other, name="D (other campaign)")
    payload = {
        "candidate_ids": [str(a.id), str(b.id), str(elsewhere.id), UNKNOWN_ID],
        "stage": "offer",
    }
    # Ids from other campaigns and unknown ids are skipped silently.
    resp = await client.patch(
        f"{API}/campaigns/{campaign.id}/candidates/bulk-pipeline", json=payload
    )
    assert {"response": response_data(resp), "stages": await _stages(db_session)} == snapshot


async def test_bulk_update_pipeline_empty_list(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    campaign = await make_campaign(db_session)
    resp = await client.patch(
        f"{API}/campaigns/{campaign.id}/candidates/bulk-pipeline",
        json={"candidate_ids": [], "stage": "hired"},
    )
    assert response_data(resp) == snapshot


async def test_bulk_update_pipeline_invalid_stage(
    client: AsyncClient, snapshot: SnapshotAssertion
) -> None:
    resp = await client.patch(
        f"{API}/campaigns/{UNKNOWN_ID}/candidates/bulk-pipeline",
        json={"candidate_ids": [UNKNOWN_ID], "stage": "interviewing"},
    )
    assert response_data(resp) == snapshot


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"stage": "offer"}, id="missing-ids"),
        pytest.param({"candidate_ids": ["nope"], "stage": "offer"}, id="invalid-id"),
        pytest.param({"candidate_ids": [UNKNOWN_ID]}, id="missing-stage"),
    ],
)
async def test_bulk_update_pipeline_validation_error(
    client: AsyncClient, snapshot: SnapshotAssertion, payload: Any
) -> None:
    resp = await client.patch(
        f"{API}/campaigns/{UNKNOWN_ID}/candidates/bulk-pipeline", json=payload
    )
    assert response_data(resp) == snapshot


# ── DELETE /campaigns/{id}/candidates/bulk ────────────────────
async def _remaining(db: AsyncSession) -> dict[str, list[str]]:
    db.expire_all()
    candidates = await db.execute(select(Candidate.name).order_by(Candidate.name))
    resumes = await db.execute(select(Resume.original_filename).order_by(Resume.original_filename))
    return {
        "candidates": [n for (n,) in candidates.all()],
        "resumes": [n for (n,) in resumes.all()],
    }


async def _candidate_with_stored_pdf(
    db: AsyncSession, storage: InMemoryStorage, campaign: Campaign, name: str
) -> Candidate:
    resume = await make_resume(db, campaign, original_filename=f"{name}.pdf")
    storage.upload_file(b"%PDF-1.4", resume.storage_path)
    return await make_candidate(db, resume=resume, name=name)


async def test_bulk_delete_candidates(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: InMemoryStorage,
    snapshot: SnapshotAssertion,
) -> None:
    campaign = await make_campaign(db_session)
    other = await make_campaign(db_session, title="Other")
    a = await _candidate_with_stored_pdf(db_session, fake_storage, campaign, "a")
    b = await _candidate_with_stored_pdf(db_session, fake_storage, campaign, "b")
    await _candidate_with_stored_pdf(db_session, fake_storage, campaign, "c_kept")
    elsewhere = await _candidate_with_stored_pdf(db_session, fake_storage, other, "d_other")
    payload = {"candidate_ids": [str(a.id), str(b.id), str(elsewhere.id), UNKNOWN_ID]}

    resp = await client.request(
        "DELETE", f"{API}/campaigns/{campaign.id}/candidates/bulk", json=payload
    )
    assert {
        "response": response_data(resp),
        "remaining": await _remaining(db_session),
        # Stored PDFs are not deleted (orphaned objects).
        "stored_objects": len(fake_storage.objects),
    } == snapshot


async def test_bulk_delete_candidates_empty_list(
    client: AsyncClient, snapshot: SnapshotAssertion
) -> None:
    resp = await client.request(
        "DELETE", f"{API}/campaigns/{UNKNOWN_ID}/candidates/bulk", json={"candidate_ids": []}
    )
    assert response_data(resp) == snapshot


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(None, id="no-body"),
        pytest.param({}, id="missing-ids"),
        pytest.param({"candidate_ids": "abc"}, id="ids-not-a-list"),
    ],
)
async def test_bulk_delete_candidates_validation_error(
    client: AsyncClient, snapshot: SnapshotAssertion, payload: Any
) -> None:
    resp = await client.request(
        "DELETE", f"{API}/campaigns/{UNKNOWN_ID}/candidates/bulk", json=payload
    )
    assert response_data(resp) == snapshot


# ── DELETE /candidates/{id} ───────────────────────────────────
async def test_delete_candidate_also_deletes_resume(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: InMemoryStorage,
    snapshot: SnapshotAssertion,
) -> None:
    campaign = await make_campaign(db_session)
    target = await _candidate_with_stored_pdf(db_session, fake_storage, campaign, "target")
    await _candidate_with_stored_pdf(db_session, fake_storage, campaign, "kept")

    resp = await client.delete(f"{API}/candidates/{target.id}")
    assert {
        "response": {"status_code": resp.status_code, "content": resp.text},
        "remaining": await _remaining(db_session),
        "stored_objects": len(fake_storage.objects),
        "get_after_delete": (await client.get(f"{API}/candidates/{target.id}")).status_code,
    } == snapshot


async def test_delete_candidate_not_found(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.delete(f"{API}/candidates/{UNKNOWN_ID}")
    assert response_data(resp) == snapshot


async def test_delete_candidate_invalid_id(
    client: AsyncClient, snapshot: SnapshotAssertion
) -> None:
    resp = await client.delete(f"{API}/candidates/not-a-uuid")
    assert response_data(resp) == snapshot
