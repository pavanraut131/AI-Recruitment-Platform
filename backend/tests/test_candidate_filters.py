"""Candidate list `skill` / `search` filters (B2), against real Postgres."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql import escape_like
from app.models.campaign import Campaign
from tests.factories import at, make_campaign, make_candidate, make_resume

API = "/api/v1"


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        ("python", "python"),
        ("", ""),
        ("c_plus", "c\\_plus"),
        ("100%", "100\\%"),
        ("a\\b", "a\\\\b"),
        ("\\%_", "\\\\\\%\\_"),
    ],
)
def test_escape_like(term: str, expected: str) -> None:
    assert escape_like(term) == expected


async def _seed(db: AsyncSession) -> Campaign:
    campaign = await make_campaign(db)
    rows = [
        ("Ada", 90.0, "strong_match", ["Python", "FastAPI"]),
        ("Ben", 70.0, "moderate_match", ["PYTHON", "Django"]),
        ("Cid", 50.0, "weak_match", ["C_plus", "Go"]),
        ("Dee", 40.0, "weak_match", ["C+plus", "100% remote"]),
        ("Eve", 30.0, "rejected", ["Rust", "a\\b"]),
        ("Fay", 20.0, "rejected", []),
    ]
    for i, (name, score, category, skills) in enumerate(rows):
        resume = await make_resume(db, campaign, original_filename=f"{name}.pdf", uploaded_at=at(i))
        await make_candidate(
            db,
            resume=resume,
            created_at=at(i),
            name=name,
            email=f"{name.lower()}@example.com",
            score=score,
            category=category,
            skills=skills,
        )
    # Same skill in another campaign must never leak in.
    other = await make_campaign(db, title="Other")
    await make_candidate(db, other, name="Zed", skills=["Python"])
    return campaign


async def _names(client: AsyncClient, campaign: Campaign, query: str) -> list[str]:
    resp = await client.get(f"{API}/campaigns/{campaign.id}/candidates", params=query)
    assert resp.status_code == 200, resp.text
    return [c["name"] for c in resp.json()]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        pytest.param("skill=python", ["Ada", "Ben"], id="match-any-case"),
        pytest.param("skill=PyThOn", ["Ada", "Ben"], id="mixed-case-term"),
        pytest.param("skill=api", ["Ada"], id="substring-of-an-element"),
        pytest.param("skill=kotlin", [], id="no-match"),
        pytest.param("skill=c_plus", ["Cid"], id="underscore-is-literal"),
        pytest.param("skill=_", ["Cid"], id="lone-underscore-is-literal"),
        pytest.param("skill=100%25", ["Dee"], id="percent-is-literal"),
        pytest.param("skill=%25", ["Dee"], id="lone-percent-is-literal"),
        pytest.param("skill=a%5Cb", ["Eve"], id="backslash-is-literal"),
        pytest.param("skill=%22", [], id="json-quote-never-matches"),
        pytest.param("skill=python&category=moderate_match", ["Ben"], id="with-category"),
        pytest.param("skill=python&min_score=80", ["Ada"], id="with-min-score"),
        pytest.param("skill=plus&min_score=45&max_score=60", ["Cid"], id="with-score-range"),
        pytest.param("skill=python&search=be", ["Ben"], id="with-search"),
        pytest.param("skill=python&page_size=1&page=2", ["Ben"], id="with-pagination"),
        pytest.param("", ["Ada", "Ben", "Cid", "Dee", "Eve", "Fay"], id="no-filter-keeps-empty"),
    ],
)
async def test_skill_filter(
    client: AsyncClient, db_session: AsyncSession, query: str, expected: list[str]
) -> None:
    campaign = await _seed(db_session)
    assert await _names(client, campaign, query) == expected


async def test_skill_filter_never_matches_empty_skills(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    campaign = await _seed(db_session)
    # "%" (escaped) and a single letter present in most skills: Fay (no skills) never appears.
    assert "Fay" not in await _names(client, campaign, "skill=o")
    assert "Fay" not in await _names(client, campaign, "skill=%25")


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        pytest.param("search=_", [], id="underscore-is-literal"),
        pytest.param("search=%25", [], id="percent-is-literal"),
        pytest.param("search=ADA", ["Ada"], id="case-insensitive"),
    ],
)
async def test_search_escapes_like_wildcards(
    client: AsyncClient, db_session: AsyncSession, query: str, expected: list[str]
) -> None:
    campaign = await _seed(db_session)
    assert await _names(client, campaign, query) == expected
