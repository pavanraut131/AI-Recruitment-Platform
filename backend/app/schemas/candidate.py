import uuid
from datetime import datetime

from pydantic import BaseModel


class CandidateListItem(BaseModel):
    id: uuid.UUID
    resume_id: uuid.UUID
    campaign_id: uuid.UUID
    name: str | None
    email: str | None
    skills: list[str]
    years_of_experience: float | None
    score: float | None
    category: str | None
    summary: str | None
    pipeline_stage: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CandidateDetail(BaseModel):
    id: uuid.UUID
    resume_id: uuid.UUID
    campaign_id: uuid.UUID
    name: str | None
    email: str | None
    phone: str | None
    github_url: str | None
    linkedin_url: str | None
    skills: list[str]
    education: list[dict]
    work_experience: list[dict]
    certifications: list[str]
    projects: list[dict]
    years_of_experience: float | None
    summary: str | None
    strengths: list[str]
    weaknesses: list[str]
    missing_skills: list[str]
    recommendation: str | None
    score: float | None
    category: str | None
    notes: str | None = None
    pipeline_stage: str | None = None
    score_breakdown: dict | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CandidateCategoryUpdate(BaseModel):
    category: str


class CandidateNotesUpdate(BaseModel):
    notes: str


class CandidatePipelineUpdate(BaseModel):
    stage: str


class CandidateFilterParams(BaseModel):
    skill: str | None = None
    min_score: float | None = None
    max_score: float | None = None
    category: str | None = None
    min_experience: float | None = None
    search: str | None = None
    page: int = 1
    page_size: int = 20


# ── Gemini response contracts ──────────────────────────────────
class ExtractionResult(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    github_url: str | None = None
    linkedin_url: str | None = None
    skills: list[str] = []
    education: list[dict] = []
    work_experience: list[dict] = []
    certifications: list[str] = []
    projects: list[dict] = []
    years_of_experience: float | None = None


class ScoringResult(BaseModel):
    score: float
    score_breakdown: dict | None = (
        None  # {skills_match, experience_match, project_relevance, education_bonus, communication_quality}
    )
    category: str  # strong_match | moderate_match | weak_match | rejected
    summary: str
    strengths: list[str] = []
    weaknesses: list[str] = []
    missing_skills: list[str] = []
    recommendation: str
