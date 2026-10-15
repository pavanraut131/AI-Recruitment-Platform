"""``FakeAIService``: deterministic, offline, and plausible enough for dev and smoke tests."""

from __future__ import annotations

import pytest

from app.modules.ai.fake import FakeAIService, find_skills
from app.modules.ai.schemas import ExtractionResult, JobRequirements

RESUME = """Jane Doe
jane.doe+cv@example.com | +91 98765 43210
github.com/janedoe | https://www.linkedin.com/in/jane-doe
Senior Backend Engineer at Acme, 6 years of experience
Skills: Python, FastAPI, PostgreSQL, Docker, Kubernetes, Go
B.Tech in Computer Science, IIT Bombay
Project: Resume parser with Python and Redis
AWS Certified Developer
"""


def test_extract_profile_reads_the_obvious_fields() -> None:
    profile = FakeAIService().extract_profile(RESUME)

    assert profile.name == "Jane Doe"
    assert profile.email == "jane.doe+cv@example.com"
    assert profile.phone == "+91 98765 43210"
    assert profile.github_url == "github.com/janedoe"
    assert profile.linkedin_url == "https://www.linkedin.com/in/jane-doe"
    assert profile.skills == [
        "Python",
        "Go",
        "PostgreSQL",
        "Redis",
        "FastAPI",
        "Docker",
        "Kubernetes",
        "AWS",
    ]
    assert profile.years_of_experience == 6.0
    assert [e["degree"] for e in profile.education] == ["B.Tech in Computer Science, IIT Bombay"]
    assert [w["role"] for w in profile.work_experience] == [
        "Senior Backend Engineer at Acme, 6 years of experience",
        "AWS Certified Developer",
    ]
    assert [p["technologies"] for p in profile.projects] == [["Python", "Redis"]]
    assert profile.certifications == ["AWS Certified Developer"]


def test_extract_profile_is_deterministic() -> None:
    assert FakeAIService().extract_profile(RESUME) == FakeAIService().extract_profile(RESUME)


def test_extract_profile_of_unrelated_text_finds_nothing() -> None:
    profile = FakeAIService().extract_profile("lorem ipsum dolor sit amet")

    assert profile == ExtractionResult(name=None, skills=[], years_of_experience=None)


def test_skill_matching_is_case_insensitive_and_whole_word() -> None:
    assert find_skills("python, GOLANG, google, go, c++ and C#, node.js") == [
        "Python",
        "Go",
        "C++",
        "C#",
        "Node.js",
    ]


def test_score_rewards_matching_skills_experience_projects_and_education() -> None:
    fake = FakeAIService()
    profile = fake.extract_profile(RESUME)
    requirements = JobRequirements(
        role="Backend Engineer", required_skills=["python", "Go", "Rust"]
    )

    result = fake.score(profile, requirements)

    assert result.score_breakdown == {
        "skills_match": 27,  # 2 of 3 required skills
        "experience_match": 30,
        "project_relevance": 5,
        "education_bonus": 7,
        "communication_quality": 3,
    }
    assert result.score == 72.0
    assert result.category == "moderate_match"
    assert result.missing_skills == ["Rust"]
    assert result.strengths == ["Knows python", "Knows Go"]
    assert result.recommendation == "Phone screen first"
    assert "Jane Doe matches 2 of 3 required skills" in result.summary
    assert result == fake.score(profile, requirements)


@pytest.mark.parametrize(
    ("skills", "years", "degree", "category"),
    [
        (["Python"], 10, True, "strong_match"),  # 40 + 30 + 7 + 3 = 80
        (["Python"], 2, False, "moderate_match"),  # 40 + 10 + 3 = 53
        (["Python"], 0, False, "weak_match"),  # 40 + 3 = 43
        ([], 4, False, "rejected"),  # 20 + 3 = 23
    ],
)
def test_score_category_follows_the_score_thresholds(
    skills: list[str], years: float, degree: bool, category: str
) -> None:
    education = [{"degree": "BSc"}] if degree else []
    profile = ExtractionResult(skills=skills, years_of_experience=years, education=education)

    result = FakeAIService().score(profile, JobRequirements(role="Dev", required_skills=["Python"]))

    assert result.category == category


async def test_parse_job_description() -> None:
    jd = "Senior Python Engineer. 5+ years with FastAPI, PostgreSQL and AWS."

    result = await FakeAIService().parse_job_description(jd)

    assert result.model_dump() == {
        "role_title": "Senior Python Engineer",
        "seniority": "senior",
        "skills": ["Python", "PostgreSQL", "FastAPI", "AWS"],
    }


@pytest.mark.parametrize(
    ("jd", "seniority"),
    [
        ("Staff Engineer", "staff"),
        ("Junior developer wanted", "junior"),
        ("Backend developer, 7 years", "senior"),
        ("Backend developer", "mid"),
        ("", "mid"),
    ],
)
async def test_parse_job_description_seniority(jd: str, seniority: str) -> None:
    result = await FakeAIService().parse_job_description(jd)

    assert result.seniority == seniority
    assert result.role_title  # never empty ("Unknown Role" for an empty JD)


def test_transcribe_page_images_is_deterministic_and_long_enough_to_process() -> None:
    fake = FakeAIService()

    text = fake.transcribe_page_images([b"page-1", b"page-2"])

    assert text == fake.transcribe_page_images([b"page-1", b"page-2"])
    assert text != fake.transcribe_page_images([b"other"])
    assert "2 page(s)" in text
    assert len(text) >= 50  # the extract task rejects shorter text
