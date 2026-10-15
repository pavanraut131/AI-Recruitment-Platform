"""hiring request/response schemas (the public API contract of the hiring endpoints).

Class names are part of the published OpenAPI schema (``components.schemas``); renaming one is
an API change recorded by the characterization snapshots.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


# ── Campaigns ─────────────────────────────────────────────────
class CampaignCreate(BaseModel):
    title: str
    role: str
    description: str | None = None
    required_skills: list[str] = []


class CampaignUpdate(BaseModel):
    title: str | None = None
    role: str | None = None
    description: str | None = None
    required_skills: list[str] | None = None
    status: str | None = None


class CampaignStats(BaseModel):
    total_resumes: int = 0
    total_processed: int = 0
    total_failed: int = 0
    total_pending: int = 0
    total_quota_exceeded: int = 0
    avg_score: float | None = None
    strong_match: int = 0
    moderate_match: int = 0
    weak_match: int = 0
    rejected: int = 0
    top_skills: list[dict[str, Any]] = []


class CampaignResponse(BaseModel):
    id: uuid.UUID
    title: str
    role: str
    description: str | None
    required_skills: list[str]
    status: str
    created_at: datetime
    updated_at: datetime
    stats: CampaignStats | None = None

    model_config = ConfigDict(from_attributes=True)


# ── Global stats ──────────────────────────────────────────────
class GlobalStats(BaseModel):
    total_campaigns: int = 0
    active_campaigns: int = 0
    closed_campaigns: int = 0
    archived_campaigns: int = 0
    total_resumes: int = 0
    total_processed: int = 0  # status = done
    total_processing: int = 0  # status IN (pending, extracting, processing)
    total_failed: int = 0  # status IN (failed, quota_exceeded)
    total_candidates: int = 0


# ── Job description parsing ───────────────────────────────────
class JDParseRequest(BaseModel):
    job_description: str


class JDParseResponse(BaseModel):
    role_title: str
    seniority: str
    skills: list[str]


# ── Candidates ────────────────────────────────────────────────
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

    model_config = ConfigDict(from_attributes=True)


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
    education: list[dict[str, Any]]
    work_experience: list[dict[str, Any]]
    certifications: list[str]
    projects: list[dict[str, Any]]
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
    score_breakdown: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CandidateCategoryUpdate(BaseModel):
    category: str


class CandidateNotesUpdate(BaseModel):
    notes: str


class CandidatePipelineUpdate(BaseModel):
    stage: str


class BulkPipelineUpdate(BaseModel):
    candidate_ids: list[uuid.UUID]
    stage: str


class BulkDeleteRequest(BaseModel):
    candidate_ids: list[uuid.UUID]


class CandidateFilterParams(BaseModel):
    skill: str | None = None
    min_score: float | None = None
    max_score: float | None = None
    category: str | None = None
    min_experience: float | None = None
    search: str | None = None
    page: int = 1
    page_size: int = 20
