"""``GeminiAIService`` on top of the ``FakeGemini`` fixture (no network).

Pins what the Gemini adapter sends (model, prompt), how it reads the answer, and how a quota
error (``ResourceExhausted``) surfaces as ``AIQuotaExceeded`` at once, with Google's retry delay.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from google.api_core.exceptions import ResourceExhausted
from google.rpc.error_details_pb2 import RetryInfo

from app.modules.ai import gemini, prompts
from app.modules.ai.gemini import GeminiAIService
from app.modules.ai.schemas import ExtractionResult, JobRequirements
from app.modules.ai.service import AIConfigurationError, AIQuotaExceeded
from app.platform.config import settings
from tests.fakes import FakeGemini

PROFILE = {
    "name": "Jane Doe",
    "email": "jane@example.com",
    "phone": None,
    "github_url": None,
    "linkedin_url": None,
    "skills": ["Python", "FastAPI"],
    "education": [{"degree": "BSc", "institution": "Uni", "year": "2015"}],
    "work_experience": [],
    "certifications": [],
    "projects": [],
    "years_of_experience": 6.0,
}


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record tenacity's waits instead of sleeping (both functions)."""
    recorded: list[float] = []
    for fn in (gemini.extract_candidate_info, gemini.score_candidate):
        monkeypatch.setattr(fn.retry, "sleep", recorded.append)  # type: ignore[attr-defined]
    return recorded


def test_extract_profile_sends_the_resume_and_parses_the_json(fake_gemini: FakeGemini) -> None:
    fake_gemini.respond_with(json.dumps(PROFILE))

    result = GeminiAIService().extract_profile("Jane Doe\nPython developer")

    assert result == ExtractionResult(**PROFILE)
    [call] = fake_gemini.calls
    assert call.model == settings.GEMINI_MODEL
    assert call.prompt == prompts.build_extraction_prompt("Jane Doe\nPython developer")
    assert "---\nJane Doe\nPython developer\n---" in call.prompt


def test_extract_profile_truncates_long_resumes(fake_gemini: FakeGemini) -> None:
    fake_gemini.respond_with("{}")

    GeminiAIService().extract_profile("x" * 20000)

    prompt = fake_gemini.calls[0].prompt
    assert "x" * prompts.MAX_RESUME_CHARS + "\n---" in prompt
    assert "x" * (prompts.MAX_RESUME_CHARS + 1) not in prompt


@pytest.mark.parametrize(
    ("score", "category"),
    [
        (80, "strong_match"),
        (75, "strong_match"),
        (60, "moderate_match"),
        (30, "weak_match"),
        (5, "rejected"),
    ],
)
def test_score_overrides_the_category_from_the_score(
    fake_gemini: FakeGemini, score: float, category: str
) -> None:
    fake_gemini.respond_with(
        json.dumps(
            {"score": score, "category": "strong_match", "summary": "s", "recommendation": "r"}
        )
    )

    result = GeminiAIService().score(
        ExtractionResult(**PROFILE), JobRequirements(role="Backend", required_skills=["Go", "SQL"])
    )

    assert (result.score, result.category) == (score, category)
    prompt = fake_gemini.calls[0].prompt
    assert "Job Role: Backend\nRequired Skills: Go, SQL\n" in prompt
    assert json.dumps(PROFILE, indent=2) in prompt


def test_quota_error_is_raised_at_once_as_ai_quota_exceeded(
    fake_gemini: FakeGemini, sleeps: list[float]
) -> None:
    fake_gemini.respond_with(ResourceExhausted("429 quota"))

    with pytest.raises(AIQuotaExceeded, match="quota") as raised:
        GeminiAIService().extract_profile("resume")

    # No retry and no wait here (E0-16, spec §5.5): the pipeline shows quota_exceeded and
    # schedules the retry itself. (Before: 3 x 3 attempts, rounds 65 s apart.)
    assert len(fake_gemini.calls) == 1
    assert sleeps == []
    assert raised.value.retry_after is None


