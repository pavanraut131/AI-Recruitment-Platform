"""Google Gemini implementation of :class:`~app.modules.ai.service.AIService`.

The only place in the app that imports ``google.*`` (import-linter enforces it). Moved from
``app.services.gemini``, ``hiring.service.parse_job_description`` and the OCR fallback in
``workers.extract_task`` (E0-10) with unchanged behaviour:

- Extraction and scoring use ``settings.GEMINI_MODEL`` with JSON output, and retry any error
  except a quota error with tenacity, up to 3 attempts with exponential backoff.
- A quota / rate-limit error (``ResourceExhausted``) is **not** retried here (E0-16, spec §5.5;
  before, it was retried for up to 3 rounds 65 s apart, so the resume's status took minutes to
  change). It becomes :class:`~app.modules.ai.service.AIQuotaExceeded` at once, carrying Google's
  retry delay (``retry_after``) when the error has one; the pipeline schedules the retry.
- Job description parsing uses ``gemini-2.5-flash`` (a fixed model name, not the setting), needs
  ``GEMINI_API_KEY`` (else :class:`~app.modules.ai.service.AIConfigurationError`), strips a
  Markdown code fence from the answer and falls back to defaults for missing keys.
- Scoring overrides the model's category with the one its score implies.

The model object is created per call (``genai.GenerativeModel`` only stores its arguments), so
replacing ``google.generativeai.GenerativeModel`` is enough to fake Gemini in tests.
"""

import json
import re
from collections.abc import Sequence
from typing import Any

import google.generativeai as genai
import structlog
from google.api_core.exceptions import ResourceExhausted
from tenacity import (
    retry,
    retry_if_exception_type,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.modules.ai import prompts
from app.modules.ai.schemas import ExtractionResult, JDParseResult, JobRequirements, ScoringResult
from app.modules.ai.service import AIConfigurationError, AIQuotaExceeded
from app.platform.config import settings

logger = structlog.get_logger()

# Job description parsing has always used this model, whatever GEMINI_MODEL says.
JD_PARSE_MODEL = "gemini-2.5-flash"

if settings.GEMINI_API_KEY:
    genai.configure(api_key=settings.GEMINI_API_KEY)


def _model(name: str | None = None) -> Any:
    return genai.GenerativeModel(name or settings.GEMINI_MODEL)


# Transient failures (network, invalid model output, ...) are retried here, up to 3 attempts with
# exponential backoff. A quota error (ResourceExhausted) is not: it surfaces at once as
# AIQuotaExceeded, so the pipeline can show it and schedule its own retry (spec §5.5).
_retry_transient_errors = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception) & retry_if_not_exception_type(ResourceExhausted),
    reraise=True,
)

_RETRY_DELAY_PATTERNS = (
    # gRPC: the RetryInfo detail as protobuf text, e.g. "retry_delay { seconds: 43 }"
    re.compile(r"retry_delay\s*\{\s*seconds:\s*(\d+)"),
    # REST / message text, e.g. "Please retry in 43.2s." or "retryDelay": "43s"
    re.compile(r"retry in (\d+(?:\.\d+)?)\s*s", re.IGNORECASE),
    re.compile(r"retryDelay\W+(\d+(?:\.\d+)?)s"),
)


def quota_retry_after(exc: ResourceExhausted) -> float | None:
    """The delay (seconds) Google asks for before retrying, if the error carries one.

    Read from the ``google.rpc.RetryInfo`` detail (a protobuf message over gRPC, a dict with
    ``retryDelay`` such as ``"43s"`` over REST), else from the message text. ``None`` when
    there is none.
    """
    for detail in exc.details or ():
        delay = getattr(detail, "retry_delay", None)
        if delay is not None and hasattr(delay, "seconds"):
            seconds = float(delay.seconds) + float(getattr(delay, "nanos", 0)) / 1e9
            if seconds > 0:
                return seconds
        if isinstance(detail, dict):
            raw = detail.get("retryDelay") or detail.get("retry_delay")
            if isinstance(raw, str) and raw.endswith("s"):
                try:
                    return float(raw[:-1])
                except ValueError:
                    pass
    text = str(exc)
    for pattern in _RETRY_DELAY_PATTERNS:
        match = pattern.search(text)
        if match:
            return float(match.group(1))
    return None


def _quota_exceeded(exc: ResourceExhausted) -> AIQuotaExceeded:
    return AIQuotaExceeded(str(exc), retry_after=quota_retry_after(exc))


def category_for_score(score: float) -> str:
    """The category a score implies (the scoring prompt's rules)."""
    if score >= 75:
        return "strong_match"
    if score >= 50:
        return "moderate_match"
    if score >= 25:
        return "weak_match"
    return "rejected"


@_retry_transient_errors
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


@_retry_transient_errors
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
            raise _quota_exceeded(e) from e

    def score(self, profile: ExtractionResult, requirements: JobRequirements) -> ScoringResult:
        try:
            return score_candidate(
                profile.model_dump(), requirements.role, requirements.required_skills
            )
        except ResourceExhausted as e:
            raise _quota_exceeded(e) from e

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
            raise _quota_exceeded(e) from e
        return "\n".join(all_text)
