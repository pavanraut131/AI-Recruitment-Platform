# Runbook · Row-level security (EPIC-02 phase 5, E2-04)

Puts every tenant table under Postgres **row-level security** and moves the API and the Celery workers to a database role that RLS restricts. Afterwards the database itself returns and accepts only the rows of the transaction's agency, even when the code forgets a filter; a session bound to no agency sees nothing. Spec: [EPIC-02 §6.2, §7.1 phase 5, §7.2](../specs/EPIC-02-identity-tenancy.md#62-roles-and-policies); decision: [ADR 0009](../adr/0009-tenant-isolation-row-level-security.md). Code: `docker/postgres/roles.sql`, `app/platform/rls.py`, `app/platform/maintenance.py` and the migration `backend/alembic/versions/e2c04a7f3b18_tenancy_rls.py` (their docstrings hold the details).

| Role | Attributes | Connects through | Used by |
|---|---|---|---|
| `POSTGRES_USER` (`resumeai` in the template) | the bootstrap superuser | psql, `make shell-db` | administration only: `roles.sql`. The app never uses it again |
| `hireflow_owner` | `LOGIN`, not a superuser, no `BYPASSRLS`; **owns** the schema and every table | `MIGRATION_DATABASE_URL` | Alembic, and the operator commands that span agencies: `verify_people_applications`, `reset_stuck`, `storage_orphans`, `backfill_tenancy`, `check_rls` |
| `hireflow_app` | `LOGIN`, not a superuser, no `BYPASSRLS`, owns nothing, member of nothing | `DATABASE_URL` (API), `SYNC_DATABASE_URL` (workers) | the API, the workers, the seed (bound to the default agency) |

In Compose the three URLs are **built by `docker-compose.yml`** from `POSTGRES_DB` and the two role passwords `HIREFLOW_OWNER_PASSWORD` / `HIREFLOW_APP_PASSWORD` (defaults `hireflow_owner_secret` / `hireflow_app_secret`). A `.env` written before E2-04 keeps working **without edits**: its `DATABASE_URL` and `SYNC_DATABASE_URL` lines are overridden inside the containers. To use other passwords, add the two `HIREFLOW_*` lines to `.env` before running `make db-roles` (and run it again after changing them).

What changes for the running system:

- The API and the workers **refuse to start** as a superuser, a `BYPASSRLS` role, or a role that owns (or is a member of the owner of) a table: `UnsafeDatabaseRole: Refusing to start the API: its database user would bypass row-level security (…)`. In the logs of a healthy start: `database_role_checked role=hireflow_app`.
- A Celery message **without `agency_id`** (queued by code older than E2-03) is **rejected**: logged as `task_without_agency_id` and dropped without running or writing anything. Hence the drain in section 2.
- In development and tests, every connection a pool hands out is checked for a leftover `app.agency_id` (`TenantLeak`).

## 1. Preconditions

