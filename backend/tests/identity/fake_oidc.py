"""A fake OpenID Connect provider for the tests (EPIC-02 spec §11.3; E2-08): no real Google or
Microsoft account, no network.

:class:`FakeProvider` plays Google or Microsoft (Entra ID, ``organizations``) at their **real**
URLs, served in-process through ``httpx.MockTransport`` (``app.modules.identity.oidc.TRANSPORT``):

- the discovery document (``/.well-known/openid-configuration``) and the JWKS of an RSA key
  pair made for the test;
- :meth:`FakeProvider.authorize` plays the user signing in at the provider: it reads the
  authorization URL the API built (and checks it: ``response_type=code``, the client id, the
  redirect URI, the scopes, ``state``, ``nonce``, an S256 ``code_challenge``), remembers what it
  was asked, and returns a one-time ``code`` with the ``state`` to send back to the callback;
- the token endpoint checks what a real provider checks (the code, used once; the client id
  and secret; the redirect URI; the PKCE ``code_verifier`` against the challenge) and answers
  with an ID token signed by the test's key, with the claims of a real one (``nonce`` from the
  authorization request) and whatever the test overrides: a wrong ``aud``, an expired ``exp``,
  another tenant's ``iss``, ``email_verified: false``, no ``xms_edov``... ``sign_with`` signs it
  with another key (a forged token) and ``header`` changes its JOSE header (``alg``, ``kid``).

The ``fake_oidc`` fixture (``tests/identity/conftest.py``) configures both providers in the
settings and installs the transport; :func:`sign_in` runs the browser's side of the flow.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from httpx import AsyncClient
from joserfc import jwt
from joserfc.jwk import RSAKey

from app.modules.identity import oidc
from app.modules.identity.schemas import OidcProvider

GOOGLE_CLIENT_ID = "hireflow-test.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "google-test-secret"
MICROSOFT_CLIENT_ID = "00000000-1111-2222-3333-444444444444"
MICROSOFT_CLIENT_SECRET = "microsoft-test-secret"
TENANT_ID = "72f988bf-86f1-41af-91ab-2d7cd011db47"
OTHER_TENANT_ID = "0e3a4b5c-6d7e-4f80-9a1b-2c3d4e5f6a7b"

CALLBACK = "http://localhost:8000/api/v2/auth/oidc/{}/callback"


@functools.cache
def _rsa_key(kid: str) -> RSAKey:
    """A 2048-bit key pair with this key id (made once per test session: they are slow)."""
    return RSAKey.generate_key(2048, parameters={"kid": kid, "use": "sig", "alg": "RS256"})


@functools.cache
def other_key(kid: str = "fake-key-1") -> RSAKey:
    """A key pair that is not the provider's, by default with the provider's key id: what a
    forger signs with."""
    return RSAKey.generate_key(2048, parameters={"kid": kid, "use": "sig", "alg": "RS256"})


def s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


@dataclass
class Grant:
    """What the provider remembers about an authorization code."""

    redirect_uri: str
    code_challenge: str
    nonce: str
    claims: dict[str, Any]
    sign_with: RSAKey | None
    header: dict[str, Any]
    used: bool = False


@dataclass
class FakeProvider:
    provider: OidcProvider
    client_id: str
    client_secret: str
    key: RSAKey = field(default_factory=lambda: _rsa_key("fake-key-1"))
    # Every request the API sent to this provider, in order.
    requests: list[httpx.Request] = field(default_factory=list)
    grants: dict[str, Grant] = field(default_factory=dict)
    # A test sets these to make the provider misbehave.
    token_error: str | None = None
    discovery_down: bool = False
    # Microsoft: the tenant in the endpoints' paths (OIDC_MICROSOFT_TENANT).
    tenant: str = "organizations"

    # ── The provider's URLs ──
    @property
    def discovery_url(self) -> str:
        if self.provider is OidcProvider.google:
            return oidc.GOOGLE_DISCOVERY_URL
        return f"{oidc.MICROSOFT_AUTHORITY}/{self.tenant}/v2.0/.well-known/openid-configuration"

    @property
    def authorization_endpoint(self) -> str:
        if self.provider is OidcProvider.google:
            return "https://accounts.google.com/o/oauth2/v2/auth"
        return f"{oidc.MICROSOFT_AUTHORITY}/{self.tenant}/oauth2/v2.0/authorize"

    @property
    def token_endpoint(self) -> str:
        if self.provider is OidcProvider.google:
            return "https://oauth2.googleapis.com/token"
        return f"{oidc.MICROSOFT_AUTHORITY}/{self.tenant}/oauth2/v2.0/token"

    @property
    def jwks_uri(self) -> str:
        if self.provider is OidcProvider.google:
            return "https://www.googleapis.com/oauth2/v3/certs"
        return f"{oidc.MICROSOFT_AUTHORITY}/{self.tenant}/discovery/v2.0/keys"

    def discovery(self) -> dict[str, Any]:
        issuer = (
            oidc.GOOGLE_ISSUER
            if self.provider is OidcProvider.google
            else f"{oidc.MICROSOFT_AUTHORITY}/{{tenantid}}/v2.0"
        )
        return {
            "issuer": issuer,
            "authorization_endpoint": self.authorization_endpoint,
            "token_endpoint": self.token_endpoint,
            "jwks_uri": self.jwks_uri,
            "response_types_supported": ["code"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "code_challenge_methods_supported": ["S256"],
        }

    def jwks(self) -> dict[str, Any]:
        public = self.key.as_dict(private=False)
        if self.provider is OidcProvider.microsoft:  # Entra ID lists more fields per key
            public["x5t"] = public["kid"]
            public["issuer"] = f"{oidc.MICROSOFT_AUTHORITY}/{{tenantid}}/v2.0"
        return {"keys": [public]}

    def owns(self, url: httpx.URL) -> bool:
        return str(url).split("?", 1)[0] in {
            self.discovery_url,
            self.token_endpoint,
            self.jwks_uri,
        }

    # ── The ID token ──
    def claims(self, nonce: str, /, **overrides: Any) -> dict[str, Any]:
        now = int(time.time())
        if self.provider is OidcProvider.google:
            claims: dict[str, Any] = {
                "iss": oidc.GOOGLE_ISSUER,
                "azp": self.client_id,
                "aud": self.client_id,
                "sub": "108123456789012345678",
                "email": "ada@example.com",
                "email_verified": True,
                "name": "Ada Lovelace",
                "iat": now,
                "exp": now + 3600,
                "nonce": nonce,
            }
        else:
            claims = {
                "iss": f"{oidc.MICROSOFT_AUTHORITY}/{TENANT_ID}/v2.0",
                "aud": self.client_id,
                "sub": "AAAAAAAAAAAAAAAAAAAAAIkzqFVrSaSaFHy782bbtaQ",
                "tid": TENANT_ID,
                "oid": "00000000-0000-0000-66f3-3332eca7ea81",
                "email": "ada@example.com",
                "xms_edov": True,
                "preferred_username": "ada@example.com",
                "name": "Ada Lovelace",
                "iat": now,
                "nbf": now,
                "exp": now + 3600,
                "nonce": nonce,
            }
        claims.update(overrides)
        return {name: value for name, value in claims.items() if value is not None}

    def id_token(
        self,
        claims: dict[str, Any],
        *,
        sign_with: RSAKey | None = None,
        header: dict[str, Any] | None = None,
    ) -> str:
        key = sign_with or self.key
        return jwt.encode(
            {"alg": "RS256", "kid": key.kid, **(header or {})}, claims, key, algorithms=["RS256"]
        )

    # ── The user at the provider ──
    def authorize(
        self,
        authorization_url: str,
        *,
        sign_with: RSAKey | None = None,
        header: dict[str, Any] | None = None,
        **claims: Any,
    ) -> tuple[str, str]:
        """The user signs in at the provider: ``(code, state)`` for the callback. ``claims``
        override the ID token's (``None`` removes one)."""
        parts = urlsplit(authorization_url)
        endpoint = f"{parts.scheme}://{parts.netloc}{parts.path}"
        assert endpoint == self.authorization_endpoint, endpoint
        query = {name: values[0] for name, values in parse_qs(parts.query).items()}
        assert query["response_type"] == "code"
        assert query["client_id"] == self.client_id
        assert query["redirect_uri"] == CALLBACK.format(self.provider.value)
        assert query["scope"] == "openid email profile"
        assert query["code_challenge_method"] == "S256"
        assert query["prompt"] == "select_account"
        assert len(query["state"]) >= 43 and len(query["nonce"]) >= 43
        code = secrets.token_urlsafe(24)
        self.grants[code] = Grant(
            redirect_uri=query["redirect_uri"],
            code_challenge=query["code_challenge"],
            nonce=query["nonce"],
            claims=self.claims(query["nonce"], **claims),
            sign_with=sign_with,
            header=header or {},
        )
        return code, query["state"]

    # ── HTTP ──
    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url).split("?", 1)[0]
        if url == self.discovery_url:
            if self.discovery_down:
                return httpx.Response(503, text="unavailable")
            return httpx.Response(200, json=self.discovery())
        if url == self.jwks_uri:
            return httpx.Response(200, json=self.jwks())
        if url == self.token_endpoint and request.method == "POST":
            return self._token(request)
        return httpx.Response(404, json={"error": "not_found"})

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {name: values[0] for name, values in parse_qs(request.content.decode()).items()}

        def refuse(error: str) -> httpx.Response:
            return httpx.Response(400, json={"error": error})

        if self.token_error is not None:
            return refuse(self.token_error)
        grant = self.grants.get(form.get("code", ""))
        if (
            form.get("grant_type") != "authorization_code"
            or grant is None
            or grant.used
            or form.get("redirect_uri") != grant.redirect_uri
        ):
            return refuse("invalid_grant")
        if form.get("client_id") != self.client_id or form.get("client_secret") != (
            self.client_secret
        ):
            return refuse("invalid_client")
        if s256(form.get("code_verifier", "")) != grant.code_challenge:
            return refuse("invalid_grant")  # PKCE
        grant.used = True
        return httpx.Response(
            200,
            json={
                "access_token": secrets.token_urlsafe(24),
                "token_type": "Bearer",
                "expires_in": 3599,
                "scope": "openid email profile",
                "id_token": self.id_token(
                    grant.claims, sign_with=grant.sign_with, header=grant.header
                ),
            },
        )

    def calls_to(self, url: str) -> int:
        return sum(1 for r in self.requests if str(r.url).split("?", 1)[0] == url)

    def rotate_key(self) -> None:
        """The provider starts signing with a new key (and publishes only that one)."""
        self.key = _rsa_key("fake-key-2")


