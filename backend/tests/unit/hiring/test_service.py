"""Unit tests for the pure parts of ``app.modules.hiring.service`` and the router's error mapping.

The endpoints themselves are pinned by the characterization snapshots; these cover the business
rules that E0-09 pulled out of the routers, without a database.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.modules.hiring import router, service


def test_build_campaign_stats_aggregates_counts_scores_and_skills() -> None:
    stats = service.build_campaign_stats(
        {
            "done": 3,
            "failed": 1,
            "pending": 1,
            "extracting": 1,
            "processing": 2,
            "quota_exceeded": 1,
        },
        [("strong_match", 2, 90.0), ("rejected", 1, 10.0), (None, 4, None)],
        [["Go", "Python"], ["Python"], None, []],
    )

    assert stats.total_resumes == 9
    assert stats.total_processed == 3
    assert stats.total_failed == 1
    assert stats.total_pending == 4
    assert stats.total_quota_exceeded == 1
    assert stats.avg_score == 50.0  # mean of the per-category averages, as before
    assert (stats.strong_match, stats.moderate_match, stats.weak_match, stats.rejected) == (
        2,
        0,
        0,
        1,
    )
    assert stats.top_skills == [{"skill": "Python", "count": 2}, {"skill": "Go", "count": 1}]


def test_build_campaign_stats_empty_and_zero_average() -> None:
    # A per-category average of 0.0 is falsy and has always been left out of avg_score.
    stats = service.build_campaign_stats({}, [("rejected", 1, 0.0)], [])
    assert stats.total_resumes == 0
    assert stats.avg_score is None
    assert stats.top_skills == []


def test_build_campaign_stats_top_skills_are_capped_and_ties_keep_first_seen_order() -> None:
    skills = [f"s{i:02}" for i in range(12)]
    stats = service.build_campaign_stats({}, [], [skills, ["s11"]])
    assert stats.top_skills[0] == {"skill": "s11", "count": 2}
    assert [s["skill"] for s in stats.top_skills[1:]] == skills[:9]


def test_build_global_stats() -> None:
    stats = service.build_global_stats(
        {"active": 2, "closed": 1, "paused": 1},
        {"pending": 1, "extracting": 1, "processing": 1, "done": 4, "failed": 2},
        7,
    )
    assert stats.model_dump() == {
        "total_campaigns": 4,
        "active_campaigns": 2,
        "closed_campaigns": 1,
        "archived_campaigns": 0,
        "total_resumes": 9,
        "total_processed": 4,
        "total_processing": 3,
        "total_failed": 2,
        "total_candidates": 7,
    }


def _sql(filters: service.CandidateFilters) -> list[str]:
    dialect = postgresql.dialect()
    return [str(c.compile(dialect=dialect)) for c in service.build_candidate_filters(filters)]


def test_candidate_filters_default_to_the_campaign_only() -> None:
    assert _sql(service.CandidateFilters(campaign_id=uuid.uuid4())) == [
        "candidates.campaign_id = %(campaign_id_1)s::UUID"
    ]


def test_candidate_filters_build_one_clause_per_filter() -> None:
    clauses = _sql(
        service.CandidateFilters(
            campaign_id=uuid.uuid4(),
            skill="py",
            category="strong_match",
            min_score=1,
            max_score=2,
            min_experience=3,
            search="ada",
            pipeline_stage="offer",
        )
    )
    assert len(clauses) == 8
    assert any("jsonb_array_elements_text(candidates.skills)" in c for c in clauses)
    assert any("candidates.name ILIKE" in c and "candidates.email ILIKE" in c for c in clauses)


def test_empty_string_filters_are_ignored() -> None:
    filters = service.CandidateFilters(
        campaign_id=uuid.uuid4(), skill="", category="", search="", pipeline_stage=""
    )
    assert len(service.build_candidate_filters(filters)) == 1


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (service.NotFoundError("Candidate not found"), 404),
        (service.InvalidInputError("Invalid stage. Must be one of: x"), 400),
        (service.JDParseError("Failed to parse job description"), 500),
    ],
)
def test_router_maps_service_errors_to_http(error: service.HiringError, status_code: int) -> None:
    with pytest.raises(HTTPException) as excinfo, router._http_errors():
        raise error
    assert excinfo.value.status_code == status_code
    assert excinfo.value.detail == error.detail


def test_every_service_error_has_a_status_code() -> None:
    assert set(router._STATUS_CODES) == set(service.HiringError.__subclasses__())
