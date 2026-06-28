import json
import structlog
from typing import List
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    wait_fixed,
    retry_if_exception_type,
)
from google.api_core.exceptions import ResourceExhausted
import google.generativeai as genai
from app.core.config import settings
from app.schemas.candidate import ExtractionResult, ScoringResult

logger = structlog.get_logger()

# Configure Gemini client once at module level
genai.configure(api_key=settings.GEMINI_API_KEY)
_model = genai.GenerativeModel(settings.GEMINI_MODEL)


EXTRACTION_PROMPT_TEMPLATE = """You are an expert resume parser. Extract all candidate information from the resume text below.

Return ONLY a valid JSON object with this exact schema (use null for missing fields):
{{
  "name": "string or null",
  "email": "string or null",
  "phone": "string or null",
  "github_url": "string or null",
  "linkedin_url": "string or null",
  "skills": ["array of skill strings"],
  "education": [
    {{"degree": "string", "institution": "string", "year": "string or null"}}
  ],
  "work_experience": [
    {{"company": "string", "role": "string", "duration": "string", "description": "string or null"}}
  ],
  "certifications": ["array of certification name strings"],
  "projects": [
    {{"name": "string", "description": "string", "technologies": ["strings"]}}
  ],
  "years_of_experience": 0.0
}}

Resume Text:
---
{resume_text}
---

Return ONLY valid JSON, no markdown, no explanation."""


SCORING_PROMPT_TEMPLATE = """You are a senior technical recruiter AI. Score this candidate against the job requirements.

Job Role: {role}
Required Skills: {required_skills}

Candidate Profile:
{candidate_profile}

Score the candidate and return ONLY a valid JSON object with this exact schema:
{{
  "score": 85.0,
  "score_breakdown": {{
    "skills_match": 35,
    "experience_match": 25,
    "project_relevance": 15,
    "education_bonus": 7,
    "communication_quality": 3
  }},
  "category": "strong_match",
  "summary": "2-3 sentence candidate summary",
  "strengths": ["strength 1", "strength 2"],
  "weaknesses": ["weakness 1"],
  "missing_skills": ["skill not in resume"],
  "recommendation": "Proceed to technical interview / Phone screen first / Not recommended"
}}

Score breakdown rules (must sum to the overall score):
- skills_match: 0-40 pts. How many required skills the candidate has.
- experience_match: 0-30 pts. Does their years/seniority match the role?
- project_relevance: 0-15 pts. Are their projects relevant to the role?
- education_bonus: 0-10 pts. Degree relevance and institution quality.
- communication_quality: 0-5 pts. Clarity and quality of resume writing.

Category rules:
- score >= 75: "strong_match"
- score >= 50: "moderate_match"  
- score >= 25: "weak_match"
- score < 25: "rejected"

Return ONLY valid JSON, no markdown, no explanation."""


def _is_rate_limit(exc: BaseException) -> bool:
    return isinstance(exc, ResourceExhausted)


def _is_transient(exc: BaseException) -> bool:
    return not isinstance(exc, ResourceExhausted)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(65),          # Gemini free-tier rate limit resets in ~60s
    retry=retry_if_exception_type(ResourceExhausted),
    reraise=True,
)
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def extract_candidate_info(resume_text: str) -> ExtractionResult:
    """Call Gemini to extract structured candidate info from raw resume text."""
    prompt = EXTRACTION_PROMPT_TEMPLATE.format(resume_text=resume_text[:12000])

    response = _model.generate_content(
        prompt,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.1,
        ),
    )

    raw = response.text.strip()
    logger.debug("gemini_extraction_response", length=len(raw))

    data = json.loads(raw)
    return ExtractionResult(**data)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(65),          # Gemini free-tier rate limit resets in ~60s
    retry=retry_if_exception_type(ResourceExhausted),
    reraise=True,
)
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def score_candidate(
    candidate_profile: dict,
    role: str,
    required_skills: List[str],
) -> ScoringResult:
    """Call Gemini to score and classify a candidate against a job role."""
    prompt = SCORING_PROMPT_TEMPLATE.format(
        role=role,
        required_skills=", ".join(required_skills),
        candidate_profile=json.dumps(candidate_profile, indent=2),
    )

    response = _model.generate_content(
        prompt,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )

    raw = response.text.strip()
    logger.debug("gemini_scoring_response", length=len(raw))

    data = json.loads(raw)

    # Enforce category based on score if Gemini drifts
    score = float(data.get("score", 0))
    if score >= 75:
        data["category"] = "strong_match"
    elif score >= 50:
        data["category"] = "moderate_match"
    elif score >= 25:
        data["category"] = "weak_match"
    else:
        data["category"] = "rejected"

    return ScoringResult(**data)


def is_resume_content(extraction: ExtractionResult) -> bool:
    """
    Heuristic check: does the extracted data look like a real resume?
    Rejects documents that yield no identifiable candidate info.
    """
    has_name = bool(extraction.name and len(extraction.name.strip()) > 1)
    has_skills = bool(extraction.skills and len(extraction.skills) > 0)
    has_experience = bool(extraction.work_experience and len(extraction.work_experience) > 0)
    has_education = bool(extraction.education and len(extraction.education) > 0)
    has_email = bool(extraction.email)

    # Must have at least 2 of these signals to be considered a real resume
    signals = sum([has_name, has_skills, has_experience, has_education, has_email])
    return signals >= 2
