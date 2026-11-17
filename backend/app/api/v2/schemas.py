"""The ``/api/v2`` resources that combine modules (EPIC-01 spec §4–§6).

Each extends one module's resource with what another module knows: a Person (talent) with their
applications (hiring), a Resume (ingestion) with its campaign (hiring), the workspace counts
(hiring, ingestion) with the number of people (talent), and the Home screen aggregate.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.modules.hiring.schemas_v2 import (
    Campaign,
    CampaignRef,
    PersonApplication,
    RecentMatch,
    WorkspaceStats,
)
from app.modules.ingestion.schemas_v2 import Resume
from app.modules.talent.schemas_v2 import PersonDetail, PersonSummary
from app.platform.pagination import Page


class PersonListItem(PersonSummary):
    application_count: int = Field(description="Applications across all campaigns.")
    last_applied_at: datetime | None = Field(
        description="When the Person's latest resume was uploaded (null: no applications)."
    )


class PersonPage(Page[PersonListItem]):
    pass


class Person(PersonDetail):
    applications: list[PersonApplication] = Field(
        description="Every application across campaigns, newest first."
    )


class Stats(WorkspaceStats):
    people: int = Field(description="People known across all campaigns.")


class ProcessingResume(Resume):
    campaign: CampaignRef | None = Field(description="null only if the campaign was deleted.")


class HomeProcessing(BaseModel):
    total: int = Field(description="Resumes still being processed, across campaigns.")
    items: list[ProcessingResume] = Field(description="The newest of them.")


class Home(BaseModel):
    """Everything the Home screen shows, in one response."""

    active_campaigns: list[Campaign] = Field(
        description="The most recently updated active campaigns, with their counts."
    )
    processing: HomeProcessing
    recent_strong_matches: list[RecentMatch] = Field(
        description="The newest strong-match applications across campaigns."
    )
