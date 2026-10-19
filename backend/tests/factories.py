"""Small async factories for test data: ``make_campaign``, ``make_resume``, ``make_candidate``
(and ``make_legacy_candidate``).

Each one adds ORM rows to the given session, flushes them (so they are visible to the app, which
shares the test's session, see ``tests/conftest.py``) and returns the main one. Defaults are
realistic and **constant** (no counters or random values), so a test's data never depends on
which tests ran before it. Override any column with a keyword argument.

Since the read switch (E0-19) the API serves the legacy "candidate" endpoints from People and
Applications, so :func:`make_candidate` builds what the upload pipeline leaves behind for one
processed resume: a ``Person``, an ``Application`` and the resume's extraction snapshot
(``resumes.extracted``). It takes the legacy candidate's column names as keywords and puts each
value where the read path takes it from (spec §7): name, contacts and profile into the
snapshot, scoring and recruiter fields onto the application. Each call creates its own Person
(no identifiers, no matching), so tests can add several "candidates" with the same email to one
campaign, as they could with legacy rows. :func:`make_legacy_candidate` still writes a
``candidates`` row, for the tests about legacy data (the seed, the backfill).

Timestamps: inside one test everything runs in one database transaction, where Postgres ``now()``
is frozen, so rows created with server defaults all get the *same* timestamp and ``ORDER BY
created_at`` would be ambiguous. The factories therefore set timestamps explicitly; pass
``created_at=at(n)`` / ``uploaded_at=at(n)`` to control ordering.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.hiring.models import Application, Campaign, Candidate
from app.modules.ingestion.models import Resume
from app.modules.talent.models import Person

BASE_TIME = datetime(2025, 1, 6, 9, 0, tzinfo=UTC)

# The legacy candidate columns that come from the AI extraction: the read path takes them from
# the resume's ``extracted`` snapshot (an ``ExtractionResult`` dumped to JSON).
EXTRACTION_FIELDS = (
    "name",
    "email",
    "phone",
    "github_url",
    "linkedin_url",
    "skills",
    "education",
    "work_experience",
    "certifications",
    "projects",
    "years_of_experience",
)


def at(minutes: int) -> datetime:
    """A fixed timestamp ``minutes`` after :data:`BASE_TIME`; later ``n`` sorts later."""
    return BASE_TIME + timedelta(minutes=minutes)


async def _save(db: AsyncSession, row: Any) -> Any:
    db.add(row)
    await db.flush()
    return row


async def make_campaign(db: AsyncSession, **overrides: Any) -> Campaign:
    fields: dict[str, Any] = {
        "title": "Backend Engineer - Q1 Hiring",
        "role": "Backend Engineer",
        "description": "Build and run the Python services behind our recruiting product.",
        "required_skills": ["Python", "FastAPI", "PostgreSQL"],
        "status": "active",
        "created_at": BASE_TIME,
        "updated_at": BASE_TIME,
    }
    fields.update(overrides)
    return await _save(db, Campaign(**fields))


async def make_resume(
    db: AsyncSession, campaign: Campaign | None = None, **overrides: Any
) -> Resume:
    """A resume row. Creates a campaign if none is given. Default status is ``done``."""
    if campaign is None:
        campaign = await make_campaign(db)
    resume_id = overrides.pop("id", None) or uuid.uuid4()
    filename = overrides.pop("original_filename", "jane_doe_resume.pdf")
    fields: dict[str, Any] = {
        "id": resume_id,
        "campaign_id": campaign.id,
        "original_filename": filename,
        "storage_path": f"campaigns/{campaign.id}/resumes/{resume_id}/{filename}",
        "file_hash": hashlib.sha256(filename.encode()).hexdigest(),
        "file_size_bytes": 48213,
        "status": "done",
        "retry_count": 0,
        "error_message": None,
        "uploaded_at": BASE_TIME,
        "updated_at": BASE_TIME,
    }
    fields.update(overrides)
    return await _save(db, Resume(**fields))


def candidate_fields(**overrides: Any) -> dict[str, Any]:
    """A fully processed legacy candidate's columns (without ids), with ``overrides`` applied."""
    fields: dict[str, Any] = {
        "name": "Jane Doe",
        "email": "jane.doe@example.com",
        "phone": "+1 555 0100",
        "github_url": "https://github.com/janedoe",
        "linkedin_url": "https://www.linkedin.com/in/janedoe",
        "skills": ["Python", "FastAPI", "PostgreSQL", "Docker"],
        "education": [{"degree": "BSc Computer Science", "institution": "State University"}],
        "work_experience": [
            {"title": "Backend Engineer", "company": "Acme Corp", "duration": "2019-2024"}
        ],
        "certifications": ["AWS Certified Developer"],
        "projects": [{"name": "Job board API", "description": "FastAPI + Postgres"}],
        "years_of_experience": 5.0,
        "summary": "Backend engineer with five years of Python API experience.",
        "strengths": ["Strong Python", "API design"],
        "weaknesses": ["Limited frontend experience"],
        "missing_skills": [],
        "recommendation": "Proceed to technical interview.",
        "score": 82.0,
        "score_breakdown": {
            "skills_match": 90,
            "experience_match": 80,
            "project_relevance": 75,
            "education_bonus": 5,
            "communication_quality": 80,
        },
        "category": "strong_match",
        "notes": None,
        "pipeline_stage": None,
        "raw_text": "Jane Doe - Backend Engineer - Python, FastAPI, PostgreSQL, Docker",
        "created_at": BASE_TIME,
        "updated_at": BASE_TIME,
    }
    fields.update(overrides)
    return fields


