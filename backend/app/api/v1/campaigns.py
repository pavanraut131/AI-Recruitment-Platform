import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.campaign import Campaign
from app.models.resume import Resume
from app.models.candidate import Candidate
from app.schemas.campaign import CampaignCreate, CampaignUpdate, CampaignResponse, CampaignStats
import structlog

logger = structlog.get_logger()
router = APIRouter(prefix="/campaigns", tags=["campaigns"])


@router.post("", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED)
async def create_campaign(payload: CampaignCreate, db: AsyncSession = Depends(get_db)):
    campaign = Campaign(**payload.model_dump())
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)
    logger.info("campaign_created", id=str(campaign.id), title=campaign.title)
    return campaign


@router.get("", response_model=List[CampaignResponse])
async def list_campaigns(
    skip: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Campaign).order_by(Campaign.created_at.desc()).offset(skip).limit(limit)
    )
    return result.scalars().all()


@router.get("/{campaign_id}", response_model=CampaignResponse)
async def get_campaign(campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Calculate stats
    stats = await _compute_stats(campaign_id, db)
    response = CampaignResponse.model_validate(campaign)
    response.stats = stats
    return response


@router.put("/{campaign_id}", response_model=CampaignResponse)
async def update_campaign(
    campaign_id: uuid.UUID,
    payload: CampaignUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(campaign, field, value)

    await db.commit()
    await db.refresh(campaign)
    return campaign


@router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_campaign(campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    await db.delete(campaign)
    await db.commit()


@router.get("/{campaign_id}/stats", response_model=CampaignStats)
async def campaign_stats(campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    return await _compute_stats(campaign_id, db)


async def _compute_stats(campaign_id: uuid.UUID, db: AsyncSession) -> CampaignStats:
    # Resume counts by status
    resume_counts = await db.execute(
        select(Resume.status, func.count(Resume.id))
        .where(Resume.campaign_id == campaign_id)
        .group_by(Resume.status)
    )
    status_map = {row[0]: row[1] for row in resume_counts}

    # Candidate category counts + avg score
    cand_stats = await db.execute(
        select(
            Candidate.category,
            func.count(Candidate.id),
            func.avg(Candidate.score),
        )
        .where(Candidate.campaign_id == campaign_id)
        .group_by(Candidate.category)
    )
    cat_map = {}
    avg_scores = []
    for row in cand_stats:
        cat_map[row[0]] = row[1]
        if row[2]:
            avg_scores.append(row[2])

    # Top skills distribution
    skills_result = await db.execute(
        select(Candidate.skills).where(Candidate.campaign_id == campaign_id)
    )
    skill_counts: dict = {}
    for (skills,) in skills_result:
        for skill in (skills or []):
            skill_counts[skill] = skill_counts.get(skill, 0) + 1

    top_skills = sorted(
        [{"skill": k, "count": v} for k, v in skill_counts.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:10]

    total = sum(status_map.values())
    quota_exceeded = status_map.get("quota_exceeded", 0)
    return CampaignStats(
        total_resumes=total,
        total_processed=status_map.get("done", 0),
        total_failed=status_map.get("failed", 0),
        total_pending=status_map.get("pending", 0) + status_map.get("extracting", 0) + status_map.get("processing", 0),
        total_quota_exceeded=quota_exceeded,
        avg_score=round(sum(avg_scores) / len(avg_scores), 2) if avg_scores else None,
        strong_match=cat_map.get("strong_match", 0),
        moderate_match=cat_map.get("moderate_match", 0),
        weak_match=cat_map.get("weak_match", 0),
        rejected=cat_map.get("rejected", 0),
        top_skills=top_skills,
    )
