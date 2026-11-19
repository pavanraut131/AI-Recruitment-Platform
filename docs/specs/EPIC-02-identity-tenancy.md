# E2 · Identity & tenancy: Design Spec

**Status:** in review  ·  **Date:** 2026-11-19  ·  **Roadmap:** [roadmap.md](../roadmap.md)  ·  **Depends on:** E1 closed (E1-10: the frontend calls only `/api/v2`)  ·  **ADRs:** [0009 Tenant isolation with row-level security](../adr/0009-tenant-isolation-row-level-security.md), [0010 Server-side sessions](../adr/0010-server-side-sessions.md)

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
- **The talent pool stays agency-wide** (§3.1): a scoped user sees every Person in the agency, which is what talent rediscovery needs. On a Person's profile, **applications to campaigns outside the scope are left out**, together with their scores and notes. This is open question Q2.
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

The same pattern as E0 (EPIC-00 §6). The existing data becomes the **default agency** ("Default agency", fixed id `DEFAULT_AGENCY_ID` in `app.platform.tenancy`) with one **default client** ("Default client"). Both are renamed by the owner afterwards. The constant only exists for the transition: E2-12 deletes it, and the agency itself becomes a normal agency.

### 7.1 Phases

| Phase | Ticket | Migration and code | Runtime behaviour |
|---|---|---|---|
| **1 · Expand** | E2-01 | Create `agencies` and `clients`; insert the default agency and default client; add **nullable** `agency_id` (and `client_id`, `created_by_user_id`, `uploaded_by_user_id`) to the tenant tables. Nothing is dropped. | Every insert sets `agency_id` (the default agency) and new campaigns get the default client: the equivalent of E0's dual write. |
| **2 · Backfill** | E2-02 | `python -m app.modules.identity.backfill_tenancy`: per table, in batches of 1,000 by keyset on `id` (`UPDATE … SET agency_id = :default WHERE id IN (…) AND agency_id IS NULL`), one transaction per batch; resumable and idempotent. Campaigns also get `client_id`. | Runs while the app is live |
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
- [ ] `identity` module skeleton (models, service, `agencies` table) with import-linter: added to `leaf-modules`, `llm-sdk-only-in-ai` and the module lists; `TABLE_OWNERS` updated.
- [ ] Expand migration: `agencies`, `clients` (hiring), nullable `agency_id` on the seven existing tenant tables, `campaigns.client_id` / `created_by_user_id`, `resumes.uploaded_by_user_id`; inserts the default agency and default client. Upgrade → downgrade → upgrade is tested.
- [ ] Every insert path (campaigns, applications, People and their identifiers, conflicts, `person_resumes`, resumes, the seed) sets `agency_id = DEFAULT_AGENCY_ID`; new campaigns get the default client.
- [ ] All v1 snapshots and v2 contract snapshots are unchanged (no API change yet).

**Concept to learn:** expand/contract for a new tenant key; metadata-only `ADD COLUMN`.

### E2-02 · Tenancy backfill, verify and contract
**Size:** M  ·  **Branch:** `e2-02-tenancy-contract`  ·  **Depends on:** E2-01

**Goal:** Every existing row belongs to the default agency, and the schema guarantees `agency_id` from now on.

**Acceptance criteria**
- [ ] `python -m app.modules.identity.backfill_tenancy`: batched, keyset-paginated, resumable, idempotent; a second run changes nothing (tested).
- [ ] `verify_people_applications` gains `tenancy_complete`, `cross_module_same_agency` and `clients_same_agency` (§7.3) and passes on the Compose seed.
- [ ] Contract migration (§7.1 phase 4): NOT NULL via validated CHECK; `UNIQUE(agency_id, id)` targets and composite FKs (`NOT VALID` → `VALIDATE`); `person_identifiers` unique becomes `(agency_id, kind, value_normalized)`; `(agency_id, …)` indexes including `resumes (agency_id, campaign_id)`; index builds `CONCURRENTLY`.
- [ ] `resolve_person` matches and locks per agency (`agency_id:kind:value` keys). The concurrency test passes 20 runs in a row with two agencies.
- [ ] A live rehearsal on the Compose stack with seed data: backfill while uploading, verify, contract; the results are in the PR.

