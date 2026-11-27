"""Sign in with Google and Microsoft: the OpenID Connect protocol (EPIC-02 spec §4.3; ADR 0010).

Private to identity: ``service`` runs the flow (account resolution, sessions, audit) and calls
this module for the protocol. Authorization code flow with PKCE, as a **confidential client**
(PKCE *and* the client secret):

1. :func:`new_flow` makes a random ``state`` and ``nonce`` (256 bits each) and a PKCE
   ``code_verifier`` (512 bits; the challenge is its S256 hash). :func:`seal` puts them, with
   the validated ``next`` path and the hash of an invitation token, into the value of the
   ``__Host-hf_oidc`` cookie: a JWT **encrypted** with ``dir`` + ``A256GCM`` (authenticated
   encryption: the browser can neither read nor change it) under a key derived from
   ``SECRET_KEY``, valid for :data:`STATE_TTL`. :func:`authorization_url` builds the provider
   URL with Authlib.
2. On the callback, :func:`unseal` returns the flow (``None`` when the cookie is missing,
   altered, from another key or expired); the caller compares ``state``.
   :func:`exchange_code` trades the code for tokens (Authlib's ``AsyncOAuth2Client``: the
   ``code_verifier`` and the client secret, ``client_secret_post``).
3. :func:`validate_id_token` checks the ID token with joserfc: the **RS256** signature against
   the provider's JWKS (``jwks_uri`` of the discovery document; both cached for an hour, and
   fetched again once when a token names an unknown key id: the provider rotated its keys),
   then ``iss``, ``aud`` (and ``azp`` when there are several audiences), ``exp``, ``iat``
   (60 s of clock skew), ``sub`` and ``nonce``. Per provider:

   - **Google:** ``iss`` is ``https://accounts.google.com`` (Google also documents
     ``accounts.google.com``, stored as the former), and the email counts only with
     ``email_verified`` true.
   - **Microsoft** (Entra ID, ``organizations`` by default): the issuer depends on the tenant,
     so ``iss`` must be exactly ``https://login.microsoftonline.com/{tid}/v2.0`` for the token's
     own ``tid``; personal accounts (the consumer tenant) are refused, and so is another tenant
     when ``OIDC_MICROSOFT_TENANT`` names one. The ``email`` claim is not proof of ownership
     (the "nOAuth" class of takeovers): it counts only with the ``xms_edov`` optional claim
     (email domain owner verified) true.

The result is an :class:`Identity`: who the provider says it is (``provider``, ``issuer``,
``subject``: the key of ``oidc_accounts``) and an email with whether the provider vouches for
it. Nothing here touches the database, and the provider's tokens are never stored or logged.

Failures raise :class:`OidcError` with a short ``reason`` for the logs and the audit event
(never shown to the user); :class:`ProviderUnavailable` when the provider cannot be reached or
answers nonsense. Network calls go through :data:`TRANSPORT` (``None``: the network), which the
tests replace with a fake provider (``tests/identity/fake_oidc.py``).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final

import authlib.deprecate  # noqa: F401 (first: it installs an "always" filter for its warnings)
import httpx
import structlog
from authlib.integrations.base_client import OAuthError
from joserfc import jwt
from joserfc.errors import (
    BadSignatureError,
    ClaimError,
    ExpiredTokenError,
    InvalidKeyIdError,
    JoseError,
    MissingClaimError,
)
from joserfc.jwe import JWERegistry
from joserfc.jwk import KeySet, OctKey

from app.modules.identity.schemas import OidcProvider
from app.platform.config import settings

with warnings.catch_warnings():
    # Authlib 1.9 prefers httpx2 (pydantic's fork) and warns once when it falls back to httpx,
    # which is this project's HTTP client (backlog: move both when the project moves).
    warnings.filterwarnings("ignore", message="The httpx module is deprecated")
    from authlib.integrations.httpx_client import AsyncOAuth2Client

logger = structlog.get_logger()

STATE_TTL: Final = timedelta(minutes=10)
"""How long a started sign-in may take (the cookie's lifetime and the sealed ``exp``)."""
SCOPE: Final = "openid email profile"
ALGORITHMS: Final = ("RS256",)
"""ID-token signature algorithms accepted (Google and Entra ID both sign with RS256)."""
LEEWAY_SECONDS: Final = 60
CACHE_SECONDS: Final = 3600.0
HTTP_TIMEOUT_SECONDS: Final = 10.0
NEXT_MAX: Final = 2048

GOOGLE_ISSUER: Final = "https://accounts.google.com"
GOOGLE_ISSUERS: Final = frozenset({GOOGLE_ISSUER, "accounts.google.com"})
GOOGLE_DISCOVERY_URL: Final = f"{GOOGLE_ISSUER}/.well-known/openid-configuration"
MICROSOFT_AUTHORITY: Final = "https://login.microsoftonline.com"
MICROSOFT_CONSUMER_TENANT: Final = "9188040d-6c67-4c5b-b112-36a304b66dad"
"""The tenant of personal Microsoft accounts (spec §15 Q5: work and school accounts only)."""

_GUID: Final = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_STATE_KEY_LABEL: Final = b"hireflow/oidc-state/v1"
_STATE_HEADER: Final = {"alg": "dir", "enc": "A256GCM"}
_STATE_REGISTRY: Final = JWERegistry(algorithms=["dir", "A256GCM"])

TRANSPORT: httpx.AsyncBaseTransport | None = None
"""The transport of every request to a provider; ``None`` is the network. Tests set a fake."""


# ── Errors ────────────────────────────────────────────────────
class OidcError(Exception):
    """The sign-in failed at the protocol level. ``reason`` is for logs and audit only."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ProviderUnavailable(OidcError):
    """The provider could not be reached, or its discovery document or keys are unusable."""

    def __init__(self, reason: str = "provider_unavailable") -> None:
        super().__init__(reason)


# ── Providers ─────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """A configured provider (both its client id and secret are set)."""

    provider: OidcProvider
    name: str
    client_id: str
    client_secret: str
    discovery_url: str
    # Microsoft only: "organizations", a tenant id or a tenant domain (lower-cased).
    tenant: str | None = None


def provider_config(provider: OidcProvider) -> ProviderConfig | None:
    """The provider's configuration, or ``None`` when it is not configured (hidden: 404)."""
    if provider is OidcProvider.google:
        client_id = settings.OIDC_GOOGLE_CLIENT_ID.strip()
        secret = settings.OIDC_GOOGLE_CLIENT_SECRET.strip()
        if not (client_id and secret):
            return None
        return ProviderConfig(provider, "Google", client_id, secret, GOOGLE_DISCOVERY_URL)
    client_id = settings.OIDC_MICROSOFT_CLIENT_ID.strip()
    secret = settings.OIDC_MICROSOFT_CLIENT_SECRET.strip()
    if not (client_id and secret):
        return None
    tenant = settings.OIDC_MICROSOFT_TENANT.strip().lower()
    discovery = f"{MICROSOFT_AUTHORITY}/{tenant}/v2.0/.well-known/openid-configuration"
    return ProviderConfig(provider, "Microsoft", client_id, secret, discovery, tenant)


def configured_providers() -> list[ProviderConfig]:
    """The configured providers, Google first."""
    return [config for p in OidcProvider if (config := provider_config(p)) is not None]


def redirect_uri(provider: OidcProvider) -> str:
    """Where the provider sends the browser back: register exactly this URL with it."""
    base = settings.API_PUBLIC_URL.strip().rstrip("/")
    return f"{base}{settings.API_V2_PREFIX}/auth/oidc/{provider.value}/callback"


# ── The flow's state (the encrypted cookie) ───────────────────
@dataclass(frozen=True, slots=True)
class Flow:
    """A sign-in in progress, as sealed in the ``__Host-hf_oidc`` cookie."""

    provider: OidcProvider
    state: str
    nonce: str
    code_verifier: str
    next: str
    # SHA-256 of the invitation token when the flow accepts an invitation.
    invitation_hash: bytes | None = None


def safe_next(value: str | None) -> str:
    """``value`` if it is a path of the frontend, else ``"/"`` (no open redirect, spec §4.3):
    it starts with exactly one ``/``, has at most :data:`NEXT_MAX` characters, all printable
    ASCII, and no backslash (which browsers read as ``/``, making ``/\\evil.com`` another
    host)."""
    if (
        not value
        or len(value) > NEXT_MAX
        or not value.startswith("/")
        or value.startswith("//")
        or any(char == "\\" or not "!" <= char <= "~" for char in value)
    ):
        return "/"
    return value


def new_flow(
    provider: OidcProvider, next_path: str | None, invitation_hash: bytes | None = None
) -> Flow:
    return Flow(
        provider=provider,
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
        # 86 characters of [A-Za-z0-9_-]: within RFC 7636's 43 to 128 unreserved characters.
        code_verifier=secrets.token_urlsafe(64),
        next=safe_next(next_path),
        invitation_hash=invitation_hash,
    )


def _state_key() -> OctKey:
    # Derived (not SECRET_KEY itself), like the CSRF key: no two uses of the secret share a key.
    raw = hmac.new(settings.SECRET_KEY.encode(), _STATE_KEY_LABEL, hashlib.sha256).digest()
    return OctKey.import_key(raw)


def seal(flow: Flow, *, now: int | None = None) -> str:
    """The cookie value for ``flow``: an encrypted JWT that expires after :data:`STATE_TTL`."""
    issued = int(time.time()) if now is None else now
    claims: dict[str, Any] = {
        "iat": issued,
        "exp": issued + int(STATE_TTL.total_seconds()),
        "provider": flow.provider.value,
        "state": flow.state,
        "nonce": flow.nonce,
        "code_verifier": flow.code_verifier,
        "next": flow.next,
    }
    if flow.invitation_hash is not None:
        claims["invitation_hash"] = flow.invitation_hash.hex()
    return jwt.encode(_STATE_HEADER, claims, _state_key(), registry=_STATE_REGISTRY)


def unseal(value: str | None, *, now: int | None = None) -> Flow | None:
    """The flow sealed in ``value``; ``None`` when there is none, or it was altered, sealed with
    another key or has expired."""
    if not value or len(value) > 4096:
        return None
    try:
        token = jwt.decode(value, _state_key(), registry=_STATE_REGISTRY)
        claims = token.claims
        jwt.JWTClaimsRegistry(now=now, exp={"essential": True}).validate(claims)
        invitation = claims.get("invitation_hash")
        return Flow(
            provider=OidcProvider(claims["provider"]),
            state=_text(claims["state"]),
            nonce=_text(claims["nonce"]),
            code_verifier=_text(claims["code_verifier"]),
            next=safe_next(_text(claims["next"])),
            invitation_hash=bytes.fromhex(_text(invitation)) if invitation is not None else None,
        )
    except (JoseError, KeyError, TypeError, ValueError):
        return None


def _text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("not a string")
    return value


# ── Talking to the provider ───────────────────────────────────
_metadata_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_keys_cache: dict[str, tuple[float, KeySet]] = {}


def clear_caches() -> None:
    """Forget the cached discovery documents and keys (tests)."""
    _metadata_cache.clear()
    _keys_cache.clear()


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, transport=TRANSPORT)


