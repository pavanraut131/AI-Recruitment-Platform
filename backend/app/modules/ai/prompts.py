"""The prompts sent to the language model. Private to the ``ai`` module.

All templates are ``str.format`` templates: literal braces are escaped as ``{{ }}``, and values
substituted into them (resume text, a job description) are not re-interpreted, so braces inside
them are safe. The exact text is part of the recorded behaviour: the parse-jd prompt is pinned
by ``tests/characterization`` (``test_parse_jd_prompt``).
"""

import json
from typing import Any

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


# The only placeholder is {job_description}; braces inside the substituted JD are not
# re-interpreted (ticket B1).
JD_PROMPT = """
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

OCR_PROMPT = (
    "Extract all text from this resume page. Return only the extracted text, no formatting."
)

# Resume text beyond this many characters is not sent to the model.
MAX_RESUME_CHARS = 12000


def build_extraction_prompt(resume_text: str) -> str:
    return EXTRACTION_PROMPT_TEMPLATE.format(resume_text=resume_text[:MAX_RESUME_CHARS])


def build_scoring_prompt(
    candidate_profile: dict[str, Any], role: str, required_skills: list[str]
) -> str:
    return SCORING_PROMPT_TEMPLATE.format(
        role=role,
        required_skills=", ".join(required_skills),
        candidate_profile=json.dumps(candidate_profile, indent=2),
    )


def build_jd_prompt(job_description: str) -> str:
    """Render :data:`JD_PROMPT` for one job description (which may itself contain braces)."""
    return JD_PROMPT.format(job_description=job_description)
