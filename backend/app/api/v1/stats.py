"""
Global stats endpoint — aggregate counts across all campaigns.
Used by the dashboard to populate the top-level stat cards.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.resume import Resume
from app.modules.hiring.models import Campaign, Candidate
from app.modules.hiring.schemas import GlobalStats
from app.platform.database import get_db

router = APIRouter(prefix="/stats", tags=["stats"])

PROCESSING_STATUSES = ("pending", "extracting", "processing")
TERMINAL_FAILED_STATUSES = ("failed", "quota_exceeded")


@router.get("", response_model=GlobalStats)
async def get_global_stats(db: AsyncSession = Depends(get_db)):
    """
    Return aggregate counts across all campaigns.
    Three single-table queries — no joins, very fast.
    """
    # 1. Campaign counts by status
    campaign_result = await db.execute(
        select(Campaign.status, func.count(Campaign.id)).group_by(Campaign.status)
    )
    campaign_map: dict[str, int] = {row[0]: row[1] for row in campaign_result}

    # 2. Resume counts by status
    resume_result = await db.execute(
        select(Resume.status, func.count(Resume.id)).group_by(Resume.status)
    )
    resume_map: dict[str, int] = {row[0]: row[1] for row in resume_result}

    # 3. Total candidates
    candidate_result = await db.execute(select(func.count(Candidate.id)))
    total_candidates: int = candidate_result.scalar_one() or 0

    total_resumes = sum(resume_map.values())
    total_processing = sum(resume_map.get(s, 0) for s in PROCESSING_STATUSES)
    total_failed = sum(resume_map.get(s, 0) for s in TERMINAL_FAILED_STATUSES)
    total_campaigns = sum(campaign_map.values())

    return GlobalStats(
        total_campaigns=total_campaigns,
        active_campaigns=campaign_map.get("active", 0),
        closed_campaigns=campaign_map.get("closed", 0),
        archived_campaigns=campaign_map.get("archived", 0),
        total_resumes=total_resumes,
        total_processed=resume_map.get("done", 0),
        total_processing=total_processing,
        total_failed=total_failed,
        total_candidates=total_candidates,
    )
