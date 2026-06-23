import uuid
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


class CandidateListItem(BaseModel):
    id: uuid.UUID
    resume_id: uuid.UUID
    campaign_id: uuid.UUID
    name: Optional[str]
    email: Optional[str]
    skills: List[str]
    years_of_experience: Optional[float]
    score: Optional[float]
    category: Optional[str]
    summary: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class CandidateDetail(BaseModel):
    id: uuid.UUID
    resume_id: uuid.UUID
    campaign_id: uuid.UUID
    name: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    github_url: Optional[str]
    linkedin_url: Optional[str]
    skills: List[str]
    education: List[dict]
    work_experience: List[dict]
    certifications: List[str]
    projects: List[dict]
    years_of_experience: Optional[float]
    summary: Optional[str]
    strengths: List[str]
    weaknesses: List[str]
    missing_skills: List[str]
    recommendation: Optional[str]
    score: Optional[float]
    category: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CandidateCategoryUpdate(BaseModel):
    category: str


class CandidateFilterParams(BaseModel):
    skill: Optional[str] = None
    min_score: Optional[float] = None
    max_score: Optional[float] = None
    category: Optional[str] = None
    min_experience: Optional[float] = None
    search: Optional[str] = None
    page: int = 1
    page_size: int = 20


# ── Gemini response contracts ──────────────────────────────────
class ExtractionResult(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    github_url: Optional[str] = None
    linkedin_url: Optional[str] = None
    skills: List[str] = []
    education: List[dict] = []
    work_experience: List[dict] = []
    certifications: List[str] = []
    projects: List[dict] = []
    years_of_experience: Optional[float] = None


class ScoringResult(BaseModel):
    score: float
    category: str  # strong_match | moderate_match | weak_match | rejected
    summary: str
    strengths: List[str] = []
    weaknesses: List[str] = []
    missing_skills: List[str] = []
    recommendation: str
