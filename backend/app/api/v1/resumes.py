import uuid
import asyncio
import json
import hashlib
from typing import List, AsyncGenerator
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, tuple_
from sse_starlette.sse import EventSourceResponse
import redis.asyncio as aioredis
import structlog

from app.core.database import get_db
from app.core.config import settings
from app.core import storage
from app.models.resume import Resume
from app.models.campaign import Campaign
from app.schemas.resume import ResumeResponse, UploadBatchResponse
from workers.extract_task import extract_resume

logger = structlog.get_logger()
router = APIRouter(tags=["resumes"])

MAX_FILE_SIZE = settings.MAX_FILE_SIZE_MB * 1024 * 1024


@router.post(
    "/campaigns/{campaign_id}/upload",
    response_model=UploadBatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def bulk_upload_resumes(
    campaign_id: uuid.UUID,
    files: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    # Validate campaign exists
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    if len(files) > settings.MAX_FILES_PER_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {settings.MAX_FILES_PER_BATCH} files per upload batch",
        )

    accepted_ids = []
    rejected = []

    for file in files:
        # Validate file type
        if not file.filename.lower().endswith(".pdf"):
            rejected.append({"filename": file.filename, "reason": "Only PDF files are accepted"})
            continue

        content = await file.read()

        # Validate size
        if len(content) > MAX_FILE_SIZE:
            rejected.append({
                "filename": file.filename,
                "reason": f"File exceeds {settings.MAX_FILE_SIZE_MB}MB limit",
            })
            continue

        # Validate PDF magic bytes
        if content[:4] != b"%PDF":
            rejected.append({"filename": file.filename, "reason": "Invalid PDF file"})
            continue

        # Calculate hash for idempotency
        file_hash = hashlib.sha256(content).hexdigest()

        # Check if already processed or processing
        existing_result = await db.execute(
            select(Resume)
            .where(Resume.campaign_id == campaign_id, Resume.file_hash == file_hash)
            .order_by(Resume.uploaded_at.desc())
        )
        existing_resume = existing_result.scalars().first()
        
        if existing_resume:
            if existing_resume.status == "done":
                rejected.append({"filename": file.filename, "reason": "Duplicate file (already processed successfully)"})
                continue
            elif existing_resume.status in ("pending", "extracting", "processing"):
                rejected.append({"filename": file.filename, "reason": "Duplicate file (currently in progress)"})
                continue
            # If status is failed or quota_exceeded, we allow re-upload (creates new Resume row)

        # Upload to MinIO
        resume_id = uuid.uuid4()
        object_key = f"campaigns/{campaign_id}/resumes/{resume_id}/{file.filename}"
        storage.upload_file(content, object_key)

        # Create Resume record
        resume = Resume(
            id=resume_id,
            campaign_id=campaign_id,
            original_filename=file.filename,
            storage_path=object_key,
            file_hash=file_hash,
            file_size_bytes=len(content),
            status="pending",
        )
        db.add(resume)
        accepted_ids.append(resume_id)

    await db.commit()

    # Enqueue extraction tasks
    for rid in accepted_ids:
        extract_resume.apply_async(args=[str(rid)], queue="extract_queue")

    logger.info(
        "bulk_upload_complete",
        campaign_id=str(campaign_id),
        accepted=len(accepted_ids),
        rejected=len(rejected),
    )

    return UploadBatchResponse(
        batch_id=str(uuid.uuid4()),
        campaign_id=campaign_id,
        total_files=len(files),
        accepted_files=len(accepted_ids),
        rejected_files=rejected,
        resume_ids=accepted_ids,
        message=f"Processing {len(accepted_ids)} resumes asynchronously",
    )


@router.get("/campaigns/{campaign_id}/resumes", response_model=List[ResumeResponse])
async def list_campaign_resumes(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Resume)
        .where(Resume.campaign_id == campaign_id)
        .order_by(Resume.uploaded_at.desc())
    )
    return result.scalars().all()


@router.get("/resumes/{resume_id}", response_model=ResumeResponse)
async def get_resume(resume_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Resume).where(Resume.id == resume_id))
    resume = result.scalar_one_or_none()
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
    return resume


@router.post("/campaigns/{campaign_id}/resumes/retry-failed")
async def retry_failed_resumes(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Re-queue all failed and quota_exceeded resumes for a campaign."""
    result = await db.execute(
        select(Resume).where(
            Resume.campaign_id == campaign_id,
            Resume.status.in_(["failed", "quota_exceeded"])
        )
    )
    resumes_to_retry = result.scalars().all()
    
    if not resumes_to_retry:
        return {"retried": 0}
        
    for resume in resumes_to_retry:
        resume.status = "pending"
        resume.retry_count = 0
        resume.error_message = None
        
    await db.commit()
    
    for resume in resumes_to_retry:
        extract_resume.apply_async(args=[str(resume.id)], queue="extract_queue")
        
    return {"retried": len(resumes_to_retry)}


@router.get("/campaigns/{campaign_id}/stream")
async def stream_campaign_status(campaign_id: uuid.UUID):
    """
    SSE endpoint — streams real-time processing updates for a campaign.
    Frontend subscribes and receives events as each resume finishes.
    """
    async def event_generator() -> AsyncGenerator:
        r = aioredis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
        pubsub = r.pubsub()
        await pubsub.subscribe("resume:all_updates")

        try:
            # Send initial connection event
            yield {"event": "connected", "data": json.dumps({"campaign_id": str(campaign_id)})}

            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = json.loads(message["data"])
                    yield {
                        "event": "resume_update",
                        "data": json.dumps(data),
                    }

        except asyncio.CancelledError:
            await pubsub.unsubscribe("resume:all_updates")
            await r.aclose()

    return EventSourceResponse(event_generator())