def test_score_quota_error_is_raised_as_ai_quota_exceeded(
    fake_gemini: FakeGemini, sleeps: list[float]
) -> None:
    fake_gemini.respond_with(ResourceExhausted("429 quota", details=[_retry_info(43)]))

    with pytest.raises(AIQuotaExceeded) as raised:
        GeminiAIService().score(ExtractionResult(), JobRequirements(role="Dev"))

    assert len(fake_gemini.calls) == 1
    assert raised.value.retry_after == 43.0


def _retry_info(seconds: int, nanos: int = 0) -> RetryInfo:
    info = RetryInfo()
    info.retry_delay.seconds = seconds
    info.retry_delay.nanos = nanos
    return info


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        # gRPC: decoded google.rpc.RetryInfo
        (ResourceExhausted("429", details=[_retry_info(43, 500_000_000)]), 43.5),
        # REST: the detail as JSON
        (
            ResourceExhausted(
                "429",
                details=[
                    {"@type": "type.googleapis.com/google.rpc.QuotaFailure"},
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "17s"},
                ],
            ),
            17.0,
        ),
        # Only in the message
        (ResourceExhausted("429 quota [retry_delay {\n  seconds: 9\n}\n]"), 9.0),
        (ResourceExhausted("429 You exceeded your quota. Please retry in 31.2s."), 31.2),
        (ResourceExhausted("429 quota"), None),
    ],
)
def test_quota_retry_after_reads_googles_retry_delay(
    error: ResourceExhausted, expected: float | None
) -> None:
    assert gemini.quota_retry_after(error) == expected


def test_other_errors_are_retried_three_times_and_reraised(
    fake_gemini: FakeGemini, sleeps: list[float]
) -> None:
    fake_gemini.respond_with("not json")

    with pytest.raises(json.JSONDecodeError):
        GeminiAIService().extract_profile("resume")

    assert len(fake_gemini.calls) == 3
    assert 65 not in sleeps


async def test_parse_job_description_uses_the_jd_model_and_strips_a_code_fence(
    fake_gemini: FakeGemini,
) -> None:
    fake_gemini.respond_with('```json\n{"role_title": "SRE", "skills": ["Go"]}\n```')

    result = await GeminiAIService().parse_job_description("We need an SRE")

    assert result.model_dump() == {"role_title": "SRE", "seniority": "mid", "skills": ["Go"]}
    [call] = fake_gemini.calls
    assert call.model == gemini.JD_PARSE_MODEL == "gemini-2.5-flash"
    assert call.prompt == prompts.build_jd_prompt("We need an SRE")


async def test_parse_job_description_without_api_key_does_not_call_gemini(
    fake_gemini: FakeGemini, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")

    with pytest.raises(AIConfigurationError, match="^Gemini API key not configured$"):
        await GeminiAIService().parse_job_description("JD")

    assert fake_gemini.calls == []


def test_transcribe_page_images_calls_gemini_once_per_page(fake_gemini: FakeGemini) -> None:
    fake_gemini.respond_with("page text")

    text = GeminiAIService().transcribe_page_images([b"png-1", b"png-2"])

    assert text == "page text\npage text"
    prompts_sent: list[Any] = [c.prompt for c in fake_gemini.calls]
    assert prompts_sent == [
        [prompts.OCR_PROMPT, {"mime_type": "image/png", "data": b"png-1"}],
        [prompts.OCR_PROMPT, {"mime_type": "image/png", "data": b"png-2"}],
    ]


def test_transcribe_quota_error_is_raised_as_ai_quota_exceeded(fake_gemini: FakeGemini) -> None:
    fake_gemini.respond_with(ResourceExhausted("429 quota"))

    with pytest.raises(AIQuotaExceeded):
        GeminiAIService().transcribe_page_images([b"png"])


def test_sdk_is_configured_on_first_use_and_when_the_key_changes(
    fake_gemini: FakeGemini, monkeypatch: pytest.MonkeyPatch
) -> None:
    import google.generativeai as genai

    keys: list[str] = []
    monkeypatch.setattr(genai, "configure", lambda *, api_key: keys.append(api_key))
    monkeypatch.setattr(gemini, "_configured_key", None)  # as right after import
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "key-1")

    gemini._model()
    gemini._model()
    assert keys == ["key-1"]  # once, not per call

    monkeypatch.setattr(settings, "GEMINI_API_KEY", "key-2")
    gemini._model()
    assert keys == ["key-1", "key-2"]
