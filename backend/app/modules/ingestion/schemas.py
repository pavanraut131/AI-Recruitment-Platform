"""ingestion request/response schemas (the public API contract of the resume endpoints).

Class names are part of the published OpenAPI schema (``components.schemas``); renaming one is
an API change recorded by the characterization snapshots.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ResumeResponse(BaseModel):
    id: uuid.UUID
    campaign_id: uuid.UUID
    original_filename: str
    storage_path: str
    file_size_bytes: int
    status: str
    retry_count: int
    error_message: str | None
    uploaded_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UploadBatchResponse(BaseModel):
    batch_id: str
    campaign_id: uuid.UUID
    total_files: int
    accepted_files: int
    # {"filename": ..., "reason": ...} per rejected file. Typed as list[Any] (the published
    # schema has untyped items).
    rejected_files: list[Any]
    resume_ids: list[uuid.UUID]
    message: str


class SSEEvent(BaseModel):
    resume_id: str
    filename: str
    status: str
    candidate_name: str | None = None
    score: float | None = None
    category: str | None = None
    error: str | None = None
