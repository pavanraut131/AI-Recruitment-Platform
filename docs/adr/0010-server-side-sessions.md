# 0010 · Server-side sessions in an httpOnly cookie; OIDC for Google and Microsoft sign-in
**Status:** proposed  ·  **Date:** 2026-11-19

## Context
E2 adds sign-in to HireFlow AI ([spec §4](../specs/EPIC-02-identity-tenancy.md#4-authentication)). The client is **our own** Next.js web app, talking to **our own** FastAPI backend: no third-party API consumers, no mobile app, no service-to-service calls with user tokens (an MCP server in E10 or ATS connectors in E11 would get their own credentials). Agencies expect to sign in with their Google Workspace or Microsoft 365 accounts as well as with a password.

The forces:

- **Revocation has to be immediate.** When an agency deactivates a recruiter, changes a role or limits a user to some clients, access must change on the next request, not when a token expires.
- **XSS should not hand over the account.** Anything JavaScript can read, an injected script can read too.
- **CSRF has to be handled** whenever the browser sends credentials automatically.
- **Long-lived SSE streams and uploads** must be authenticated the same way as plain requests (`EventSource` cannot set an `Authorization` header).
- **One developer** maintains it: the fewer moving parts (key rotation, refresh flows, token storage), the better.

## Decision
- **Opaque, server-side sessions.** At sign-in the backend creates a session row (`user_sessions`) and sends a 256-bit random id in a cookie: `__Host-hf_session; HttpOnly; Secure; SameSite=Lax; Path=/`. The database stores only the id's SHA-256.
  - Each request loads the session, the user and the active membership in one query, so the role, client scope and account status are always current.
  - Sessions have an idle timeout and an absolute lifetime, and the id **rotates** on every authentication (login, OIDC, invitation acceptance, password change).
  - Logout, password reset and deactivation revoke sessions in the database, which takes effect at once.
- **CSRF:** a signed double-submit token bound to the session, sent in `X-CSRF-Token` on unsafe methods, plus an `Origin` check, on top of `SameSite=Lax`.
- **Passwords:** Argon2id (`argon2-cffi`), with login rate limits that throttle rather than lock accounts.
- **Google and Microsoft sign-in through OpenID Connect**, authorization code flow with PKCE, as a confidential client (PKCE and a client secret), implemented with **Authlib**:
  - `state`, `nonce` and the PKCE verifier are kept in a short-lived encrypted cookie;
  - the ID token's signature, `iss` (per tenant for Microsoft), `aud`, `exp` and `nonce` are validated;
  - provider identities are linked by `(provider, issuer, subject)`. An email is used to link only when the provider vouches for it (Google `email_verified`, Microsoft `xms_edov`) or an invitation token proves the inbox.
  - The provider's tokens are not stored. After the callback the user holds a normal HireFlow session.
- **Same-site deployment.** The frontend and API must share a registrable domain (preferably one origin, E5) so the `SameSite=Lax` cookie is sent. Development on `localhost:3000` and `localhost:8000` is same-site already.

## Alternatives considered
- **JWT access tokens in `localStorage`** (with a refresh token). This is the common SPA tutorial setup, but:
  - any XSS can read the tokens and use them from anywhere;
  - a token stays valid until it expires, so a deactivation or role change is not immediate unless every request checks a denylist (which is a server-side session again, with more steps);
  - it needs key management, refresh rotation and reuse detection;
  - `EventSource` cannot send the header, so the SSE stream would need a token in the URL, which ends up in logs.

  Statelessness, the main benefit of JWTs, does not help a single backend that already reads Postgres on every request.
- **JWTs in an httpOnly cookie.** This fixes the XSS exposure, but keeps the revocation and key-management problems for no gain over an opaque id, and the cookie gets large.
- **Sessions in Redis instead of Postgres.** Lookups are faster and expiry is built in, but sessions then live outside backups and outside the transactional joins with users and memberships (one query per request today), and Redis is configured `allkeys-lru` in Compose, so it could evict sessions. Postgres stays the source of truth; a Redis cache in front is an optimisation for later if the lookup shows up in profiles.
- **A hosted identity provider** (Auth0, Clerk, WorkOS, Cognito, Keycloak). It would deliver MFA, SSO and SCIM sooner, but adds a per-user cost and a vendor in the critical path, splits user data from memberships and roles, and takes away the learning goal of this epic. WorkOS or Keycloak stay options for SAML/SCIM when an enterprise customer asks (backlog).
- **Magic-link-only sign-in** (as the PRD sketches for candidates). It is simpler, with no passwords, but recruiters sign in many times a day, and it makes email deliverability a hard dependency of every login. It can be added later as another authenticator.
- **OAuth 2.0 without OIDC** (calling Google's or Microsoft's user-info APIs with an access token). It has no signed identity assertion, no `nonce` and no standard subject. OIDC's ID token gives us exactly that.
- **The implicit flow, or the authorization code flow without PKCE.** The implicit flow is deprecated (OAuth 2.0 Security BCP, RFC 9700). PKCE protects the code even for a confidential client and costs nothing.
- **Starlette's `SessionMiddleware`** for the OIDC state. It is a signed (not encrypted) whole-app cookie session that we would use for one flow only. A dedicated, short-lived, encrypted cookie keeps the state contained.

## Consequences
- **Easier:**
  - Revocation, role changes and deactivation take effect immediately.
  - No token is readable by JavaScript.
  - SSE, uploads and fetches authenticate the same way (the cookie).
  - No signing keys to rotate.
  - Adding MFA later is a step between "password OK" and "create session".
- **Harder:**
  - One session lookup per request (an indexed join, measured in the query-count tests).
  - CSRF protection is now our job.
  - The frontend and API must be deployed same-site.
  - Expired sessions need a periodic purge.
  - OIDC needs app registrations at Google and Microsoft per environment.
- **Watch for:**
  - Cookie attributes in each environment (the `Secure` flag on plain-HTTP development hosts; the `__Host-` prefix needs `Secure`).
  - Session fixation regressions (a new id at every authentication is tested).
  - Account-linking rules: never link by an unverified email (the "nOAuth" class of takeover).
  - The write rate of `last_seen_at` (throttled to once every 5 minutes).
  - A future non-browser client (E10 MCP, E11 integrations): it gets its own credential type (API keys or OAuth client credentials), not these cookies.