@dataclass
class FakeProviders:
    google: FakeProvider
    microsoft: FakeProvider

    def __getitem__(self, provider: OidcProvider | str) -> FakeProvider:
        return self.google if OidcProvider(provider) is OidcProvider.google else self.microsoft

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            for fake in (self.google, self.microsoft):
                if fake.owns(request.url):
                    return fake.handle(request)
            raise AssertionError(f"unexpected request to {request.url}")

        return httpx.MockTransport(handle)


def new_providers() -> FakeProviders:
    return FakeProviders(
        google=FakeProvider(OidcProvider.google, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET),
        microsoft=FakeProvider(
            OidcProvider.microsoft, MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET
        ),
    )


# ── The browser's side ────────────────────────────────────────
V2 = "/api/v2"
SESSION_COOKIE = "hf_session"
CSRF_COOKIE = "hf_csrf"
OIDC_COOKIE = "hf_oidc"


async def csrf_headers(client: AsyncClient) -> dict[str, str]:
    """The ``X-CSRF-Token`` for the client's session, or an anonymous one (``GET /auth/csrf``)."""
    if client.cookies.get(SESSION_COOKIE) and client.cookies.get(CSRF_COOKIE):
        return {"X-CSRF-Token": client.cookies.get(CSRF_COOKIE) or ""}
    token = (await client.get(f"{V2}/auth/csrf")).json()["csrf_token"]
    return {"X-CSRF-Token": token}


