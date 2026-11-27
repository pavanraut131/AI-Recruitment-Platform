"""The prompt of ``POST /api/v2/campaigns/parse-jd`` (ticket B1): rendering, and the exact text
sent to Gemini (pinned here since the v1 characterization snapshot went with v1, E2-12)."""

import json

import pytest
from httpx import AsyncClient

from app.modules.ai.prompts import JD_PROMPT as PROMPT
from app.modules.ai.prompts import build_jd_prompt as build_prompt
from tests.fakes import FakeGemini

JD_WITH_BRACES = 'Senior Go dev. Config like {"replicas": 3} and templates {{ .Values }} or {name}.'


def test_build_prompt_keeps_braces_in_job_description() -> None:
    prompt = build_prompt(JD_WITH_BRACES)

    assert f"---\n{JD_WITH_BRACES}\n---" in prompt
    assert "{job_description}" not in prompt


def test_build_prompt_renders_schema_with_single_braces() -> None:
    prompt = build_prompt("Backend engineer")

    assert '{\n  "role_title": "extracted job title",' in prompt
    assert '"skills": ["list", "of", "technical", "skills", "required"]\n}' in prompt
    assert "{{" not in prompt
    assert "}}" not in prompt


def test_prompt_template_has_only_the_job_description_placeholder() -> None:
    # Any other unescaped {name} in PROMPT would make str.format raise KeyError again.
    expected = PROMPT.replace("{job_description}", "").replace("{{", "{").replace("}}", "}")
    assert build_prompt("") == expected


@pytest.mark.asyncio
async def test_parse_jd_with_braces_in_job_description(
    client: AsyncClient, fake_gemini: FakeGemini
) -> None:
    fake_gemini.respond_with(
        json.dumps({"role_title": "Senior Go Developer", "seniority": "senior", "skills": ["Go"]})
    )

    resp = await client.post("/api/v2/campaigns/parse-jd", json={"job_description": JD_WITH_BRACES})

    assert resp.status_code == 200
    assert resp.json() == {
        "role_title": "Senior Go Developer",
        "seniority": "senior",
        "skills": ["Go"],
    }
    assert len(fake_gemini.calls) == 1
    assert JD_WITH_BRACES in fake_gemini.calls[0].prompt


# The exact prompt for this job description, as the v1 characterization snapshot recorded it
# (test_parse_jd_prompt, E0-04/B1): a list of lines, so no line is whitespace-only.
JD = "Senior Python Engineer. 5+ years with FastAPI, PostgreSQL and AWS."
JD_PROMPT_LINES = [
    "",
    "You are a technical recruiter. Extract the key information from this job description.",
    "",
    "Return ONLY valid JSON with this exact schema:",
    "{",
    '  "role_title": "extracted job title",',
    '  "seniority": "junior|mid|senior|staff",',
    '  "skills": ["list", "of", "technical", "skills", "required"]',
    "}",
    "",
    "Rules:",
    "- skills: extract ONLY technical skills (languages, frameworks, tools, platforms)",
    '- seniority: infer from title/requirements ("5+ years" = senior, etc.)',
    "- role_title: extract the actual job title from the JD",
    "",
    "Job Description:",
    "---",
    "Senior Python Engineer. 5+ years with FastAPI, PostgreSQL and AWS.",
    "---",
    "",
    "Return ONLY valid JSON, no markdown, no explanation.",
]


@pytest.mark.asyncio
async def test_the_parse_jd_prompt_is_pinned(client: AsyncClient, fake_gemini: FakeGemini) -> None:
    fake_gemini.respond_with("{}")
    await client.post("/api/v2/campaigns/parse-jd", json={"job_description": JD})
    assert [c.prompt.splitlines() for c in fake_gemini.calls] == [JD_PROMPT_LINES]
