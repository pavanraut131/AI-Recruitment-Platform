# E2 · Identity & tenancy: Design Spec

**Status:** approved (Sprint 7 started 2026-11-19)  ·  **Date:** 2026-11-19  ·  **Roadmap:** [roadmap.md](../roadmap.md)  ·  **Depends on:** E1 closed (E1-10: the frontend calls only `/api/v2`)  ·  **ADRs:** [0009 Tenant isolation with row-level security](../adr/0009-tenant-isolation-row-level-security.md), [0010 Server-side sessions](../adr/0010-server-side-sessions.md)

## 1. Goal

Make HireFlow AI safe to put in front of more than one agency. Today every request is anonymous and every row belongs to everyone. After E2:

1. **Tenancy:** every row the product stores belongs to an **Agency**. Campaigns also belong to one of the agency's **Clients**. The talent pool (People) is agency-wide.
2. **Isolation:** one agency can never read or change another's data, even when application code forgets a filter. Postgres **row-level security** (RLS) is the backstop under explicit `agency_id` filtering in the repositories.
3. **Identity:** people sign in with email and password, or with Google or Microsoft, into a server-side session held in an httpOnly cookie. Agency owners invite their team by email.
4. **Authorization:** four agency roles (`owner`, `admin`, `recruiter`, `viewer`), optionally narrowed to some clients, enforced on every `/api/v2` route.
5. **Clean-up:** `/api/v1` is removed at the end of the epic, so there is exactly one API and it is authenticated.

**Learning focus:** multi-tenant data isolation (RLS, roles, transaction-scoped settings, connection pooling), session management and cookie security, OAuth 2.0 / OpenID Connect (authorization code + PKCE), RBAC, CSRF, rate limiting, and zero-downtime schema changes for a new tenant key.

## 2. Non-goals

- **TOTP MFA, passkeys, SSO/SAML, SCIM provisioning.** Backlog. The session and identity tables leave room for them (a user has several authenticators).
- **Self-serve agency sign-up and billing.** An operator command creates an agency and invites its owner. Sign-up arrives with billing in **E11**.
- **A full, immutable audit log** with retention and a viewer. E2 records security-relevant events in a minimal `audit_events` table; **E7** builds the audit log.
- **Hiring-manager, interviewer and candidate roles** (PRD §5.9) and **shareable client shortlists** (E11). The roles here are agency staff only.
- **Per-client data isolation in the database.** Client scoping is an authorization rule in the application (§5.3), not an RLS policy.
- **Deployment.** Nothing is exposed publicly until E5. E2 makes the app *ready* for it: secure cookie flags, secrets required outside development, same-site deployment documented.
- **Sessions UI** ("signed-in devices", "sign out everywhere"), breached-password checks and email-change flows. Backlog.

## 3. Tenancy model and data

### 3.1 Concepts

```
Agency (tenant) ─┬─ Members (users with a role, optionally limited to some clients)
                 ├─ Clients ── Campaigns ── Applications
                 ├─ People (talent pool, agency-wide) ── identifiers, conflicts
                 └─ Resumes (stored PDFs, extraction snapshots)
```

- An **Agency** is the tenant: the unit of isolation, billing (E11) and data ownership.
- A **Client** is the agency's customer. Every campaign is run for exactly one client. Clients are archived, never deleted, because other tables refer to them by id (ADR 0003).
- A **User** is a human with a login. Users are **global**: one email, one password, and possibly memberships in several agencies (in practice agency staff have one; our own support staff and freelancers may have more). A **Membership** gives a user a role in one agency.
- **People stay agency-wide.** The same candidate applying through two agencies is two People, one per agency. Matching (ADR 0004) never crosses agencies.

### 3.2 Module ownership

A new module, **`identity`**, owns agencies, users, memberships, sessions, invitations, password-reset tokens, OIDC accounts and audit events. It is a **leaf** like `talent` and `ai`: it imports no other module. **Clients belong to `hiring`**, next to the campaigns that refer to them, so `campaigns.client_id` is a same-module foreign key.

| Module | New tables | New columns |
|---|---|---|
| identity | `agencies`, `users`, `memberships`, `membership_clients`, `user_sessions`, `invitations`, `password_reset_tokens`, `oidc_accounts`, `audit_events` | — |
| hiring | `clients` | `campaigns.agency_id`, `campaigns.client_id`, `campaigns.created_by_user_id`, `applications.agency_id` |
| talent | — | `agency_id` on `people`, `person_identifiers`, `identity_conflicts`, `person_resumes` |
| ingestion | — | `resumes.agency_id`, `resumes.uploaded_by_user_id` |

`agency_id` on hiring, talent and ingestion tables refers to identity's `agencies` **without a foreign key** (ADR 0003). The database still guarantees consistency in three ways: RLS `WITH CHECK` rejects a row for any agency but the current one (§6.2), **composite same-module foreign keys** tie children to a parent of the same agency (below), and `verify_people_applications` gains cross-module checks (§7.3). `tests/test_no_cross_module_foreign_keys.py` gets the new tables in `TABLE_OWNERS`.

**Shared types live in the platform.** `app.platform.tenancy` holds `TenantContext(agency_id)` and `AccessScope(agency_id, client_ids: frozenset[UUID] | None)` (`None` = all clients), the session binding (§6.3) and the default-agency constant used during the migration. Modules take these types without importing `identity`.

### 3.3 Tables

**identity**

| Table | Key columns | Constraints and notes |
|---|---|---|
| `agencies` | id, name (≤ 200), slug, status (`active` / `suspended`), created_at, updated_at | UNIQUE(slug). Suspended agencies cannot sign in (checked per request). |
| `users` | id, email (≤ 320, stored lower-cased and trimmed), email_verified_at, password_hash (null for OIDC-only users), display_name, status (`active` / `disabled`), last_login_at, created_at, updated_at | UNIQUE(email). `disabled` is a platform-level switch (abuse, offboarding from every agency); per-agency deactivation is on the membership. |
| `memberships` | id, agency_id FK, user_id FK, role, all_clients (bool), status (`active` / `deactivated`), invited_by_user_id, last_active_at, created_at, updated_at, deactivated_at | UNIQUE(agency_id, user_id). CHECK role IN (`owner`, `admin`, `recruiter`, `viewer`). CHECK `all_clients OR role IN ('recruiter', 'viewer')`: owners and admins always see every client. |
| `membership_clients` | membership_id FK (cascade), client_id | PK(membership_id, client_id). `client_id` refers to hiring's `clients` (no FK). Used only when `all_clients` is false. |
| `user_sessions` | id_hash (bytea, PK: SHA-256 of the cookie value), user_id FK (cascade), active_agency_id (null until chosen), created_at, last_seen_at, idle_expires_at, absolute_expires_at, revoked_at, ip (inet), user_agent | Index (user_id). The cookie value itself is never stored (§4.1). |
| `invitations` | id, agency_id FK, email, role, all_clients, client_ids (uuid[]), token_hash (UNIQUE), invited_by_user_id, expires_at, accepted_at, accepted_by_user_id, revoked_at, created_at | Partial UNIQUE(agency_id, email) WHERE accepted_at IS NULL AND revoked_at IS NULL: one open invitation per address per agency. |
| `password_reset_tokens` | id, user_id FK (cascade), token_hash (UNIQUE), expires_at, used_at, created_at | A new request invalidates the user's older unused tokens. |
| `oidc_accounts` | id, user_id FK (cascade), provider (`google` / `microsoft`), issuer, subject, email_at_link, created_at, last_used_at | UNIQUE(provider, issuer, subject). Never keyed by email (§4.4). |
| `audit_events` | id (bigint identity), occurred_at, agency_id (null for events before an agency is known), actor_user_id, action, target_type, target_id, ip, user_agent, details (JSONB) | Append-only: the app role has INSERT and SELECT, no UPDATE or DELETE. Index (agency_id, occurred_at). |

**hiring, talent, ingestion**

| Table | Change | Constraints |
|---|---|---|
| `clients` (new) | id, agency_id, name (≤ 200), status (`active` / `archived`), created_at, updated_at | UNIQUE(agency_id, lower(name)); UNIQUE(agency_id, id) (target of the composite FK) |
| `campaigns` | + agency_id, client_id, created_by_user_id (plain UUID, identity) | FK (agency_id, client_id) → clients(agency_id, id); UNIQUE(agency_id, id); index (agency_id, status, updated_at) |
| `applications` | + agency_id | FK (agency_id, campaign_id) → campaigns(agency_id, id) ON DELETE CASCADE (replaces the single-column FK); UNIQUE(campaign_id, person_id) stays |
| `people` | + agency_id | UNIQUE(agency_id, id); index (agency_id, updated_at) |
| `person_identifiers` | + agency_id | **UNIQUE(agency_id, kind, value_normalized)** replaces UNIQUE(kind, value_normalized) (§3.4); FK (agency_id, person_id) → people(agency_id, id) |
| `identity_conflicts`, `person_resumes` | + agency_id | Composite FKs to `people` as above |
| `resumes` | + agency_id, uploaded_by_user_id | Index **(agency_id, campaign_id)** (closes the backlog item "Index on `resumes.campaign_id`") |
| `legacy_candidate_map` | unchanged | Read only by `/api/v1`; dropped with it in E2-12 (§7.4). Until then it is on the RLS allowlist: it holds only id pairs, and the applications they lead to are under RLS. |

