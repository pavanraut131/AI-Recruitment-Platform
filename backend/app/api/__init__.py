from fastapi import APIRouter

router = APIRouter(prefix="/api/v1", tags=["root"])

from app.api.v1 import campaigns, resumes, candidates

router.include_router(campaigns.router)
router.include_router(resumes.router)
router.include_router(candidates.router)