**Concept to learn:** `NOT VALID` constraints, `CREATE INDEX CONCURRENTLY`, `ADD CONSTRAINT … USING INDEX`.

### E2-03 · Tenant context
**Size:** L  ·  **Branch:** `e2-03-tenant-context`  ·  **Depends on:** E2-01

**Goal:** Every database session, task, stream and stored file knows its agency, explicitly and in one way.

**Acceptance criteria**
- [ ] `app.platform.tenancy`: `TenantContext`, `AccessScope`, `bind`, `agency_id_of` (raises when unbound), and the `after_begin` listener that runs `set_config('app.agency_id', …, true)` at every transaction start of a bound session (async and sync). A CI check rejects session-level `SET app.`.
- [ ] Every repository query on a tenant table filters by `agency_id_of(db)` and every insert sets it (replacing E2-01's constant). Services that list or fetch campaign data take an `AccessScope` (all clients until E2-06).
- [ ] v2 routers use `tenant_db` instead of `get_db`. Until E2-05 it binds the default agency; v1 binds the default agency until E2-12.
- [ ] Celery: `TenantTask` base class; `extract_resume` and `process_candidate_ai` take `agency_id`; failure handlers use the task's agency; a message without `agency_id` falls back to the default agency (removed in E2-04); `reset_stuck` re-enqueues with each row's agency.
- [ ] Redis channels `agency:{agency_id}:campaign:{campaign_id}:events`; storage keys `agencies/{agency_id}/campaigns/{campaign_id}/resumes/{resume_id}.pdf`; `storage_orphans` scans the old and new prefixes.
- [ ] structlog binds `agency_id` on requests and tasks. All snapshots unchanged except the recorded storage keys (listed in the PR).

**Concept to learn:** transaction-scoped settings, SQLAlchemy session events, carrying context through a task queue.

### E2-04 · Row-level security
**Size:** L  ·  **Branch:** `e2-04-rls`  ·  **Depends on:** E2-02, E2-03

**Goal:** The database itself refuses to return or accept another agency's rows, even when the code forgets a filter.

**Acceptance criteria**
- [ ] Roles `hireflow_owner` and `hireflow_app`: Compose init script and `make db-roles` (idempotent); `MIGRATION_DATABASE_URL` for Alembic and the operator scripts; the API and workers connect as `hireflow_app`.
- [ ] Migration: `app_current_agency()`, grants and default privileges, `ENABLE` + `FORCE` RLS, `tenant_isolation` and `maintenance` policies on every tenant table (§6.2); `audit_events` grant without UPDATE or DELETE (applied when the table arrives in E2-05).
- [ ] Start-up check: the API and the workers refuse to run as a superuser, a `BYPASSRLS` role or a table owner.
- [ ] Celery messages without `agency_id` are rejected; the runbook (`docs/runbooks/tenancy-rls.md`) covers draining queues, enabling, verifying and rolling back.
- [ ] Test infrastructure on the app role (§11.1); the whole suite passes under RLS; the tenant-isolation suite (§11.2) is in place, including `test_forgotten_filter_is_contained` and the pool-checkout test.
- [ ] v1 snapshots and v2 contract snapshots are unchanged.

**Concept to learn:** Postgres RLS (`USING` vs `WITH CHECK`, `FORCE`, policies per role), role design, connection-pool safety.

### E2-05 · Identity core
**Size:** L  ·  **Branch:** `e2-05-identity-core`  ·  **Depends on:** E2-01, E2-03

**Goal:** People sign in with email and password into a secure server-side session, and `/api/v2` requires one.

**Acceptance criteria**
- [ ] Tables `users`, `memberships`, `membership_clients`, `user_sessions`, `audit_events` (global, §6.4).
- [ ] Argon2id hashing (`argon2-cffi`), the password policy, `POST /auth/login`, `POST /auth/logout`, `GET /auth/csrf`, `GET/PATCH /me`, `POST /me/password`, `PUT /me/active-agency`, `GET/PATCH /agency` (§8.2), with session rotation, idle and absolute expiry, throttled `last_seen_at` writes.
- [ ] CSRF middleware (signed double-submit + `Origin`) on the v2 app; CORS headers narrowed; security headers.
- [ ] Rate limiter (`app.platform.ratelimit`) on login; `429` with `Retry-After`.
- [ ] `current_principal`, `tenant_db` from the session's agency; every v2 route requires a session. A temporary `AUTH_REQUIRED=false` (refused outside `development`/`test`) lets the frontend and E2E work as the default agency's owner until E2-09.
- [ ] `python -m app.modules.identity.cli create-agency --name … --owner-email …` and `create-owner` for the default agency; the seed creates two agencies and one user per role.
- [ ] Audit events for the flows in this ticket; the auth flow tests of §11.3 for login, logout, fixation, expiry, CSRF and rate limits; the OpenAPI document declares the security schemes and 401/403.
- [ ] `SECRET_KEY` validation (§10); ADR 0010 accepted.

**Concept to learn:** session management, cookie security attributes, CSRF defences, password hashing.

### E2-06 · Authorization and clients
**Size:** M  ·  **Branch:** `e2-06-rbac-clients`  ·  **Depends on:** E2-05

**Goal:** Each role can do exactly what §5.2 says, limited to its clients, and campaigns belong to clients.

**Acceptance criteria**
- [ ] `identity.permissions` (`ROLE_PERMISSIONS`); `require(permission)` on every v2 route; the v2 app refuses a route without a permission or a `public` marker.
- [ ] Client scoping (§5.3) in hiring and the composed endpoints: lists filtered, out-of-scope objects 404, a Person's applications filtered.
- [ ] Clients API (`/clients`), `Campaign.client`, required `CampaignCreate.client_id`, `client_id` filters on campaigns, Home and stats; contract snapshots and the client regenerated.
- [ ] The role × operation matrix test and the client-scope tests (§11.3); a test that the §5.2 table matches `ROLE_PERMISSIONS`.

**Concept to learn:** RBAC design, deny-by-default enforcement, IDOR prevention.

### E2-07 · Invitations, team and password reset
**Size:** M  ·  **Branch:** `e2-07-invites-team-reset`  ·  **Depends on:** E2-05 (invitations validate client ids through `hiring.service`, which has clients since E2-01)

**Goal:** An owner can build a team without touching the database, and anyone can recover their account.

**Acceptance criteria**
- [ ] `app.platform.email` (console and SMTP backends), Mailpit in Compose, the `identity.send_email` task and templates.
- [ ] Tables `invitations`, `password_reset_tokens`; the invitation endpoints, the team endpoints with the guard rails (§5.2) and the reset endpoints (§8.2), composed with hiring for client names.
- [ ] Rate limits on reset and invitations; tokens hashed, single use, in the URL fragment; reset revokes sessions and notifies.
- [ ] `identity.purge_expired` in a Celery beat schedule.
- [ ] Flow tests for invitation (new user, existing user, expired, replaced, revoked) and reset (incl. no enumeration); audit events asserted.

**Concept to learn:** token-based flows, account enumeration, email deliverability basics.

### E2-08 · Sign in with Google and Microsoft
**Size:** M  ·  **Branch:** `e2-08-oidc`  ·  **Depends on:** E2-05, E2-07

**Goal:** Users can sign in, and accept invitations, with their Google or Microsoft work account.

**Acceptance criteria**
- [ ] `oidc_accounts`; `GET /auth/providers`, `POST /auth/oidc/{provider}/start`, `GET /auth/oidc/{provider}/callback` with Authlib: code + PKCE, `state` / `nonce` in the encrypted `hf_oidc` cookie, ID-token validation, the Microsoft `tid` issuer check, the linking rules of §4.3.
- [ ] Invitation acceptance through OIDC.
- [ ] A fake OIDC provider for tests (in-process discovery, JWKS and token endpoint); the validation unit tests of §11.3.
- [ ] README: how to register the Google and Microsoft apps for local development (redirect URIs, scopes `openid email profile`).

**Concept to learn:** OAuth 2.0 authorization code + PKCE, OpenID Connect ID tokens, account-linking pitfalls.

### E2-09 · Frontend authentication
**Size:** M  ·  **Branch:** `e2-09-frontend-auth`  ·  **Depends on:** E2-05, E2-07

**Goal:** The product is usable only after signing in, with every auth page in the HireFlow design system.

**Acceptance criteria**
- [ ] `(auth)` and `(app)` route groups; the `/login`, `/forgot-password`, `/reset-password`, `/accept-invite` and `/select-agency` pages (§9.1), with OIDC buttons when E2-08 has merged.
- [ ] The generated client, uploads and `EventSource` send credentials; the CSRF middleware; 401 → sign-out redirect with `next`; `AuthGate`; `proxy.ts` optimistic redirect; the user menu with Sign out.
- [ ] `AUTH_REQUIRED` is removed from the backend and the E2E stack signs in. The hero flow passes signed in as a recruiter.
- [ ] Vitest for the middleware (CSRF header, 401 handling), the forms (inline 422 and 429 errors), the token pages (fragment read, no token in requests' URLs).

**Concept to learn:** cookie-based auth in an SPA, route groups, auth gates.

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
- [ ] Removed: `app/api/routes.py`, `app/modules/hiring/router.py`, `app/modules/ingestion/router.py`, v1-only schemas and service functions, `tests/characterization/` (its masking helpers move to `tests/contract_v2` or `tests/snapshot_utils`), `API_V1_PREFIX`, the root app's v1 exception handler and `strict_content_type=False`, the default-agency binding and `DEFAULT_AGENCY_ID`.
- [ ] Contract migration drops `legacy_candidate_map` (downgrade re-creates it empty); `TABLE_OWNERS` and the RLS allowlist updated.
- [ ] `/api/v1/*` answers 404; the v2 contract snapshots are unchanged; import-linter's `v2-independent-of-v1` contract is deleted with its targets.
- [ ] Backlog: "Remove `/api/v1` after E2" removed; B5, B7–B10 closed as "removed with v1".

**Concept to learn:** retiring an API version cleanly.

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

## 15. Open questions

1. **Several agencies per user.** The design supports it (memberships and the agency switcher). Is it needed for customers, or only for our own support staff? If only support, the switcher could stay hidden behind a single-membership UI.
2. **Client-scoped users and the talent pool.** Proposed: they see the whole agency's People, but not applications, scores or notes from other clients' campaigns. The alternative is to show only People who applied to their clients' campaigns, which weakens rediscovery.
3. **Session lifetime.** The proposed defaults are 8 h idle and 7 days absolute. That is friendlier than ASVS L2 (about 30 minutes idle or 12 h absolute without MFA). Is it acceptable until MFA arrives?
4. **Matrix defaults.** Should recruiters delete applications and export CSV? Should viewers export? Should recruiters see the team list?
5. **Microsoft accounts.** Work and school accounts only (`organizations`), or personal Microsoft accounts too?
6. **Domain-based joining.** Should an agency be able to say "anyone signing in with @agency.com joins as a recruiter"? Proposed: later, invitations only in E2.
7. **Names for migrated data.** "Default agency" and "Default client" for the existing rows, or should the owner name them during the E2-05 `create-owner` step?
