"""talent data contracts: what callers pass to and get back from ``talent.service``.

Public: other modules may import these (import-linter allows ``app.modules.talent.schemas``).
"""

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict


class PersonProfile(BaseModel):
    """The profile part of one resume's extraction (spec §4 ``people`` columns).

    A field is **missing** when it is ``None``, an empty string or an empty list. A missing field
    never overwrites a value the Person already has (spec §5.3 profile refresh).
    """

    model_config = ConfigDict(frozen=True)

    full_name: str | None = None
    github_url: str | None = None
    linkedin_url: str | None = None
    skills: list[str] = []
    education: list[dict[str, Any]] = []
    work_experience: list[dict[str, Any]] = []
    certifications: list[str] = []
    projects: list[dict[str, Any]] = []
    years_of_experience: float | None = None


class ResolveResult(BaseModel):
    """Which Person a resume belongs to (spec §5.3)."""

    model_config = ConfigDict(frozen=True)

    person_id: uuid.UUID
    # True when the resume was attached to a Person that existed before this resume.
    matched_existing: bool
    # Set when the email matched one Person and the phone another (the resume went to the email
    # match); the row is in ``identity_conflicts`` for the E6 review queue.
    conflict_id: uuid.UUID | None = None