async def _get_json(url: str) -> dict[str, Any]:
    try:
        async with _http_client() as client:
            response = await client.get(url, headers={"Accept": "application/json"})
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("oidc_fetch_failed", url=url, error=type(exc).__name__)
        raise ProviderUnavailable() from exc
    if not isinstance(data, dict):
        raise ProviderUnavailable()
    return data


def _is_https(value: object) -> bool:
    return isinstance(value, str) and value.startswith("https://")


async def metadata(config: ProviderConfig) -> dict[str, Any]:
    """The provider's discovery document (cached for :data:`CACHE_SECONDS`)."""
    now = time.monotonic()
    cached = _metadata_cache.get(config.discovery_url)
    if cached is not None and cached[0] > now:
        return cached[1]
    data = await _get_json(config.discovery_url)
    endpoints = ("authorization_endpoint", "token_endpoint", "jwks_uri")
    if not all(_is_https(data.get(name)) for name in endpoints):
        logger.warning("oidc_discovery_unusable", url=config.discovery_url)
        raise ProviderUnavailable("discovery_unusable")
    _metadata_cache[config.discovery_url] = (now + CACHE_SECONDS, data)
    return data


async def _keys(jwks_uri: str, *, refresh: bool = False) -> KeySet:
    now = time.monotonic()
    cached = _keys_cache.get(jwks_uri)
    if cached is not None and cached[0] > now and not refresh:
        return cached[1]
    data = await _get_json(jwks_uri)
    try:
        keys = KeySet.import_key_set(data)  # type: ignore[arg-type]
    except (JoseError, KeyError, TypeError, ValueError) as exc:
        logger.warning("oidc_jwks_unusable", url=jwks_uri, error=type(exc).__name__)
        raise ProviderUnavailable("jwks_unusable") from exc
    _keys_cache[jwks_uri] = (now + CACHE_SECONDS, keys)
    return keys


