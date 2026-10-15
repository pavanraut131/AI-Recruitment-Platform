"""Characterization: /api/v1/campaigns CRUD, stats and parse-jd. Records current behaviour."""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from syrupy.assertion import SnapshotAssertion

from app.models.campaign import Campaign
from app.models.candidate import Candidate
from app.models.resume import Resume
from tests.characterization.snapshot_utils import response_data
from tests.factories import at, make_campaign, make_candidate, make_resume
from tests.fakes import FakeGemini, InMemoryStorage

API = "/api/v1"
UNKNOWN_ID = "00000000-0000-0000-0000-000000000000"


async def _seed_campaign_with_results(db: AsyncSession) -> Campaign:
    """A campaign with resumes in every status and candidates in every category.

    Scores per category: strong_match 90 and 80 (avg 85), weak_match 20, rejected 0, plus one
    unscored placeholder candidate (category and score NULL).
    """
    campaign = await make_campaign(db)
    for i, status in enumerate(
        ["pending", "extracting", "processing", "failed", "quota_exceeded", "done"]
    ):
        await make_resume(
            db, campaign, original_filename=f"{status}.pdf", status=status, uploaded_at=at(i)
        )
    scored = [
        ("strong_match", 90.0, ["Python", "FastAPI", "Docker"]),
        ("strong_match", 80.0, ["Python", "FastAPI"]),
        ("weak_match", 20.0, ["Python"]),
        ("rejected", 0.0, []),
    ]
    for i, (category, score, skills) in enumerate(scored):
        resume = await make_resume(db, campaign, original_filename=f"scored_{i}.pdf")
        await make_candidate(db, resume=resume, category=category, score=score, skills=skills)
    placeholder = await make_resume(db, campaign, original_filename="placeholder.pdf")
    await make_candidate(db, resume=placeholder, category=None, score=None, skills=[])
    return campaign


# ── POST /campaigns ───────────────────────────────────────────
async def test_create_campaign(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    payload = {
        "title": "AI Engineer Batch 1",
        "role": "AI Engineer",
        "description": "LLM tooling team",
        "required_skills": ["Python", "FastAPI", "LLMs"],
    }
    resp = await client.post(f"{API}/campaigns", json=payload)
    assert response_data(resp) == snapshot


async def test_create_campaign_minimal_payload(
    client: AsyncClient, snapshot: SnapshotAssertion
) -> None:
    resp = await client.post(f"{API}/campaigns", json={"title": "Minimal", "role": "Dev"})
    assert response_data(resp) == snapshot


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(None, id="no-body"),
        pytest.param({}, id="empty-object"),
        pytest.param({"title": "Missing role"}, id="missing-role"),
        pytest.param({"title": 123, "role": "Dev"}, id="title-not-a-string"),
        pytest.param(
            {"title": "T", "role": "Dev", "required_skills": "Python"}, id="skills-not-a-list"
        ),
    ],
)
async def test_create_campaign_validation_error(
    client: AsyncClient, snapshot: SnapshotAssertion, payload: Any
) -> None:
    resp = await client.post(f"{API}/campaigns", json=payload)
    assert response_data(resp) == snapshot


async def test_create_campaign_title_too_long_is_a_500(
    raw_client: AsyncClient, snapshot: SnapshotAssertion
) -> None:
    # BUG (recorded): no length validation; the VARCHAR(255) overflow surfaces as a 500.
    resp = await raw_client.post(f"{API}/campaigns", json={"title": "x" * 256, "role": "Dev"})
    assert response_data(resp) == snapshot


