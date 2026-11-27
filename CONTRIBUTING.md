# Contributing

How work flows through this repo. The full process (planning levels, ticket format, sprint rhythm,
ADRs) lives in [docs/process.md](docs/process.md); the epic order is in [docs/roadmap.md](docs/roadmap.md).

## Workflow

1. **Pick a ticket** from the current sprint file in `docs/sprints/`. Ticket IDs look like `E0-03`.
2. **Branch** from `master`, one branch per ticket, named `e<epic>-<nn>-short-name`, e.g. `e0-03-person-application-split`.
3. **Commit** using [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`.
4. **Open one PR per ticket.** Title it with the ticket ID, e.g. `E0-03: Split Candidate into Person + Application`.
   Fill in the PR template, including the Definition of done checklist.
5. **Merge** once CI is green and the checklist is complete.

### Stacked PRs

When a ticket depends on another ticket whose PR hasn't merged yet:

1. Branch from the parent ticket's branch instead of `master`.
2. Open the PR with the parent's branch as its **base**, so the diff shows only your ticket's changes.
3. After the parent merges, retarget your PR to `master` (Edit → base branch) and rebase or merge `master` in if needed.

## Local checks before pushing

```bash
make lint        # ruff check + ruff format --check + lint-imports (module boundaries)
make typecheck   # mypy
make test        # pytest
make api-client  # after a /api/v2 change: regenerate frontend/src/lib/api/ (CI fails if stale)
cd frontend && npm run lint && npx tsc --noEmit && npm test
make e2e         # Playwright + axe against an isolated Compose stack (README: End-to-end tests)
E2E_SCREENSHOT_DIR=docs/screenshots make e2e   # after a visible UI change: refresh the README's screenshots, then commit them
pre-commit install   # once per clone; runs the hooks on every commit
```

> These Make targets and the pre-commit config arrive with ticket **E0-02**. Until it merges they may not exist yet.

### Running the frontend while developing (Windows)

Run the frontend natively and keep the rest in Docker. On Windows the bind-mounted `./frontend`
makes the containerised `next dev` slow to compile and blind to files a `git pull` creates (a
stale "Module not found" until the container is recreated), and the Docker VM's memory is shared
with every stack and test database:

```bash
docker compose stop frontend          # the backend, workers and databases stay in Docker
cd frontend && npm ci                 # in frontend/, never the repository root
npm run dev -- -p 3001                # 3001 is in the dev CORS_ORIGINS
```

If the containerised frontend is used anyway, recreate it after a pull that adds files:
`docker compose up -d --force-recreate --renew-anon-volumes frontend`.

### Signing in while developing

`/api/v2` requires a session (E2-05). The seed creates one account per role in two agencies
(password `hireflow-dev-password`, development only; the list is in the README under "Signing in
locally"). The app signs in at `/login` (E2-09): open it on `localhost` (the session cookie is the
API's, and `127.0.0.1` is another site), sign in as one of those accounts, and the page you asked
for comes back; the user menu at the foot of the sidebar signs you out. The dev stack still runs
with `AUTH_REQUIRED=false` until E2-11 (an API request without a cookie acts as the default
agency's owner, handy for `curl`), but the app never relies on it, and the E2E stack requires a
session.

In the frontend, every request carries the session through the generated client
(`src/lib/api/transport.ts`: credentials, the CSRF header, one retry after a CSRF 403, a 401
reported to the `SessionWatcher`); the upload XHR and the event stream do the same. Pages inside
the app shell are signed in already (`AuthGate`); read the user with `useSignedInUser()` from
`@/lib/auth/session`. In Vitest a readable `hf_csrf` cookie is set for every test
(`vitest.setup.ts`), and `src/test/auth.ts` has a signed-in `Me` and a fake API that records each
request's headers. In Playwright, specs start signed in as the seeded recruiter
(`e2e/auth.setup.ts`); a spec that needs to start signed out uses
`test.use({ storageState: { cookies: [], origins: [] } })`.

In backend tests the `client`
fixture acts as an owner of the default agency (it overrides `current_principal`); tests that
need real cookies and CSRF tokens use `auth_client` and `users` from `tests/identity/conftest.py`
(never both in one test: the `client` override applies to every request of the app);
`users.limit(...)` makes a membership limited to some clients.

Every `/api/v2` route declares who may call it (E2-06): a permission of the matrix in
`app.modules.identity.permissions`, with `Depends(require_permission(Permission.…))`, or the
`any_member` / `public` marker; the app refuses to start otherwise. Give a new operation its row in
`EXPECTED` in `tests/identity/test_permissions.py` (the role × operation test runs from it) and,
if it takes a body or a new kind of id, an entry in `tests/tenancy/test_api_sweep.py`.

Emails (invitations, password resets, E2-07) go to **Mailpit** in the Compose stack: read them at
http://localhost:8025. README "Inviting a teammate locally" shows the invitation flow with
`curl`. In backend tests the console backend keeps them in memory: the `mailbox` fixture
(`tests/identity/conftest.py`) delivers what the code queued and reads the token from a link.

Google and Microsoft sign-in (E2-08) is tested against fake providers, never the real ones: the
`fake_oidc` fixture configures both and serves their discovery document, keys and token endpoint
in-process (`tests/identity/fake_oidc.py`, whose `sign_in` plays the browser). Any other request
to a provider fails the test.

### Tests run under row-level security

The test session connects as `hireflow_app` (E2-04), so Postgres RLS is on for every test: a
session sees and writes only the rows of the agency it is bound to. Write another agency's rows
through a session bound to it (`session_for` / `other_agency.db` in `tests/tenancy/conftest.py`).
Migrations, `alembic_version` and reads across agencies use the owner role
(`settings.migration_database_url`; `postgres.throwaway_database` yields an owner engine and
`postgres.as_role(engine, postgres.APP_ROLE)` the app role's URL). A new table needs
`enable_tenant_rls` in its migration or a reasoned entry in `app.platform.rls.GLOBAL_TABLES`;
`tests/tenancy/test_isolation.py` fails otherwise.

## Branch protection on `master` (repo owner, one-time)

Do this after ticket **E0-05** (the CI rewrite) has merged and its workflow has run at least once.
GitHub only lists a status check in the picker after it has reported on the repo.

1. On GitHub, open the repo and go to **Settings → Branches** (or **Settings → Rules → Rulesets** for the newer rulesets UI).
2. Click **Add branch protection rule** (or **New ruleset → New branch ruleset**) and target the branch `master`.
3. Enable **Require a pull request before merging**. Required approvals can be **0** for a solo repo.
4. Enable **Require status checks to pass before merging** and add these checks:
   - `backend-quality`
   - `backend-test`
   - `migrations`
   - `frontend`
   - `compose`
   - `e2e`: add it once the job has been green on `master` (it arrived with E1-09 and is not required yet).
5. Enable **Require branches to be up to date before merging**.
6. Make sure **force pushes** and **deletions** are blocked (leave "Allow force pushes" and "Allow deletions" unchecked, or enable "Block force pushes" and "Restrict deletions" in a ruleset).
7. Save the rule. Check it works by pushing a commit directly to `master`; it should be rejected.
