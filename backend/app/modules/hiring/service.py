"""hiring service: the public interface of the hiring module.

Campaign CRUD and stats, the global stats, job description parsing, and the candidate list,
export, detail, recruiter updates and deletes. The routers in ``router.py`` only translate HTTP
to these calls; other modules (E0-10: ``ingestion``) may import this module and
``app.modules.hiring.schemas``, never ``models`` or ``repository`` (import-linter enforces it).

Business rules live here: the category / pipeline-stage vocabularies, the candidate filter
builder shared by the list and the CSV export, the stats aggregation and the JD prompt. Errors
are raised as :class:`HiringError` subclasses carrying the user-facing ``detail``; the router
maps them to status codes.

Cross-module dependencies (resolved in E0-10)
---------------------------------------------
Hiring still reads or deletes ingestion-owned resume data in the places below. Today that is
legal (``Resume`` lives in ``app.models``, outside ``app.modules``), but once Resume moves to
``app.modules.ingestion`` the layer contract (``ingestion -> hiring -> talent``) forbids hiring
from importing it. Proposed resolution for each, none of which needs hiring to import ingestion:

1. **Campaign stats resume counts** (``GET /campaigns/{id}``, ``GET /campaigns/{id}/stats``)
   and **global stats resume counts** (``GET /stats``) via ``repository.resume_status_counts``.
   The aggregation is already split out as pure functions (:func:`build_campaign_stats`,
   :func:`build_global_stats`) that take the resume counts as an argument. E0-10 moves the
   resume-count query into ``ingestion.service`` and composes the stats one level up: the
   stats endpoints are served from ``ingestion`` (which may call ``hiring.service``) or from an
   app-level composition router that calls both services. Hiring keeps only its own numbers.
2. **Deleting a candidate also deletes its resume** (``DELETE /candidates/{id}``,
   ``DELETE /campaigns/{id}/candidates/bulk``) via ``repository.delete_resume(s)``. The
   service functions already return the affected resume ids; E0-10 makes ``ingestion`` own
   the use case ("withdraw a submission"): ingestion calls ``hiring.service`` to delete the
   candidates, gets the resume ids back and deletes its own resumes in the same transaction.
3. **Deleting a campaign cascades to its resumes** through the ORM relationship
   ``Campaign.resumes`` (plus ``resumes.campaign_id ON DELETE CASCADE``). E0-10 drops the
   cross-module relationships (``Campaign.resumes``, ``Candidate.resume`` and the reverse
   ones on ``Resume``) and relies on the database cascade until ADR 0003 removes the
   cross-module foreign keys; after that, ingestion deletes a campaign's resumes when told
   to (``hiring.service`` publishes the deletion, or the composing endpoint calls both).
4. **``GET /resumes/{id}/candidate``** only queries ``candidates.resume_id``; it needs no
   resume data and stays a hiring endpoint (the URL is the only resume-shaped part of it).
5. **The upload pipeline and workers** (``app/api/v1/resumes.py``, ``workers/*``) read
   ``Campaign`` and create/update ``Candidate`` rows through the hiring models. That is the
   allowed direction (ingestion -> hiring), but must go through this service in E0-10
   (e.g. ``campaign_exists``, ``get_required_skills``, ``record_extraction``,
   ``record_scoring``), never ``hiring.models``.
"""

import csv
import io
import json
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import google.generativeai as genai
import structlog
from sqlalchemy import ColumnElement, column, exists, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.hiring import repository
from app.modules.hiring.models import Campaign, Candidate
from app.modules.hiring.schemas import (
    CampaignCreate,
    CampaignResponse,
    CampaignStats,
    CampaignUpdate,
    GlobalStats,
    JDParseRequest,
    JDParseResponse,
)
from app.platform.config import settings
from app.platform.sql import LIKE_ESCAPE, escape_like

logger = structlog.get_logger()

if settings.GEMINI_API_KEY:
    genai.configure(api_key=settings.GEMINI_API_KEY)

VALID_CATEGORIES = {"strong_match", "moderate_match", "weak_match", "rejected"}
VALID_PIPELINE_STAGES = {"screened", "phone_call", "technical", "offer", "hired", "rejected_manual"}

