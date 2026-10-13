import uuid
from datetime import datetime

from pydantic import BaseModel


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
    top_skills: list[dict] = []


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

    model_config = {"from_attributes": True}