def _oauth_client(config: ProviderConfig) -> Any:
    return AsyncOAuth2Client(
        client_id=config.client_id,
        client_secret=config.client_secret,
        scope=SCOPE,
        redirect_uri=redirect_uri(config.provider),
        code_challenge_method="S256",
        token_endpoint_auth_method="client_secret_post",
        timeout=HTTP_TIMEOUT_SECONDS,
        transport=TRANSPORT,
    )


async def authorization_url(config: ProviderConfig, flow: Flow) -> str:
    """Where to send the browser: the provider's authorization endpoint with ``response_type
    =code``, the client id, redirect URI, scopes ``openid email profile``, ``state``, ``nonce``,
    the S256 ``code_challenge``, and ``prompt=select_account`` (so someone with several accounts
    can pick the one HireFlow knows)."""
    endpoint = (await metadata(config))["authorization_endpoint"]
    client = _oauth_client(config)
    try:
        url, _ = client.create_authorization_url(
            endpoint,
            state=flow.state,
            code_verifier=flow.code_verifier,
            nonce=flow.nonce,
            prompt="select_account",
        )
    finally:
        await client.aclose()
    return str(url)


async def exchange_code(config: ProviderConfig, *, code: str, code_verifier: str) -> str:
    """Trade the authorization code for tokens; returns the ID token (the others are dropped)."""
    endpoint = (await metadata(config))["token_endpoint"]
    client = _oauth_client(config)
    try:
        token = await client.fetch_token(
            endpoint, grant_type="authorization_code", code=code, code_verifier=code_verifier
        )
    except OAuthError as exc:
        logger.warning("oidc_token_refused", provider=config.provider.value, error=exc.error)
        raise OidcError("token_exchange_refused") from exc
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "oidc_token_failed", provider=config.provider.value, error=type(exc).__name__
        )
        raise OidcError("token_exchange_failed") from exc
    finally:
        await client.aclose()
    id_token = token.get("id_token") if isinstance(token, Mapping) else None
    if not isinstance(id_token, str) or not id_token:
        raise OidcError("no_id_token")
    return id_token