PROCESSING_STATUSES = ("pending", "extracting", "processing")
TERMINAL_FAILED_STATUSES = ("failed", "quota_exceeded")


# ── Errors ────────────────────────────────────────────────────
class HiringError(Exception):
    """A hiring operation failed; ``detail`` is the message returned to the client."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NotFoundError(HiringError):
    """The campaign or candidate does not exist."""


class InvalidInputError(HiringError):
    """The request names a category or pipeline stage outside the allowed vocabulary."""


class JDParseError(HiringError):
    """The job description could not be parsed (no API key, or Gemini/its answer failed)."""


# ── Campaigns ─────────────────────────────────────────────────
async def create_campaign(db: AsyncSession, payload: CampaignCreate) -> Campaign:
    campaign = await repository.add_campaign(db, Campaign(**payload.model_dump()))
    logger.info("campaign_created", id=str(campaign.id), title=campaign.title)
    return campaign


async def list_campaigns(db: AsyncSession, skip: int, limit: int) -> Sequence[Campaign]:
    return await repository.list_campaigns(db, skip, limit)


async def _require_campaign(db: AsyncSession, campaign_id: uuid.UUID) -> Campaign:
    campaign = await repository.get_campaign(db, campaign_id)
    if not campaign:
        raise NotFoundError("Campaign not found")
    return campaign


async def get_campaign(db: AsyncSession, campaign_id: uuid.UUID) -> CampaignResponse:
    """The campaign with its stats attached."""
    campaign = await _require_campaign(db, campaign_id)
    stats = await campaign_stats(db, campaign_id)
    response = CampaignResponse.model_validate(campaign)
    response.stats = stats
    return response


async def update_campaign(
    db: AsyncSession, campaign_id: uuid.UUID, payload: CampaignUpdate
) -> Campaign:
    """Apply the fields the client sent (unset fields are left alone)."""
    campaign = await _require_campaign(db, campaign_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(campaign, field, value)
    await repository.commit_and_refresh(db, campaign)
    return campaign


async def delete_campaign(db: AsyncSession, campaign_id: uuid.UUID) -> None:
    campaign = await _require_campaign(db, campaign_id)
    await repository.delete_campaign(db, campaign)


# ── Stats ─────────────────────────────────────────────────────
async def campaign_stats(db: AsyncSession, campaign_id: uuid.UUID) -> CampaignStats:
    """Stats for one campaign. An unknown campaign id yields all-zero stats, not a 404."""
    # TODO(E0-10): the resume counts come from ingestion (see "Cross-module dependencies").
    resume_counts = await repository.resume_status_counts(db, campaign_id)
    category_rows = await repository.candidate_category_stats(db, campaign_id)
    skill_lists = await repository.candidate_skill_lists(db, campaign_id)
    return build_campaign_stats(resume_counts, category_rows, skill_lists)


def build_campaign_stats(
    resume_counts: Mapping[str, int],
    category_rows: Iterable[tuple[str | None, int, float | None]],
    skill_lists: Iterable[list[str] | None],
) -> CampaignStats:
    """Aggregate a campaign's resume counts by status, candidate ``(category, count, average
    score)`` rows and candidates' skill lists into :class:`CampaignStats`."""
    cat_map: dict[str | None, int] = {}
    avg_scores: list[float] = []
    for category, count, avg in category_rows:
        cat_map[category] = count
        if avg:
            avg_scores.append(avg)

    # Top skills distribution
    skill_counts: dict[str, int] = {}
    for skills in skill_lists:
        for skill in skills or []:
            skill_counts[skill] = skill_counts.get(skill, 0) + 1

    # Most frequent first; ties keep first-seen order (a reverse sort is still stable).
    ranked = sorted(skill_counts.items(), key=lambda item: item[1], reverse=True)[:10]
    top_skills: list[dict[str, Any]] = [{"skill": k, "count": v} for k, v in ranked]

    total = sum(resume_counts.values())
    quota_exceeded = resume_counts.get("quota_exceeded", 0)
    return CampaignStats(
        total_resumes=total,
        total_processed=resume_counts.get("done", 0),
        total_failed=resume_counts.get("failed", 0),
        total_pending=resume_counts.get("pending", 0)
        + resume_counts.get("extracting", 0)
        + resume_counts.get("processing", 0),
        total_quota_exceeded=quota_exceeded,
        avg_score=round(sum(avg_scores) / len(avg_scores), 2) if avg_scores else None,
        strong_match=cat_map.get("strong_match", 0),
        moderate_match=cat_map.get("moderate_match", 0),
        weak_match=cat_map.get("weak_match", 0),
        rejected=cat_map.get("rejected", 0),
        top_skills=top_skills,
    )


