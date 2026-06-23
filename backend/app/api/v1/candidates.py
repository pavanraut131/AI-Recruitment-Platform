import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, cast
from sqlalchemy.dialects.postgresql import JSONB
import structlog

from app.core.database import get_db
from app.models.candidate import Candidate
from app.models.resume import Resume
from app.schemas.candidate import CandidateListItem, CandidateDetail, CandidateCategoryUpdate

logger = structlog.get_logger()
router = APIRouter(tags=["candidates"])

VALID_CATEGORIES = {"strong_match", "moderate_match", "weak_match", "rejected"}


@router.get("/campaigns/{campaign_id}/candidates", response_model=List[CandidateListItem])
async def list_candidates(
    campaign_id: uuid.UUID,
    skill: Optional[str] = Query(None, description="Filter by skill keyword"),
    category: Optional[str] = Query(None, description="Filter by match category"),
    min_score: Optional[float] = Query(None, ge=0, le=100),
    max_score: Optional[float] = Query(None, ge=0, le=100),
    min_experience: Optional[float] = Query(None, ge=0),
    search: Optional[str] = Query(None, description="Search by name or email"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
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
        # JSONB array contains skill (case-insensitive approximate)
        filters.append(
            Candidate.skills.cast(JSONB).astext.ilike(f"%{skill}%")
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


@router.get("/resumes/{resume_id}/candidate", response_model=CandidateDetail)
async def get_candidate_by_resume(resume_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Candidate).where(Candidate.resume_id == resume_id)
    )
    candidate = result.scalar_one_or_none()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not yet processed")
    return candidate


@router.delete("/candidates/{candidate_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_candidate(candidate_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    # Delete the underlying resume which cascade-deletes the candidate
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