# ── The ID token ──────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Identity:
    """Who a validated ID token says signed in."""

    provider: OidcProvider
    issuer: str
    subject: str
    # The provider's address, normalised (trimmed, lower-cased); None without one.
    email: str | None
    # The provider vouches for the address (Google email_verified, Microsoft xms_edov).
    email_verified: bool
    name: str | None


def _decode(id_token: str, keys: KeySet) -> jwt.Token:
    try:
        return jwt.decode(id_token, keys, algorithms=list(ALGORITHMS))
    except InvalidKeyIdError:
        raise
    except BadSignatureError as exc:
        raise OidcError("bad_signature") from exc
    except (JoseError, TypeError, ValueError) as exc:
        raise OidcError("malformed_id_token") from exc


def _issuer(config: ProviderConfig, claims: Mapping[str, Any]) -> tuple[str, str]:
    """``(the iss the token must carry, the issuer to store)`` for this provider."""
    iss = claims.get("iss")
    if config.provider is OidcProvider.google:
        if not isinstance(iss, str) or iss not in GOOGLE_ISSUERS:
            raise OidcError("wrong_issuer")
        return iss, GOOGLE_ISSUER
    tid = claims.get("tid")
    if not isinstance(tid, str) or not _GUID.fullmatch(tid):
        raise OidcError("no_tenant")
    if tid == MICROSOFT_CONSUMER_TENANT:
        raise OidcError("personal_account")
    if config.tenant is not None and _GUID.fullmatch(config.tenant) and tid != config.tenant:
        raise OidcError("wrong_tenant")
    expected = f"{MICROSOFT_AUTHORITY}/{tid}/v2.0"
    if iss != expected:
        raise OidcError("wrong_issuer")
    return expected, expected


