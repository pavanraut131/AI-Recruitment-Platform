# Sprint 8 · Roles, team and sign-in screens; `/api/v1` removed

**Epic:** [E2 · Identity & tenancy](../specs/EPIC-02-identity-tenancy.md)  ·  **Dates:** 2026-11-27 → 2026-12-04  ·  **Status:** in progress

## Sprint goal

> Each role can do exactly what the permission matrix allows, limited to its clients. An agency owner can:
> - invite the team by email;
> - manage roles and clients;
> - reset passwords;
> - sign in with email and password, Google or Microsoft.
>
> HireFlow AI has the login, invitation and reset pages, the agency and client switchers, and Settings → Team, all in the D1 design. Playwright covers sign-in, invitations, roles and isolation, and the security checklist is reviewed. The dev-only `AUTH_REQUIRED=false` bridge is gone, `/api/v1` is removed, and **E2 closes** (spec §13).

## Board

| Ticket | Title | Size | Status |
|---|---|---|---|
| E2-06 | Authorization: permission matrix on every v2 route, client scoping, clients API, campaigns get a client | M | doing |
| E2-07 | Invitations, team management API, password reset, email (Mailpit), session purge | M | doing |
| E2-08 | Sign in with Google and Microsoft (OIDC + PKCE) | M | todo |
| E2-09 | Frontend auth: login, forgot and reset password, accept invite, auth gate, credentials + CSRF, 401 handling, user menu | M | todo |
| E2-10 | Frontend tenancy and team: agency and client switchers, client on campaigns, Settings → Profile, Team, Clients, Agency | L | todo |
| E2-11 | E2E and security pass: Playwright auth, invitations, roles and isolation; ASVS review; runbook; `AUTH_REQUIRED` removed | M | todo |
| E2-12 | Remove `/api/v1` | S | todo |

**Order** (spec §12):
- **Wave 1:** E2-06 and E2-07 in parallel, both backend.
  - **E2-06** owns `app.modules.identity.permissions` (the §5.2 matrix and `require_permission`), client scoping, the clients API, and `client_id` on campaign create. That last part fixes "a second agency can't create campaigns" from E2-05.
  - **E2-07** owns invitations, the team endpoints, password reset, `app.platform.email` with Mailpit in Compose, and the first Celery beat task (`identity.purge_expired`). It keeps its team permission checks in one small function, which the merge with E2-06 swaps for `require_permission`.
  - **Merge:** E2-07 merges E2-06's branch before its PR opens (the Sprint 7 pattern). Their migrations are chained the same way.
- **Wave 2:** E2-08 (OIDC, needs E2-07's invitations) and E2-09 (frontend auth, needs E2-07's accept and reset endpoints) in parallel. E2-09 is frontend-only.
- **Wave 3:** E2-10, after E2-06 and E2-09.
- **Wave 4:**
  - **E2-11:** the E2E suite signs in for real, so `AUTH_REQUIRED` and its dev fallback are deleted here.
  - **E2-12:** removing v1. It touches no frontend, and merges last.

**Working agreements** (from the [Sprint 7 retro](sprint-07.md#sprint-review--retro)):
- **Only a sprint docs PR edits the board, the roadmap and other tickets' sections.** A ticket PR ticks its own criteria in spec §12, may add notes under its own section here, and adds backlog items next to related ones.
- **Each agent runs E2E under its own project and ports:** `E2E_PROJECT=hireflow-e2e<ticket>`. The default `hireflow-e2e` project is for the owner and CI.
- **The second of two parallel tickets merges the first's branch before its PR opens**, and re-points its migration so Alembic keeps one head.
- **After a merge with a migration or a new dependency, the coordinator upgrades the local stack** with the runbook and checks it for real (a sign-in, an API call).
- **The test suite runs as `hireflow_app` under row-level security.** Anything that writes across agencies uses `maintenance.owner_session()`.
- **Security changes are checked against spec §10.** The PR says which items it covers. **Frontend tickets follow `docs/design/README.md`** (tokens, voice), and the owner reviews them in the running app.

---

### E2-06 · Authorization and clients
**Status:** doing  ·  **Size:** M  ·  **Branch:** `e2-06-rbac-clients`

Goal and acceptance criteria: [spec §12, E2-06](../specs/EPIC-02-identity-tenancy.md#e2-06--authorization-and-clients). Design: spec §5 (roles, matrix, client scoping, enforcement) and §8.

---

### E2-07 · Invitations, team and password reset
**Status:** doing  ·  **Size:** M  ·  **Branch:** `e2-07-invitations-team`

Goal and acceptance criteria: [spec §12, E2-07](../specs/EPIC-02-identity-tenancy.md#e2-07--invitations-team-and-password-reset). Design: spec §4.3 (invitation, reset), §4.4 (email), §4.6–4.7, §8.

---

### E2-08 · Sign in with Google and Microsoft
**Status:** todo  ·  **Size:** M  ·  **Branch:** `e2-08-oidc`

[Spec §12, E2-08](../specs/EPIC-02-identity-tenancy.md#e2-08--sign-in-with-google-and-microsoft); design §4.3 (OIDC). Microsoft: work and school accounts only (§15 Q5).

---

### E2-09 · Frontend authentication
**Status:** todo  ·  **Size:** M  ·  **Branch:** `e2-09-frontend-auth`

[Spec §12, E2-09](../specs/EPIC-02-identity-tenancy.md#e2-09--frontend-authentication); design §9.

---

### E2-10 · Frontend tenancy and team
**Status:** todo  ·  **Size:** L  ·  **Branch:** `e2-10-frontend-tenancy`

[Spec §12, E2-10](../specs/EPIC-02-identity-tenancy.md#e2-10--frontend-tenancy-and-team); design §9. The sidebar header slot D1 left for the agency switcher is where it goes.

---

### E2-11 · E2E and security pass
**Status:** todo  ·  **Size:** M  ·  **Branch:** `e2-11-e2e-security`

[Spec §12, E2-11](../specs/EPIC-02-identity-tenancy.md#e2-11--e2e-and-security-pass). It also removes `AUTH_REQUIRED` and its dev fallback (E2-05).

---

### E2-12 · Remove `/api/v1`
**Status:** todo  ·  **Size:** S  ·  **Branch:** `e2-12-remove-v1`

[Spec §12, E2-12](../specs/EPIC-02-identity-tenancy.md#e2-12--remove-apiv1). It closes backlog bugs B5 and B7–B10 for v1 and removes `legacy_candidate_map`. It decides the "merged legacy candidate ids" backlog item; old links already predate any customer.

---

## Sprint review & retro

*(To be filled in at the end of the sprint.)*
