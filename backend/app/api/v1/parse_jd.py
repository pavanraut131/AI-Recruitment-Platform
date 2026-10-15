import json

import google.generativeai as genai
import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.config import settings

logger = structlog.get_logger()
router = APIRouter(prefix="/campaigns", tags=["campaigns"])

if settings.GEMINI_API_KEY:
    genai.configure(api_key=settings.GEMINI_API_KEY)


class JDParseRequest(BaseModel):
    job_description: str


class JDParseResponse(BaseModel):
    role_title: str
    seniority: str
    skills: list[str]


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


@router.post("/parse-jd", response_model=JDParseResponse)
async def parse_job_description(payload: JDParseRequest):
    if not settings.GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="Gemini API key not configured")

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
        raise HTTPException(status_code=500, detail="Failed to parse job description") from e
