import csv
import io
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from sqlalchemy.dialects.postgresql import JSONB
import structlog

from app.core.database import get_db
from app.models.candidate import Candidate
from app.models.resume import Resume
from app.schemas.candidate import (
    CandidateListItem, CandidateDetail,
    CandidateCategoryUpdate, CandidateNotesUpdate, CandidatePipelineUpdate,
)
from pydantic import BaseModel

logger = structlog.get_logger()
router = APIRouter(tags=["candidates"])

VALID_CATEGORIES = {"strong_match", "moderate_match", "weak_match", "rejected"}
VALID_PIPELINE_STAGES = {
    "screened", "phone_call", "technical", "offer", "hired", "rejected_manual"
}

class BulkPipelineUpdate(BaseModel):
    candidate_ids: List[uuid.UUID]
    stage: str

class BulkDeleteRequest(BaseModel):
    candidate_ids: List[uuid.UUID]


# ── Helper ────────────────────────────────────────────────────

def _build_candidate_filters(
    campaign_id: uuid.UUID,
    skill: Optional[str],
    category: Optional[str],
    min_score: Optional[float],
    max_score: Optional[float],
    min_experience: Optional[float],
    search: Optional[str],
    pipeline_stage: Optional[str],
) -> list:
    filters = [Candidate.campaign_id == campaign_id]
    if category:
        filters.append(Candidate.category == category)
    if min_score is not None:
        filters.append(Candidate.score >= min_score)
    if max_score is not None:
        filters.append(Candidate.score <= max_score)
    if min_experience is not None:
        filters.append(Candidate.years_of_experience >= min_experience)
    if search:
        filters.append(
            or_(
                Candidate.name.ilike(f"%{search}%"),
                Candidate.email.ilike(f"%{search}%"),
            )
        )
    if skill:
        filters.append(
            Candidate.skills.cast(JSONB).astext.ilike(f"%{skill}%")
        )
    if pipeline_stage:
        filters.append(Candidate.pipeline_stage == pipeline_stage)
    return filters


# ── List candidates ───────────────────────────────────────────