async def global_stats(db: AsyncSession) -> GlobalStats:
    """Aggregate counts across all campaigns: three single-table queries, no joins."""
    campaign_counts = await repository.campaign_status_counts(db)
    # TODO(E0-10): the resume counts come from ingestion (see "Cross-module dependencies").
    resume_counts = await repository.resume_status_counts(db)
    total_candidates = await repository.count_candidates(db)
    return build_global_stats(campaign_counts, resume_counts, total_candidates)


def build_global_stats(
    campaign_counts: Mapping[str, int],
    resume_counts: Mapping[str, int],
    total_candidates: int,
) -> GlobalStats:
    """Aggregate campaign and resume counts by status into :class:`GlobalStats`."""
    return GlobalStats(
        total_campaigns=sum(campaign_counts.values()),
        active_campaigns=campaign_counts.get("active", 0),
        closed_campaigns=campaign_counts.get("closed", 0),
        archived_campaigns=campaign_counts.get("archived", 0),
        total_resumes=sum(resume_counts.values()),
        total_processed=resume_counts.get("done", 0),
        total_processing=sum(resume_counts.get(s, 0) for s in PROCESSING_STATUSES),
        total_failed=sum(resume_counts.get(s, 0) for s in TERMINAL_FAILED_STATUSES),
        total_candidates=total_candidates,
    )


# ── Job description parsing ───────────────────────────────────
# str.format template: literal braces are escaped as {{ }}. The only placeholder is
# {job_description}; braces inside the substituted JD are not re-interpreted.
PROMPT = """
You are a technical recruiter. Extract the key information from this job description.

Return ONLY valid JSON with this exact schema:
{{
  "role_title": "extracted job title",
  "seniority": "junior|mid|senior|staff",
  "skills": ["list", "of", "technical", "skills", "required"]
}}

Rules:
- skills: extract ONLY technical skills (languages, frameworks, tools, platforms)
- seniority: infer from title/requirements ("5+ years" = senior, etc.)
- role_title: extract the actual job title from the JD

Job Description:
---
{job_description}
---

Return ONLY valid JSON, no markdown, no explanation.
"""


def build_prompt(job_description: str) -> str:
    """Render PROMPT for one job description (which may itself contain braces)."""
    return PROMPT.format(job_description=job_description)


async def parse_job_description(payload: JDParseRequest) -> JDParseResponse:
    """Extract role title, seniority and skills from a job description with Gemini.

    TODO(E0-10): call ``ai.service`` instead of ``google.generativeai`` directly.
    """
    if not settings.GEMINI_API_KEY:
        raise JDParseError("Gemini API key not configured")

    try:
        model = genai.GenerativeModel("gemini-2.5-flash")
        prompt = build_prompt(payload.job_description)

        response = await model.generate_content_async(prompt)
        text = response.text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.endswith("```"):
            text = text[:-3]

        data = json.loads(text.strip())
        return JDParseResponse(
            role_title=data.get("role_title", "Unknown Role"),
            seniority=data.get("seniority", "mid"),
            skills=data.get("skills", []),
        )
    except Exception as e:
        logger.error("jd_parse_failed", error=str(e))
        raise JDParseError("Failed to parse job description") from e


# ── Candidate filters (shared by the list and the CSV export) ──
@dataclass(frozen=True)
class CandidateFilters:
    """The candidate list filters. ``None`` (or an empty string) means "don't filter"."""

    campaign_id: uuid.UUID
    skill: str | None = None
    category: str | None = None
    min_score: float | None = None
    max_score: float | None = None
    min_experience: float | None = None
    search: str | None = None
    pipeline_stage: str | None = None


