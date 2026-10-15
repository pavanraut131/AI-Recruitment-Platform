"""Gemini response contracts used by ``app.services.gemini`` and the AI worker.

The candidate API schemas moved to ``app.modules.hiring.schemas`` (E0-09). These two models are
AI types, not hiring ones. TODO(E0-10): move them into the ``ai`` module (spec section 3).
"""

from typing import Any

from pydantic import BaseModel


# ── Gemini response contracts ──────────────────────────────────
class ExtractionResult(BaseModel):
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
