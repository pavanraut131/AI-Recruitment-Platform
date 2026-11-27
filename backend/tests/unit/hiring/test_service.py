"""Unit tests for the pure parts of ``app.modules.hiring.service``, without a database.

The endpoints themselves are pinned by the contract snapshots (``tests/contract_v2``); these
cover the business rules: which filters read the extraction snapshot, the mean score (B9), the
top skills and the CSV row of an application. (Until E2-12 they covered the v1 stats builders,
the v1 candidate filters and the v1 router's error mapping, removed with v1.)
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime

from sqlalchemy.dialects import postgresql

from app.modules.hiring import service
from app.modules.hiring.models import Application
from app.modules.hiring.schemas_v2 import SkillCount


# ── The application filters ───────────────────────────────────
def _sql(query: service.ApplicationQuery) -> list[str]:
    dialect = postgresql.dialect()
    return [str(c.compile(dialect=dialect)) for c in service._application_clauses(query)]


def test_application_filters_default_to_the_campaign_only() -> None:
    query = service.ApplicationQuery(campaign_id=uuid.uuid4())
    assert _sql(query) == ["applications.campaign_id = %(campaign_id_1)s::UUID"]
    assert not query.profile().active


def test_application_filters_split_into_application_and_profile_filters() -> None:
    query = service.ApplicationQuery(
        campaign_id=uuid.uuid4(),
        q="ada",
        categories=["strong_match"],
        stages=["offer"],
        min_score=1,
        max_score=2,
        skill="py",
        min_experience=3,
        ids=[uuid.uuid4()],
    )
    # On applications: campaign, categories, stages, score range, ids.
    clauses = _sql(query)
    assert len(clauses) == 6
    assert all(c.startswith("applications.") for c in clauses)
    # On the resume's extraction snapshot (ingestion): q, skill, experience.
    assert query.profile() == service.ProfileFilters(search="ada", skill="py", min_experience=3)
    assert query.profile().active


def test_empty_string_profile_filters_are_ignored() -> None:
    query = service.ApplicationQuery(campaign_id=uuid.uuid4(), q="", skill="")
    assert query.profile() == service.ProfileFilters()
    assert not query.profile().active


def test_zero_min_experience_is_a_profile_filter() -> None:
    query = service.ApplicationQuery(campaign_id=uuid.uuid4(), min_experience=0)
    assert query.profile().active


# ── Stats ─────────────────────────────────────────────────────
def test_the_mean_score_is_over_every_scored_application() -> None:
    # (category, applications, scored, sum of scores): a 0 mean counts (B9).
    rows = [("strong_match", 2, 2, 180.0), ("rejected", 1, 1, 0.0), (None, 4, 0, None)]
    assert service.mean_score(rows) == 60.0
    assert service.mean_score([(None, 3, 0, None)]) is None


def test_top_skills_are_capped_flag_required_ones_and_keep_first_seen_order_on_ties() -> None:
    skills = [f"s{i:02}" for i in range(12)]
    top = service.top_skills([skills, ["s11"], None, []], required=["S03"])
    assert top[0] == SkillCount(skill="s11", count=2, required=False)
    assert [s.skill for s in top[1:]] == skills[:9]
    assert [s.skill for s in top if s.required] == ["s03"]


# ── The CSV export ────────────────────────────────────────────
def test_an_applications_csv_row_takes_per_resume_facts_from_its_snapshot() -> None:
    at = datetime(2026, 1, 1, tzinfo=UTC)
    application = Application(
        id=uuid.uuid4(),
        campaign_id=uuid.uuid4(),
        person_id=uuid.uuid4(),
        resume_id=uuid.uuid4(),
        score=87.5,
        category="strong_match",
        pipeline_stage="offer",
        strengths=["Python", "APIs"],
        weaknesses=[],
        missing_skills=["Go"],
        recommendation="Proceed",
        created_at=at,
        updated_at=at,
    )
    extracted = {
        "name": "Ada",
        "email": "ada@example.com",
        "skills": ["Python", "SQL"],
        "years_of_experience": 4,
        "linkedin_url": "https://linkedin.example/ada",
    }
    text = service.render_applications_csv([(application, extracted), (application, None)])

    rows = list(csv.DictReader(io.StringIO(text)))
    assert list(rows[0]) == service.CSV_FIELDS
    assert rows[0] == {
        "Name": "Ada",
        "Email": "ada@example.com",
        "Phone": "",
        "Score": "88",
        "Category": "strong_match",
        "Pipeline Stage": "offer",
        "Years Experience": "4.0",
        "Skills": "Python | SQL",
        "Missing Skills": "Go",
        "Strengths": "Python | APIs",
        "Recommendation": "Proceed",
        "LinkedIn URL": "https://linkedin.example/ada",
        "GitHub URL": "",
    }
    # Without a snapshot: no per-resume facts, the application's own fields still there.
    assert (rows[1]["Name"], rows[1]["Skills"], rows[1]["Score"]) == ("", "", "88")
