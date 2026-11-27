"""Contract: signing in with Google or Microsoft (E2-08): ``GET /auth/providers``, ``POST
/auth/oidc/{provider}/start`` (its body, cookie and problems) and the callback's redirects (signed
in, and each ``error`` of the login page), against the fake providers of
``tests/identity/fake_oidc.py``. The random parts of the authorization URL (``state``,
``nonce``, ``code_challenge``) become ``<random>`` and the "new sign-in method" email is
recorded, on top of ``test_identity``'s masking (cookie values, ``Retry-After``)."""

from __future__ import annotations

import re
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from syrupy.assertion import SnapshotAssertion

from app.modules.identity import oidc
from app.platform.config import settings
from tests.contract_v2.test_identity import _data
from tests.identity.conftest import (  # noqa: F401 (fixtures)
    V2,
    Mailbox,
    Users,
    csrf,
    fake_oidc,
    mailbox,
)
from tests.identity.fake_oidc import FakeProviders, callback, sign_in, start

RANDOM = re.compile(r"((?:state|nonce|code_challenge)=)[A-Za-z0-9_-]+")


def _flow(resp: httpx.Response, *headers: str) -> dict[str, Any]:
    data = _data(resp, *headers)
    body = data["body"]
    if isinstance(body, dict) and "authorization_url" in body:
        body["authorization_url"] = RANDOM.sub(r"\1<random>", body["authorization_url"])
    return data


async def test_providers(
    auth_client: AsyncClient, snapshot: SnapshotAssertion, monkeypatch: pytest.MonkeyPatch
) -> None:
    responses = {"none_configured": _data(await auth_client.get(f"{V2}/auth/providers"))}
    for name, value in {
        "OIDC_GOOGLE_CLIENT_ID": "g",
        "OIDC_GOOGLE_CLIENT_SECRET": "g",
        "OIDC_MICROSOFT_CLIENT_ID": "m",
        "OIDC_MICROSOFT_CLIENT_SECRET": "m",
    }.items():
        monkeypatch.setattr(settings, name, value)
    responses["both_configured"] = _data(await auth_client.get(f"{V2}/auth/providers"))
    assert responses == snapshot


async def test_start(
    users: Users,
    auth_client: AsyncClient,
    fake_oidc: FakeProviders,  # noqa: F811
    mailbox: Mailbox,  # noqa: F811
    snapshot: SnapshotAssertion,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses: dict[str, Any] = {}
    responses["google"] = _flow(await start(auth_client, "google", next="/campaigns"))
    responses["microsoft"] = _flow(await start(auth_client, "microsoft"))
    responses["without_csrf"] = _flow(
        await auth_client.post(f"{V2}/auth/oidc/google/start", json={})
    )
    token = (await auth_client.get(f"{V2}/auth/csrf")).json()["csrf_token"]
    responses["unknown_provider"] = _flow(
        await auth_client.post(
            f"{V2}/auth/oidc/github/start", json={}, headers={"X-CSRF-Token": token}
        )
    )
    responses["invalid_body"] = _flow(
        await auth_client.post(
            f"{V2}/auth/oidc/google/start",
            json={"next": "/" * 3000, "invitation_token": ""},
            headers={"X-CSRF-Token": token},
        )
    )
    responses["invitation_gone"] = _flow(
        await start(auth_client, "google", invitation_token="t" * 43)
    )
    fake_oidc.microsoft.discovery_down = True
    oidc.clear_caches()  # the discovery document was cached by the first Microsoft start
    responses["provider_down"] = _flow(await start(auth_client, "microsoft"))
    monkeypatch.setattr(settings, "OIDC_MICROSOFT_CLIENT_ID", "")
    monkeypatch.setattr(settings, "OIDC_MICROSOFT_CLIENT_SECRET", "")
    responses["not_configured"] = _flow(await start(auth_client, "microsoft"))
    monkeypatch.setattr(settings, "OIDC_MAX_ATTEMPTS_PER_IP", 0)
    responses["rate_limited"] = _flow(await start(auth_client, "google"), "retry-after")
    assert responses == snapshot


async def test_callback(
    users: Users,
    auth_client: AsyncClient,
    fake_oidc: FakeProviders,  # noqa: F811
    mailbox: Mailbox,  # noqa: F811
    snapshot: SnapshotAssertion,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await users.user("ada@example.com")
    responses: dict[str, Any] = {}
    responses["signed_in"] = _flow(
        await sign_in(auth_client, fake_oidc.google, next="/campaigns"), "location"
    )
    message = mailbox.last("ada@example.com")
    responses["linked_email"] = {"subject": message.subject, "text": message.text}
    auth_client.cookies.clear()
    responses["no_account"] = _flow(
        await sign_in(
            auth_client, fake_oidc.google, email="nobody@example.com", sub="another-identity"
        ),
        "location",
    )
    responses["email_not_verified"] = _flow(
        await sign_in(auth_client, fake_oidc.microsoft, xms_edov=None, sub="other"), "location"
    )
    responses["invalid_id_token"] = _flow(
        await sign_in(auth_client, fake_oidc.google, aud="someone-else"), "location"
    )
    responses["no_flow_cookie"] = _flow(
        await callback(auth_client, "google", code="c", state="s"), "location"
    )
    started = await start(auth_client, "google")
    _, state = fake_oidc.google.authorize(started.json()["authorization_url"])
    responses["cancelled"] = _flow(
        await callback(auth_client, "google", state=state, error="access_denied"), "location"
    )
    monkeypatch.setattr(settings, "OIDC_GOOGLE_CLIENT_ID", "")
    monkeypatch.setattr(settings, "OIDC_GOOGLE_CLIENT_SECRET", "")
    responses["not_configured"] = _flow(
        await callback(auth_client, "google", code="c", state="s"), "location"
    )
    monkeypatch.setattr(settings, "OIDC_MAX_ATTEMPTS_PER_IP", 0)
    responses["rate_limited"] = _flow(
        await callback(auth_client, "microsoft", code="c", state="s"), "location"
    )
    assert responses == snapshot
