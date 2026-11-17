"""talent resources of the ``/api/v2`` API (EPIC-01 spec §5.4, §6): people.

Public (import-linter allows ``app.modules.<name>.schemas_v2``). Talent knows nothing of
applications; ``app.api.v2`` adds them (counts for the list, the applications themselves for the
profile) to these shapes.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class IdentifierKind(str, enum.Enum):
    email = "email"
    phone = "phone"


class ConflictRole(str, enum.Enum):
    """This Person's side of an identity conflict (ADR 0004)."""

    chosen = "chosen"  # the resume was attached to this Person (their email matched)
    conflicting = "conflicting"  # the resume's phone belongs to this Person (it went elsewhere)


class PersonSummary(BaseModel):
    """A Person in the talent list."""

    id: uuid.UUID
    name: str | None
    email: str | None = Field(description="The first email seen for the Person, as written.")
    latest_role: str | None = Field(
        description="Title of the first entry of the extracted work experience (resumes list "
        "the most recent first)."
    )
    skills: list[str]
    years_of_experience: float | None
    created_at: datetime
    updated_at: datetime


class Identifier(BaseModel):
    kind: IdentifierKind
    value: str = Field(description="As written on the resume it was first seen on.")
    inferred: bool = Field(
        description="A phone whose country was only assumed: kept, never used to match."
    )


class IdentityConflictFlag(BaseModel):
    """A resume whose email matched one Person and phone another (read-only; reviewed in E6)."""

    id: uuid.UUID
    role: ConflictRole
    resume_id: uuid.UUID
    other_person_id: uuid.UUID
    reason: str
    created_at: datetime
    resolved_at: datetime | None


class Profile(BaseModel):
    """The Person's profile: from their most recently uploaded resume, in any campaign."""

    skills: list[str]
    education: list[dict[str, Any]]
    work_experience: list[dict[str, Any]]
    certifications: list[str]
    projects: list[dict[str, Any]]
    years_of_experience: float | None


class PersonDetail(BaseModel):
    id: uuid.UUID
    name: str | None
    github_url: str | None
    linkedin_url: str | None
    profile: Profile
    identifiers: list[Identifier]
    conflicts: list[IdentityConflictFlag]
    has_unresolved_conflicts: bool
    created_at: datetime
    updated_at: datetime