async def make_candidate(
    db: AsyncSession,
    campaign: Campaign | None = None,
    resume: Resume | None = None,
    **overrides: Any,
) -> Application:
    """A fully processed "candidate" as the switched read path sees it: its resume (created if
    not given, with its campaign) with the extraction snapshot and text, a Person resolved from
    it, and the Person's application to the campaign. Takes the legacy candidate's column names
    (and ``id``, the application's id); returns the application, whose ``id`` and ``resume_id``
    are what the API returns as the candidate's."""
    if resume is None:
        resume = await make_resume(db, campaign)
    application_id = overrides.pop("id", None) or uuid.uuid4()
    fields = candidate_fields(**overrides)
    extracted = {name: fields.pop(name) for name in EXTRACTION_FIELDS}
    person = await _save(
        db,
        Person(
            full_name=extracted["name"],
            github_url=extracted["github_url"],
            linkedin_url=extracted["linkedin_url"],
            skills=extracted["skills"],
            education=extracted["education"],
            work_experience=extracted["work_experience"],
            certifications=extracted["certifications"],
            projects=extracted["projects"],
            years_of_experience=extracted["years_of_experience"],
            latest_resume_id=resume.id,
            created_at=fields["created_at"],
            updated_at=fields["updated_at"],
        ),
    )
    resume.extracted = extracted
    resume.raw_text = fields.pop("raw_text")
    resume.person_id = person.id
    return await _save(
        db,
        Application(
            id=application_id,
            campaign_id=resume.campaign_id,
            person_id=person.id,
            resume_id=resume.id,
            resume_uploaded_at=resume.uploaded_at,
            **fields,
        ),
    )


async def make_legacy_candidate(
    db: AsyncSession,
    campaign: Campaign | None = None,
    resume: Resume | None = None,
    **overrides: Any,
) -> Candidate:
    """A pre-switch legacy ``candidates`` row (nothing writes these since E0-19). Creates its
    resume (and campaign) if not given."""
    if resume is None:
        resume = await make_resume(db, campaign)
    fields = candidate_fields(resume_id=resume.id, campaign_id=resume.campaign_id, **overrides)
    return await _save(db, Candidate(**fields))
