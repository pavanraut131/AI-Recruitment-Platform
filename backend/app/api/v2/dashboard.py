"""``/api/v2/stats`` and ``/api/v2/home``: workspace counts and the Home screen (all modules).

``GET /home`` answers everything the Home screen shows in one call (spec §4): the active
campaigns with their counts, the resumes still being processed, and the recent strong matches.
Each part is bounded (:data:`HOME_CAMPAIGNS`, :data:`HOME_PROCESSING`, :data:`HOME_MATCHES`
items), and the number of queries does not depend on the data.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v2.schemas import Home, HomeProcessing, ProcessingResume, Stats
from app.modules.hiring import service as hiring
from app.modules.ingestion import service as ingestion
from app.modules.talent import service as talent
from app.platform.database import get_db

router = APIRouter(tags=["dashboard"])

HOME_CAMPAIGNS = 10
HOME_PROCESSING = 10
HOME_MATCHES = 10


@router.get("/stats", operation_id="getStats", response_model=Stats)
async def get_stats(db: AsyncSession = Depends(get_db)) -> Stats:
    """Counts across all campaigns: campaigns by status, resumes by status, applications by
    category and stage, and people."""
    workspace = await hiring.workspace_stats(db, await ingestion.resume_tally(db))
    return Stats(**workspace.model_dump(), people=await talent.count_people(db))


@router.get("/home", operation_id="getHome", response_model=Home)
async def get_home(db: AsyncSession = Depends(get_db)) -> Home:
    """The Home screen in one call."""
    campaigns = await hiring.active_campaigns(db, HOME_CAMPAIGNS, ingestion.resume_tallies(db))
    in_progress, total = await ingestion.resumes_in_progress(db, HOME_PROCESSING)
    refs = await hiring.campaign_refs(db, list(dict.fromkeys(r.campaign_id for r in in_progress)))
    matches = await hiring.recent_strong_matches(db, HOME_MATCHES, ingestion.resume_profiles(db))
    return Home(
        active_campaigns=campaigns,
        processing=HomeProcessing(
            total=total,
            items=[
                ProcessingResume(**r.model_dump(), campaign=refs.get(r.campaign_id))
                for r in in_progress
            ],
        ),
        recent_strong_matches=matches,
    )
