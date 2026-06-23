import uuid
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, field_validator


class CampaignCreate(BaseModel):
    title: str
    role: str
    description: Optional[str] = None
    required_skills: List[str] = []


class CampaignUpdate(BaseModel):
    title: Optional[str] = None
    role: Optional[str] = None
    description: Optional[str] = None
    required_skills: Optional[List[str]] = None
    status: Optional[str] = None


class CampaignStats(BaseModel):
    total_resumes: int = 0
    total_processed: int = 0
    total_failed: int = 0
    total_pending: int = 0
    total_quota_exceeded: int = 0
    avg_score: Optional[float] = None
    strong_match: int = 0
    moderate_match: int = 0
    weak_match: int = 0
    rejected: int = 0
    top_skills: List[dict] = []


class CampaignResponse(BaseModel):
    id: uuid.UUID
    title: str
    role: str
    description: Optional[str]
    required_skills: List[str]
    status: str
    created_at: datetime
    updated_at: datetime
    stats: Optional[CampaignStats] = None

    model_config = {"from_attributes": True}
