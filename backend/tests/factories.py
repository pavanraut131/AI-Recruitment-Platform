"""Small async factories for test data: ``make_campaign``, ``make_resume``, ``make_candidate``.

Each one adds an ORM row to the given session, flushes it (so it is visible to the app, which
shares the test's session, see ``tests/conftest.py``) and returns it. Defaults are realistic and
**constant** (no counters or random values), so a test's data never depends on which tests ran
before it. Override any column with a keyword argument.

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

from app.models.resume import Resume
from app.modules.hiring.models import Campaign, Candidate

BASE_TIME = datetime(2025, 1, 6, 9, 0, tzinfo=UTC)


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


async def make_candidate(
    db: AsyncSession,
    campaign: Campaign | None = None,
    resume: Resume | None = None,
    **overrides: Any,
) -> Candidate:
    """A fully processed candidate. Creates its resume (and campaign) if not given."""
    if resume is None:
        resume = await make_resume(db, campaign)
    fields: dict[str, Any] = {
        "resume_id": resume.id,
        "campaign_id": resume.campaign_id,
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
    return await _save(db, Candidate(**fields))
