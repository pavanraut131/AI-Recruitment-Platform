"""Google Gemini implementation of :class:`~app.modules.ai.service.AIService`.

The only place in the app that imports ``google.*`` (import-linter enforces it). Moved from
``app.services.gemini``, ``hiring.service.parse_job_description`` and the OCR fallback in
``workers.extract_task`` (E0-10) with unchanged behaviour:

- Extraction and scoring use ``settings.GEMINI_MODEL`` with JSON output, and retry with tenacity:
  any error up to 3 attempts with exponential backoff, and on top of that ``ResourceExhausted``
  (quota / rate limit) up to 3 rounds 65 s apart. A ``ResourceExhausted`` that survives the
  retries becomes :class:`~app.modules.ai.service.AIQuotaExceeded`.
- Job description parsing uses ``gemini-2.5-flash`` (a fixed model name, not the setting), needs
  ``GEMINI_API_KEY`` (else :class:`~app.modules.ai.service.AIConfigurationError`), strips a
  Markdown code fence from the answer and falls back to defaults for missing keys.
- Scoring overrides the model's category with the one its score implies.

The model object is created per call (``genai.GenerativeModel`` only stores its arguments), so
replacing ``google.generativeai.GenerativeModel`` is enough to fake Gemini in tests.
"""

import json
from collections.abc import Sequence
from typing import Any

import google.generativeai as genai
import structlog
from google.api_core.exceptions import ResourceExhausted
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_fixed,
)

from app.modules.ai import prompts
from app.modules.ai.schemas import ExtractionResult, JDParseResult, JobRequirements, ScoringResult
from app.modules.ai.service import AIConfigurationError, AIQuotaExceeded
from app.platform.config import settings

logger = structlog.get_logger()

# Job description parsing has always used this model, whatever GEMINI_MODEL says.
JD_PARSE_MODEL = "gemini-2.5-flash"

# The key the SDK was last configured with (see _configure_client).
_configured_key: str | None = None


def _configure_client() -> None:
    """Configure the SDK with ``settings.GEMINI_API_KEY`` on first use, and again if it changes.

    Done lazily rather than at import so that importing this module has no side effects (with
    ``AI_PROVIDER=fake`` there may be no key at all). Settings validation guarantees a key when
    ``AI_PROVIDER=gemini``.
    """
    global _configured_key
    key = settings.GEMINI_API_KEY
    if key and key != _configured_key:
        genai.configure(api_key=key)
        _configured_key = key


def _model(name: str | None = None) -> Any:
    _configure_client()
    return genai.GenerativeModel(name or settings.GEMINI_MODEL)


def category_for_score(score: float) -> str:
    """The category a score implies (the scoring prompt's rules)."""
    if score >= 75:
        return "strong_match"
    if score >= 50:
        return "moderate_match"
    if score >= 25:
        return "weak_match"
    return "rejected"


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(65),  # Gemini free-tier rate limit resets in ~60s
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
    response = _model().generate_content(
        prompts.build_extraction_prompt(resume_text),
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.1,
        ),
    )

    raw = str(response.text).strip()
    logger.debug("gemini_extraction_response", length=len(raw))

    data = json.loads(raw)
    return ExtractionResult(**data)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(65),  # Gemini free-tier rate limit resets in ~60s
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
    candidate_profile: dict[str, Any],
    role: str,
    required_skills: list[str],
) -> ScoringResult:
    """Call Gemini to score and classify a candidate against a job role."""
    response = _model().generate_content(
        prompts.build_scoring_prompt(candidate_profile, role, required_skills),
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )

    raw = str(response.text).strip()
    logger.debug("gemini_scoring_response", length=len(raw))

    data = json.loads(raw)

    # Enforce category based on score if Gemini drifts
    data["category"] = category_for_score(float(data.get("score", 0)))

    return ScoringResult(**data)


class GeminiAIService:
    """:class:`~app.modules.ai.service.AIService` backed by Google Gemini."""

    def extract_profile(self, text: str) -> ExtractionResult:
        try:
            return extract_candidate_info(text)
        except ResourceExhausted as e:
            raise AIQuotaExceeded(str(e)) from e

    def score(self, profile: ExtractionResult, requirements: JobRequirements) -> ScoringResult:
        try:
            return score_candidate(
                profile.model_dump(), requirements.role, requirements.required_skills
            )
        except ResourceExhausted as e:
            raise AIQuotaExceeded(str(e)) from e

    async def parse_job_description(self, text: str) -> JDParseResult:
        if not settings.GEMINI_API_KEY:
            raise AIConfigurationError("Gemini API key not configured")

        response = await _model(JD_PARSE_MODEL).generate_content_async(
            prompts.build_jd_prompt(text)
        )
        answer = str(response.text).strip()
        if answer.startswith("```json"):
            answer = answer[7:]
        if answer.endswith("```"):
            answer = answer[:-3]

        data = json.loads(answer.strip())
        return JDParseResult(
            role_title=data.get("role_title", "Unknown Role"),
            seniority=data.get("seniority", "mid"),
            skills=data.get("skills", []),
        )

    def transcribe_page_images(self, images: Sequence[bytes]) -> str:
        # No retries here (there never were); the extract task retries the whole step.
        genai.configure(api_key=settings.GEMINI_API_KEY)
        model = _model()
        all_text = []
        try:
            for img_bytes in images:
                response = model.generate_content(
                    [prompts.OCR_PROMPT, {"mime_type": "image/png", "data": img_bytes}]
                )
                all_text.append(str(response.text))
        except ResourceExhausted as e:
            raise AIQuotaExceeded(str(e)) from e
        return "\n".join(all_text)