| Check | How |
|---|---|
| The E2-02 contract is applied (migration `e2b02c7a4d19`): `agency_id` is `NOT NULL` everywhere | `docker compose exec backend alembic current` shows `e2b02c7a4d19` (or E2-05's `e2c05b3f7a21`) before you pull E2-04. The RLS migration `e2c04a7f3b18` follows E2-05's `e2c05b3f7a21`, so `alembic upgrade head` applies both when needed |
| The E2-03 code (or later) has been running in the workers and the API, so every message queued since carries `agency_id` | `git log`; the workers were restarted after E2-03 was deployed |
| The verification passes | `docker compose exec -T backend python -m app.modules.ingestion.verify_people_applications` exits `0` |
| A database backup exists (production) | your backup tooling. Locally: `docker compose exec -T postgres pg_dump -U resumeai -Fc -f /tmp/before-e2-04.dump resumeai`, then `docker compose cp postgres:/tmp/before-e2-04.dump .` (before section 3 recreates the container) |
| No long-running transaction | `SELECT pid, state, xact_start, query FROM pg_stat_activity WHERE xact_start < now() - interval '1 minute';` |

## 2. Order of operations

1. **Drain the queues.** Stop new uploads (nobody uploading), then wait until both Celery queues and the unacknowledged set are empty:

   ```bash
   docker compose exec redis redis-cli LLEN extract_queue     # 0
   docker compose exec redis redis-cli LLEN ai_queue          # 0
   docker compose exec redis redis-cli HLEN unacked           # 0 (messages a worker holds)
   ```

   A message queued by E2-03 code carries `agency_id` and would run after the switch too; the drain is for anything older (rejected from now on) and so that no task is half-way when the workers restart. Resumes left `extracting` or `processing` can be re-queued afterwards with `reset_stuck` (section 6).
2. **Pull the E2-04 code** (`git pull`). It adds no dependency: no image rebuild is needed.
3. **Create the roles and hand the tables to the owner**: `make db-roles` (section 3). Safe to run while the old containers are up.
4. **Enable RLS**: run the migration as the owner in a one-off container, so the running services are not recreated yet (section 4):

   ```bash
   docker compose run --rm --no-deps backend alembic upgrade head
   ```

5. **Switch the services to the app role**: `docker compose up -d` recreates the backend, workers, beat and Flower with the new URLs (their configuration changed), and they check their role at start-up.
6. **Verify** (section 5), upload one resume and watch it reach `done`.

Between steps 4 and 5 the old containers still connect as the bootstrap superuser, which row-level security does not restrict: the app keeps working, exactly as before. From step 5 on it runs as `hireflow_app`.

## 3. Create the roles on an existing volume (`make db-roles`)

A **new** volume gets the roles from the init script (`docker/postgres/init/10-roles.sh`, run once by the postgres image after it creates `POSTGRES_DB`). A volume created **before E2-04** needs it run once by hand:

```bash
make db-roles
# without make (PowerShell, or Git Bash with MSYS_NO_PATHCONV=1):
docker compose up -d --wait postgres
docker compose exec -T postgres bash /docker-entrypoint-initdb.d/10-roles.sh
```

`docker compose up -d postgres` recreates the postgres **container** (it gains two read-only mounts, `docker/postgres` and the init directory, and the two password variables); the **volume and its data stay**. The script runs `docker/postgres/roles.sql` with psql as `POSTGRES_USER`, connected to `POSTGRES_DB`:

1. creates `hireflow_owner` and `hireflow_app` if they are missing, and (every run) resets their attributes (`LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION`) and passwords, and removes any membership of the app role in the owner role;
2. `GRANT CONNECT, TEMPORARY` on the database to both, `ALTER SCHEMA public OWNER TO hireflow_owner`, `REVOKE CREATE ON SCHEMA public FROM PUBLIC`, `GRANT USAGE ON SCHEMA public TO hireflow_app`;
3. hands every table, view, sequence, function and type of `public` that the owner role does not own yet to `hireflow_owner` (`ALTER … OWNER TO`; sequences that belong to a column follow their table; extension objects are left alone).

It changes **no data, no grant on a table and no policy** (those are the migration's), takes only brief catalog locks, and is **idempotent**: a second run changes nothing. Output: `hireflow roles: hireflow_owner and hireflow_app are ready in database "resumeai"`.

## 4. Enable row-level security (the migration)

`alembic upgrade head` (as `hireflow_owner`, through `MIGRATION_DATABASE_URL`) applies `e2c04a7f3b18` in **one transaction** with `lock_timeout` 5 s (each `ALTER TABLE` and `CREATE POLICY` takes a short `ACCESS EXCLUSIVE` lock; nothing is scanned or rewritten):

- `app_current_agency()`: `nullif(current_setting('app.agency_id', true), '')::uuid`;
- for `hireflow_app`: `SELECT, INSERT, UPDATE, DELETE` on every table except `alembic_version` (nothing), `USAGE` on every sequence, and `ALTER DEFAULT PRIVILEGES FOR ROLE hireflow_owner` so that tables and sequences the owner creates later get the same grants (E2-05's identity tables);
- on `clients`, `campaigns`, `applications`, `people`, `person_identifiers`, `identity_conflicts`, `person_resumes`, `resumes`: `ENABLE` and `FORCE ROW LEVEL SECURITY`, policy `tenant_isolation` (`FOR ALL TO hireflow_app USING (agency_id = (SELECT app_current_agency())) WITH CHECK (…same…)`) and policy `maintenance` (`FOR ALL TO hireflow_owner USING (true) WITH CHECK (true)`);
- the global tables `agencies` and `legacy_candidate_map` get the grants and no policy (spec §6.4). (`legacy_candidate_map` was dropped with `/api/v1` in E2-12, migration `e2f12a6c9d40`, and left the allowlist.)

It **refuses**, changing nothing, when:

| Message | Fix |
|---|---|
| `the database roles hireflow_app, hireflow_owner do not exist` | run section 3 first |
| `this migration runs as hireflow_owner, not resumeai` | `MIGRATION_DATABASE_URL` must name the owner role (Compose sets it; outside Compose, set it) |
| `these tables are not owned by hireflow_owner: …` | run section 3 (it hands them over) |
| `MIGRATION_DATABASE_URL is not set` | the backend container was created before E2-04: use `docker compose run --rm --no-deps backend …` (a fresh container) or `docker compose up -d` first |
| `canceling statement due to lock timeout` | a long transaction holds a table: wait for it (or end it) and run the migration again |

## 5. Verify

```bash
docker compose exec backend python -m app.devtools.check_rls
```

It reads the catalogue (as the owner) and checks everything spec §6.2 asks for: the roles, the owner of every relation, `ENABLE` + `FORCE` and both policies on every table that is not on the global allowlist, the app role's grants table by table and on every sequence, the default privileges, `app_current_agency()`, and the app's own URL as the start-up check sees it. Expected output:

```
check_rls: 8 tenant tables: applications, campaigns, clients, identity_conflicts, people, person_identifiers, person_resumes, resumes
check_rls: global (allowlisted) tables: agencies, alembic_version, audit_events, invitations, membership_clients, memberships, oidc_accounts, password_reset_tokens, user_sessions, users
check_rls: OK
```

Exit `1` lists each problem (`PROBLEM people: no tenant_isolation policy`, …). The same check runs in the isolation suite and in the CI `migrations` job. By hand, in `make shell-db`:

```sql
-- RLS enabled and forced, per table (global tables: f, f)
SELECT relname, relrowsecurity, relforcerowsecurity
FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind = 'r' ORDER BY 1;

-- The two policies of every tenant table
SELECT tablename, policyname, roles, cmd, qual, with_check FROM pg_policies ORDER BY 1, 2;

-- The roles: neither is a superuser or BYPASSRLS
SELECT rolname, rolsuper, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname LIKE 'hireflow_%';

-- Owners: hireflow_owner everywhere, hireflow_app nowhere
SELECT tablename, tableowner FROM pg_tables WHERE schemaname = 'public' ORDER BY 1;

-- The app role's grants
SELECT table_name, string_agg(privilege_type, ', ' ORDER BY privilege_type)
FROM information_schema.role_table_grants WHERE grantee = 'hireflow_app' GROUP BY 1 ORDER BY 1;

-- What the app role sees without an agency: nothing
SET ROLE hireflow_app; SELECT count(*) FROM campaigns; RESET ROLE;
-- ...and with one (in a transaction: the setting is transaction-local)
BEGIN; SET ROLE hireflow_app;
SELECT set_config('app.agency_id', '00000000-0000-4000-8000-00000000a001', true);
SELECT count(*) FROM campaigns; ROLLBACK;
```

Then: `docker compose logs backend worker_extract worker_ai | grep database_role_checked` shows `role=hireflow_app` for each; `make verify` passes (it runs as the owner); an upload is processed to `done`.

## 6. After the switch

- **Re-queue stuck resumes** (as the owner, every agency, each with its own agency): `docker compose exec backend python -m app.modules.ingestion.reset_stuck`.
- **Operator commands** connect as the owner through `MIGRATION_DATABASE_URL` (`app.platform.maintenance`). A new command that reads several agencies uses `maintenance.owner_session()`; as the app role it would silently see nothing.
- **A new tenant table** gets `enable_tenant_rls(table)` (`app.platform.rls`) in its migration; a new global table joins `GLOBAL_TABLES` with a reason. `check_rls` and the isolation suite fail otherwise. Grants come from the default privileges.

## 7. Roll back

The start-up check refuses the owner role and the superuser, so the E2-04 code cannot simply be pointed back at another role. Roll back the migration with the E2-04 code (it holds the downgrade), then run the previous release, which connects as `POSTGRES_USER` again (the `.env` URLs; a superuser is not restricted by RLS):

```bash
docker compose stop backend worker_extract worker_ai celery_beat flower   # drain first (section 2)
docker compose run --rm --no-deps backend alembic downgrade e2c05b3f7a21   # E2-05 stays
git checkout <the release before E2-04>
docker compose up -d
```

The downgrade drops the policies, `NO FORCE` / `DISABLE` RLS, revokes the grants and default privileges it gave the app role (E2-05's identity tables keep the grants their own migration gave) and drops `app_current_agency()`. The roles and the new ownership stay: they are harmless to the previous release (its superuser owns nothing but can do everything) and are reused when E2-04 is deployed again (`make db-roles` is idempotent). To remove them as well, after the downgrade, as the superuser: `REASSIGN OWNED BY hireflow_owner TO resumeai; DROP OWNED BY hireflow_app; DROP ROLE hireflow_app; DROP OWNED BY hireflow_owner; DROP ROLE hireflow_owner;`.

## 8. Your local dev stack after merging E2-04 (with E2-05)

E2-05 (identity core, PR #58) merges first, then E2-04; pull both and upgrade once. The dev stack is an existing volume with real data (the `resumeai_*` containers of the `ai-recruitment-platform` Compose project). From the repository root, in PowerShell (in Git Bash, prefix the `exec … /docker-entrypoint-initdb.d/…` line with `MSYS_NO_PATHCONV=1`). No `.env` edit is needed: Compose builds the database URLs, and `AUTH_REQUIRED` defaults to `false` in the dev stack.

```powershell
git pull                                                     # master with E2-05 and E2-04
# 1. Drain: no uploads running; these must print 0
docker compose exec redis redis-cli LLEN extract_queue
docker compose exec redis redis-cli LLEN ai_queue
docker compose exec redis redis-cli HLEN unacked
# 2. Optional backup (written in the container, then copied out: PowerShell 5.1 would mangle a
#    binary dump piped through `>`)
docker compose exec -T postgres pg_dump -U resumeai -Fc -f /tmp/before-e2-04.dump resumeai
docker compose cp postgres:/tmp/before-e2-04.dump .
# 3. Rebuild the backend images: E2-05 adds a dependency (argon2-cffi)
docker compose build backend worker_extract worker_ai celery_beat flower
# 4. Roles and ownership (recreates the postgres container; the volume stays)
docker compose up -d --wait postgres
docker compose exec -T postgres bash /docker-entrypoint-initdb.d/10-roles.sh
# 5. Migrate as hireflow_owner, in a one-off container on the new image: E2-05's identity
#    tables (e2c05b3f7a21), then row-level security (e2c04a7f3b18)
docker compose run --rm --no-deps backend alembic upgrade head
# 6. Recreate the services on the new image and the app role
docker compose up -d --wait
# 7. Verify
docker compose exec backend alembic current                  # e2c04a7f3b18 (head)
docker compose exec backend python -m app.devtools.check_rls # check_rls: OK
docker compose exec -T backend python -m app.modules.ingestion.verify_people_applications
docker compose logs backend worker_extract worker_ai | Select-String database_role_checked
# 8. Sign-in accounts (E2-05; README "Signing in locally"): the seed creates them, and with
#    --if-empty leaves your data alone
docker compose exec -T backend python -m app.devtools.seed --if-empty
```

With `make`: `docker compose build`, `make db-roles`, then `docker compose run --rm --no-deps backend alembic upgrade head`, then `docker compose up -d --wait`. If you use your own `POSTGRES_DB` or passwords in `.env`, nothing changes: the URLs are built from them.
