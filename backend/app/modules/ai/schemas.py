"""ai data contracts: what the language model returns, and what it is asked about.

Public: other modules may import these (import-linter allows ``app.modules.ai.schemas``).
``ExtractionResult`` and ``ScoringResult`` moved here from ``app.schemas.candidate`` (E0-10)
unchanged; they are the shapes every :class:`~app.modules.ai.service.AIService` implementation
returns, whatever the provider.
"""

from typing import Any

from pydantic import BaseModel


class ExtractionResult(BaseModel):
    """A candidate profile extracted from resume text."""

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    github_url: str | None = None
    linkedin_url: str | None = None
    skills: list[str] = []
    education: list[dict[str, Any]] = []
    work_experience: list[dict[str, Any]] = []
    certifications: list[str] = []
    projects: list[dict[str, Any]] = []
    years_of_experience: float | None = None


class ScoringResult(BaseModel):
    """A candidate's fit for a role."""

    score: float
    score_breakdown: dict[str, Any] | None = (
        None  # {skills_match, experience_match, project_relevance, education_bonus, communication_quality}
    )
    category: str  # strong_match | moderate_match | weak_match | rejected
    summary: str
    strengths: list[str] = []
    weaknesses: list[str] = []
    missing_skills: list[str] = []
    recommendation: str


class JobRequirements(BaseModel):
    """What a candidate is scored against."""

    role: str
    required_skills: list[str] = []


class JDParseResult(BaseModel):
    """The key facts of a job description."""

    role_title: str
    seniority: str  # junior | mid | senior | staff
    skills: list[str]