async def start(
    client: AsyncClient,
    provider: OidcProvider | str,
    *,
    next: str | None = None,
    invitation_token: str | None = None,
) -> httpx.Response:
    body: dict[str, Any] = {}
    if next is not None:
        body["next"] = next
    if invitation_token is not None:
        body["invitation_token"] = invitation_token
    return await client.post(
        f"{V2}/auth/oidc/{OidcProvider(provider).value}/start",
        json=body,
        headers=await csrf_headers(client),
    )


async def callback(
    client: AsyncClient, provider: OidcProvider | str, **params: str
) -> httpx.Response:
    return await client.get(
        f"{V2}/auth/oidc/{OidcProvider(provider).value}/callback", params=params
    )


async def sign_in(
    client: AsyncClient,
    fake: FakeProvider,
    *,
    next: str | None = None,
    invitation_token: str | None = None,
    sign_with: RSAKey | None = None,
    header: dict[str, Any] | None = None,
    **claims: Any,
) -> httpx.Response:
    """Start, sign in at the fake provider with these ID-token ``claims``, and come back: the
    callback's response (a redirect)."""
    started = await start(client, fake.provider, next=next, invitation_token=invitation_token)
    assert started.status_code == 200, started.text
    code, state = fake.authorize(
        started.json()["authorization_url"], sign_with=sign_with, header=header, **claims
    )
    return await callback(client, fake.provider, code=code, state=state)


def location(resp: httpx.Response) -> str:
    assert resp.status_code == 302, (resp.status_code, resp.text)
    return resp.headers["location"]


def login_error(resp: httpx.Response, provider: str = "google") -> str:
    """The ``error`` of a redirect to the login page (asserting it is one)."""
    target = location(resp)
    prefix = "http://localhost:3000/login?error="
    assert target.startswith(prefix), target
    error, _, rest = target[len(prefix) :].partition("&")
    assert rest == f"provider={provider}", target
    return error
