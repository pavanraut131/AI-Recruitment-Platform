"""The ``/api/v2`` resources that combine modules (EPIC-01 spec §4–§6, EPIC-02 §8.1).

Each extends one module's resource with what another module knows: a Person (talent) with their
applications (hiring), a Resume (ingestion) with its campaign (hiring), the workspace counts
(hiring, ingestion) with the number of people (talent), the Home screen aggregate, and (E2-07)
team members and invitations (identity) with their clients' names (hiring).
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.modules.hiring.schemas_v2 import (
    Campaign,
    CampaignRef,
    ClientRef,
    PersonApplication,
    RecentMatch,
    WorkspaceStats,
)
from app.modules.identity.schemas import InvitationStatus, MemberStatus, Role
from app.modules.identity.schemas_v2 import MemberUser
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


# ── Team (E2-07) ──────────────────────────────────────────────
class Member(BaseModel):
    """A member of the agency: their account, role, client access and status."""

    id: uuid.UUID = Field(description="The membership's id (the path of the member endpoints).")
    user: MemberUser
    role: Role
    all_clients: bool = Field(description="Every client of the agency (owners and admins: always).")
    clients: list[ClientRef] = Field(
        description="The clients a recruiter or viewer may see when all_clients is false, by "
        "name; empty otherwise."
    )
    status: MemberStatus
    last_active_at: datetime | None = Field(
        description="Their last sign-in to or request in this agency (null: never)."
    )
    joined_at: datetime
    deactivated_at: datetime | None


class MemberPage(Page[Member]):
    pass


class Invitation(BaseModel):
    """An open invitation: pending, or expired (resend it to renew it)."""

    id: uuid.UUID
    email: str
    role: Role
    all_clients: bool
    clients: list[ClientRef] = Field(
        description="The clients it gives when all_clients is false, by name; empty otherwise."
    )
    invited_by: MemberUser | None = Field(description="Who sent it (null if unknown).")
    status: InvitationStatus
    expires_at: datetime
    created_at: datetime


class InvitationPage(Page[Invitation]):
    pass
