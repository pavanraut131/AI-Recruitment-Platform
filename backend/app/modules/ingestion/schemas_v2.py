"""ingestion resources of the ``/api/v2`` API (EPIC-01 spec §6): resumes, uploads and the
progress events.

Public, like ``schemas.py`` (import-linter allows ``app.modules.<name>.schemas_v2``). Kept apart
from the frozen v1 schemas so the class names, which are the v2 OpenAPI component names, can be
plain resource names.
"""

import enum
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.hiring.schemas_v2 import ApplicationCategory
from app.platform.pagination import Page


class ResumeStatus(str, enum.Enum):
    """``pending`` (queued) → ``extracting`` (PDF text) → ``processing`` (AI) → ``done``; or
    ``failed`` / ``quota_exceeded`` (the AI provider's quota; retry later)."""

    pending = "pending"
    extracting = "extracting"
    processing = "processing"
    done = "done"
    failed = "failed"
    quota_exceeded = "quota_exceeded"


class ProcessingStep(str, enum.Enum):
    """The step in progress, or the one that failed."""

    extract_text = "extract_text"
    extract_profile = "extract_profile"
    resolve_person = "resolve_person"
    score = "score"
    save = "save"


class Resume(BaseModel):
    id: uuid.UUID
    campaign_id: uuid.UUID
    filename: str
    size_bytes: int
    status: ResumeStatus
    processing_step: ProcessingStep | None
    error_message: str | None
    retry_count: int
    application_id: uuid.UUID | None = Field(
        description="The application the resume belongs to, once it is scored."
    )
    uploaded_at: datetime
    updated_at: datetime


class ResumePage(Page[Resume]):
    pass


class RejectedFile(BaseModel):
    filename: str
    reason: str = Field(description="Why it was not accepted (type, size, duplicate, ...).")


class UploadResult(BaseModel):
    accepted: list[Resume] = Field(description="Stored and queued for processing.")
    rejected: list[RejectedFile]


class RetryResult(BaseModel):
    retried: list[uuid.UUID] = Field(description="The resumes queued again.")


class ResumeEvent(BaseModel):
    """The data of a ``resume_update`` event: a resume of the campaign changed status.

    The fields a status does not carry are ``null``: ``filename`` (``extracting``, ``done``),
    ``error`` (``failed``, ``quota_exceeded``), and for ``done`` the scoring summary and the
    Person the resume was attached to.
    """

    model_config = ConfigDict(extra="ignore")

    resume_id: uuid.UUID
    status: ResumeStatus
    filename: str | None = None
    error: str | None = None
    candidate_name: str | None = None
    score: float | None = None
    category: ApplicationCategory | None = None
    person_id: uuid.UUID | None = None
    matched_existing: bool | None = Field(
        default=None, description="Whether the Person existed before this resume (done only)."
    )


class ConnectedEvent(BaseModel):
    """The data of the first event, ``connected``: the stream is subscribed."""

    campaign_id: uuid.UUID
