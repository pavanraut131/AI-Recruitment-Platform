"""hiring resources of the ``/api/v2`` API (EPIC-01 spec §6, EPIC-02 §8): clients, campaigns
and applications.

Public, like ``schemas.py``: ``app.api`` and the other modules may import these (import-linter
allows ``app.modules.<name>.schemas_v2``). They are kept apart from the v1 schemas so that the
class names, which are the component names of the v2 OpenAPI document and the type names of the
generated TypeScript client, can be the plain resource names (``Campaign``, ``CampaignCreate``)
without clashing with the frozen v1 ones.

Enums are declared as such, so the OpenAPI document lists their values and the client gets
literal unions.
"""

import enum
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, StringConstraints

from app.platform.pagination import Page


# ── Vocabularies ──────────────────────────────────────────────
class CampaignStatus(str, enum.Enum):
    active = "active"
    closed = "closed"
    archived = "archived"


class ClientStatus(str, enum.Enum):
    """``archived`` clients are kept (campaigns refer to them) but hidden from pickers."""

    active = "active"
    archived = "archived"


class ApplicationCategory(str, enum.Enum):
    """The AI's verdict (or the recruiter's override of it)."""

    strong_match = "strong_match"
    moderate_match = "moderate_match"
    weak_match = "weak_match"
    rejected = "rejected"


class PipelineStage(str, enum.Enum):
    """Where the recruiter has moved the application (``null``: not reviewed yet)."""

    screened = "screened"
    phone_call = "phone_call"
    technical = "technical"
    offer = "offer"
    hired = "hired"
    rejected_manual = "rejected_manual"


class ApplicationSort(str, enum.Enum):
    """List order. ``-`` is descending; scores sort unscored last either way; ties by id."""

    score = "score"
    score_desc = "-score"
    updated_at = "updated_at"
    updated_at_desc = "-updated_at"
    name = "name"


Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
ClientName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Description = Annotated[str, StringConstraints(max_length=5000)]
Skill = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Skills = Annotated[list[Skill], Field(max_length=50)]
Notes = Annotated[str, StringConstraints(max_length=10_000)]


def omittable() -> Any:
    """The default of a PATCH field that may be left out but not sent as ``null``: the field
    keeps its non-nullable type (so ``null`` is a 422 and the schema says so), and the ``None``
    default only marks it as not sent. It is never validated, and the services read the
    payload with ``exclude_unset=True``."""
    return Field(default=None)


# ── Clients ───────────────────────────────────────────────────
class Client(BaseModel):
    """One of the agency's customers; every campaign is run for one client (EPIC-02 §3.1)."""

    id: uuid.UUID
    name: str = Field(description="Unique in the agency (ignoring case).")
    status: ClientStatus
    created_at: datetime
    updated_at: datetime


class ClientPage(Page[Client]):
    pass


class ClientRef(BaseModel):
    """One of the agency's clients, by name (a campaign's client, a member's clients)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str


class ClientCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ClientName


class ClientUpdate(BaseModel):
    """Only the fields sent are changed: rename, archive (``status: archived``) or restore
    (``active``). Neither may be ``null``."""

    model_config = ConfigDict(extra="forbid")

    name: ClientName = omittable()
    status: ClientStatus = omittable()


# ── Campaigns ─────────────────────────────────────────────────
class CampaignCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    role: Title
    description: Description | None = None
    required_skills: Skills = []
    status: CampaignStatus = CampaignStatus.active
    client_id: uuid.UUID | None = Field(
        default=None,
        description="The client the campaign is run for: an active client you have access to "
        "(422 otherwise). Omitted or null: your default client, the agency's oldest active "
        "client you have access to.",
    )


class CampaignUpdate(BaseModel):
    """Only the fields sent are changed. ``description`` may be ``null`` (cleared); the others
    may be omitted but not ``null``. ``client_id`` moves the campaign to another active client
    you have access to (422 otherwise)."""

    model_config = ConfigDict(extra="forbid")

    title: Title = omittable()
    role: Title = omittable()
    description: Description | None = None
    required_skills: Skills = omittable()
    status: CampaignStatus = omittable()
    client_id: uuid.UUID = omittable()


class CampaignCounts(BaseModel):
    resumes: int = Field(description="Resumes uploaded, in any status.")
    in_progress: int = Field(description="Resumes still being processed.")
    failed: int = Field(description="Resumes that failed or hit the AI quota.")
    new_resumes_24h: int = Field(description="Resumes uploaded in the last 24 hours.")
    applications: int = Field(description="Scored applications.")
    strong_matches: int = Field(description="Applications in the strong_match category.")


class Campaign(BaseModel):
    id: uuid.UUID
    title: str
    role: str
    description: str | None
    required_skills: list[str]
    status: CampaignStatus
    client: ClientRef = Field(description="The client the campaign is run for.")
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime = Field(
        description="Latest change to the campaign, one of its applications or its resumes."
    )
    counts: CampaignCounts


class CampaignPage(Page[Campaign]):
    pass


class CampaignRef(BaseModel):
    id: uuid.UUID
    title: str
    status: CampaignStatus


# ── Campaign stats (Insights) ─────────────────────────────────
class ResumeCounts(BaseModel):
    total: int = 0
    pending: int = 0
    extracting: int = 0
    processing: int = 0
    done: int = 0
    failed: int = 0
    quota_exceeded: int = 0


class CategoryCounts(BaseModel):
    strong_match: int = 0
    moderate_match: int = 0
    weak_match: int = 0
    rejected: int = 0


class StageCounts(BaseModel):
    unreviewed: int = Field(default=0, description="Applications with no stage yet.")
    screened: int = 0
    phone_call: int = 0
    technical: int = 0
    offer: int = 0
    hired: int = 0
    rejected_manual: int = 0


class ApplicationCounts(BaseModel):
    total: int = 0
    by_category: CategoryCounts = CategoryCounts()
    by_stage: StageCounts = StageCounts()


class ScoreBucket(BaseModel):
    min: int = Field(description="Lower bound, inclusive.")
    max: int = Field(description="Upper bound, exclusive (inclusive for the last bucket).")
    count: int


class SkillCount(BaseModel):
    skill: str
    count: int = Field(description="Applications whose resume lists the skill.")
    required: bool = Field(description="Whether the campaign requires it (case-insensitive).")


class CampaignStats(BaseModel):
    resumes: ResumeCounts
    applications: ApplicationCounts
    avg_score: float | None = Field(
        description="Mean score of the scored applications, to 2 decimals; null if none."
    )
    score_histogram: list[ScoreBucket] = Field(description="Ten buckets of width 10, 0 to 100.")
    top_skills: list[SkillCount] = Field(description="The 10 most frequent skills.")


class CampaignStatusCounts(BaseModel):
    total: int = 0
    active: int = 0
    closed: int = 0
    archived: int = 0


class WorkspaceStats(BaseModel):
    campaigns: CampaignStatusCounts
    resumes: ResumeCounts
    applications: ApplicationCounts


# ── Job description parsing ───────────────────────────────────
class JobDescription(BaseModel):
    job_description: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50_000)
    ]


class ParsedJobDescription(BaseModel):
    role_title: str
    seniority: str = Field(description="junior | mid | senior | staff, as the AI judged it.")
    skills: list[str]


# ── Applications ──────────────────────────────────────────────
class PersonRef(BaseModel):
    """Who applied. ``name`` and ``email`` are those of the resume this application was scored
    on (its current resume), as in the candidate list; the Person's profile may be newer."""

    id: uuid.UUID
    name: str | None
    email: str | None


