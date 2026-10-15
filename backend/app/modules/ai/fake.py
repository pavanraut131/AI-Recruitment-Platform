"""Deterministic, offline implementation of :class:`~app.modules.ai.service.AIService`.

Selected with ``AI_PROVIDER=fake``. No network, no API key and no randomness: the same input
always gives the same output, and the output is derived from the input text with simple rules
(regular expressions and a fixed skill vocabulary), so it is plausible enough to click through
the app locally, run an end-to-end smoke test, or write unit tests against:

- **Profile:** the first line that looks like a name, the first e-mail, phone, GitHub and
  LinkedIn URL, every vocabulary skill mentioned, ``N years`` as experience, lines naming a
  degree as education and lines naming an engineering role as work experience.
- **Score:** the share of required skills present (40 pts), years of experience (30), projects
  (15), education (10) and a constant for writing quality (3); the category follows the same
  thresholds as the Gemini scoring prompt.
- **Job description:** the first sentence as the title, seniority from keywords or
  ``N+ years``, and the vocabulary skills mentioned.
- **OCR:** a fixed text naming the page count and a digest of the images.
"""

import hashlib
import re
from collections.abc import Sequence
from typing import Any

from app.modules.ai.schemas import ExtractionResult, JDParseResult, JobRequirements, ScoringResult

# Skills the fake recognises, in the spelling it reports them in. Matching is case-insensitive
# and on word boundaries ("Go" does not match "Google").
SKILL_VOCABULARY = (
    "Python",
    "Java",
    "JavaScript",
    "TypeScript",
    "Go",
    "Rust",
    "C++",
    "C#",
    "Ruby",
    "PHP",
    "Kotlin",
    "Swift",
    "SQL",
    "PostgreSQL",
    "MySQL",
    "MongoDB",
    "Redis",
    "Kafka",
    "RabbitMQ",
    "Celery",
    "FastAPI",
    "Django",
    "Flask",
    "Spring",
    "Node.js",
    "React",
    "Next.js",
    "Vue",
    "Angular",
    "GraphQL",
    "REST",
    "Docker",
    "Kubernetes",
    "Terraform",
    "AWS",
    "GCP",
    "Azure",
    "Linux",
    "Git",
    "CI/CD",
    "Machine Learning",
    "Pandas",
    "PyTorch",
    "TensorFlow",
)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(r"\+?\d[\d ()-]{7,}\d")
_GITHUB_RE = re.compile(r"(?:https?://)?(?:www\.)?github\.com/[\w-]+", re.IGNORECASE)
_LINKEDIN_RE = re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[\w-]+", re.IGNORECASE)
_YEARS_RE = re.compile(r"(\d{1,2}(?:\.\d)?)\+?\s*(?:years?|yrs?)", re.IGNORECASE)
_NAME_RE = re.compile(r"^[A-Z][A-Za-z'.-]+(?: [A-Z][A-Za-z'.-]+){1,3}$")
_DEGREE_RE = re.compile(
    r"\b(B\.?S\.?c?|M\.?S\.?c?|B\.?Tech|M\.?Tech|B\.?E|Bachelor|Master|Ph\.?D|MBA)\b"
)
_ROLE_RE = re.compile(r"\b(Engineer|Developer|Architect|Scientist|Analyst|Manager|Lead)\b")


def _skill_pattern(skill: str) -> re.Pattern[str]:
    # \b does not work next to "+", "#" or "." at the end of a skill; use look-arounds instead.
    return re.compile(rf"(?<![\w+#]){re.escape(skill)}(?![\w+#])", re.IGNORECASE)


_SKILL_PATTERNS = [(skill, _skill_pattern(skill)) for skill in SKILL_VOCABULARY]


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def find_skills(text: str) -> list[str]:
    """The vocabulary skills mentioned in ``text``, in vocabulary order."""
    return [skill for skill, pattern in _SKILL_PATTERNS if pattern.search(text)]