Every unique constraint on a tenant table **includes `agency_id`**. A global unique would leak across tenants through its error ("this client name exists" for another agency's client), and RLS does not hide constraint violations.

### 3.4 What changes in existing behaviour

- **Person matching is per agency.** `resolve_person` reads and writes identifiers of the current agency only; the advisory-lock keys become `agency_id:kind:value`, so two agencies never wait on each other's locks (and lock waits cannot reveal that another agency is processing the same email). The concurrency test runs with two agencies.
- **Duplicate-file detection** is already per campaign, so it is per agency by construction.
- **Counts and lists** (`/stats`, `/home`, `/people`, campaign lists) cover the current agency, and for client-scoped users their clients (§5.3).

## 4. Authentication

### 4.1 Sessions and cookies

Decision and alternatives: [ADR 0010](../adr/0010-server-side-sessions.md).

- **Session id:** 32 random bytes (`secrets.token_urlsafe(32)`, 256 bits), sent as the cookie value. The server stores only its **SHA-256** (`user_sessions.id_hash`), so a database read does not yield usable sessions.
- **Cookie:** `__Host-hf_session` with `HttpOnly; Secure; SameSite=Lax; Path=/`, no `Domain` (host-only). The `__Host-` prefix makes the browser refuse the cookie unless it is Secure, host-only and path `/`. In development over plain `http://localhost` the name is `hf_session` and `Secure` follows `SESSION_COOKIE_SECURE` (the settings refuse `false` outside `APP_ENV=development|test`).
- **Lifetime:** idle timeout `SESSION_IDLE_TIMEOUT` (default 8 h) and absolute lifetime `SESSION_ABSOLUTE_TIMEOUT` (default 7 days). Activity extends the idle deadline; `last_seen_at` / `idle_expires_at` are written at most once every 5 minutes per session, so reads do not become writes.
- **Per request:** one indexed query loads the session, the user, the active membership and its client list. Role, client scope, membership status, agency status and user status are read **live** on every request: deactivating a member or changing a role takes effect on their next request, with no token to wait out.
- **Same-site requirement.** Browsers send a `SameSite=Lax` cookie on cross-*origin* but same-*site* requests. In development the frontend (`localhost:3000`) and API (`localhost:8000`) are same-site; deployments (E5) must serve both under one registrable domain, preferably one origin (path routing `/api` → backend). This is written into the E5 notes.

### 4.2 Passwords

- **Argon2id** via `argon2-cffi` (`PasswordHasher` with the RFC 9106 low-memory profile: 64 MiB, 3 iterations, 4 lanes; tunable by setting). `check_needs_rehash` on each successful login upgrades old hashes. Hashing runs in a worker thread so it never blocks the event loop.
- **Policy** (NIST SP 800-63B, ASVS V2.1): 12–128 characters, any characters, no composition rules, not equal to the email; no forced rotation. A breached-password check (k-anonymity range API) is backlog.
- Changing a password requires the current one and revokes every other session of the user.

### 4.3 Flows

All endpoints below are under `/api/v2`. "Rotate" means: revoke any session cookie the request carried, create a new session row with a new id, and set new session and CSRF cookies.

**Login (email and password)**
1. The login page loads; `GET /auth/csrf` sets the CSRF cookie (§4.5) for the anonymous browser.
2. `POST /auth/login {email, password}` with `X-CSRF-Token`.
3. Rate limits are checked first (§4.6): over the limit is `429` with `Retry-After`, before any password work.
4. The email is normalised and the user looked up. If there is none, the password is verified against a fixed dummy hash, so an unknown email takes as long as a wrong password.
5. Wrong password, unknown email or disabled user: `401` with the same body, `"Invalid email or password"` (no enumeration). Audit `auth.login_failed` (with a hash of the email, never the password).
6. Right password but no active membership in an active agency: `403` *"Your account has no active agency access"*. Only the account holder can reach this answer.
7. Success: **rotate** (session fixation defence), choose the active agency (the only membership, else the last used one, else none and the frontend shows the agency picker), update `last_login_at`, audit `auth.login_succeeded`, answer `200` with the `Me` resource.

**Sign in with Google or Microsoft (OIDC authorization code + PKCE)**
1. The user clicks "Continue with Google". The frontend calls `POST /auth/oidc/{provider}/start {next, invitation_token?}`.
2. The backend creates `state`, `nonce` and a PKCE `code_verifier` (S256 challenge). It stores them, with `next` and the invitation token's hash, in a short-lived cookie `__Host-hf_oidc` (10 minutes, HttpOnly, SameSite=Lax, authenticated encryption with a key derived from `SECRET_KEY`), and answers `{authorization_url}`. The frontend navigates there.
3. The provider authenticates the user and redirects (top-level GET, so the Lax cookie is sent) to `GET /auth/oidc/{provider}/callback?code&state`.
4. The backend checks `state` against the cookie, then exchanges the code with the `code_verifier` and the client secret (confidential client: PKCE *and* a secret). It validates the ID token: signature against the provider's JWKS (from discovery metadata, cached), `iss`, `aud`, `exp`, `iat` and `nonce`.
   - **Google:** `iss` is `https://accounts.google.com`, and `email_verified` must be true to use the email.
   - **Microsoft** (Entra ID, `organizations` endpoint by default): the issuer depends on the tenant, so `iss` must equal `https://login.microsoftonline.com/{tid}/v2.0` for the token's own `tid`. The `email` claim is **not** proof of ownership (the "nOAuth" class of account takeovers), so Microsoft emails are trusted only with the `xms_edov` (email domain owner verified) optional claim.
5. The backend resolves the user, in this order:
   1. An `oidc_accounts` row with this `(provider, issuer, subject)`: sign in as its user.
   2. The flow carries an **invitation**: the invitation token proves the invitee controls the invited inbox, so the identity is linked to the invited user (created if new) and the invitation accepted.
   3. A user with this email exists **and** the provider vouches for the email (step 4): link the identity to that user, audit `auth.oidc_linked`, and email the user that a new sign-in method was added.
   4. Otherwise: no account. Redirect to `/login?error=no_account` (*"There is no HireFlow account for this address. Ask your agency to invite you."*). There is no self sign-up (§2).
6. **Rotate**, audit `auth.login_succeeded` (`method: google|microsoft`), delete the OIDC cookie, and redirect to `FRONTEND_URL + next`. `next` must be a relative path starting with a single `/`, otherwise `/` (no open redirect).

Library: **Authlib** (`AsyncOAuth2Client` for the code exchange, `joserfc` for JWT validation), with our own state handling instead of Starlette's `SessionMiddleware`. Provider credentials: `OIDC_GOOGLE_CLIENT_ID/SECRET`, `OIDC_MICROSOFT_CLIENT_ID/SECRET`, `OIDC_MICROSOFT_TENANT` (default `organizations`). A provider without credentials is hidden: `GET /auth/providers` lists the configured ones.

**Invitation and onboarding**
1. An owner or admin sends `POST /team/invitations {email, role, all_clients, client_ids}`. The role must be one they may grant (§5.2). An address that is already an active member is a `409`. An open invitation for the same address is replaced (the old token stops working).
2. The backend stores the SHA-256 of a 32-byte token (expires in 7 days), audits `team.invitation_created`, and queues an email with the link `{FRONTEND_URL}/accept-invite#token=…`. The token is in the URL **fragment**, which browsers never send to a server, so it cannot end up in access logs or `Referer` headers.
3. The accept page reads the fragment and calls `POST /auth/invitations/preview {token}`, which returns the agency name, inviter, role, email and whether an account with that email exists. An expired, revoked or used token is `410`.
4. **New user:** a form (display name, password) or "Continue with Google/Microsoft" (the OIDC flow with `invitation_token`). `POST /auth/invitations/accept {token, display_name, password}` creates the user (email verified, proven by the token), the membership and its client list, **rotates** into a new session with the agency active, and audits `team.invitation_accepted`.
5. **Existing user:** they sign in first (password or OIDC), then accept with their session; the signed-in email must equal the invited email (`403` otherwise). The new agency becomes active.

**Password reset**
1. `POST /auth/password-reset {email}` always answers `202` with the same body. Per-email and per-IP limits apply (§4.6).
2. If an active user has this email, a token (32 bytes, hashed, single use, 30 minutes) is created and the link `{FRONTEND_URL}/reset-password#token=…` is emailed. The email goes out from a Celery task in every case where it is sent, so response time does not reveal whether the account exists.
3. `POST /auth/password-reset/confirm {token, new_password}`: the token must be valid, unused and unexpired. The backend sets the password (the user may have had none: OIDC-only users can add one this way), marks the token used, **revokes every session** of the user, sends a "your password was changed" email, audits `auth.password_reset_completed`, and answers `204`. The frontend sends the user to the login page.

**Logout:** `POST /auth/logout` revokes the session row (`revoked_at`), clears both cookies, audits `auth.logout`, answers `204`. Replaying the old cookie is a `401`.

**Agency switch:** `PUT /me/active-agency {agency_id}` requires an active membership in an active agency (`404` otherwise, so non-membership is not distinguishable from non-existence). It updates the session row and audits `auth.agency_switched`. The frontend then clears its Query cache (§9.4).

**Expiry and rotation**
- An expired (idle or absolute), revoked or unknown session is a `401` problem, and the response clears the cookie. The frontend sends the user to `/login?next=<current path>&reason=expired`.
- The session id rotates on login, OIDC login, invitation acceptance and password change. A role change does not rotate it (permissions are read live), and neither does an agency switch (same user, same authentication).
- A daily Celery beat task (`identity.purge_expired`, the first beat schedule) deletes sessions expired for 30 days, used or expired reset tokens, and invitations expired for 30 days. These tables are global (§6.4), so the task needs no tenant.

### 4.4 Email

- `app.platform.email` with two backends: `console` (logs the message; the default in tests) and `smtp` (development uses **Mailpit** in Compose, web UI on `MAILPIT_UI_HOST_PORT`, default 8025, with an HTTP API the E2E tests read invitation links from). Production chooses a provider in E5.
- Messages are sent from a Celery task (`identity.send_email`, queue `default`) so the HTTP response never waits on SMTP and timing reveals nothing. Templates are plain text plus a minimal HTML version, with `FRONTEND_URL` links.
- Emails: invitation, password reset, password changed, new sign-in method linked.

### 4.5 CSRF

The session cookie is `SameSite=Lax`, which already stops cross-site POSTs in current browsers. That is not enough on its own (older browsers, same-site subdomains, method-override bugs), so unsafe methods (`POST`, `PUT`, `PATCH`, `DELETE`) also need:

1. **A signed double-submit token.** Cookie `__Host-hf_csrf` (`hf_csrf` in development; not HttpOnly, so the frontend can read it; Secure, SameSite=Strict) holds `random.HMAC(SECRET_KEY, session_id_hash + random)`. It is bound to the session, so a token planted by a sibling subdomain does not validate. Before login it is bound to an anonymous nonce, and login issues a new one. The request must echo it in **`X-CSRF-Token`**; the server checks the HMAC and compares the header to the cookie in constant time. The token is also returned in `Me.csrf_token`, for deployments where the frontend cannot read the API's cookies.
2. **An `Origin` check.** When present, `Origin` (else `Referer`) must be one of `CORS_ORIGINS`.

A failure is `403` with problem detail *"CSRF token missing or invalid"*. The custom header also forces a CORS preflight, which the CORS policy refuses for unknown origins. The OIDC callback is a GET and is protected by `state` instead. CORS keeps `allow_credentials=True` with an explicit origin list (never `*`), and its allowed headers become `Content-Type, X-CSRF-Token`.

### 4.6 Rate limiting

A small Redis limiter in `app.platform.ratelimit` (fixed windows with `INCR` + `EXPIRE`; a protocol, so tests use the existing `FakeRedis`). Keys use a hash of the email, never the address. Requests over a limit get `429` with `Retry-After` and are audited once per window (`auth.rate_limited`). The design throttles but **never locks accounts**, so an attacker cannot lock a victim out.

| Endpoint | Limits (defaults, configurable) |
|---|---|
| `POST /auth/login` | 5 failures per email per 15 min; 30 attempts per IP per 15 min |
| `POST /auth/password-reset` | 3 per email per hour; 10 per IP per hour |
| `POST /auth/invitations/preview`, `/accept` | 20 per IP per 15 min |
| `POST /auth/oidc/*/start`, callback | 30 per IP per 15 min |
| `POST /team/invitations` | 50 per agency per day (stops the product being used to send spam) |

The client IP comes from the socket. Behind the E5 load balancer, `--forwarded-allow-ips` makes uvicorn trust `X-Forwarded-For` from the proxy only.

### 4.7 Audit events

`identity.service.record_audit(db, action, …)` writes in the caller's transaction, so an event exists exactly when its change commits. Failed logins are written in a transaction of their own. Actions in E2:

- `auth.login_succeeded`, `auth.login_failed`, `auth.logout`, `auth.rate_limited`, `auth.agency_switched`, `auth.oidc_linked`
- `auth.password_changed`, `auth.password_reset_requested`, `auth.password_reset_completed`
- `team.invitation_created`, `team.invitation_resent`, `team.invitation_revoked`, `team.invitation_accepted`
- `team.member_role_changed` (old and new role, old and new client list), `team.member_deactivated`, `team.member_reactivated`
- `agency.updated`, `client.created`, `client.updated`

Client events are recorded by the composed endpoint in `app.api`. Events are queryable by SQL only; there is no viewer in E2 (E7).

## 5. Authorization

### 5.1 Roles

| Role | Meant for |
|---|---|
| `owner` | The agency's principal. Everything, including managing admins and other owners. At least one active owner per agency at all times. |
| `admin` | Runs the workspace: team (recruiters and viewers), clients, all campaigns. |
| `recruiter` | Day-to-day screening for their clients: campaigns, uploads, review. |
| `viewer` | Read-only: account managers or a junior who shadows. Can be restricted to some clients. |

### 5.2 Permission matrix

"Scoped" means limited to the user's clients when the membership has `all_clients = false` (§5.3). Owners and admins always have every client.

| Permission | Allows | owner | admin | recruiter | viewer |
|---|---|:-:|:-:|:-:|:-:|
| `workspace:read` | Campaigns, applications, People, Home, stats, Insights, processing events | ✅ | ✅ | ✅ scoped | ✅ scoped |
| `campaign:write` | Create and edit campaigns (status included), parse a JD with AI | ✅ | ✅ | ✅ scoped | — |
| `campaign:delete` | Delete a campaign with its applications and resumes | ✅ | ✅ | — | — |
| `resume:upload` | Upload resumes, retry failed ones | ✅ | ✅ | ✅ scoped | — |
| `application:review` | Stage, notes, category override, bulk stage and reject | ✅ | ✅ | ✅ scoped | — |
| `application:delete` | Delete applications (single and bulk) | ✅ | ✅ | ✅ scoped | — |
| `application:export` | CSV export (bulk personal data) | ✅ | ✅ | ✅ scoped | — |
| `client:read` | List and view clients | ✅ | ✅ | ✅ scoped | ✅ scoped |
| `client:manage` | Create, rename, archive clients | ✅ | ✅ | — | — |
| `team:read` | Members and invitations | ✅ | ✅ | — | — |
| `team:manage` | Invite, change role and client access, deactivate and reactivate **recruiters and viewers**; resend and revoke their invitations | ✅ | ✅ | — | — |
| `team:manage_privileged` | The same for **owners and admins** (granting, changing, removing) | ✅ | — | — | — |
| `agency:manage` | Rename the agency | ✅ | — | — | — |
| *(any member)* | `/me`: own profile, password, active agency, sign out | ✅ | ✅ | ✅ | ✅ |

**Guard rails**
- The last active owner cannot be demoted or deactivated (`409`).
- A user cannot change their own role or deactivate themselves.
- An admin cannot see or touch the client list of owners and admins (always all clients).

The matrix lives in one module, `app.modules.identity.permissions` (`ROLE_PERMISSIONS: Mapping[Role, frozenset[Permission]]`). The authorization tests and this table are both generated from it, and a test checks the spec table against it so they cannot drift.

### 5.3 Client scoping

- A membership with `all_clients = false` lists its clients in `membership_clients`. Only `recruiter` and `viewer` can be scoped.
- **Lists** (campaigns, applications through their campaign, Home, stats, Insights, clients) are filtered to the allowed clients. **Single objects** outside the scope answer `404`, exactly like another agency's objects, so the response never confirms that something exists.
- **The talent pool stays agency-wide** (§3.1): a scoped user sees every Person in the agency, which is what talent rediscovery needs. On a Person's profile, **applications to campaigns outside the scope are left out**, together with their scores and notes. Decided in §15 (Q2).
- Uploads, events and exports go through the campaign, so they follow the campaign's client.
- Client scoping is enforced in the application, not by RLS: it is a contractual boundary inside one tenant, not a legal one between tenants. It is covered by the authorization test suite (§11.3).

### 5.4 Enforcement

- `app.modules.identity.dependencies` (public, like `service`; import-linter allows it from `app.v2`, `app.api.v2` and `app.modules.*.router_v2` only):
  - `current_principal` → `Principal(user_id, session, membership, scope: AccessScope)`: `401` without a valid session, and `403` *"Choose an agency"* when the session has no active agency (only `/me` and `/me/active-agency` accept that state).
  - `require(permission)` → `403` *"You don't have permission to …"* when the role lacks the permission.
  - `tenant_db` → an `AsyncSession` bound to the principal's agency (§6.3). It replaces `Depends(get_db)` in every v2 router.
- **Every v2 route declares a permission.** The v2 app refuses to start if a route has neither `require(…)` nor an explicit `public` marker (the auth endpoints, `GET /auth/providers`, `GET /auth/csrf`), the same way it already refuses a route without an `operation_id` (ADR 0008).
- Services take an `AccessScope` wherever they list or fetch campaign-owned data (hiring, the composed endpoints), and filter by `client_id` in their queries. They never import `identity`.
- The OpenAPI document declares the `cookieAuth` (the session cookie) and `csrfToken` (header `X-CSRF-Token`) security schemes. Every protected operation lists them and the `401` / `403` problem responses, so the generated client knows about them.

## 6. Tenant isolation with row-level security

Decision and alternatives: [ADR 0009](../adr/0009-tenant-isolation-row-level-security.md).

### 6.1 Two layers

1. **Explicit filtering (primary).** Every repository query on a tenant table filters `agency_id = :agency_id`, and every insert sets it. The agency comes from the session binding (§6.3) through `tenancy.agency_id_of(db)`, which **raises** `TenantNotBound` when the session has no tenant, so a query without a tenant fails loudly instead of returning everything.
2. **RLS (backstop).** Postgres filters every row the application role reads or writes by the transaction's `app.agency_id`. A repository that forgets its filter still sees only the current agency, and code that forgets to bind a tenant sees **nothing**.

The application keeps the explicit filter even though RLS would be enough on its own. It makes the intent readable in the code, lets the planner use the `(agency_id, …)` indexes directly, and keeps the code correct if it ever runs against a database without the policies (a restored dump, a misconfigured role).

### 6.2 Roles and policies

| Role | Attributes | Used by |
|---|---|---|
| `hireflow_owner` | LOGIN, NOSUPERUSER, NOBYPASSRLS; **owns** the schema and every table | Alembic migrations, operator commands that span agencies (`verify_people_applications`, `storage_orphans`, `reset_stuck`, `create_agency`) through `MIGRATION_DATABASE_URL` |
| `hireflow_app` | LOGIN, NOSUPERUSER, NOBYPASSRLS, owns nothing | The API (`DATABASE_URL`) and the Celery workers (`SYNC_DATABASE_URL`) |

- **Grants:** `hireflow_app` gets `SELECT, INSERT, UPDATE, DELETE` on the tables and `USAGE` on the sequences, except `audit_events` (`SELECT, INSERT` only) and `alembic_version` (nothing). `ALTER DEFAULT PRIVILEGES FOR ROLE hireflow_owner` gives tables created later the same grants, and the isolation suite checks the grants table by table (§11.2).
- **Policies**, created for every tenant table by a migration helper (`op_enable_tenant_rls(table)`):

```sql
CREATE FUNCTION app_current_agency() RETURNS uuid LANGUAGE sql STABLE PARALLEL SAFE AS
  $$ SELECT nullif(current_setting('app.agency_id', true), '')::uuid $$;

ALTER TABLE campaigns ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaigns FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON campaigns FOR ALL TO hireflow_app
  USING      (agency_id = (SELECT app_current_agency()))
  WITH CHECK (agency_id = (SELECT app_current_agency()));

CREATE POLICY maintenance ON campaigns FOR ALL TO hireflow_owner
  USING (true) WITH CHECK (true);
```

- **Fail closed:** with no `app.agency_id` set, `current_setting(…, true)` is empty, `app_current_agency()` is NULL, `agency_id = NULL` is never true, so reads return no rows and writes are rejected.
- **Why FORCE plus a named owner policy:** without `FORCE` the owner bypasses RLS silently. With `FORCE`, every role is subject to the policies, and the owner's access is a visible, named policy granted to one role. A role that holds no policy (a future reporting role, or the app role if it ever became the owner by accident) sees nothing. `BYPASSRLS` on the migrator was rejected (ADR 0009).
- `(SELECT app_current_agency())` is evaluated once per statement (an init plan), not once per row.
- **Start-up check:** the API and the workers refuse to start if their database user is a superuser, has `BYPASSRLS`, or owns any tenant table. The check runs `SELECT rolsuper, rolbypassrls …` and `pg_tables.tableowner` once at start-up.

### 6.3 Setting the tenant: HTTP, SSE, Celery

**The session binding.** `app.platform.tenancy.bind(session, agency_id)` stores the agency in `session.info`. An `after_begin` listener on the session class (the sync `Session` class behind both `AsyncSessionLocal` and the workers' `SyncSession`) runs this at the start of **every** transaction of a bound session:

```sql
SELECT set_config('app.agency_id', :agency_id, true)   -- true = local to this transaction
```

- `set_config(…, true)` is `SET LOCAL` with a bind parameter: no string formatting, so no injection, and it works through asyncpg's prepared statements.
- **Per transaction, not per session.** The tasks commit several times (spec E0 §5.1), and each commit ends the transaction and with it the setting. The listener re-applies it at each `BEGIN`, so a later transaction of the same session is never left unbound.
- **Connection pooling:** a `LOCAL` setting ends with its transaction, so a connection goes back to the pool clean. Session-level `SET app.agency_id` is **forbidden** (it would survive on the pooled connection into the next request); a ruff/grep check in CI rejects `SET app.` outside `tenancy.py`. This also keeps us compatible with PgBouncer in transaction mode or a Cloud SQL connector (E5). A pool `checkout` listener asserts the setting is empty when `APP_ENV` is `development` or `test`.
- **No autocommit sessions.** `SET LOCAL` outside a transaction does nothing, so tenant work never uses autocommit.

**HTTP.** `tenant_db` (§5.4) opens the request's session. It loads the principal in a first, short transaction (identity's global tables, §6.4) and commits it, binds the session to the active agency, and yields it. Every later transaction in the request starts with the setting. Unauthenticated endpoints (`/auth/*`) use an unbound session and touch only global tables.

**SSE** (`GET /campaigns/{id}/events`):
- The endpoint authenticates. It checks in one bound transaction (RLS and client scope) that the campaign is visible, and ends that transaction before streaming, as today: an open stream holds no database connection.
- Redis channels are namespaced per agency: `agency:{agency_id}:campaign:{campaign_id}:events`. The tasks publish with the agency they carry, and the stream subscribes with the principal's agency. Two agencies cannot share a channel even if campaign ids were reused.
- A stream outlives the request that authenticated it. It re-checks the session every 60 s (on its keep-alive ping, without holding a pooled connection between checks) and closes when the session is revoked or expired, the membership is deactivated, or the campaign has left the user's scope. `EventSource` reconnects and authenticates again (`withCredentials: true`).
- The backlog's workspace-wide stream (E1-08, Home) becomes `agency:{agency_id}:events` when it is built.

**Celery.**
- Every tenant task takes `agency_id` **explicitly**: `extract_resume(resume_id, agency_id)` and `process_candidate_ai(resume_id, agency_id)`. The task names stay (they are a contract); the new argument is a keyword.
- A base class `TenantTask` validates the argument in `before_start`, binds it to the structlog context, and provides `self.tenant_session()`, a `SyncSession` bound to the agency. `on_failure`, `_mark_failed` and `_mark_quota_exceeded` use it too: today they open unbound sessions.
- **Transition:** until RLS is enforced (E2-04), a message without `agency_id` (queued before the deploy) falls back to the default agency. From E2-04 on, it is rejected and logged. The E2-04 runbook drains the queues before enforcement.
- Operator scripts that span agencies (`reset_stuck`, `storage_orphans`, `verify_people_applications`) connect as `hireflow_owner` and re-enqueue each resume with its own row's `agency_id`.
- Tasks that touch no tenant data (`identity.send_email`, `identity.purge_expired`) take no agency.

### 6.4 Global tables (not under agency RLS)

Some identity tables must be read **before** a tenant is known: at login (users by email), on every request (session → user → memberships), on invitation acceptance (by token) and in the OIDC callback.

| Table | Why it is not under agency RLS | How it is protected instead |
|---|---|---|
| `agencies`, `users`, `memberships`, `membership_clients`, `user_sessions`, `invitations`, `password_reset_tokens`, `oidc_accounts`, `audit_events` | Read before or across tenants (login, the agency switcher, invitation and reset tokens, failed-login events with no agency) | Only `identity` imports their models (import-linter). Tokens are looked up by hash. Listing endpoints filter by the principal's agency explicitly and are covered by the isolation API sweep (§11.2). `audit_events` is append-only by grant. |
| `legacy_candidate_map` | Read only by `/api/v1`, which is removed in E2-12 | Holds only id pairs; the applications it leads to are under RLS |
| `alembic_version` | Bookkeeping | No grant to `hireflow_app` |

The isolation suite asserts that **every** other table has RLS enabled and forced and a `tenant_isolation` policy. A new table must either get the policy or join this allowlist with a reason, in review.

### 6.5 Object storage

- New keys: `agencies/{agency_id}/campaigns/{campaign_id}/resumes/{resume_id}.pdf`. Today's keys embed the uploaded file name (`…/{resume_id}/{filename}`): the name stays in `resumes.original_filename`, not in the key, so a crafted name cannot shape a path.
- Existing objects keep their keys (`resumes.storage_path` is stored per row, so reads and deletes keep working). `storage_orphans` scans both prefixes. Moving the old objects is an optional devtool, in the backlog.
- PDFs are only ever read by the workers or deleted by ingestion after an authorized, RLS-filtered lookup of the row. No endpoint serves them yet. When one does (E7 evidence), it must presign only after that lookup, with a short TTL.
- E5 can add a bucket policy or IAM condition per prefix, if the hosting allows it.

### 6.6 Other shared state

- **Redis:** the pub/sub channels are namespaced per agency (§6.3). Rate-limit keys use hashed identifiers. Celery messages carry `agency_id` (not a secret; visible in Flower, which stays internal).
- **Logs:** structlog binds `request_id`, `user_id` and `agency_id` on every request and task. Session ids, tokens, passwords and CSRF values are never logged; the auth endpoints do not log request bodies.
- **Query cache (frontend):** every query key is implicitly per agency, because the cache is cleared on agency switch and on logout (§9.4).

## 7. Migration plan (expand → backfill → contract → enforce)

The same pattern as E0 (EPIC-00 §6). The existing data becomes the **default agency** ("Default agency", fixed id `DEFAULT_AGENCY_ID` in `app.platform.tenancy`) with one **default client** ("General", §15 Q7). Both are renamed by the owner afterwards. The constant only exists for the transition: E2-12 deletes it, and the agency itself becomes a normal agency.

### 7.1 Phases

| Phase | Ticket | Migration and code | Runtime behaviour |
|---|---|---|---|
| **1 · Expand** | E2-01 | Create `agencies` and `clients`; insert the default agency and default client; add **nullable** `agency_id` (and `client_id`, `created_by_user_id`, `uploaded_by_user_id`) to the tenant tables. Nothing is dropped. | Every insert sets `agency_id` (the default agency) and new campaigns get the default client: the equivalent of E0's dual write. |
| **2 · Backfill** | E2-02 | `python -m app.modules.ingestion.backfill_tenancy` (in `ingestion`, the top layer, like E0's backfill: `identity` is a leaf and may not write other modules' tables): per table, in batches of 1,000 by keyset on `id` (`UPDATE … SET agency_id = :default WHERE id IN (…) AND agency_id IS NULL`), one transaction per batch; resumable and idempotent. Campaigns also get `client_id`. | Runs while the app is live |
| **3 · Verify** | E2-02 | `verify_people_applications` gains `tenancy_complete` (no NULL `agency_id`) and `cross_module_same_agency` (an application, its Person, its resume and its campaign share one agency). It must pass before phase 4. | Read-only |
| **4 · Contract** | E2-02 | `agency_id` NOT NULL using `ADD CONSTRAINT … CHECK (agency_id IS NOT NULL) NOT VALID` → `VALIDATE CONSTRAINT` → `SET NOT NULL` → drop the check (no long exclusive lock). Unique and composite-FK targets built `CONCURRENTLY` in an `autocommit_block()`, then attached with `ADD CONSTRAINT … USING INDEX`. Swap `person_identifiers`' unique to `(agency_id, kind, value_normalized)`. Composite FKs added `NOT VALID`, then validated. | Unchanged |
| **5 · Enforce** | E2-04 | Create the roles (if missing), grants, default privileges, `app_current_agency()`, `ENABLE` + `FORCE` RLS and the policies. The app switches `DATABASE_URL` / `SYNC_DATABASE_URL` to `hireflow_app`. | Tenant-bound sessions only. Rollback: point the URLs back at the owner (the owner policy keeps the app working) and downgrade the migration. |

The PostgreSQL 11+ "fast default" (`ADD COLUMN agency_id uuid NOT NULL DEFAULT '<default id>'`, no table rewrite) would have made phases 1–4 one statement. It was rejected because it makes the default agency a schema constant that has to be removed again, and the backfill pattern is what a real second migration of this kind will need.

### 7.2 Roles in each environment

- **Compose (development):** Postgres's bootstrap superuser (`POSTGRES_USER`) stays for administration only. An init script (`docker/postgres/init/10-roles.sql`, run on a fresh volume) creates `hireflow_owner` and `hireflow_app`. For existing volumes, `make db-roles` runs the same idempotent SQL. `.env.example` gets `MIGRATION_DATABASE_URL` (owner); `DATABASE_URL` and `SYNC_DATABASE_URL` switch to the app role in E2-04. `alembic/env.py` reads `MIGRATION_DATABASE_URL`. After pulling E2-04, existing stacks need `make db-roles` and a rebuild (new dependencies: `argon2-cffi`, `authlib`).
- **Tests:** the testcontainers session creates both roles as the container's superuser, migrates as `hireflow_owner`, and gives the app engines `hireflow_app` (§11.1).
- **CI `migrations` job:** runs as the owner and also checks the RLS catalogue (§11.2).
- **E5 (Cloud SQL):** the same two roles, created by Terraform; the superuser-equivalent is never used by the app.

### 7.3 Verification additions

`verify_people_applications` (owner role) gains:
- `tenancy_complete`: no NULL `agency_id`; every `agency_id` is a known agency.
- `cross_module_same_agency`: the agency of an application equals that of its Person, its resume and its campaign; the same for resumes and people (`resumes.person_id`).
- `clients_same_agency`: every campaign's client belongs to the campaign's agency (also enforced by the composite FK).

### 7.4 `/api/v1` during and after E2

`/api/v1` is unauthenticated and frozen. The frontend stopped using it in E1-10. From E2-03 it binds its sessions to the default agency, so its 179 characterization snapshots stay green through the RLS work. This is an accepted, temporary hole: nothing is deployed (E5 follows E2), and it is closed by E2-12. **E2-12 removes it:** `app.api.routes`, the module `router.py` files, `schemas.py` models used only by v1, the v1 characterization snapshots, `legacy_candidate_map` (a contract migration), the default-agency binding, the root app's v1-only exception handler (it echoes the URL, B10), `API_V1_PREFIX`, and the backlog's v1-only bug notes (B5, B7–B10 close as "removed with v1").

## 8. API

### 8.1 Conventions

- Everything is in the `/api/v2` sub-app (ADR 0008), with problem+json errors and explicit operation ids. Identity endpoints live in `app/modules/identity/router_v2.py`; clients in `hiring/router_v2.py`; endpoints that need identity and hiring (team members with their client names, client changes with audit) in `app/api/v2/`.
- New problem statuses: `401` (no or expired session), `403` (missing permission, CSRF, no agency chosen), `409` (conflicts: last owner, already a member), `410` (expired or used token), `429` (rate limited, with `Retry-After`).
- Out-of-scope and other-agency objects are `404`, never `403`.

### 8.2 New endpoints

| Method | Path | operationId | Permission | Notes |
|---|---|---|---|---|
| GET | `/auth/providers` | `listAuthProviders` | public | Configured OIDC providers |
| GET | `/auth/csrf` | `getCsrfToken` | public | Sets the anonymous CSRF cookie, returns the token |
| POST | `/auth/login` | `login` | public | 200 `Me` |
| POST | `/auth/logout` | `logout` | session | 204 |
| POST | `/auth/oidc/{provider}/start` | `startOidcLogin` | public | 200 `{authorization_url}` |
| GET | `/auth/oidc/{provider}/callback` | `completeOidcLogin` | public | 302 to the frontend |
| POST | `/auth/password-reset` | `requestPasswordReset` | public | Always 202 |
| POST | `/auth/password-reset/confirm` | `confirmPasswordReset` | public | 204; 410 bad token |
| POST | `/auth/invitations/preview` | `previewInvitation` | public | 200 `InvitationPreview`; 410 |
| POST | `/auth/invitations/accept` | `acceptInvitation` | public or session | 200 `Me` |
| GET | `/me` | `getMe` | session | `Me`: user, memberships `[{agency {id, name}, role}]`, active agency, role, permissions, `client_scope` (`all` or client list), `csrf_token` |
| PATCH | `/me` | `updateMe` | session | display name |
| POST | `/me/password` | `changePassword` | session | current + new; revokes other sessions |
| PUT | `/me/active-agency` | `setActiveAgency` | session | 200 `Me` |
| GET / PATCH | `/agency` | `getAgency` / `updateAgency` | `workspace:read` / `agency:manage` | name, slug (read-only) |
| GET | `/team/members` | `listMembers` | `team:read` | Page of `Member` (user, role, client access with names, status, last active) |
| PATCH | `/team/members/{membership_id}` | `updateMember` | `team:manage` (+ `team:manage_privileged` for owner/admin) | role, `all_clients`, `client_ids` |
| POST | `/team/members/{membership_id}/deactivate`, `/reactivate` | `deactivateMember` / `reactivateMember` | as above | Deactivation ends their access at once |
| GET / POST | `/team/invitations` | `listInvitations` / `createInvitation` | `team:read` / `team:manage` | 201 `Invitation` |
| POST | `/team/invitations/{id}/resend` | `resendInvitation` | `team:manage` | new token, new expiry |
| DELETE | `/team/invitations/{id}` | `revokeInvitation` | `team:manage` | 204 |
| GET / POST | `/clients` | `listClients` / `createClient` | `client:read` / `client:manage` | List: `status`, `q`; scoped users see their clients |
| GET / PATCH | `/clients/{client_id}` | `getClient` / `updateClient` | `client:read` / `client:manage` | name, status (`archived`) |

### 8.3 Changes to existing endpoints

- **Every** existing v2 endpoint requires a session and the permission from §5.2. `GET` endpoints need `workspace:read`. For writes the mapping is: create/patch campaign and parse-jd → `campaign:write`; delete campaign → `campaign:delete`; uploads and retry → `resume:upload`; application patch and bulk stage/reject → `application:review`; deletes → `application:delete`; export → `application:export`.
- `Campaign` gains `client {id, name}` and `created_by {id, display_name} | null`. `CampaignCreate` gains a required `client_id` (an active client in the user's scope; 422 otherwise). `CampaignUpdate` may move a campaign to another client (requires both clients in scope).
- `GET /campaigns`, `GET /home` and `GET /stats` accept a repeatable `client_id` filter, intersected with the user's scope.
- `Resume` gains `uploaded_by {id, display_name} | null`.
- The OpenAPI snapshot, every v2 contract snapshot that shows a campaign, and the generated TypeScript client are updated in the PRs that change them.

## 9. Frontend

### 9.1 Routes

| Route | Screen |
|---|---|
| `/login` | Email + password, "Continue with Google / Microsoft" (from `listAuthProviders`), "Forgot password?". Shows `reason=expired` and `error=no_account` messages. |
| `/forgot-password` | Email field; always the same confirmation ("If an account exists, we've sent a link"). |
| `/reset-password` | Reads `#token`; new password twice; on success → `/login` with a confirmation. |
| `/accept-invite` | Reads `#token`; preview (agency, inviter, role); new user: name + password or OIDC; existing user: "Sign in to accept" then accept. Expired → explanation and "ask for a new invite". |
| `/select-agency` | For a user with several memberships and none active. |
| `/settings` | Becomes a section with tabs: **Profile** (name, password), **Team** (admin+), **Clients** (admin+), **Agency** (owner). |

The auth pages live in an `(auth)` route group with a centred, shell-less layout. The product routes move into an `(app)` group whose layout renders the app shell. The token pages set `<meta name="referrer" content="no-referrer">`. The pages use the E1 design system (react-hook-form + zod; 422 field errors map inline; 429 shows the retry time).

### 9.2 App shell

- **Agency switcher** in the sidebar header (the slot E1 left): the agency name, plus a menu of the user's agencies when they have more than one. Choosing one calls `setActiveAgency`, clears the Query cache and goes to `/`.
- **Client switcher** next to it: "All clients" or one client (only the user's clients). It writes the `client` URL parameter on Home and the campaign list (ADR 0007: URL state) and remembers the last choice per agency in `localStorage` as the default when the parameter is absent. The Talent list ignores it (agency-wide).
- **User menu** in the sidebar footer: name, email, role badge, Profile, theme, **Sign out**.
- **Permission-aware UI:** `usePermissions()` reads `Me.permissions`. Actions the user lacks are hidden (Upload, Delete, Export, New campaign, Settings tabs), never just disabled without explanation. The server enforces every rule anyway.

### 9.3 Campaigns and Settings

- **Campaigns:** a client column and filter in the list; a required client select (preselected from the switcher) in the new-campaign form, with "New client" inline for admins; the client on the workspace header and in Settings (move to another client).
- **Settings → Team:** a members table (name, email, role select, client access, status, last active) with an **Invite** dialog (email, role, all clients or a client multi-select), pending invitations with Resend and Revoke, and Deactivate / Reactivate with a confirmation. Rows the user may not change are read-only. The last owner's row explains why it cannot be changed.
- **Settings → Clients:** a list with create, rename and archive. Archived clients are hidden from pickers but still shown on their campaigns.

### 9.4 Client and data layer

- **Credentials:** the generated client's fetch uses `credentials: "include"`, the upload `XMLHttpRequest` sets `withCredentials = true`, and `EventSource` is opened with `{ withCredentials: true }`.
- **CSRF middleware** (openapi-fetch `onRequest`): adds `X-CSRF-Token` to unsafe methods, from `Me.csrf_token` (in the Query cache) or the cookie. The upload request adds it too.
- **401 handling:** the problem middleware turns a `401` into a sign-out. It clears the Query cache and sends the browser to `/login?next=<path>&reason=expired`, except on the auth pages. A `403` is a problem toast, or the view's "You don't have access" state.
- **Auth gate:** the `(app)` layout renders an `AuthGate` that loads `getMe` before the shell. A Next.js `proxy.ts` (Next 16's renamed middleware) only redirects to `/login` when no session cookie is present, as an optimisation; the API's answer is what counts.
- **Cache hygiene:** `queryClient.clear()` on logout, agency switch and 401, so no data of one agency or user is ever shown to another.

## 10. Security checklist

ASVS 4.0.3 references in brackets. Every item has a test (§11) or a review step in E2-11.

**Authentication**
- [ ] Passwords hashed with Argon2id; rehash on login when parameters change [V2.4.1].
- [ ] Minimum length 12, maximum 128, no composition rules, no truncation, pasting allowed [V2.1.1–V2.1.9].
- [ ] Login, reset and invitation endpoints rate-limited per identifier and per IP; throttling, not lockout [V2.2.1].
- [ ] **No account enumeration:** one message for unknown email, wrong password and disabled user; equal timing via a dummy hash; reset always 202; emails sent asynchronously.
- [ ] Reset and invitation tokens: 256-bit random, stored hashed, single use, short-lived, delivered in the URL fragment [V2.3.1, V2.5.x].
- [ ] Password change requires the current password; reset and change revoke other sessions and notify by email [V2.1.6, V2.2.3, V2.5.x].
- [ ] OIDC: authorization code + PKCE (S256), `state` and `nonce` checked, ID token signature, `iss`, `aud`, `exp` verified; Microsoft issuer per `tid`; accounts linked by `(provider, issuer, subject)`, never by an unverified email (nOAuth) [OAuth 2.0 Security BCP, RFC 9700].
- [ ] No open redirect: `next` is a relative path only.

**Sessions**
- [ ] Opaque 256-bit session ids, stored hashed server-side [V3.2.2, V3.2.3].
- [ ] **Session fixation:** a new id on every authentication; any session cookie sent with a login request is revoked [V3.2.1].
- [ ] Cookie flags: `__Host-` prefix, `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`, no `Domain` [V3.4.1–V3.4.5].
- [ ] Idle and absolute timeouts enforced server-side; logout invalidates the session server-side [V3.3.1, V3.3.2].
- [ ] Role, membership and agency status re-checked on every request; long-lived SSE streams re-check every 60 s.

**Access control**
- [ ] Deny by default: a v2 route without a declared permission fails start-up [V4.1.1, V4.1.5].
- [ ] Permission matrix tested for every role × endpoint; client scope tested [V4.1.3, V4.2.1].
- [ ] Other-agency and out-of-scope objects are 404 (no IDOR, no existence leak) [V4.2.1].
- [ ] Tenant isolation enforced by RLS with a non-owner, non-superuser, non-`BYPASSRLS` app role; checked at start-up [V4.1.x].
- [ ] Privilege changes (role, client access, invitations) are limited by the granter's own role; the last owner cannot be removed.

**CSRF and browser**
- [ ] Signed double-submit token bound to the session on every unsafe method, plus an `Origin` check [V4.2.2, V13.2.3].
- [ ] CORS: explicit origins with credentials, never `*`; minimal allowed headers [V14.5.3].
- [ ] Security headers on the API (`X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` on auth routes, `Cache-Control: no-store` on authenticated responses) [V14.4.x].

**Secrets and configuration**
- [ ] `SECRET_KEY` (CSRF HMAC, OIDC cookie encryption) required, at least 32 bytes and not the template value outside development; the settings refuse to start otherwise [V2.10.4, V6.4.1].
- [ ] OIDC client secrets and SMTP credentials from the environment only; `.env.example` has placeholders [V2.10.4].
- [ ] No secrets, tokens, session ids or passwords in logs [V7.1.1, V7.1.2].
- [ ] Security events recorded in `audit_events`; append-only by grant [V7.1.3, V7.1.4].
- [ ] Flower's default `admin:admin` basic auth and MinIO's default credentials stay development-only (E5 replaces them); listed in the E5 notes.

## 11. Testing

### 11.1 Test infrastructure changes

- The session creates `hireflow_owner` and `hireflow_app` in the testcontainer, runs `alembic upgrade head` as the owner, and builds `db_engine` and the workers' sync engine as `hireflow_app`. **No test talks to tenant tables as a superuser**, so RLS is exercised by the whole suite, not only by the isolation tests.
- New fixtures: `agency` (the test's agency, bound to `db_session`), `other_agency` (a second, populated agency), `principal(role=…, clients=…)`, and `client_as(role)` (an `AsyncClient` with a real session cookie and CSRF header for that role; the existing `client` fixture acts as an owner of the test agency). The factories take the agency from the bound session.
- Most contract tests override `current_principal` with an owner of the test agency, to keep them short. The auth tests use real cookies end to end.
- `throwaway_database` creates its databases with the same two roles.

### 11.2 Tenant-isolation suite (`tests/tenancy/`)

| Test | What it proves |
|---|---|
| `test_catalogue` | Every table not on the global allowlist (§6.4) has `relrowsecurity` and `relforcerowsecurity` and a `tenant_isolation` policy for `hireflow_app`. `hireflow_app` is not superuser, has no `BYPASSRLS` and owns nothing. Grants match §6.2, and `audit_events` has no UPDATE or DELETE. |
| `test_unbound_session_sees_nothing` | Two agencies' data in every tenant table; a session with no tenant reads 0 rows from each and cannot insert. |
| `test_forgotten_filter_is_contained` | **The backstop test.** Raw ORM queries with **no `agency_id` filter** (`select(Model)` for every tenant model, plus one deliberately unfiltered copy of a real repository query) on a session bound to agency A return only A's rows. `UPDATE` / `DELETE` without a filter touch only A's rows. Inserting a row with B's `agency_id` is rejected by `WITH CHECK`. |
| `test_tenant_does_not_leak_across_pool_checkouts` | Bind A, commit, return the connection to a 1-connection pool; the next checkout (unbound) has an empty `app.agency_id` and sees nothing. A session that commits and continues still has the setting in its second transaction. |
| `test_api_sweep` | Generated from the v2 OpenAPI document, so new endpoints are covered automatically. As an owner of A, call every `GET` (lists and the A-shaped detail URLs) and assert that **no id of B** appears anywhere in any response body. Then call every endpoint with B's ids in the path: all `404`. |
| `test_celery_tenant` | A task run with agency A for B's resume finds nothing and changes nothing; a task without `agency_id` is rejected (after E2-04); `on_failure` writes under the task's agency. |
| `test_sse_tenant` | A's user cannot open B's campaign stream (404); the channel names include the agency; a revoked session closes the stream at the next check. |
| `test_storage_keys` | New uploads are stored under `agencies/{agency_id}/`. |
| `test_matching_per_agency` | The same email in two agencies yields two People; the concurrency test (20 parallel resolutions) runs in two agencies at once without cross-agency waits. |

### 11.3 Authentication and authorization tests

- **Unit:** password policy and hashing parameters, token generation and hashing, the CSRF token sign and verify, cookie attributes, the `next` validation, the OIDC ID-token validation (signed with an in-test key pair and JWKS: wrong `iss`, `aud`, `nonce`, expired, Microsoft `tid` mismatch, missing `email_verified` / `xms_edov`).
- **Flows (integration, real cookies):** login success and failure with identical bodies; fixation (a pre-set cookie is not reused); logout and replay → 401; idle and absolute expiry (frozen time) → 401 with the cookie cleared; rate limit → 429 with `Retry-After`; CSRF missing, mismatched, from another session, or with a foreign `Origin` → 403; invitation create → preview → accept (new and existing user, expired, revoked, replaced); password reset (unknown email gives the same 202 and sends nothing; token single use; sessions revoked); OIDC start → callback against a fake provider (in-process discovery, JWKS and token endpoint), with linking rules 1–4 of §4.3.
- **Authorization matrix:** parametrized over every role × every v2 operation, expecting allow or `403` from `ROLE_PERMISSIONS`. Plus client scoping: lists filtered, out-of-scope ids `404`, a Person's applications filtered, campaign creation outside scope `422`. Team guard rails: last owner, self-change, an admin managing an owner.
- **Audit:** each flow asserts its `audit_events` row.
- **Migration tests:** expand → backfill → verify → contract with E0-style seeded data in the default agency (`tests/migrations/test_tenancy_e2_0x.py`); upgrade → downgrade → upgrade for each new migration. The CI `migrations` job runs `alembic check` as before.
- **Query counts:** `test_query_counts.py` keeps its bounds, plus at most one extra query per request (the principal lookup).

### 11.4 End-to-end (Playwright)

- The E2E stack seeds two agencies with one user per role (known development passwords, printed by the seed; refused when `APP_ENV=production`), runs Mailpit, and sets `AI_PROVIDER=fake`.
- A setup project logs in through the UI once per role and saves `storageState`. The hero flow runs as a recruiter.
- New specs:
  - **login and logout** (wrong password message, then success, sign out, back button does not show data);
  - **invitation** (an admin invites, the test reads the email from Mailpit's API, accepts as a new user, lands in the agency);
  - **password reset** through Mailpit;
  - **viewer** (no Upload, Delete, Export or New campaign; a direct URL to a write action gets the no-access state);
  - **isolation** (agency B's user sees none of agency A's campaigns, and A's campaign URL is a not-found page);
  - **client scoping** (a scoped recruiter sees only their client's campaigns).
- axe runs on the new pages. OIDC is not exercised in E2E (no real providers in CI); the fake-provider integration tests cover it.

## 12. Plan (two sprints after E1 closes)

| Ticket | Title | Size |
|---|---|---|
| E2-01 | Tenancy expand: `agencies` and `clients`, nullable `agency_id` / `client_id`, default agency and client, writes set them | M |
| E2-02 | Tenancy backfill, verify and contract: NOT NULL, per-agency uniques, composite FKs, indexes, per-agency matching | M |
| E2-03 | Tenant context: tenant-bound sessions, explicit repository filters, `AccessScope`, Celery `agency_id`, SSE channels and storage keys per agency | L |
| E2-04 | Row-level security: roles, grants, `ENABLE` + `FORCE` policies, app role everywhere, start-up check, tenant-isolation suite | L |
| E2-05 | Identity core: users, memberships, sessions, Argon2id login and logout, `/me`, CSRF, rate limiting, audit events; v2 requires a session | L |
| E2-06 | Authorization: permission matrix on every v2 route, client scoping, clients API, campaigns get a client | M |
| E2-07 | Invitations, team management API, password reset, email (Mailpit) | M |
| E2-08 | Sign in with Google and Microsoft (OIDC + PKCE) | M |
| E2-09 | Frontend auth: login, forgot and reset password, accept invite, auth gate, credentials + CSRF in the client, 401 handling, user menu | M |
| E2-10 | Frontend tenancy and team: agency and client switchers, client on campaigns, Settings → Profile, Team, Clients, Agency | L |
| E2-11 | E2E and security pass: Playwright auth, invitation, roles and isolation; ASVS checklist review; runbook and README | M |
| E2-12 | Remove `/api/v1` | S |

**Order:**
- **Sprint 7 (data and isolation, then identity):** E2-01 → E2-02 and E2-03 (parallel) → E2-04; E2-05 can start after E2-03.
- **Sprint 8 (product surface):** E2-06 and E2-07 after E2-05 (parallel); E2-08 after E2-07; E2-09 after E2-05 and E2-07; E2-10 after E2-06 and E2-09; E2-11 after E2-08 and E2-10; **E2-12 last**.

### E2-01 · Tenancy expand
**Size:** M  ·  **Branch:** `e2-01-tenancy-expand`  ·  **Depends on:** E1-10 merged

**Goal:** Add the tenant key to the schema without breaking anything, and make every write set it.

**Acceptance criteria**
- [x] `identity` module skeleton (models, service, `agencies` table) with import-linter: added to `leaf-modules`, `llm-sdk-only-in-ai` and the module lists; `TABLE_OWNERS` updated.
- [x] Expand migration: `agencies`, `clients` (hiring), nullable `agency_id` on the seven existing tenant tables, `campaigns.client_id` / `created_by_user_id`, `resumes.uploaded_by_user_id`; inserts the default agency and default client. Upgrade → downgrade → upgrade is tested.
- [x] Every insert path (campaigns, applications, People and their identifiers, conflicts, `person_resumes`, resumes, the seed) sets `agency_id = DEFAULT_AGENCY_ID`; new campaigns get the default client.
- [x] All v1 snapshots and v2 contract snapshots are unchanged (no API change yet).

**Concept to learn:** expand/contract for a new tenant key; metadata-only `ADD COLUMN`.

**Notes (done):** migration `e2a01d5c8f36`. Both transition ids are in `app.platform.tenancy`: `DEFAULT_AGENCY_ID` (`…a001`, "Default agency", slug `default`) and `DEFAULT_CLIENT_ID` (`…c001`, "General" per §15 Q7). The client id sits next to the agency id because the E2-02 backfill needs it and `identity` may not import `hiring`. `clients` is new, so its `agency_id` is `NOT NULL` and its uniques (`(agency_id, id)`, `(agency_id, lower(name))` as a unique index) exist already; no composite FK or index on the existing tables yet (E2-02). Rows written before E2-01 keep `agency_id` NULL, and so do they when the pipeline re-scores them (the upsert sets it on insert only): the backfill fills them. Rehearsed on the Compose-size seed written by the E0-20 code: the upgrade rewrites no table, `verify_people_applications` passes, new writes carry the default agency.

### E2-02 · Tenancy backfill, verify and contract
**Size:** M  ·  **Branch:** `e2-02-tenancy-contract`  ·  **Depends on:** E2-01

**Goal:** Every existing row belongs to the default agency, and the schema guarantees `agency_id` from now on.

**Acceptance criteria**
- [x] `python -m app.modules.ingestion.backfill_tenancy`: batched, keyset-paginated, resumable, idempotent; a second run changes nothing (tested).
- [x] `verify_people_applications` gains `tenancy_complete`, `cross_module_same_agency` and `clients_same_agency` (§7.3) and passes on the Compose seed.
- [x] Contract migration (§7.1 phase 4): NOT NULL via validated CHECK; `UNIQUE(agency_id, id)` targets and composite FKs (`NOT VALID` → `VALIDATE`); `person_identifiers` unique becomes `(agency_id, kind, value_normalized)`; `(agency_id, …)` indexes including `resumes (agency_id, campaign_id)`; index builds `CONCURRENTLY`.
- [x] `resolve_person` matches and locks per agency (`agency_id:kind:value` keys). The concurrency test passes 20 runs in a row with two agencies.
- [x] A live rehearsal on the Compose stack with seed data: backfill while uploading, verify, contract; the results are in the PR.

**Concept to learn:** `NOT VALID` constraints, `CREATE INDEX CONCURRENTLY`, `ADD CONSTRAINT … USING INDEX`.

**Notes (done):** migration `e2b02c7a4d19`; runbook [tenancy-backfill.md](../runbooks/tenancy-backfill.md). The backfill needs no checkpoint table: each batch selects the next rows still missing their agency after the previous batch's last key, so a new run starts where the last one stopped. Each module fills its own tables (`hiring.service` and `talent.service` `fill_tenancy_batch`, ingestion's repository) and `updated_at` is left alone. A batch runs with `lock_timeout` 2 s and is retried on a lock timeout or deadlock. The contract refuses to run while any row has no agency, and every step checks the catalog first, so after a half-way failure running it again finishes the job. The composite keys replace the single-column ones (`(agency_id, campaign_id) -> campaigns`, `(agency_id, …person_id) -> people` from the three talent tables) and add `(agency_id, client_id) -> clients`; `person_resumes.conflict_id` stays single-column (backlog). `resolve_person` takes the agency as a parameter, which must be its session's: since the merge with E2-03 the callers pass `agency_id_of(session)`, and its lookups (`find_identifiers`, `lock_person`, the stored resolution) go through `tenant_filter`, the filter E2-03 left out of matching until the per-agency unique existed; the concurrency tests add a 20-worker run across two agencies and a no-cross-agency-wait test, and passed 20 runs in a row in a Linux container (on the Windows host the 20-worker tests sometimes fail to open connections through the Docker Desktop port proxy under load, as on `master`; backlog). Until the contract the default agency also matches identifiers that have no agency yet (`tenant_filter`'s transition branch), so uploads during the backfill find existing People (backlog, with E2-04). Rehearsed on a throwaway Compose project seeded by the pre-E2-01 code (15 campaigns, 1,125 resumes, 808 people), then migrated to E2-01: the backfill ran in 1.9 s at 25 rows per batch, with 40 uploads running at the same time; a second run filled 0 rows; verification passed; the contract took 2 to 8 s (wall clock, Alembic start-up included) with 30 uploads running, and nothing failed.

### E2-03 · Tenant context
**Size:** L  ·  **Branch:** `e2-03-tenant-context`  ·  **Depends on:** E2-01

**Goal:** Every database session, task, stream and stored file knows its agency, explicitly and in one way.

**Acceptance criteria**
- [x] `app.platform.tenancy`: `TenantContext`, `AccessScope`, `bind`, `agency_id_of` (raises when unbound), and the `after_begin` listener that runs `set_config('app.agency_id', …, true)` at every transaction start of a bound session (async and sync). A CI check rejects session-level `SET app.`.
- [x] Every repository query on a tenant table filters by `agency_id_of(db)` and every insert sets it (replacing E2-01's constant). Services that list or fetch campaign data take an `AccessScope` (all clients until E2-06).
- [x] v2 routers use `tenant_db` instead of `get_db`. Until E2-05 it binds the default agency; v1 binds the default agency until E2-12.
- [x] Celery: `TenantTask` base class; `extract_resume` and `process_candidate_ai` take `agency_id`; failure handlers use the task's agency; a message without `agency_id` falls back to the default agency (removed in E2-04); `reset_stuck` re-enqueues with each row's agency.
- [x] Redis channels `agency:{agency_id}:campaign:{campaign_id}:events`; storage keys `agencies/{agency_id}/campaigns/{campaign_id}/resumes/{resume_id}.pdf`; `storage_orphans` scans the old and new prefixes.
- [x] structlog binds `agency_id` on requests and tasks. All snapshots unchanged except the recorded storage keys (listed in the PR).

**Concept to learn:** transaction-scoped settings, SQLAlchemy session events, carrying context through a task queue.

**Notes (done):**
- **Where the agency is bound.** `app.platform.tenancy` holds the types, `bind` (once per session, before its first transaction), `agency_id_of`, `tenant_filter`, `require_scope` and the `after_begin` listener on `Session` (`set_config('app.agency_id', :id, true)` with bind parameters). HTTP: `app.platform.request_tenant` gives `tenant_db` / `access_scope` (v2, from `request_tenant()`: the default agency until E2-05, which moves them to `identity.dependencies`) and `default_agency_db` / `default_agency_scope` (v1 until E2-12); both build on `get_db`, so test overrides keep working, and both bind `agency_id` into the structlog context. SSE: the stream checks the campaign in one bound transaction, ends it, then subscribes with the scope's agency; v1's stream subscribes with the default agency. Celery: `app.platform.celery_app.TenantTask` (validate in `before_start`, structlog bind/unbind, `tenant_session(agency_id)`); `extract_resume` and `process_candidate_ai` take `agency_id` as a keyword, the failure handlers and the quota and failed-step writers open sessions bound to the task's agency, and an invalid `agency_id` fails the task without writing. `reset_stuck` re-queues with each row's agency (`NULL` → default); `reset_stuck`, `storage_orphans` and the verification read every agency through unbound sessions.
- **Rows without an agency before the backfill.** E2-03 can merge before E2-02, so `tenant_filter` matches `agency_id = :bound OR agency_id IS NULL` **while the default agency is bound** (only then; another agency never sees NULL rows). After E2-02's contract the branch matches nothing; E2-04 removes it (backlog). Tested in `tests/tenancy/test_repository_filters.py`.
- **Not filtered yet, on purpose:** `talent`'s matching lookups (`find_identifiers`, `lock_person`) and the advisory-lock keys. They become per agency in E2-02 together with the per-agency unique on identifiers; filtering the lookup before that constraint exists would make a second agency's insert collide with the global unique. The resolution lookup and every people read are filtered.
- **`AccessScope`.** Hiring checks it (`hiring.service.check_scope`) in every use case that lists or fetches campaign data, and ingestion's campaign use cases go through hiring for it. A client-limited scope raises `NotImplementedError` until E2-06, rather than being widened to all clients.
- **Snapshots.** Only the expected two kinds changed: new upload keys (`agencies/<default-agency>/campaigns/<uuid>/resumes/<uuid>.pdf`, no file name) and the Celery calls' `kwargs` (`{'agency_id': '<default-agency>'}`). The masker names the fixed default agency id instead of numbering it, so no other placeholder moved. The v2 OpenAPI document and the generated client are unchanged.
- **CI check:** `tests/tenancy/test_no_session_level_setting.py` rejects `SET [SESSION] app.…` and `set_config('app.…', …, false)` in `backend/app`, `backend/alembic`, `scripts/` and `docker/`. The debug pool-checkout assertion of §6.3 is left to E2-04 with its pool-checkout test (backlog); `tests/tenancy/test_session_binding.py` already shows a one-connection pool hands out a clean connection after a bound session commits or rolls back.
- **Deploy note:** events published on the old channel name (`campaign:<id>:events`) by a worker still running the previous code reach no stream; the page catches up on its next fetch.

### E2-04 · Row-level security
**Size:** L  ·  **Branch:** `e2-04-rls`  ·  **Depends on:** E2-02, E2-03

**Goal:** The database itself refuses to return or accept another agency's rows, even when the code forgets a filter.

**Acceptance criteria**
- [x] Roles `hireflow_owner` and `hireflow_app`: Compose init script and `make db-roles` (idempotent); `MIGRATION_DATABASE_URL` for Alembic and the operator scripts; the API and workers connect as `hireflow_app`.
- [x] Migration: `app_current_agency()`, grants and default privileges, `ENABLE` + `FORCE` RLS, `tenant_isolation` and `maintenance` policies on every tenant table (§6.2); `audit_events` grant without UPDATE or DELETE (applied when the table arrives in E2-05).
- [x] Start-up check: the API and the workers refuse to run as a superuser, a `BYPASSRLS` role or a table owner.
- [x] Celery messages without `agency_id` are rejected; the runbook (`docs/runbooks/tenancy-rls.md`) covers draining queues, enabling, verifying and rolling back.
- [x] Test infrastructure on the app role (§11.1); the whole suite passes under RLS; the tenant-isolation suite (§11.2) is in place, including `test_forgotten_filter_is_contained` and the pool-checkout test.
- [x] v1 snapshots and v2 contract snapshots are unchanged.

**Concept to learn:** Postgres RLS (`USING` vs `WITH CHECK`, `FORCE`, policies per role), role design, connection-pool safety.

**Notes (done):** migration `e2c04a7f3b18` (after E2-05's `e2c05b3f7a21`; the two tickets were merged before E2-04 landed); runbook [tenancy-rls.md](../runbooks/tenancy-rls.md).
- **Roles.** `docker/postgres/roles.sql` is the one source: it creates both roles if missing, resets their attributes and passwords on every run, makes `hireflow_owner` the owner of the `public` schema and hands it every relation, function and type there (an existing volume's tables belong to the bootstrap superuser). Psql runs it from `docker/postgres/init/10-roles.sh` (a `.sh`, not the `10-roles.sql` of §7.2, because the passwords come from the environment), on a new volume at initdb and on an existing one through `make db-roles`; the test harness and the CI `migrations` job run the same file. Compose builds `DATABASE_URL`, `SYNC_DATABASE_URL` (`hireflow_app`) and `MIGRATION_DATABASE_URL` (`hireflow_owner`) for the backend services from `POSTGRES_DB` and `HIREFLOW_OWNER_PASSWORD` / `HIREFLOW_APP_PASSWORD` (development defaults), overriding the URLs of a `.env` written before E2-04, so an existing `.env` needs no edit.
- **The migration cannot create the roles** (§7.1 phase 5 says "if missing"): it runs as `hireflow_owner`, which has no `CREATEROLE`. It refuses, changing nothing, unless the roles exist, it runs as `hireflow_owner` and that role owns every table, naming `make db-roles`. Policies come from `app.platform.rls.enable_tenant_rls` (the §6.2 helper); the global allowlist is `app.platform.rls.GLOBAL_TABLES` (`agencies`, `legacy_candidate_map`, `alembic_version`), and `catalogue_problems` checks everything §11.2 `test_catalogue` lists, also as `python -m app.devtools.check_rls` and in the CI `migrations` job (with a downgrade and upgrade). E2-05's five identity tables are already on the allowlist (a listed table may not exist yet) and `audit_events` is expected at `SELECT, INSERT` only (`APP_PRIVILEGE_EXCEPTIONS`); the migration keeps it there if E2-05's ran first, and its downgrade leaves the identity tables' grants to their own migration.
- **Start-up check.** `app.platform.rls.check_app_role` in the API's lifespan and the workers' `worker_init` (a `SystemExit`: Celery swallows a signal handler's exceptions). Beyond §6.2 it also refuses a role that is a *member* of a table's owner, since a policy granted to a role applies to its members. No escape hatch, in any environment. Consequence for §7.1's rollback ("point the URLs back at the owner"): the E2-04 code refuses the owner, so the runbook rolls back by downgrading with the E2-04 code and then running the previous release (which connects as the bootstrap superuser).
- **Operator commands** (`reset_stuck`, `storage_orphans`, `verify_people_applications`, `backfill_tenancy`, `check_rls`) use `app.platform.maintenance.owner_session()`. The seed stays on the app role, bound to the default agency (RLS limits its `--reset` to that agency).
- **Celery.** `TenantTask.before_start` raises `Reject(requeue=False)` for a message without `agency_id` (logged `task_without_agency_id`), so neither the body nor the failure handlers run; an invalid id still fails the task without a write.
- **Tests.** The session runs `roles.sql` in the testcontainer, migrates as the owner, and gives `DATABASE_URL` / `SYNC_DATABASE_URL` to `hireflow_app`; `throwaway_database` and `scratch_database` hand out owner engines (setup across agencies, like the operator commands) and `as_role` the app role's URL for the code under test. A test's sessions share one rolled-back connection, so `tests/tenant_sessions.py` keeps each session's statements under its own agency (it re-applies `app.agency_id` when another session ran last, on the DBAPI cursor so the statement-count tests are unaffected); another agency's rows are written through a session bound to it. Tests that made `agency_id` nullable to imitate E2-01, and the `tenant_filter` transition tests, are gone with the branch. New: `tests/tenancy/test_isolation.py` (catalogue, unbound session, `test_forgotten_filter_is_contained`, the pool checkouts and the development guard), `test_api_sweep.py` (§11.2's sweep over every v2 operation from the OpenAPI document), `test_startup_role_check.py`, `test_operator_commands.py`, and `tests/migrations/test_tenancy_e2_04.py` (upgrade → downgrade → upgrade, default privileges, the refusals, `roles.sql` on a pre-E2-04 database).
- **Pool checkouts.** `tenancy.guard_pool_checkouts` (the §6.3 assertion) is on the API's, the workers' and the owner's engines when `APP_ENV` is `development` or `test`.

### E2-05 · Identity core
**Size:** L  ·  **Branch:** `e2-05-identity-core`  ·  **Depends on:** E2-01, E2-03

**Goal:** People sign in with email and password into a secure server-side session, and `/api/v2` requires one.

**Acceptance criteria**
- [x] Tables `users`, `memberships`, `membership_clients`, `user_sessions`, `audit_events` (global, §6.4).
- [x] Argon2id hashing (`argon2-cffi`), the password policy, `POST /auth/login`, `POST /auth/logout`, `GET /auth/csrf`, `GET/PATCH /me`, `POST /me/password`, `PUT /me/active-agency`, `GET/PATCH /agency` (§8.2), with session rotation, idle and absolute expiry, throttled `last_seen_at` writes.
- [x] CSRF middleware (signed double-submit + `Origin`) on the v2 app; CORS headers narrowed; security headers.
- [x] Rate limiter (`app.platform.ratelimit`) on login; `429` with `Retry-After`.
- [x] `current_principal`, `tenant_db` from the session's agency; every v2 route requires a session. A temporary `AUTH_REQUIRED=false` (refused outside `development`/`test`) lets the frontend and E2E work as the default agency's owner until E2-09.
- [x] `python -m app.modules.identity.cli create-agency --name … --owner-email …` and `create-owner` for the default agency; the seed creates two agencies and one user per role.
- [x] Audit events for the flows in this ticket; the auth flow tests of §11.3 for login, logout, fixation, expiry, CSRF and rate limits; the OpenAPI document declares the security schemes and 401/403.
- [x] `SECRET_KEY` validation (§10); ADR 0010 accepted.

**Concept to learn:** session management, cookie security attributes, CSRF defences, password hashing.

**Notes (done):**
- **Schema.** Migration `e2c05b3f7a21` (after `e2b02c7a4d19`) creates the five tables; nothing existing changes. `users.email` is stored trimmed and lower-cased (a CHECK), so `UNIQUE(email)` is case-insensitive. `user_sessions.active_agency_id` and `memberships.invited_by_user_id` are same-module foreign keys (`ON DELETE SET NULL`); `audit_events` has none (an event outlives what it describes) and a bigint identity `id`. **Grants** are in a `DO` block that runs only when the role `hireflow_app` exists (E2-04 creates it): the four read-write tables get `SELECT, INSERT, UPDATE, DELETE`, and `audit_events` gets `REVOKE ALL` then `SELECT, INSERT` (plus `USAGE` on its sequence), which also undoes E2-04's default privileges for that table. Tested with a stand-in role and default privileges (`tests/migrations/test_identity_e2_05.py`).
- **Where things live.** `app.modules.identity.dependencies` has `current_session`, `current_principal`, `tenant_db`, `access_scope` and the `public` marker. A new import-linter contract (`identity-dependencies-http-only`) lets only `app.v2`, `app.api.v2` and the modules' `router_v2` import it, and the two interface contracts ignore exactly those imports. `app.platform.request_tenant` keeps v1's default-agency binding (and `bind_request`, which both use). Cookies, CSRF, the rate limiter and the security headers are platform modules (`auth_cookies`, `csrf`, `ratelimit`, `security_headers`); identity imports them, never the reverse.
- **Sessions.** The principal lookup is one query (session, user, the membership in the active agency, that agency, and the membership's client ids as a correlated `array_agg`), in a short transaction of its own on the request's session, committed before `tenant_db` binds it; a signed-in request costs exactly one query more (tested). User, membership and agency status are read on every request. `last_seen_at`, `idle_expires_at` and `memberships.last_active_at` are written at most every 5 minutes, and the idle deadline never passes the absolute one. An ended, revoked or unknown session is a `401` that clears the cookie. Sign-in picks the only membership, else the most recently used one (`memberships.last_active_at`), else none (`403` *"Choose an agency"* on agency-scoped routes until `PUT /me/active-agency`). A password change revokes every session of the user and continues in a new one (new cookies).
- **CSRF, one refinement of §4.5.** The token is required on unsafe methods whenever the request **carries a session cookie**, and always under `/auth/` (login CSRF). A request without a session cookie carries no credential: it gets the endpoint's `401`, or, with `AUTH_REQUIRED=false`, acts as the fallback owner, which the Origin check still guards against other sites. This keeps the cookie-less frontend working until E2-09 without a CSRF exemption. The Origin check also accepts the API's own origin (Swagger UI on `/api/v2/docs`). The token's key is derived from `SECRET_KEY` (an HMAC with a label). `/me` returns the CSRF cookie's token when it is valid for the session, else a new one (sent as the cookie too), so header and cookie always match.
- **`AUTH_REQUIRED=false`** (development and test only, refused elsewhere at start-up) applies to requests **without** a session cookie: they act as `DEV_FALLBACK`, an owner of the default agency with no user (`/me` and the other session endpoints stay `401`). A request with a cookie is always checked. The default is `true`. `.env.example`, the dev Compose backend (`${AUTH_REQUIRED:-false}`, so an existing `.env` needs no edit) and the E2E stack set `false`. E2-09 removes the setting.
- **Rate limits.** Login: 30 attempts per IP and 5 failures per email per 15 minutes (settings), checked before any password work, keyed by HMACs of the IP and the normalised email; a success clears the email's failures; `auth.rate_limited` is written once per window. Wrong current passwords on `POST /me/password` count as failures of the user's email too. If Redis is unreachable the limiter fails open and logs `ratelimit_unavailable` (sign-in must not depend on a cache; Argon2 still bounds guessing).
- **Audit actions** written: `auth.login_succeeded`, `auth.login_failed` (`reason`: `unknown_email`, `wrong_password`, `user_disabled`, `no_password`, `no_agency_access`; an email hash, never the address), `auth.logout`, `auth.rate_limited`, `auth.agency_switched`, `auth.password_changed`, `agency.updated`, and for the operator command and the seed `agency.created`, `team.member_added` and `team.member_role_changed` (`details.by = "operator"`).
- **Left for later tickets, on purpose:** `PATCH /agency` checks "owner" inline until E2-06's permission matrix; `Me` has no `permissions` field yet (E2-06 adds it with `ROLE_PERMISSIONS`); a client-limited membership can only come from SQL (the CLI and the seed give every client), and its scope still raises `NotImplementedError` in hiring until E2-06. Campaigns created in an agency other than the default one fail (they still get the default client) until E2-06 gives agencies their clients. The SSE stream does not re-check the session every 60 s yet (backlog).
- **Snapshots.** v1 characterization snapshots unchanged. The v2 OpenAPI snapshot is re-recorded: the nine new operations and their schemas, the `cookieAuth` / `csrfToken` security schemes, a paragraph on authentication in the description, and on every existing operation its `security` requirement and its `401` / `403` problem responses; nothing else moved (compared key by key). New contract snapshots in `tests/contract_v2/test_identity.py`. The TypeScript client is regenerated; no frontend code changed.

### E2-06 · Authorization and clients
**Size:** M  ·  **Branch:** `e2-06-rbac-clients`  ·  **Depends on:** E2-05

**Goal:** Each role can do exactly what §5.2 says, limited to its clients, and campaigns belong to clients.

**Acceptance criteria**
- [x] `identity.permissions` (`ROLE_PERMISSIONS`); `require(permission)` on every v2 route; the v2 app refuses a route without a permission or a `public` marker.
- [x] Client scoping (§5.3) in hiring and the composed endpoints: lists filtered, out-of-scope objects 404, a Person's applications filtered.
- [x] Clients API (`/clients`), `Campaign.client`, required `CampaignCreate.client_id`, `client_id` filters on campaigns, Home and stats; contract snapshots and the client regenerated.
- [x] The role × operation matrix test and the client-scope tests (§11.3); a test that the §5.2 table matches `ROLE_PERMISSIONS`.

**Concept to learn:** RBAC design, deny-by-default enforcement, IDOR prevention.

**Notes (done):**
- **The matrix.** `app.modules.identity.permissions` holds `Permission` (the 13 names of §5.2, `str` values such as `campaign:write`), `ROLE_PERMISSIONS` (exactly the table), `CLIENT_SCOPED_ROLES` (recruiter, viewer: the "scoped" cells), `has_permission`, `permissions_of` and the 403 wording (*"You don't have permission to create or edit campaigns"*). Client scoping is not a permission: it is the membership's `all_clients` / `membership_clients`, carried as the `AccessScope`. `tests/identity/test_permissions.py` parses this spec's §5.2 table and compares every cell (✅, ✅ scoped, —) with the code.
- **Enforcement.** `identity.dependencies.require_permission(permission)` (the spec's `require`) is a dependency that resolves to the `Principal` after `current_principal` and the role check; the same callable is returned for the same permission. Every v2 route declares exactly one kind of access: one or more permissions, `any_member` (a new marker: a session, no permission: `/me`, `/me/*`, sign-out) or `public`. `app.v2.route_access` refuses to start otherwise, including a route with only a session, `any_member` without one, or two kinds at once; the routers are no longer included with a router-level `current_principal`. The document lists each operation's permissions as `x-permissions` (the generated client ignores it). The bulk endpoint declares `application:review`; its `delete` action also checks `application:delete` in the handler (`ensure_permission`), as §8.3 maps it. `PATCH /agency` lost its inline owner check for `agency:manage`. `Me.permissions` lists the role's permissions in the matrix's order (empty without an active agency) for E2-10's `usePermissions()`.
- **Client scoping.** `AccessScope` gains `allows` and `restricted_to` (the intersection with a request's `client_id` filter: never wider). Hiring's repository keeps every v2 query to the scope (`campaigns.client_id = ANY(:clients)`; applications through `campaign_id IN (SELECT id FROM campaigns …)`; clients by id); with every client in scope the clause is the agency filter alone, so owners, admins and unlimited members run the same SQL as before and the query-count bounds are unchanged. A campaign, application or client outside the scope is the same 404 as another agency's; uploads, resumes, retries, the stream and the export go through `campaign_exists` / the campaign, so they follow its client. Home's resumes in progress and the stats' resume tally take the scope's campaign ids from hiring (`campaign_ids_in_scope`: no query for an unlimited scope, one for a limited one). People stay agency-wide; a Person's profile and the list's `application_count` count only in-scope applications (§15 Q2). The `/api/v1` use cases replace E2-03's `NotImplementedError` with `check_legacy_scope`: v1 serves the default agency's owner only, so a client-limited scope is a `ValueError` there, never widened.
- **Clients and campaigns.** `GET /clients` (`status`, `q`, paged, by name ignoring case) and `GET /clients/{id}` are hiring's (`client:read`); `POST /clients` and `PATCH /clients/{id}` (rename, archive, restore) are composed in `app.api.v2.clients` (`client:manage`) and record `client.created` / `client.updated` (old and new values) in the same transaction. A name in use in the agency (ignoring case, archived ones included) is a 409. `Campaign` gains `client {id, name}`, loaded with the campaign by an inner join on a read-only relationship, so no request gained a query. Campaigns record `created_by_user_id`, and uploads `uploaded_by_user_id`, for signed-in requests. `GET /campaigns`, `/home` and `/stats` take a repeatable `client_id`.
- **Deviation: `CampaignCreate.client_id` is optional.** Omitted (or `null`), the campaign goes to the caller's default client: the oldest active client in their scope ("General" for migrated data). A named client must be an active client in the scope; anything else is a 422 on `client_id` ("Unknown client" for one outside the scope or agency, "This client is archived"), and so is a caller with no active client. This keeps the current new-campaign form working until E2-10 adds the client select. `CampaignUpdate.client_id` moves a campaign under the same rule. `Campaign.created_by` and `Resume.uploaded_by` (§8.3) are not in the resources yet (backlog).
- **Migration** `e2d06b8c4f17` (after E2-07's `e2d07c3f9b12`, data only): every agency without any client gets "General", so the seed's second agency and any agency created with `create-agency` before it can create campaigns (the E2-05 gap). Downgrade keeps the clients. The seed gives its agencies the same (`hiring.ensure_first_client`). An agency created with the CLI afterwards starts without a client (backlog).
- **Tests.** New: `tests/identity/test_permissions.py` (spec table, `EXPECTED` operation → permission from §8.2/§8.3 for every operation, the role × operation matrix with real sign-in for all four roles, `Me.permissions`, bulk delete), `tests/identity/test_client_scope.py` (a recruiter and a viewer limited to one of three clients: lists and the `client_id` filter, a 404 sweep over every operation with a campaign, application or client id, the Person profile, creation and moves, an empty scope, uploads), `tests/contract_v2/test_clients.py`, `tests/migrations/test_clients_e2_06.py`; the API sweep covers `client_id` and the client bodies. The snapshot masker names `DEFAULT_CLIENT_ID` `<default-client>` (as E2-03 did for the agency), so no other placeholder moved.
- **Merged with E2-07** (its PR first, then this one). E2-07's stand-in matrix (`TEAM_PERMISSIONS`, `has_team_permission`, `require_team_permission`) became `ROLE_PERMISSIONS`: the eight `/team` routes declare `team:read` / `team:manage` with `require_permission`, and the service checks again (`check_permission`) and keeps `can_manage` (`team:manage_privileged` when an owner or admin is involved) and the guard rails. The 403 wording is the matrix's (`team:read`: *"You don't have permission to see the team"*, as E2-07 had it). One `ClientRef`; `hiring.client_refs` and `check_grantable_clients` take the caller's `AccessScope`, so nobody grants (or sees the name of) a client outside their own scope. `EXPECTED` covers E2-07's 12 operations (4 public, 2 `team:read`, 6 `team:manage`), and `test_client_scope` adds a recruiter invited for one client, who then sees only it. `e2d06b8c4f17` was re-pointed after `e2d07c3f9b12` (one head).
- **Snapshots.** v1 characterization snapshots unchanged. v2: every `Campaign` shows `client`; `Me` shows `permissions`; the OpenAPI document gains the four client operations and their schemas, `Permission`, `x-permissions` on every protected operation, `client_id` on `CampaignCreate`, `CampaignUpdate` and the three filters (with their 422s), and reworded descriptions. The TypeScript client is regenerated; the frontend's campaign fixtures gained `client`.

### E2-07 · Invitations, team and password reset
**Size:** M  ·  **Branch:** `e2-07-invites-team-reset`  ·  **Depends on:** E2-05 (invitations validate client ids through `hiring.service`, which has clients since E2-01)

**Goal:** An owner can build a team without touching the database, and anyone can recover their account.

**Acceptance criteria**
- [x] `app.platform.email` (console and SMTP backends), Mailpit in Compose, the `identity.send_email` task and templates.
- [x] Tables `invitations`, `password_reset_tokens`; the invitation endpoints, the team endpoints with the guard rails (§5.2) and the reset endpoints (§8.2), composed with hiring for client names.
- [x] Rate limits on reset and invitations; tokens hashed, single use, in the URL fragment; reset revokes sessions and notifies.
- [x] `identity.purge_expired` in a Celery beat schedule.
- [x] Flow tests for invitation (new user, existing user, expired, replaced, revoked) and reset (incl. no enumeration); audit events asserted.

**Concept to learn:** token-based flows, account enumeration, email deliverability basics.

**Notes (done):**
- **Schema.** Migration `e2d07c3f9b12` (after E2-04's `e2c04a7f3b18`) creates the two tables of §3.3, global (§6.4: on `app.platform.rls.GLOBAL_TABLES`, looked up by token hash before any agency is known). Only SHA-256 hashes of the 256-bit tokens are stored (`UNIQUE`). `invitations` also checks a normalised email, the roles, "owners and admins see every client" and "a client list exactly when access is limited" (`all_clients = (cardinality(client_ids) = 0)`); the one-open-invitation rule is the partial unique index of §3.3. No `GRANT`: E2-04's default privileges give `hireflow_app` `SELECT, INSERT, UPDATE, DELETE` on both (tested, with `check_rls` clean, upgrade → downgrade → upgrade in `tests/migrations/test_identity_e2_07.py`).
- **Who may do what.** Until E2-06's matrix exists, every team decision goes through `identity.service.has_team_permission(role, permission)` (the team rows of §5.2, `TEAM_PERMISSIONS`) and `can_manage(principal, target_role)` (`team:manage` for recruiters and viewers, `team:manage_privileged` for owners and admins, as the old and the new role). Each `/team` route also checks `team:read` / `team:manage` in a dependency (`app.api.v2.team.team_permission`); merging E2-06 swaps `TEAM_PERMISSIONS` for `ROLE_PERMISSIONS` and that dependency for `require_permission`. Guard rails: changing or deactivating yourself is a `403`; the last active owner cannot be demoted or deactivated (`409`), and the active owners are row-locked first (in id order) so two owners demoting each other at once cannot both succeed. Only the development fallback owner (`AUTH_REQUIRED=false`) can reach the last owner today, since an owner's own row is refused as a self-change.
- **Composition.** The eight `/team` operations (tag `team`) are in `app.api.v2.team`: identity owns memberships and invitations, hiring checks requested client ids (`hiring.service.check_grantable_clients`: active clients of the agency, `422` on `client_ids`) and names them (`client_refs`, the new `ClientRef {id, name}` in `hiring.schemas_v2`). The four anonymous endpoints are in `identity.router_v2`; `acceptInvitation` takes an optional session (`identity.dependencies.optional_session`: an ended cookie is ignored, not a `401`). The service errors' problem mapping moved to `identity.dependencies.http_error` / `service_errors`, shared by both routers. Status codes: `409` for an active member's address, an existing account accepting without signing in, or accepting into an agency you are already in; `403` for a signed-in user whose email is not the invited one; `410` for every token that is unknown, expired, revoked, replaced or used (one message, so a link says nothing about why).
- **Invitations.** `POST /team/invitations` revokes an open invitation for the same address (an admin cannot replace an owner's or admin's) and emails `{FRONTEND_URL}/accept-invite#token=…` (7 days, `INVITATION_TTL`). Resending gives a new token and a new expiry (it also renews an expired one; the list shows open invitations as `pending` or `expired`). Accepting creates the user (email verified: the token proves the inbox) or uses the signed-in one, creates the membership with its client list (or reactivates a deactivated one), sets `invited_by_user_id`, revokes the session the request carried and starts a new one with the agency active.
- **Password reset.** `202` with one body for every address, after the same limits, lookup and audit write (`auth.password_reset_requested` with a keyed email hash and, for no email sent, `reason`); the email is queued after the commit. A new request deletes the user's unused links. Confirm (`204`) checks the policy before using the link (a refused password leaves it usable), sets the password (OIDC-only users get one), marks the user's links used, revokes every session, clears the cookies, sets `email_verified_at` if empty and emails "your password was changed". E2-05's `POST /me/password` sends that email too now (§10).
- **Email.** `app.platform.email`: `console` (logs and keeps the message in `outbox`; the settings refuse it outside development and test, since it logs the links) and `smtp` (text plus HTML, `STARTTLS` and login optional, envelope recipient exactly the one address). Templates in `identity.emails` keep people's names on one line and escape them in HTML. `identity.send_email` runs on the new `default` queue (`task_default_queue`, the new `worker_default` Compose service), is retried with back-off (12 times, up to 15 minutes apart) and hides its arguments from logs and Flower (`argsrepr` / `kwargsrepr`): the token travels only in the Redis message. Mailpit `v1.33.1` in Compose (`MAILPIT_UI_HOST_PORT` 8025, `MAILPIT_SMTP_HOST_PORT` 1025) and in the E2E override (only its HTTP API, on `E2E_MAILPIT_PORT`, by default the API port + 25; `scripts/e2e.sh` passes `E2E_MAILPIT_URL` to Playwright).
- **Rate limits** (settings, `429` with `Retry-After`, audited once per window): reset 3 per address and 10 per IP an hour, counted whether or not the account exists; 20 per IP per 15 minutes shared by invitation preview, accept **and reset confirm** (the three endpoints that take an emailed token; §4.6 lists the first two); 50 invitation emails per agency per day, new and resent (checked before, counted once the email is queued).
- **Purge.** `identity.purge_expired` (daily at 03:17 UTC, the first `beat_schedule`) deletes sessions that ended (idle, absolute or revoked) more than `IDENTITY_PURGE_AFTER` (30 days) ago, used or expired reset tokens, and invitations that expired more than 30 days ago. It runs as the workers' application role on an unbound session: the tables are global, and the owner role is for commands that span agencies' tenant rows.
- **Audit.** `team.invitation_created` (with `replaced_invitation_id`), `team.invitation_resent`, `team.invitation_revoked`, `team.invitation_accepted` (`new_account`), `team.member_role_changed` (old and new role, `all_clients` and client ids), `team.member_deactivated`, `team.member_reactivated`, `auth.password_reset_requested`, `auth.password_reset_completed` (`sessions_revoked`, `password_added`).
- **Snapshots.** v1 unchanged. The v2 OpenAPI snapshot is re-recorded with only additions: the 12 operations, their 16 schemas and the `team` tag (and the `auth` tag's description); compared key by key. New contract snapshots in `tests/contract_v2/test_team.py` and `test_auth_tokens.py` (the emails included). The TypeScript client is regenerated; no frontend code changed.
- **After pulling:** `docker compose up -d --build` (two new services, `mailpit` and `worker_default`; no new Python dependency) and `alembic upgrade head`. An existing `.env` needs no edit (Compose defaults `EMAIL_BACKEND=smtp`, `SMTP_HOST=mailpit`, `FRONTEND_URL=http://localhost:3000`); set `FRONTEND_URL` if the frontend runs elsewhere (e.g. `npm run dev -- -p 3001`).

### E2-08 · Sign in with Google and Microsoft
**Size:** M  ·  **Branch:** `e2-08-oidc`  ·  **Depends on:** E2-05, E2-07

**Goal:** Users can sign in, and accept invitations, with their Google or Microsoft work account.

**Acceptance criteria**
- [x] `oidc_accounts`; `GET /auth/providers`, `POST /auth/oidc/{provider}/start`, `GET /auth/oidc/{provider}/callback` with Authlib: code + PKCE, `state` / `nonce` in the encrypted `hf_oidc` cookie, ID-token validation, the Microsoft `tid` issuer check, the linking rules of §4.3.
- [x] Invitation acceptance through OIDC.
- [x] A fake OIDC provider for tests (in-process discovery, JWKS and token endpoint); the validation unit tests of §11.3.
- [x] README: how to register the Google and Microsoft apps for local development (redirect URIs, scopes `openid email profile`).

**Concept to learn:** OAuth 2.0 authorization code + PKCE, OpenID Connect ID tokens, account-linking pitfalls.

**Notes (done):**
- **Schema.** Migration `e2e08a4c7d21` (after E2-06's `e2d06b8c4f17`) creates `oidc_accounts` as in §3.3: `UNIQUE(provider, issuer, subject)`, `CHECK provider IN ('google', 'microsoft')`, `user_id` → `users` `ON DELETE CASCADE` (indexed), `email_at_link` (the provider's address when linked, for the record). Global (§6.4: the callback resolves an identity before any agency is known): on `app.platform.rls.GLOBAL_TABLES` with the reason; no `GRANT` (E2-04's default privileges give `hireflow_app` `SELECT, INSERT, UPDATE, DELETE`). Tested in `tests/migrations/test_identity_e2_08.py` (constraints, upgrade → downgrade → upgrade, grants, `check_rls` clean, `alembic check`).
- **Where things live.** `app.modules.identity.oidc` is the protocol (private to identity): the providers from the settings, the sealed flow cookie, discovery and JWKS (cached), Authlib's `AsyncOAuth2Client` for the authorization URL and the code exchange, joserfc for the ID token. `identity.service` runs the use cases (`start_oidc_login`, `complete_oidc_login`, `_oidc_sign_in`: resolution, linking, rotation, audit), `identity.router_v2` has the three `public` endpoints (`listAuthProviders`, `startOidcLogin`, `completeOidcLogin`), `app.platform.auth_cookies` names and sets the `__Host-hf_oidc` cookie (`hf_oidc` without Secure cookies). New settings: `OIDC_GOOGLE_CLIENT_ID/SECRET`, `OIDC_MICROSOFT_CLIENT_ID/SECRET`, `OIDC_MICROSOFT_TENANT` (default `organizations`), `OIDC_MAX_ATTEMPTS_PER_IP` / `OIDC_RATE_LIMIT_WINDOW`, and **`API_PUBLIC_URL`** (not in §4.3: the redirect URI `{API_PUBLIC_URL}/api/v2/auth/oidc/{provider}/callback` must be registered exactly, so it is configured rather than guessed from the request). The settings refuse a provider with only one of id and secret, a Microsoft tenant of `common` or `consumers` (§15 Q5) or one that does not fit a URL path, and, outside development, a configured provider with a non-https `API_PUBLIC_URL`. Dependencies `authlib==1.9.10` and `joserfc==1.8.0`.
- **The flow.** Start: 256-bit `state` and `nonce`, a 512-bit PKCE verifier (S256 challenge); they go, with `next` and the SHA-256 of `invitation_token`, into a JWT **encrypted** with `dir` + `A256GCM` under an HMAC-derived key of `SECRET_KEY` (its own label), with `exp` 10 minutes, in an HttpOnly, SameSite=Lax cookie of the same lifetime; the URL asks `prompt=select_account` (someone with several accounts can pick the one HireFlow knows). An invitation that can no longer be accepted is a `410` already at the start (and checked again on the way back); a provider whose discovery document cannot be fetched is a `502`. Callback: the cookie must decrypt, be unexpired and name the path's provider, and its `state` equal the query's (constant time); the cookie is deleted on every outcome. The code is exchanged as a confidential client (`client_secret_post` and the verifier). The ID token: **RS256 only**, signature against the JWKS of the discovery document's `jwks_uri` (both cached an hour; an unknown `kid` fetches the keys again once, for key rotation), then `iss`, `aud` (and `azp` when there are several audiences), `exp`, `iat` (60 s of skew), `sub`, `nonce`. Google: `iss` is `https://accounts.google.com` (Google also documents `accounts.google.com`; accepted and stored in full), the email counts only with `email_verified`. Microsoft: `iss` must be exactly `https://login.microsoftonline.com/{tid}/v2.0` for the token's own `tid` (a GUID), the consumer tenant (personal accounts) is refused, a pinned `OIDC_MICROSOFT_TENANT` id must equal `tid`, and the email counts only with `xms_edov` true (booleans sent as `"true"` / `"1"` are understood). Nothing of the provider's tokens is stored or logged.
- **Linking (§4.3 step 5), as built.** (1) `(provider, issuer, subject)`: its user, whatever the provider now says the address is; with an invitation, the user's email must be the invited one. (2) An invitation: a new account (no password, email verified by the token, display name from `name`) is created and linked whatever the identity's own address; **an existing account is linked only when the provider vouches for that same address** (refinement: otherwise a forwarded invitation link would attach a stranger's identity to an existing account, which the password flow does not allow either: it asks to sign in first). The acceptance itself is E2-07's code (`_join_agency`, extracted from `accept_invitation`). (3) A vouched address of an existing account: linked, and the holder emailed ("Google sign-in was added to your HireFlow AI account"; not for an account the invitation just created). (4) Otherwise `no_account`, or **`email_not_verified`** when the provider does not vouch for the address (refinement: such an address is never looked up, so "no account" would not be true). The account must be active, with an active membership in an active agency. Then rotation (the carried session is revoked), the invitation's agency or the login rule's active agency, and a redirect to `FRONTEND_URL + next` (`next`: a single leading `/`, printable ASCII, no backslash, ≤ 2048 characters, else `/`).
- **Errors.** Every failed callback redirects to `{FRONTEND_URL}/login?error=<code>&provider=<provider>`, with `OidcLoginError` (in the OpenAPI document as a schema, so the frontend's copy is typed): `no_account`, `no_access` (disabled, or no active agency), `email_not_verified`, `invitation_invalid`, `invitation_mismatch`, `account_exists`, `already_member`, `cancelled` (the provider's `access_denied`), `expired` (no, altered or old flow cookie), `oidc_failed` (anything else: state, code exchange, ID token), `rate_limited`. A provider that is not configured is a `404` on all its endpoints; an unknown one is a `422` (the path's enum).
- **Rate limits** (§4.6): start and callback have a counter each, 30 per IP per 15 minutes (so an office behind one address gets 30 sign-ins, not 15). Deviation: over the limit, the start is a `429` with `Retry-After`, but the **callback redirects with `error=rate_limited`**: it is a top-level navigation, where a problem document would be a raw JSON page. Both are audited once per window (`auth.rate_limited`, `oidc_start_ip` / `oidc_callback_ip`).
- **Audit.** `auth.login_succeeded` (`method: google|microsoft`), `auth.oidc_linked` (`provider`, `issuer`, `via: verified_email|invitation`, `new_account`), `team.invitation_accepted` (with `method`), and `auth.login_failed` for every failed callback (`method`, `reason`: `no_state_cookie`, `state_mismatch`, `provider_error` with the provider's `error`, `token_exchange_refused`, `bad_signature`, `wrong_issuer`, `invalid_claim:aud`, `no_account` with an email hash, `email_not_verified`, `user_disabled`, …), committed on its own after the attempt's writes are rolled back.
- **Tests (no real Google or Microsoft).** `tests/identity/fake_oidc.py` plays both providers at their real URLs through `httpx.MockTransport` (`oidc.TRANSPORT`): discovery, JWKS of an RSA key made for the session, an authorization step that checks the URL the API built, and a token endpoint that checks the code (once), client id and secret, redirect URI and the PKCE verifier, and signs an ID token with the test's claims (or another key). An autouse fixture makes any other request to a provider fail. `tests/identity/test_oidc.py` (64 tests): providers listing, start (URL, cookie, CSRF, 502), the linking rules 1-4 for both providers, invitations (new and existing accounts, gone, mismatch, already a member), rotation, `next` (12 cases), the state cookie (another browser, wrong or missing state, altered, other provider, expired), single-use codes, provider errors, 12 invalid ID tokens (nonce, aud, iss, exp, iat, missing claims, another tenant's issuer, the consumer tenant), a forged signature, unknown `kid` and key rotation, `alg: none` / HS256, `azp`, a pinned tenant, the rate limits, Redis down (fails open), the settings' refusals. `EXPECTED`, the API sweep (a `provider` parameter: unconfigured, so 404), `test_auth_required`'s public list and the cookie-attribute test cover the new operations.
- **Snapshots.** v1 unchanged. The v2 OpenAPI snapshot is re-recorded with additions only, and the `auth` tag's description: the three operations, `AuthProvider`, `AuthProviders`, `OidcStartRequest`, `OidcStart`, `OidcProvider` and `OidcLoginError` (compared key by key). New contract snapshots in `tests/contract_v2/test_oidc.py`. The TypeScript client is regenerated; no frontend code changed.
- **After pulling:** `docker compose up -d --build` (new Python dependencies: `authlib`, `joserfc`) and `alembic upgrade head`. An existing `.env` needs no edit: both providers stay hidden until their credentials are set (README "Enable Google/Microsoft sign-in").

### E2-09 · Frontend authentication
**Size:** M  ·  **Branch:** `e2-09-frontend-auth`  ·  **Depends on:** E2-05, E2-07

**Goal:** The product is usable only after signing in, with every auth page in the HireFlow design system.

**Acceptance criteria**
- [x] `(auth)` and `(app)` route groups; the `/login`, `/forgot-password`, `/reset-password`, `/accept-invite` and `/select-agency` pages (§9.1), with OIDC buttons when E2-08 has merged.
- [x] The generated client, uploads and `EventSource` send credentials; the CSRF middleware; 401 → sign-out redirect with `next`; `AuthGate`; `proxy.ts` optimistic redirect; the user menu with Sign out.
- [x] `AUTH_REQUIRED` is removed from the backend and the E2E stack signs in. The hero flow passes signed in as a recruiter.
- [x] Vitest for the middleware (CSRF header, 401 handling), the forms (inline 422 and 429 errors), the token pages (fragment read, no token in requests' URLs).

**Concept to learn:** cookie-based auth in an SPA, route groups, auth gates.

**Notes (done):**
- **Frontend only.** No backend file changed. One part of the criteria moved: removing `AUTH_REQUIRED` from the backend is E2-11's (the Sprint 8 plan); here the E2E stack runs with `AUTH_REQUIRED=true` instead, so a request that forgot its cookie fails the suite rather than acting as the fallback owner.
- **Google and Microsoft (merged with E2-08, which merges first).** `features/auth/sign-in-providers.tsx` lists `GET /auth/providers` (Google first) as "Continue with {name}" outline buttons with the providers' own marks, above an "or" and the email form, on `/login` (`next` = the page's `next`) and on `/accept-invite` (`next` = `/`, with the invitation's token). Nothing renders when no provider is configured (the development and E2E stacks), while the list loads, or if it fails. A click sends `POST /auth/oidc/{provider}/start` (with the CSRF header; an anonymous token from `GET /auth/csrf` before sign-in) and opens `authorization_url` as a new page. Starting can fail with a 410 (the invitation page shows its "can't be used any more" state), a 429 (the wait from `Retry-After`), a 502 ("Google can't be reached right now") or a 404 (the provider is no longer configured: said, and the list refetched). Coming back, `/login?error=…&provider=…` words every `OidcLoginError` (`features/auth/oidc.ts`, `oidcErrorNotice`; an unknown code gets a general message); success lands on `next` signed in, where the gate's `GET /me` takes over.
- **Routes.** The product routes moved into `src/app/(app)` (layout: `AuthGate` around the app shell); the sign-in pages are in `src/app/(auth)` with a centred, shell-less layout (`components/auth/auth-layout.tsx`: the D1 paper and one white panel). The pages live in `features/auth`. `/login` and `/select-agency` read `next` on the server (`safeNextPath`: a path on this site only, never a sign-in page, so no open redirect and no loop). The token pages set `referrer: no-referrer`.
- **Client** (`lib/api/transport.ts`, `lib/api/csrf.ts`). The generated client sends `credentials: "include"`; its `fetch` adds `X-CSRF-Token` to POST, PUT, PATCH and DELETE: the readable `hf_csrf` / `__Host-hf_csrf` cookie, else the last token the API returned in a body (`GET /auth/csrf`, `Me.csrf_token`: a frontend on another host cannot read the API's cookies), else one fetched from `GET /auth/csrf` (one request shared by concurrent callers). A 403 whose detail is the CSRF failure is repeated once, with a fresh token and a copy of the body. A 401 from anything but `/auth/*` (where it means a wrong password) and a 403 "Choose an agency" are reported to listeners. The upload XHR does the same (`withCredentials`, the header, the retry, the report); the event stream opens with `withCredentials: true`. `ApiProblem.retryAfter` reads `Retry-After` (the CORS policy exposes it).
- **Ended sessions.** `SessionWatcher` (mounted with the Query provider) turns a reported 401 into a sign-out: the Query cache cleared, the remembered CSRF token dropped, and a full page load of `/login?next=<page>`, with `reason=expired` when a user was signed in on the page (a first visit has no reason). Never on the sign-in pages (no loops), and once per page however many requests fail. A "Choose an agency" refetches `/me`, so the gate moves on to the picker.
- **Gate.** The client gate is the source of truth, as this Next.js version's authentication guide recommends (Proxy for optimistic checks only; the real check close to the data, which is the API): `AuthGate` renders nothing inside it until `GET /me` answers, so no screen requests data without a session. No active agency goes on to `/select-agency?next=…`; an unreachable API shows an error with a retry; a page restored from the back-forward cache reloads. `proxy.ts` redirects a page request without a session cookie to `/login?next=…` before anything renders, but only when the API shares the page's host (ports do not matter for cookies): with the API on another host the page cannot see its host-only cookie, and redirecting would bounce a signed-in user between `/login` and the page. A cookie proves nothing; `/me` decides.
- **Pages.** `/login`: Google and Microsoft when offered, then email and password; the API's one message for a wrong email or password (the password field is cleared), the 403 "no active agency access" with what to do, a 429 with its wait ("Try again in 14 minutes."), a 422 under its field; notices for `reason=expired`, `reason=password_changed` and every `error` E2-08's callback sends; a signed-in visitor goes straight on to `next`. `/forgot-password`: always the same confirmation, and "send another link". `/reset-password` and `/accept-invite` read `#token=`, keep it in memory, take it out of the address bar (a reload then explains the incomplete link; the emailed link still works) and send it in bodies only. Reset: the policy (12 to 128 characters, counted as the API counts them), the password twice, 410 and 422 states, then the login page with a confirmation. Accept: the preview (agency, inviter, role, address), then a new account (name and password), or for an existing account sign in then join, or "Sign out and continue" when signed in as another address; a 409 on the new-account form switches it to signing in; a 410 explains and links to sign-in. `/select-agency`: the memberships with their roles; choosing one (`PUT /me/active-agency`) starts a fresh cache and goes on; with none left it says so and offers to sign out. Signing in, accepting and choosing an agency clear the Query cache (`startSession`).
- **User menu and Home.** The sidebar's foot shows the user as the D1 mock-up draws it (initials, name, role · agency); the menu adds the email and a role badge, and Sign out (`POST /auth/logout`, a 401 counted as done, then the cache cleared and a full page load of `/login`). Home has a quiet "Good afternoon, <first name>" above its title (the reader's clock). The agency switcher stays E2-10's.
- **E2E.** A `setup` project signs in once through the login page as `recruiter@default.example` and saves the browser state; the hero flow runs from it unchanged. `e2e/auth.spec.ts` starts signed out, each test with its own seeded account: the server redirect, sign in, Home, sign out; a wrong password; `next`; forgot password → Mailpit → reset → sign in with the new password; an invitation sent through the API, read from Mailpit and accepted as a new account; an unknown invitation link. It also checks that the stack offers no provider (no button), and the page an OIDC `no_account` comes back to. Every sign-in page gets axe and screenshots in both themes. `scripts/e2e.sh` passes `E2E_API_URL`. The axe check now runs with reduced motion and waits for sonner's toasts to finish entering (`e2e/support/checks.ts`), which removes the intermittent "toast measured mid-fade" contrast failure.
- **Tests.** Vitest: the transport (credentials, the header on unsafe methods only, the cookie, the remembered and the fetched token, one retry and only for CSRF, the 401 and "Choose an agency" reports, `Retry-After`), the upload XHR's session, the event stream's credentials, the gate and the watcher, the proxy (with `unstable_doesMiddlewareMatch` for the matcher), the user menu, each page (validation; the 401, 403, 409, 410, 422 and 429 states; the fragment read and removed; tokens only in bodies), the provider buttons (one per provider, none when none is configured, the start request with its CSRF token and the navigation, the 404, 410, 429 and 502 answers) and the words for every `OidcLoginError`, `safeNextPath`, and Home's greeting. `vitest.setup.ts` gives every test a readable CSRF cookie.
- **Backend gaps** (backlog, next to the identity items): the CSRF and "Choose an agency" 403s have no problem `type`, so the client recognises them by their detail; an existing user with no active agency cannot accept an invitation (login is a 403 without a session, and accepting without one is a 409); the seed has no user in two agencies, so `/select-agency` is unit-tested only.

### E2-10 · Frontend tenancy and team
**Size:** L  ·  **Branch:** `e2-10-frontend-tenancy`  ·  **Depends on:** E2-06, E2-09

**Goal:** Agencies, clients and the team are first-class in the UI, and the UI shows each role only what it can do.

**Acceptance criteria**
- [ ] Agency switcher and client switcher in the shell (§9.2); the cache is cleared on switch.
- [ ] Campaigns: client column and filter, required client in the new-campaign form (inline "New client" for admins), client in the workspace header and Settings.
- [ ] Settings → Profile, Team (members, invite dialog, pending invitations, role and client changes, deactivate), Clients, Agency (§9.3).
- [ ] `usePermissions()`; write actions hidden for roles without them; a no-access state for direct URLs.
- [ ] Vitest for the switchers, the team table and invite dialog, and permission-aware rendering; axe clean on the new screens.

**Concept to learn:** permission-aware UI, multi-tenant client state.

### E2-11 · E2E and security pass
**Size:** M  ·  **Branch:** `e2-11-e2e-security`  ·  **Depends on:** E2-08, E2-10

**Goal:** The auth and isolation guarantees are proven end to end and reviewed against the checklist.

**Acceptance criteria**
- [ ] The Playwright specs of §11.4 (login/logout, invitation via Mailpit, password reset, viewer, isolation, client scoping), with axe and screenshots.
- [ ] §10 reviewed item by item; each item links its test or explains the review; gaps become backlog items.
- [ ] `pip-audit` and `npm audit` show no new High/Critical advisories from E2's dependencies (`argon2-cffi`, `authlib`, `joserfc`).
- [ ] README: sign-in for development (seeded users, Mailpit), the role model; runbook `tenancy-rls.md` complete; ADRs 0009 and 0010 accepted.

**Concept to learn:** security verification against a standard (ASVS), E2E auth with saved state.

### E2-12 · Remove `/api/v1`
**Size:** S  ·  **Branch:** `e2-12-remove-v1`  ·  **Depends on:** E2-04, E2-11 (last ticket of the epic)

**Goal:** One authenticated API: delete v1 and everything that existed only for it.

**Acceptance criteria**
- [x] Removed: `app/api/routes.py`, `app/modules/hiring/router.py`, `app/modules/ingestion/router.py`, v1-only schemas and service functions, `tests/characterization/` (its masking helpers move to `tests/contract_v2` or `tests/snapshot_utils`), `API_V1_PREFIX`, the root app's v1 exception handler and `strict_content_type=False`, the default-agency binding and `DEFAULT_AGENCY_ID`.
- [x] Contract migration drops `legacy_candidate_map` (downgrade re-creates it empty); `TABLE_OWNERS` and the RLS allowlist updated.
- [x] `/api/v1/*` answers 404; the v2 contract snapshots are unchanged; import-linter's `v2-independent-of-v1` contract is deleted with its targets.
- [x] Backlog: "Remove `/api/v1` after E2" removed; B5, B7–B10 closed as "removed with v1".

**Concept to learn:** retiring an API version cleanly.

**Notes (done):** migration `e2f12a6c9d40` (after E2-08's `e2e08a4c7d21`).
- **Removed.** The v1 routers (`app/api/routes.py`, `hiring/router.py`, `ingestion/router.py`) and v1's schemas (`hiring/schemas.py`, `ingestion/schemas.py`: v2 has its own `schemas_v2`); v1's use cases in `hiring.service` (campaign CRUD, the stats builders, the candidate list, export, detail, updates and deletes, `check_legacy_scope`, the merged-id lookups) and `ingestion.service` (resume list and detail, `resume_status_counts`, the v1 upload and retry wrappers), with the repository functions only they used and `ResumeProfiles.owner`; `LegacyCandidateMap`; `API_V1_PREFIX`; the root app's catch-all exception handler (it echoed the URL, B10) and `strict_content_type=False`; `app.platform.request_tenant` (v1's default-agency binding; `bind_request`, its only other part, is now inside `identity.dependencies.tenant_db`, its only user); the devtools `capture_api` / `compare_api` (they compared `/api/v1` responses, E0-19/E0-20; the runbook says so); `tests/characterization/` (186 tests, 184 snapshots). The root app serves `/health` and `/health/ready` only; every former v1 path answers 404 (`test_every_former_v1_endpoint_is_not_found`: the 23 operations and `/api/v1/health`), and the root `/openapi.json` lists the health checks only.
- **Kept, without v1.** The CSV export renders an application and its snapshot directly (`hiring.service.render_applications_csv`), no longer through v1's `CandidateDetail`; job descriptions are parsed by `parse_job_description_v2` alone. v2's 500 handler (`app.platform.problems`) now logs `unhandled_exception` itself (method and path, never the query string, which can carry an OIDC code): the root handler logged it until now. The masker, `response_data`, `celery_calls_data` and `read_sse` moved to `tests/snapshot_utils.py` and `tests/sse.py` unchanged, so every v2 snapshot is byte-identical; the parse-jd prompt pin (a v1 snapshot) is a literal list in `tests/test_parse_jd.py`.
- **The default agency is an ordinary agency.** `DEFAULT_AGENCY_ID` and `DEFAULT_CLIENT_ID` are gone from `app.platform.tenancy`. The commands that work on that agency look it up by its slug `default` (`identity.service.DEFAULT_AGENCY_SLUG`) and take its oldest active client ("General", `hiring.service.first_client_id`): `identity.cli create-owner` (an error names the slug if it is missing), the seed (the plan carries no tenancy any more: `generate` writes the session's agency and its oldest client) and `seed_users`. Tests take the migration's fixed ids from `tests/default_agency.py`. **Left for E2-11:** the `AUTH_REQUIRED=false` fallback (`identity.dependencies.DEV_FALLBACK`) keeps a private copy of the id, `_FALLBACK_AGENCY`, marked `# removed with AUTH_REQUIRED in E2-11`.
- **The tenancy backfill is kept, working without the constant.** E0 removed its backfill because the table it read (`candidates`) was dropped; this one's inputs all still exist, and it is the only way to bring a database from before E2 (an old backup) to `head` with today's code (up to the expand, the backfill, then the contract), which is also how the migration tests that start before E2-02 cross the contract. It finds the agency by slug and the client as above (`backfill_tenancy.default_agency`; without them it exits `1` and fills nothing). On a database past the contract it fills nothing. [tenancy-backfill.md](../runbooks/tenancy-backfill.md) says when it is still needed.
- **Migration.** Drops `legacy_candidate_map` (index and foreign key with it) under `lock_timeout` 5 s: an exclusive lock on the table and, for a moment, on `applications` (the foreign key's triggers), catalog only. The downgrade re-creates it **empty** with E0-14's schema and names; the owner's default privileges give the app role its grants back. Off `GLOBAL_TABLES` and `TABLE_OWNERS`; migration tests that check an older revision's catalogue use `catalogue_problems_before_e2_12`. `tests/migrations/test_legacy_map_e2_12.py`: gone at head, upgrade → downgrade → upgrade with a merged id in the map, the recreated schema and grants, `check_rls` clean, `alembic check`. The backlog's "merged legacy candidate ids in v2" is closed: those links predate any customer.
- **Tests that used v1 were ported to v2, not dropped,** where they checked something v2 still does: the B2 filters (`skill`, `q`), the seed served end to end (`tests/ingestion/test_people_applications.py`: list, detail, export and filters against the seed, campaign and application deletes with their PDFs, an upload after the contract, and the list query's plans as the app role under RLS, now for v2's application list: 4 and 5 statements, index scans), the upload limits and the B4 parsing guard, the ingestion service, the field sources, the tenancy tests and the harness. `tests/tenancy/test_session_binding.py` checks `tenant_db`'s binding and log context instead of `request_tenant`'s.
- **Snapshots.** No v2 contract snapshot changed; the v2 OpenAPI document and the generated TypeScript client are byte-identical. Import-linter: `v2-independent-of-v1` deleted, 9 contracts kept.
- **After pulling:** `docker compose exec backend alembic upgrade head` (drops `legacy_candidate_map`: on a stack backfilled in E0 its merged ids are gone), then restart the workers (`docker compose restart worker_extract worker_ai worker_default`). No new dependency, no `.env` change.

## 13. Definition of done for the epic

- [ ] Every tenant table has `agency_id NOT NULL`, RLS enabled and forced, and a tenant policy. The app and workers run as a non-owner, non-superuser role, checked at start-up.
- [ ] The tenant-isolation suite passes, including the forgotten-filter backstop test and the OpenAPI-driven API sweep.
- [ ] Every v2 endpoint requires a session and a declared permission. The role × operation matrix test passes, and the spec's matrix matches the code.
- [ ] Email and password, Google and Microsoft sign-in, invitations and password reset work end to end. Playwright covers login, invitation, reset, viewer restrictions, client scoping and isolation in CI.
- [ ] The security checklist (§10) is reviewed item by item, with every gap in the backlog.
- [ ] Existing data lives in the default agency, with verification passing on a Compose rehearsal.
- [ ] `/api/v1` and `legacy_candidate_map` are gone.
- [ ] ADRs 0009 and 0010 are accepted. The runbook covers enabling RLS and the role setup.

## 14. Risks

| Risk | Mitigation |
|---|---|
| A query path runs unbound and silently returns nothing (fail-closed looks like "no data") | `agency_id_of` raises when unbound; the whole test suite runs as the app role, so a missing binding fails tests rather than production; a debug-mode pool checkout assertion |
| The tenant setting leaks between pooled connections | Only `set_config(…, true)` (transaction-local); session-level `SET` banned by a CI check; the pool-checkout test |
| RLS changes query plans (policy quals before non-leakproof operators such as `ILIKE`) | `(agency_id, …)` indexes and explicit filters; the query-count tests; `EXPLAIN` of the list endpoints on the seed, recorded in E2-04 |
| The test suite rewrite (factories, fixtures, app role) is bigger than it looks | Done once in E2-04 with the RLS change, not spread over the epic; E2-01 to E2-03 keep the old harness green |
| OIDC account takeover through email linking | Link only by `(provider, issuer, subject)`; link by email only with a provider-verified email or an invitation token; Microsoft `xms_edov`; notification email on every new link |
| The epic grows into an identity platform (MFA, SSO, SCIM, sessions UI) | Non-goals (§2) are explicit; new ideas go to the backlog |
| The frontend and E2E break while the backend requires auth but the UI cannot sign in | `AUTH_REQUIRED=false` (development and test only, refused otherwise) from E2-05 to E2-09, then deleted |
| v1 stays unauthenticated during the epic | Bound to the default agency; nothing is deployed before E5; removed by E2-12 |
| Cookies do not reach the API in a split-domain deployment | Same-site requirement documented (§4.1) and carried into E5; `Me.csrf_token` covers a frontend that cannot read the API's cookies |

## 15. Decisions on the open questions (2026-11-19)

These were open in the first draft. They are decided here and can be revisited with a real customer.

1. **Several agencies per user: yes, for anyone.** The model supports it anyway, and freelance recruiters who work for several agencies are a real case. The UI shows the agency switcher only when a user has more than one membership.
2. **Client-scoped users see the whole talent pool**, as proposed in §5.3. Applications, scores and notes from campaigns outside their scope stay hidden. Rediscovery is the product's core value, and restricting People would weaken it.
3. **Sessions: 8 h idle, 7 days absolute** until MFA exists. Both are settings. TOTP MFA is on the backlog as a requirement before the first paying customer. When it ships, the absolute lifetime for users without MFA drops to 12 h.
4. **Permission defaults as in the §5.2 matrix:**
   - Recruiters may delete and export within their scope.
   - Viewers may not export.
   - Recruiters do not see the team list.
5. **Microsoft: work and school accounts only** (`organizations`). Agencies sign in with business identities, and personal accounts would add verification risk for little value.
6. **Joining by email domain: later.** E2 uses invitations only, and domain joining goes to the backlog.
7. **Names for migrated data:** the E2-01 expand migration creates them as "Default agency" and "General". `create-owner` (E2-05) takes `--agency-name` to rename the agency at setup, and the owner can rename both in Settings.
