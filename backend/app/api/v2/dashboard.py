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
from app.modules.identity.dependencies import access_scope, tenant_db
from app.modules.ingestion import service as ingestion
from app.modules.talent import service as talent
from app.platform.tenancy import AccessScope

router = APIRouter(tags=["dashboard"])

HOME_CAMPAIGNS = 10
HOME_PROCESSING = 10
HOME_MATCHES = 10


@router.get("/stats", operation_id="getStats", response_model=Stats)
async def get_stats(
    db: AsyncSession = Depends(tenant_db), scope: AccessScope = Depends(access_scope)
) -> Stats:
    """Counts across all campaigns: campaigns by status, resumes by status, applications by
    category and stage, and people."""
    workspace = await hiring.workspace_stats(db, scope, await ingestion.resume_tally(db, scope))
    return Stats(**workspace.model_dump(), people=await talent.count_people(db))


@router.get("/home", operation_id="getHome", response_model=Home)
async def get_home(
    db: AsyncSession = Depends(tenant_db), scope: AccessScope = Depends(access_scope)
) -> Home:
    """The Home screen in one call."""
    campaigns = await hiring.active_campaigns(
        db, scope, HOME_CAMPAIGNS, ingestion.resume_tallies(db)
    )
    in_progress, total = await ingestion.resumes_in_progress(db, scope, HOME_PROCESSING)
    refs = await hiring.campaign_refs(
        db, scope, list(dict.fromkeys(r.campaign_id for r in in_progress))
    )
    matches = await hiring.recent_strong_matches(
        db, scope, HOME_MATCHES, ingestion.resume_profiles(db)
    )
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
