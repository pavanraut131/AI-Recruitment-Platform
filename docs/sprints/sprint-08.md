# Sprint 8 · Roles, team and sign-in screens; `/api/v1` removed

**Epic:** [E2 · Identity & tenancy](../specs/EPIC-02-identity-tenancy.md)  ·  **Dates:** after Sprint 7 (planned)  ·  **Status:** planned

## Sprint goal (draft)

> Each role can do exactly what the permission matrix allows, limited to its clients. An agency owner can:
> - invite the team by email;
> - manage roles and clients;
> - reset passwords;
> - sign in with email and password, Google or Microsoft.
>
> HireFlow AI has the login, invitation and reset pages, the agency and client switchers, and Settings → Team. Playwright covers sign-in, invitations, roles and isolation, and the security checklist is reviewed. `/api/v1` is removed and **E2 closes** (spec §13).

## Board (draft)

| Ticket | Title | Size | Status |
|---|---|---|---|
| E2-06 | Authorization: permission matrix on every v2 route, client scoping, clients API | M | planned |
| E2-07 | Invitations, team management API, password reset, email (Mailpit) | M | planned |
| E2-08 | Sign in with Google and Microsoft (OIDC + PKCE) | M | planned |
| E2-09 | Frontend auth: login, forgot and reset password, accept invite, auth gate, 401 handling | M | planned |
| E2-10 | Frontend tenancy and team: agency and client switchers, Settings → Profile, Team, Clients, Agency | L | planned |
| E2-11 | E2E and security pass: Playwright auth, invitations, roles and isolation; ASVS review; runbook | M | planned |
| E2-12 | Remove `/api/v1` | S | planned |

**Order** (spec §12):
- E2-06 and E2-07 in parallel after E2-05.
- E2-08 after E2-07.
- E2-09 after E2-05 and E2-07.
- E2-10 after E2-06 and E2-09.
- E2-11 after E2-08 and E2-10.
- E2-12 last. It also closes backlog bugs B5 and B7–B10 for v1 and removes `legacy_candidate_map`.

Goals and acceptance criteria for each ticket are in [spec §12](../specs/EPIC-02-identity-tenancy.md#12-plan-two-sprints-after-e1-closes). The board, dates and waves are confirmed when Sprint 7 closes.
