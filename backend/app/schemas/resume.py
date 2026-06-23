import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class ResumeResponse(BaseModel):
    id: uuid.UUID
    campaign_id: uuid.UUID
    original_filename: str
    storage_path: str
    file_size_bytes: int
    status: str
    retry_count: int
    error_message: Optional[str]
    uploaded_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UploadBatchResponse(BaseModel):
    batch_id: str
    campaign_id: uuid.UUID
    total_files: int
    accepted_files: int
    rejected_files: list
    resume_ids: list[uuid.UUID]
    message: str


class SSEEvent(BaseModel):
    resume_id: str
    filename: str
    status: str
    candidate_name: Optional[str] = None
    score: Optional[float] = None
    category: Optional[str] = None
    error: Optional[str] = None