class Application(BaseModel):
    id: uuid.UUID
    campaign_id: uuid.UUID
    resume_id: uuid.UUID = Field(description="The current resume, the one scored.")
    person: PersonRef
    score: float | None
    score_breakdown: dict[str, Any] | None = Field(
        description="Sub-scores: skills_match, experience_match, project_relevance, "
        "education_bonus, communication_quality."
    )
    category: ApplicationCategory | None
    summary: str | None
    strengths: list[str]
    weaknesses: list[str]
    missing_skills: list[str]
    recommendation: str | None
    skills: list[str] = Field(description="Skills extracted from the current resume.")
    years_of_experience: float | None
    pipeline_stage: PipelineStage | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class ApplicationPage(Page[Application]):
    pass


class ApplicationUpdate(BaseModel):
    """Only the fields sent are changed. ``pipeline_stage`` and ``notes`` may be ``null``
    (cleared); ``category`` (the recruiter's override of the AI's) may not."""

    model_config = ConfigDict(extra="forbid")

    pipeline_stage: PipelineStage | None = None
    notes: Notes | None = None
    category: ApplicationCategory = omittable()


BulkIds = Annotated[list[uuid.UUID], Field(min_length=1, max_length=500)]


class BulkStage(BaseModel):
    """Move the applications to ``stage``."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["stage"]
    ids: BulkIds
    stage: PipelineStage


class BulkReject(BaseModel):
    """Move the applications to the ``rejected_manual`` stage."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["reject"]
    ids: BulkIds


class BulkDelete(BaseModel):
    """Delete the applications and their resumes."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["delete"]
    ids: BulkIds


BulkAction = BulkStage | BulkReject | BulkDelete


class BulkApplicationAction(RootModel[Annotated[BulkAction, Field(discriminator="action")]]):
    """``{action, ids, stage?}``: ``stage`` is required for (and only allowed with) the
    ``stage`` action. A model of its own so the OpenAPI document names it and keeps the
    discriminator (the client gets a tagged union)."""


class BulkApplicationResult(BaseModel):
    action: Literal["stage", "reject", "delete"]
    affected: list[uuid.UUID] = Field(description="The campaign's applications acted on.")
    not_found: list[uuid.UUID] = Field(
        description="Requested ids that are not applications of this campaign (nothing done)."
    )


class PersonApplication(BaseModel):
    """One of a Person's applications, for their profile."""

    id: uuid.UUID
    campaign: CampaignRef
    resume_id: uuid.UUID
    score: float | None
    category: ApplicationCategory | None
    pipeline_stage: PipelineStage | None
    created_at: datetime
    updated_at: datetime


class RecentMatch(BaseModel):
    """A recent strong match, for the Home screen."""

    id: uuid.UUID = Field(description="The application's id.")
    campaign: CampaignRef
    person: PersonRef
    score: float | None
    category: ApplicationCategory | None
    pipeline_stage: PipelineStage | None
    created_at: datetime