def _first(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(0) if match else None


def _years(text: str) -> float | None:
    values = [float(m) for m in _YEARS_RE.findall(text)]
    return max(values) if values else None


def _category(score: float) -> str:
    if score >= 75:
        return "strong_match"
    if score >= 50:
        return "moderate_match"
    if score >= 25:
        return "weak_match"
    return "rejected"


def _has(skills: Sequence[str], skill: str) -> bool:
    return skill.casefold() in {s.casefold() for s in skills}


class FakeAIService:
    """:class:`~app.modules.ai.service.AIService` with deterministic, rule-based answers."""

    def extract_profile(self, text: str) -> ExtractionResult:
        lines = _lines(text)
        name = next((line for line in lines[:5] if _NAME_RE.match(line)), None)
        education: list[dict[str, Any]] = [
            {"degree": line, "institution": "", "year": None}
            for line in lines
            if _DEGREE_RE.search(line)
        ]
        work_experience: list[dict[str, Any]] = [
            {"company": "", "role": line, "duration": "", "description": None}
            for line in lines
            if _ROLE_RE.search(line) and line != name
        ]
        projects: list[dict[str, Any]] = [
            {"name": line, "description": line, "technologies": find_skills(line)}
            for line in lines
            if line.lower().startswith("project")
        ]
        return ExtractionResult(
            name=name,
            email=_first(_EMAIL_RE, text),
            phone=_first(_PHONE_RE, text),
            github_url=_first(_GITHUB_RE, text),
            linkedin_url=_first(_LINKEDIN_RE, text),
            skills=find_skills(text),
            education=education[:3],
            work_experience=work_experience[:5],
            certifications=[line for line in lines if "certified" in line.lower()][:5],
            projects=projects[:5],
            years_of_experience=_years(text),
        )

    def score(self, profile: ExtractionResult, requirements: JobRequirements) -> ScoringResult:
        required = requirements.required_skills
        matched = [s for s in required if _has(profile.skills, s)]
        missing = [s for s in required if not _has(profile.skills, s)]
        share = len(matched) / len(required) if required else 1.0
        years = profile.years_of_experience or 0.0
        breakdown = {
            "skills_match": round(40 * share),
            "experience_match": min(30, round(years * 5)),
            "project_relevance": min(15, 5 * len(profile.projects)),
            "education_bonus": 7 if profile.education else 0,
            "communication_quality": 3,
        }
        score = float(sum(breakdown.values()))
        category = _category(score)
        who = profile.name or "The candidate"
        strengths = [f"Knows {s}" for s in matched[:5]] or ["Clear resume"]
        weaknesses = [f"No {s} on the resume" for s in missing[:3]]
        if years < 2:
            weaknesses.append("Limited professional experience")
        recommendation = {
            "strong_match": "Proceed to technical interview",
            "moderate_match": "Phone screen first",
        }.get(category, "Not recommended")
        return ScoringResult(
            score=score,
            score_breakdown=breakdown,
            category=category,
            summary=(
                f"{who} matches {len(matched)} of {len(required)} required skills for the "
                f"{requirements.role} role with {years:g} years of experience."
            ),
            strengths=strengths,
            weaknesses=weaknesses,
            missing_skills=missing,
            recommendation=recommendation,
        )

    async def parse_job_description(self, text: str) -> JDParseResult:
        lines = _lines(text)
        lowered = text.lower()
        years = _years(text)
        if re.search(r"\b(staff|principal)\b", lowered):
            seniority = "staff"
        elif re.search(r"\b(senior|sr\.?|lead)\b", lowered) or (years or 0) >= 5:
            seniority = "senior"
        elif re.search(r"\b(junior|jr\.?|graduate|intern|entry[- ]level)\b", lowered):
            seniority = "junior"
        else:
            seniority = "mid"
        # First sentence of the first line, e.g. "Senior Python Engineer. 5+ years ..."
        title = re.split(r"(?<=[.!?])\s", lines[0])[0].rstrip(".") if lines else ""
        return JDParseResult(
            role_title=title[:120] or "Unknown Role",
            seniority=seniority,
            skills=find_skills(text),
        )

    def transcribe_page_images(self, images: Sequence[bytes]) -> str:
        digest = hashlib.sha256(b"".join(images)).hexdigest()[:12]
        return (
            f"Scanned resume ({len(images)} page(s), digest {digest}).\n"
            "Text recognition is simulated by the fake AI provider (AI_PROVIDER=fake)."
        )