def build_candidate_filters(filters: CandidateFilters) -> list[ColumnElement[bool]]:
    """The SQL ``WHERE`` clauses (to be AND-ed) for ``filters``."""
    clauses: list[ColumnElement[bool]] = [Candidate.campaign_id == filters.campaign_id]
    if filters.category:
        clauses.append(Candidate.category == filters.category)
    if filters.min_score is not None:
        clauses.append(Candidate.score >= filters.min_score)
    if filters.max_score is not None:
        clauses.append(Candidate.score <= filters.max_score)
    if filters.min_experience is not None:
        clauses.append(Candidate.years_of_experience >= filters.min_experience)
    if filters.search:
        search_pattern = f"%{escape_like(filters.search)}%"
        clauses.append(
            or_(
                Candidate.name.ilike(search_pattern, escape=LIKE_ESCAPE),
                Candidate.email.ilike(search_pattern, escape=LIKE_ESCAPE),
            )
        )
    if filters.skill:
        # Any skill contains the term, case-insensitively:
        #   EXISTS (SELECT 1 FROM jsonb_array_elements_text(candidates.skills) AS skill
        #           WHERE skill.value ILIKE :pattern ESCAPE '\')
        # Matching each element (rather than `skills::text`) keeps the JSON syntax (quotes,
        # commas, brackets) out of the match. An empty array has no elements: never matches.
        skill_elem = (
            func.jsonb_array_elements_text(Candidate.skills)
            .table_valued(column("value"))
            .alias("skill")
        )
        clauses.append(
            exists()
            .select_from(skill_elem)
            .where(skill_elem.c.value.ilike(f"%{escape_like(filters.skill)}%", escape=LIKE_ESCAPE))
        )
    if filters.pipeline_stage:
        clauses.append(Candidate.pipeline_stage == filters.pipeline_stage)
    return clauses


# ── Candidates ────────────────────────────────────────────────
async def list_candidates(
    db: AsyncSession, filters: CandidateFilters, page: int, page_size: int
) -> Sequence[Candidate]:
    """One page of matching candidates, best score first (unscored last)."""
    return await repository.list_candidates(
        db, build_candidate_filters(filters), offset=(page - 1) * page_size, limit=page_size
    )


CSV_FIELDS = [
    "Name",
    "Email",
    "Phone",
    "Score",
    "Category",
    "Pipeline Stage",
    "Years Experience",
    "Skills",
    "Missing Skills",
    "Strengths",
    "Recommendation",
    "LinkedIn URL",
    "GitHub URL",
]


async def export_candidates_csv(db: AsyncSession, filters: CandidateFilters) -> str:
    """All matching candidates (no paging) as a CSV document, best score first."""
    candidates = await repository.list_candidates(db, build_candidate_filters(filters))
    return render_candidates_csv(candidates)


def _join(values: Sequence[Any] | None) -> str:
    return " | ".join(str(v) for v in (values or []))


