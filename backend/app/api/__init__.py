from fastapi import APIRouter

from app.api.v1 import campaigns, candidates, resumes

router = APIRouter(prefix="/api/v1", tags=["root"])

router.include_router(campaigns.router)
router.include_router(resumes.router)
router.include_router(candidates.router)