def _vouched(value: object) -> bool:
    """A boolean claim that is true (some issuers send ``"true"`` or ``1``)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    return isinstance(value, str) and value.strip().lower() in {"true", "1"}


def _email(claims: Mapping[str, Any]) -> str | None:
    value = claims.get("email")
    if not isinstance(value, str):
        return None
    address = value.strip().lower()
    if not address or len(address) > 320 or address.count("@") != 1:
        return None
    return address


def _name(claims: Mapping[str, Any]) -> str | None:
    value = claims.get("name")
    if not isinstance(value, str):
        return None
    name = " ".join(value.split())[:200]
    return name or None


async def validate_id_token(
    config: ProviderConfig, id_token: str, *, nonce: str, now: int | None = None
) -> Identity:
    """Validate ``id_token`` (see the module docstring) and return who it names; raises
    :class:`OidcError` (with a ``reason``) for anything wrong."""
    jwks_uri = (await metadata(config))["jwks_uri"]
    try:
        token = _decode(id_token, await _keys(jwks_uri))
    except InvalidKeyIdError:
        try:  # a key id we have not seen: the provider may have rotated its keys
            token = _decode(id_token, await _keys(jwks_uri, refresh=True))
        except InvalidKeyIdError as exc:
            raise OidcError("unknown_key") from exc
    claims = token.claims

    iss, issuer = _issuer(config, claims)
    registry = jwt.JWTClaimsRegistry(
        now=now,
        leeway=LEEWAY_SECONDS,
        iss={"essential": True, "value": iss},
        aud={"essential": True, "value": config.client_id},
        exp={"essential": True},
        iat={"essential": True},
        sub={"essential": True},
        nonce={"essential": True, "value": nonce},
    )
    try:
        registry.validate(claims)
    except ExpiredTokenError as exc:
        raise OidcError("expired") from exc
    except ClaimError as exc:  # names the claim, never its value
        kind = "missing" if isinstance(exc, MissingClaimError) else "invalid"
        raise OidcError(f"{kind}_claim:{exc.claim}"[:80]) from exc
    audiences = claims["aud"]
    if isinstance(audiences, list) and len(audiences) > 1 and claims.get("azp") != config.client_id:
        raise OidcError("invalid_claim:azp")
    subject = claims["sub"]
    if len(subject) > 255:
        raise OidcError("invalid_claim:sub")

    email = _email(claims)
    if config.provider is OidcProvider.google:
        vouched = _vouched(claims.get("email_verified"))
    else:
        vouched = _vouched(claims.get("xms_edov"))
    return Identity(
        provider=config.provider,
        issuer=issuer,
        subject=subject,
        email=email,
        email_verified=email is not None and vouched,
        name=_name(claims),
    )