def render_candidates_csv(candidates: Iterable[Candidate]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for c in candidates:
        writer.writerow(
            {
                "Name": c.name or "",
                "Email": c.email or "",
                "Phone": c.phone or "",
                "Score": f"{c.score:.0f}" if c.score is not None else "",
                "Category": c.category or "",
                "Pipeline Stage": c.pipeline_stage or "",
                "Years Experience": f"{c.years_of_experience:.1f}"
                if c.years_of_experience is not None
                else "",
                "Skills": _join(c.skills),
                "Missing Skills": _join(c.missing_skills),
                "Strengths": _join(c.strengths),
                "Recommendation": c.recommendation or "",
                "LinkedIn URL": c.linkedin_url or "",
                "GitHub URL": c.github_url or "",
            }
        )
    return output.getvalue()


async def _require_candidate(db: AsyncSession, candidate_id: uuid.UUID) -> Candidate:
    candidate = await repository.get_candidate(db, candidate_id)
    if not candidate:
        raise NotFoundError("Candidate not found")
    return candidate


async def get_candidate(db: AsyncSession, candidate_id: uuid.UUID) -> Candidate:
    return await _require_candidate(db, candidate_id)


async def get_candidate_by_resume(db: AsyncSession, resume_id: uuid.UUID) -> Candidate:
    candidate = await repository.get_candidate_by_resume(db, resume_id)
    if not candidate:
        raise NotFoundError("Candidate not yet processed")
    return candidate


def _check_category(category: str) -> None:
    if category not in VALID_CATEGORIES:
        raise InvalidInputError(
            f"Invalid category. Must be one of: {', '.join(VALID_CATEGORIES)}",
        )


def _check_pipeline_stage(stage: str) -> None:
    if stage not in VALID_PIPELINE_STAGES:
        raise InvalidInputError(
            f"Invalid stage. Must be one of: {', '.join(sorted(VALID_PIPELINE_STAGES))}",
        )


async def update_candidate_category(
    db: AsyncSession, candidate_id: uuid.UUID, category: str
) -> Candidate:
    """Recruiter override of the AI category. The value is checked before the lookup."""
    _check_category(category)
    candidate = await _require_candidate(db, candidate_id)
    candidate.category = category
    await repository.commit_and_refresh(db, candidate)
    logger.info("category_overridden", candidate_id=str(candidate_id), category=category)
    return candidate


async def update_candidate_notes(
    db: AsyncSession, candidate_id: uuid.UUID, notes: str
) -> Candidate:
    candidate = await _require_candidate(db, candidate_id)
    candidate.notes = notes
    await repository.commit_and_refresh(db, candidate)
    logger.info("notes_updated", candidate_id=str(candidate_id))
    return candidate


async def update_candidate_pipeline(
    db: AsyncSession, candidate_id: uuid.UUID, stage: str
) -> Candidate:
    """Move one candidate to a pipeline stage. The stage is checked before the lookup."""
    _check_pipeline_stage(stage)
    candidate = await _require_candidate(db, candidate_id)
    candidate.pipeline_stage = stage
    await repository.commit_and_refresh(db, candidate)
    logger.info("pipeline_updated", candidate_id=str(candidate_id), stage=stage)
    return candidate


async def bulk_update_pipeline(
    db: AsyncSession, campaign_id: uuid.UUID, candidate_ids: Sequence[uuid.UUID], stage: str
) -> int:
    """Move the campaign's candidates among ``candidate_ids`` to ``stage``; returns how many."""
    _check_pipeline_stage(stage)
    candidates = await repository.candidates_in_campaign(db, campaign_id, candidate_ids)
    for c in candidates:
        c.pipeline_stage = stage
    await repository.commit(db)
    logger.info("bulk_pipeline_updated", count=len(candidates), stage=stage)
    return len(candidates)


async def bulk_delete_candidates(
    db: AsyncSession, campaign_id: uuid.UUID, candidate_ids: Sequence[uuid.UUID]
) -> list[uuid.UUID]:
    """Delete the campaign's candidates among ``candidate_ids`` and their resumes.

    Returns the deleted candidates' resume ids (one per deleted candidate).
    TODO(E0-10): the resume deletion moves to ingestion (see "Cross-module dependencies").
    """
    candidates = await repository.candidates_in_campaign(db, campaign_id, candidate_ids)
    resume_ids = [c.resume_id for c in candidates]

    for c in candidates:
        await repository.delete_candidate(db, c)

    if resume_ids:
        await repository.delete_resumes(db, resume_ids)

    await repository.commit(db)
    logger.info("bulk_candidates_deleted", count=len(candidates))
    return resume_ids


async def delete_candidate(db: AsyncSession, candidate_id: uuid.UUID) -> uuid.UUID:
    """Delete one candidate and its resume; returns the resume id.

    TODO(E0-10): the resume deletion moves to ingestion (see "Cross-module dependencies").
    """
    candidate = await _require_candidate(db, candidate_id)

    # Delete candidate first to avoid SQLAlchemy trying to set resume_id=NULL
    await repository.delete_candidate(db, candidate)
    await repository.delete_resume(db, candidate.resume_id)

    await repository.commit(db)
    logger.info("candidate_deleted", candidate_id=str(candidate_id))
    return candidate.resume_id