@router.get("/campaigns/{campaign_id}/candidates", response_model=List[CandidateListItem])
async def list_candidates(
    campaign_id: uuid.UUID,
    skill: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    min_score: Optional[float] = Query(None, ge=0, le=100),
    max_score: Optional[float] = Query(None, ge=0, le=100),
    min_experience: Optional[float] = Query(None, ge=0),
    search: Optional[str] = Query(None),
    pipeline_stage: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    filters = _build_candidate_filters(
        campaign_id, skill, category, min_score, max_score,
        min_experience, search, pipeline_stage,
    )
    offset = (page - 1) * page_size
    result = await db.execute(
        select(Candidate)
        .where(and_(*filters))
        .order_by(Candidate.score.desc().nullslast())
        .offset(offset)
        .limit(page_size)
    )
    return result.scalars().all()


# ── CSV Export ────────────────────────────────────────────────
# NOTE: must be declared before /{candidate_id} to avoid routing conflict

@router.get("/campaigns/{campaign_id}/candidates/export")
async def export_candidates_csv(
    campaign_id: uuid.UUID,
    category: Optional[str] = Query(None),
    min_score: Optional[float] = Query(None, ge=0, le=100),
    max_score: Optional[float] = Query(None, ge=0, le=100),
    search: Optional[str] = Query(None),
    pipeline_stage: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Download a filtered candidate list as CSV.
    Applies the same filters as the list endpoint.
    """
    filters = _build_candidate_filters(
        campaign_id, None, category, min_score, max_score,
        None, search, pipeline_stage,
    )
    result = await db.execute(
        select(Candidate)
        .where(and_(*filters))
        .order_by(Candidate.score.desc().nullslast())
    )
    candidates = result.scalars().all()

    def _join(values: list) -> str:
        return " | ".join(str(v) for v in (values or []))

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "Name", "Email", "Phone", "Score", "Category", "Pipeline Stage",
        "Years Experience", "Skills", "Missing Skills", "Strengths",
        "Recommendation", "LinkedIn URL", "GitHub URL",
    ])
    writer.writeheader()
    for c in candidates:
        writer.writerow({
            "Name": c.name or "",
            "Email": c.email or "",
            "Phone": c.phone or "",
            "Score": f"{c.score:.0f}" if c.score is not None else "",
            "Category": c.category or "",
            "Pipeline Stage": c.pipeline_stage or "",
            "Years Experience": f"{c.years_of_experience:.1f}" if c.years_of_experience is not None else "",
            "Skills": _join(c.skills),
            "Missing Skills": _join(c.missing_skills),
            "Strengths": _join(c.strengths),
            "Recommendation": c.recommendation or "",
            "LinkedIn URL": c.linkedin_url or "",
            "GitHub URL": c.github_url or "",
        })

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=candidates_{campaign_id}.csv"
        },
    )


# ── Get / Update individual candidates ───────────────────────

@router.get("/candidates/{candidate_id}", response_model=CandidateDetail)
async def get_candidate(candidate_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Candidate).where(Candidate.id == candidate_id))
    candidate = result.scalar_one_or_none()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return candidate


@router.put("/candidates/{candidate_id}/category", response_model=CandidateDetail)
async def update_candidate_category(
    candidate_id: uuid.UUID,
    payload: CandidateCategoryUpdate,
    db: AsyncSession = Depends(get_db),
):
    if payload.category not in VALID_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid category. Must be one of: {', '.join(VALID_CATEGORIES)}",
        )
    result = await db.execute(select(Candidate).where(Candidate.id == candidate_id))
    candidate = result.scalar_one_or_none()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    candidate.category = payload.category
    await db.commit()
    await db.refresh(candidate)
    logger.info("category_overridden", candidate_id=str(candidate_id), category=payload.category)
    return candidate


@router.patch("/candidates/{candidate_id}/notes", response_model=CandidateDetail)
async def update_candidate_notes(
    candidate_id: uuid.UUID,
    payload: CandidateNotesUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Candidate).where(Candidate.id == candidate_id))
    candidate = result.scalar_one_or_none()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    candidate.notes = payload.notes
    await db.commit()
    await db.refresh(candidate)
    logger.info("notes_updated", candidate_id=str(candidate_id))
    return candidate


@router.patch("/candidates/{candidate_id}/pipeline", response_model=CandidateDetail)
async def update_candidate_pipeline(
    candidate_id: uuid.UUID,
    payload: CandidatePipelineUpdate,
    db: AsyncSession = Depends(get_db),
):
    if payload.stage not in VALID_PIPELINE_STAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid stage. Must be one of: {', '.join(sorted(VALID_PIPELINE_STAGES))}",
        )
    result = await db.execute(select(Candidate).where(Candidate.id == candidate_id))
    candidate = result.scalar_one_or_none()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    candidate.pipeline_stage = payload.stage
    await db.commit()
    await db.refresh(candidate)
    logger.info("pipeline_updated", candidate_id=str(candidate_id), stage=payload.stage)
    return candidate


@router.get("/resumes/{resume_id}/candidate", response_model=CandidateDetail)
async def get_candidate_by_resume(resume_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Candidate).where(Candidate.resume_id == resume_id)
    )
    candidate = result.scalar_one_or_none()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not yet processed")
    return candidate


# ── Bulk Actions ──────────────────────────────────────────────

@router.patch("/campaigns/{campaign_id}/candidates/bulk-pipeline")
async def bulk_update_pipeline(
    campaign_id: uuid.UUID,
    payload: BulkPipelineUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Move multiple candidates to the same pipeline stage in one request."""
    if payload.stage not in VALID_PIPELINE_STAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid stage. Must be one of: {', '.join(sorted(VALID_PIPELINE_STAGES))}",
        )
    result = await db.execute(
        select(Candidate).where(
            Candidate.campaign_id == campaign_id,
            Candidate.id.in_(payload.candidate_ids),
        )
    )
    candidates = result.scalars().all()
    for c in candidates:
        c.pipeline_stage = payload.stage
    await db.commit()
    logger.info("bulk_pipeline_updated", count=len(candidates), stage=payload.stage)
    return {"updated": len(candidates)}


@router.delete("/campaigns/{campaign_id}/candidates/bulk", status_code=status.HTTP_200_OK)
async def bulk_delete_candidates(
    campaign_id: uuid.UUID,
    payload: BulkDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple candidates (and their resumes) in one request."""
    result = await db.execute(
        select(Candidate).where(
            Candidate.campaign_id == campaign_id,
            Candidate.id.in_(payload.candidate_ids),
        )
    )
    candidates = result.scalars().all()
    resume_ids = [c.resume_id for c in candidates]

    for c in candidates:
        await db.delete(c)

    if resume_ids:
        resume_result = await db.execute(
            select(Resume).where(Resume.id.in_(resume_ids))
        )
        for r in resume_result.scalars().all():
            await db.delete(r)

    await db.commit()
    logger.info("bulk_candidates_deleted", count=len(candidates))
    return {"deleted": len(candidates)}


@router.delete("/candidates/{candidate_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_candidate(candidate_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Candidate).where(Candidate.id == candidate_id))
    candidate = result.scalar_one_or_none()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    # Delete candidate first to avoid SQLAlchemy trying to set resume_id=NULL
    await db.delete(candidate)

    resume_result = await db.execute(select(Resume).where(Resume.id == candidate.resume_id))
    resume = resume_result.scalar_one_or_none()
    if resume:
        await db.delete(resume)

    await db.commit()
    logger.info("candidate_deleted", candidate_id=str(candidate_id))