# ── GET /campaigns ────────────────────────────────────────────
async def test_list_campaigns_empty(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.get(f"{API}/campaigns")
    assert response_data(resp) == snapshot


@pytest.mark.parametrize(
    "query",
    ["", "?skip=1", "?limit=2", "?skip=1&limit=1", "?skip=10", "?limit=0"],
)
async def test_list_campaigns_order_and_pagination(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion, query: str
) -> None:
    # Newest first (created_at DESC); stats is always null in the list.
    await make_campaign(db_session, title="Oldest", created_at=at(0))
    await make_campaign(db_session, title="Middle", status="closed", created_at=at(1))
    await make_campaign(db_session, title="Newest", status="archived", created_at=at(2))
    resp = await client.get(f"{API}/campaigns{query}")
    assert response_data(resp) == snapshot


@pytest.mark.parametrize("query", ["?skip=abc", "?limit=1.5"])
async def test_list_campaigns_validation_error(
    client: AsyncClient, snapshot: SnapshotAssertion, query: str
) -> None:
    resp = await client.get(f"{API}/campaigns{query}")
    assert response_data(resp) == snapshot


@pytest.mark.parametrize("query", ["?skip=-1", "?limit=-1"])
async def test_list_campaigns_negative_paging_is_a_500(
    raw_client: AsyncClient, snapshot: SnapshotAssertion, query: str
) -> None:
    # BUG (recorded): skip/limit are not bounded; Postgres rejects negative OFFSET/LIMIT -> 500.
    resp = await raw_client.get(f"{API}/campaigns{query}")
    assert response_data(resp) == snapshot


# ── GET /campaigns/{id} ───────────────────────────────────────
async def test_get_campaign_without_resumes(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    campaign = await make_campaign(db_session)
    resp = await client.get(f"{API}/campaigns/{campaign.id}")
    assert response_data(resp) == snapshot


async def test_get_campaign_includes_stats(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    campaign = await _seed_campaign_with_results(db_session)
    resp = await client.get(f"{API}/campaigns/{campaign.id}")
    assert response_data(resp) == snapshot


async def test_get_campaign_not_found(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.get(f"{API}/campaigns/{UNKNOWN_ID}")
    assert response_data(resp) == snapshot


async def test_get_campaign_invalid_id(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.get(f"{API}/campaigns/not-a-uuid")
    assert response_data(resp) == snapshot


# ── PUT /campaigns/{id} ───────────────────────────────────────
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"title": "Renamed"}, id="partial-title"),
        pytest.param(
            {
                "title": "Platform Engineer - Q2",
                "role": "Platform Engineer",
                "description": None,
                "required_skills": ["Go", "Kubernetes"],
                "status": "closed",
            },
            id="all-fields",
        ),
        pytest.param({}, id="empty-object"),
        # BUG (recorded): status is free text, any value is stored.
        pytest.param({"status": "banana"}, id="unknown-status-accepted"),
        pytest.param({"unknown_field": "ignored"}, id="unknown-field-ignored"),
    ],
)
async def test_update_campaign(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion, payload: Any
) -> None:
    campaign = await make_campaign(db_session)
    resp = await client.put(f"{API}/campaigns/{campaign.id}", json=payload)
    assert response_data(resp) == snapshot


async def test_update_campaign_not_found(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.put(f"{API}/campaigns/{UNKNOWN_ID}", json={"title": "x"})
    assert response_data(resp) == snapshot


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(None, id="no-body"),
        pytest.param({"required_skills": "Python"}, id="skills-not-a-list"),
    ],
)
async def test_update_campaign_validation_error(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion, payload: Any
) -> None:
    campaign = await make_campaign(db_session)
    resp = await client.put(f"{API}/campaigns/{campaign.id}", json=payload)
    assert response_data(resp) == snapshot


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"title": None}, id="null-title"),
        pytest.param({"status": "s" * 21}, id="status-too-long"),
    ],
)
async def test_update_campaign_db_constraint_is_a_500(
    raw_client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion, payload: Any
) -> None:
    # BUG (recorded): CampaignUpdate allows null/oversized values the table rejects -> 500.
    campaign = await make_campaign(db_session)
    resp = await raw_client.put(f"{API}/campaigns/{campaign.id}", json=payload)
    assert response_data(resp) == snapshot


# ── DELETE /campaigns/{id} ────────────────────────────────────
async def test_delete_campaign_cascades_to_resumes_and_candidates(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: InMemoryStorage,
    snapshot: SnapshotAssertion,
) -> None:
    campaign = await make_campaign(db_session)
    other = await make_campaign(db_session, title="Other campaign")
    candidate = await make_candidate(db_session, campaign)
    await make_resume(db_session, campaign, original_filename="unprocessed.pdf", status="pending")
    await make_candidate(db_session, other)
    resume = await db_session.get(Resume, candidate.resume_id)
    assert resume is not None
    fake_storage.upload_file(b"%PDF-1.4", resume.storage_path)

    resp = await client.delete(f"{API}/campaigns/{campaign.id}")

    db_session.expire_all()

    async def count(model: Any) -> int:
        return (await db_session.execute(select(func.count()).select_from(model))).scalar_one()

    assert {
        "response": {"status_code": resp.status_code, "content": resp.text},
        "rows_left": {
            "campaigns": await count(Campaign),
            "resumes": await count(Resume),
            "candidates": await count(Candidate),
        },
        # The PDF in object storage is not deleted (orphaned).
        "stored_object_keys_left": len(fake_storage.objects),
        "get_after_delete": (await client.get(f"{API}/campaigns/{campaign.id}")).status_code,
    } == snapshot


