"""Prompt rendering for ``POST /campaigns/parse-jd`` (ticket B1)."""

import json

import pytest
from httpx import AsyncClient

from app.api.v1.parse_jd import PROMPT, build_prompt
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

    resp = await client.post("/api/v1/campaigns/parse-jd", json={"job_description": JD_WITH_BRACES})

    assert resp.status_code == 200
    assert resp.json() == {
        "role_title": "Senior Go Developer",
        "seniority": "senior",
        "skills": ["Go"],
    }
    assert len(fake_gemini.calls) == 1
    assert JD_WITH_BRACES in fake_gemini.calls[0].prompt