async def test_delete_campaign_not_found(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.delete(f"{API}/campaigns/{UNKNOWN_ID}")
    assert response_data(resp) == snapshot


async def test_delete_campaign_invalid_id(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.delete(f"{API}/campaigns/not-a-uuid")
    assert response_data(resp) == snapshot


# ── GET /campaigns/{id}/stats ─────────────────────────────────
async def test_campaign_stats_empty(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    campaign = await make_campaign(db_session)
    resp = await client.get(f"{API}/campaigns/{campaign.id}/stats")
    assert response_data(resp) == snapshot


async def test_campaign_stats_populated(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    # avg_score is the mean of the per-category means: (85 + 20) / 2 = 52.5. The rejected
    # category's mean of 0 is dropped (falsy), and the per-candidate mean would be 47.5.
    campaign = await _seed_campaign_with_results(db_session)
    await make_candidate(db_session, await make_campaign(db_session, title="Other"), score=100.0)
    resp = await client.get(f"{API}/campaigns/{campaign.id}/stats")
    assert response_data(resp) == snapshot


async def test_campaign_stats_top_skills_capped_at_ten(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    # Candidate i has skills S01..S(11-i), so S01 appears 11 times, S02 10 times, ..., S11 once.
    campaign = await make_campaign(db_session)
    skills = [f"S{n:02d}" for n in range(1, 12)]
    for i in range(11):
        resume = await make_resume(db_session, campaign, original_filename=f"r{i}.pdf")
        await make_candidate(db_session, resume=resume, skills=skills[: 11 - i])
    resp = await client.get(f"{API}/campaigns/{campaign.id}/stats")
    assert response_data(resp) == snapshot


async def test_campaign_stats_unknown_campaign(
    client: AsyncClient, snapshot: SnapshotAssertion
) -> None:
    # Unknown campaign is not a 404: all-zero stats.
    resp = await client.get(f"{API}/campaigns/{UNKNOWN_ID}/stats")
    assert response_data(resp) == snapshot


async def test_campaign_stats_invalid_id(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.get(f"{API}/campaigns/not-a-uuid/stats")
    assert response_data(resp) == snapshot


# ── POST /campaigns/parse-jd ──────────────────────────────────
JD = "Senior Python Engineer. 5+ years with FastAPI, PostgreSQL and AWS."


@pytest.mark.parametrize(
    "gemini_response",
    [
        pytest.param(
            json.dumps(
                {
                    "role_title": "Senior Python Engineer",
                    "seniority": "senior",
                    "skills": ["Python", "FastAPI", "PostgreSQL", "AWS"],
                }
            ),
            id="plain-json",
        ),
        pytest.param(
            '```json\n{"role_title": "Senior Python Engineer", "seniority": "senior", '
            '"skills": ["Python"]}\n```',
            id="fenced-json",
        ),
        pytest.param("{}", id="json-missing-keys"),
        pytest.param('{"role_title": "Senior Python Engineer"}', id="json-partial-keys"),
        pytest.param(
            '{"role_title": "Senior Python Engineer", "seniority": "senior", "skills": "Python"}',
            id="json-wrong-types",
        ),
        pytest.param('["Python", "FastAPI"]', id="json-not-object"),
        pytest.param("not json at all", id="invalid-json"),
        pytest.param(RuntimeError("Gemini unavailable"), id="gemini-error"),
    ],
)
async def test_parse_jd(
    client: AsyncClient,
    fake_gemini: FakeGemini,
    snapshot: SnapshotAssertion,
    gemini_response: str | Exception,
) -> None:
    # Fixed in B1: this used to be a 500 for every request (PROMPT.format() raised KeyError
    # before Gemini was called). A JSON object -> 200, missing keys fall back to defaults.
    # Output that does not fit the response model (invalid JSON, not an object, wrong types)
    # and Gemini errors -> the generic 500 "Failed to parse job description".
    fake_gemini.respond_with(gemini_response)
    resp = await client.post(f"{API}/campaigns/parse-jd", json={"job_description": JD})
    assert {
        "response": response_data(resp),
        # The prompt does not depend on Gemini's answer; test_parse_jd_prompt records it once.
        "gemini_calls": [{"model": c.model} for c in fake_gemini.calls],
    } == snapshot


async def test_parse_jd_prompt(
    client: AsyncClient, fake_gemini: FakeGemini, snapshot: SnapshotAssertion
) -> None:
    # The exact prompt sent to Gemini, as a list of lines: a multi-line string would be written
    # with whitespace-only lines, which the trailing-whitespace pre-commit hook would strip.
    fake_gemini.respond_with("{}")
    await client.post(f"{API}/campaigns/parse-jd", json={"job_description": JD})
    assert [c.prompt.splitlines() for c in fake_gemini.calls] == snapshot


async def test_parse_jd_without_api_key(
    client: AsyncClient,
    fake_gemini: FakeGemini,
    monkeypatch: pytest.MonkeyPatch,
    snapshot: SnapshotAssertion,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    resp = await client.post(f"{API}/campaigns/parse-jd", json={"job_description": JD})
    assert {"response": response_data(resp), "gemini_calls": len(fake_gemini.calls)} == snapshot


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(None, id="no-body"),
        pytest.param({}, id="missing-job-description"),
        pytest.param({"job_description": None}, id="null-job-description"),
    ],
)
async def test_parse_jd_validation_error(
    client: AsyncClient, snapshot: SnapshotAssertion, payload: Any
) -> None:
    resp = await client.post(f"{API}/campaigns/parse-jd", json=payload)
    assert response_data(resp) == snapshot
