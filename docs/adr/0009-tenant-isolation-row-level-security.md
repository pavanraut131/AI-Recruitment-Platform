# 0009 · Tenant isolation with Postgres row-level security behind explicit filtering
**Status:** accepted (E2-04, 2026-11-24)  ·  **Date:** 2026-11-19

## Context
E2 makes HireFlow AI multi-tenant ([spec §3](../specs/EPIC-02-identity-tenancy.md#3-tenancy-model-and-data)): every campaign, application, Person, identifier, conflict and resume belongs to one **agency**. Agencies are competitors, and the data is candidates' personal data. One agency seeing another's talent pool would be a data breach under GDPR / DPDP, not just a bug, and it would end the product's credibility with the agencies it is sold to.

The forces:

- **Isolation has to survive mistakes.** The backend has dozens of repository queries across `hiring`, `talent` and `ingestion`, plus composed endpoints, Celery tasks, an SSE stream and operator scripts. A single query without `WHERE agency_id = …` (including a future one written in a hurry) must not leak.
- **One database, one schema.** The modular monolith ([ADR 0001](0001-modular-monolith.md)) shares one Postgres, and the expected scale (tens to hundreds of agencies, thousands to low millions of rows each) does not justify a database or schema per tenant.
- **No cross-module foreign keys** ([ADR 0003](0003-no-cross-module-foreign-keys.md)): `agency_id` on hiring, talent and ingestion tables cannot reference identity's `agencies` table.
- **Several execution contexts:** async requests on a pooled asyncpg engine, sync Celery workers that commit several times per task, long-lived SSE streams, and operator scripts that legitimately span agencies.
- **Managed Postgres later** (E5, Cloud SQL): the application will not have a real superuser, and may sit behind a transaction-mode pooler.

## Decision
- **Shared schema with an `agency_id` column** on every tenant-owned table, NOT NULL, leading every relevant index, and part of every unique constraint (a global unique would reveal other tenants' rows through its error).
- **Two layers of isolation:**
  1. **Explicit filtering in the repositories.** The agency is bound to the database session (`app.platform.tenancy.bind`). Repositories read it with `agency_id_of(db)`, which raises when the session is unbound, filter every query by it and set it on every insert.
  2. **Postgres row-level security as the backstop.** Every tenant table has `ENABLE` and `FORCE ROW LEVEL SECURITY` and a policy `tenant_isolation … TO hireflow_app USING (agency_id = app_current_agency()) WITH CHECK (same)`. `app_current_agency()` reads the transaction-local setting `app.agency_id` and is NULL when it is unset, so an unbound session sees no rows and can write none (fail closed).
- **Roles:** the application and the workers connect as `hireflow_app`: not a superuser, no `BYPASSRLS`, owns nothing. Migrations and operator scripts that span agencies connect as `hireflow_owner`, which owns the tables and has an explicit `maintenance` policy (`USING (true)`). The API and workers check their own role at start-up and refuse to run as a superuser, a `BYPASSRLS` role or a table owner.
- **Setting the tenant:** `set_config('app.agency_id', :id, true)` (transaction-local, `SET LOCAL` with a bind parameter), run by an `after_begin` session listener at the start of **every** transaction of a bound session. Session-level `SET` is forbidden, so a pooled connection never carries a tenant into the next checkout.
  - **HTTP:** the request's session is bound to the principal's active agency.
  - **SSE:** the agency is checked when the stream opens; the Redis channel is namespaced by agency, and the stream holds no database connection.
  - **Celery:** tasks carry `agency_id` as an explicit argument, and the task base class binds it.
- **Global tables:** identity tables that must be read before a tenant is known (users, sessions, memberships, invitations, reset tokens, OIDC accounts, audit events) are not under agency RLS. They are private to the `identity` module and listed in an allowlist that a test checks.
- **Client scoping** (a user limited to some of the agency's clients) is enforced in the application, not by RLS: it is a boundary inside one tenant.
- **Proof:** the test suite runs as `hireflow_app`. A tenant-isolation suite checks the catalogue (RLS on every non-allowlisted table, the roles, the grants) and shows that a query with **no** agency filter, on a session bound to agency A, returns only A's rows. It also checks that an unbound session sees nothing, and that no response body from any v2 endpoint contains another agency's ids.

## Alternatives considered
- **Application filtering only** (a `WHERE agency_id = …` in every query, perhaps with a SQLAlchemy `do_orm_execute` hook adding it automatically). It is simpler and has no database coupling, but nothing catches the query that bypasses the hook (raw SQL, `text()`, a Core statement in a script) or forgets it. One mistake is a breach, and the only defence is review. We keep the explicit filters as the first layer, but not as the only one.
- **A database per tenant.** It gives the strongest isolation and per-tenant backup and restore, but it means N connection pools, N migration runs, cross-tenant operations (support, analytics, the operator scripts) across N databases, and provisioning per sign-up. It is too heavy for a solo-built product with many small agencies. It could come back for a large customer that requires it.
- **A schema per tenant** (`search_path` per request). Isolation is better than filtering, but migrations multiply by the number of tenants, `search_path` is session state that leaks through pooled connections exactly as `SET` would, and the ORM metadata assumes one schema.
- **RLS without `FORCE`.** Then the table owner bypasses every policy silently. With `FORCE`, the owner's access is a named policy granted to one role, and any other role, including the app role if it ever owned a table by accident, gets no rows by default.
- **`BYPASSRLS` on the migration role** instead of an owner policy. It works on self-hosted Postgres, but a role can only get it from a superuser (or a role that has it itself), which managed Postgres restricts. It is also invisible in `pg_policies`, so the catalogue test could not see it.
- **A session-level `SET app.agency_id`** (or a connection per tenant). With a connection pool it leaks the previous request's tenant to the next one, which is the classic RLS pooling bug. Transaction-local settings end with the transaction.
- **Passing the agency as a function argument through every service and repository** instead of binding it to the session. It is very explicit, but it changes nearly every signature in three modules, and nothing ties the argument to what the database enforces. Binding it to the session gives one source for both the filter and the RLS setting, and the same mechanism works in HTTP, SSE checks and Celery.

## Consequences
- **Easier:**
  - A forgotten filter or a raw query is contained by the database.
  - New tables get isolation from one migration helper and a failing catalogue test if they do not.
  - Operator scripts state their cross-tenant intent by using the owner role.
  - The design works unchanged with PgBouncer in transaction mode or Cloud SQL.
- **Harder:**
  - Two database roles to provision in every environment (Compose init script, tests, Terraform in E5).
  - The test harness must run as the app role, so fixtures and factories bind a tenant.
  - Data migrations must run as the owner.
  - Debugging "no rows" now has one more cause (an unbound session), which `agency_id_of` makes loud.
- **Watch for:**
  - Policy quals can change query plans. Non-leakproof operators such as `ILIKE` run after the policy filter, so check `EXPLAIN` on the list endpoints and keep `(agency_id, …)` indexes.
  - Any `SET` without `LOCAL`, and any autocommit session doing tenant work.
  - New global tables, which must be justified in the allowlist.
  - Unique constraints without `agency_id`.
  - Cross-module consistency (an application, its Person and its resume in one agency), which no foreign key can enforce: the verification script checks it.

## Implementation notes (E2-04)
- **Roles:** `docker/postgres/roles.sql` creates `hireflow_owner` and `hireflow_app` (attributes and passwords reset on every run) and gives the owner the `public` schema and every existing relation. A new Compose volume runs it from the init script (`docker/postgres/init/10-roles.sh`); an existing one with `make db-roles`; the tests and the CI `migrations` job run the same file. The owner role has no `CREATEROLE`, so the migration does not create the roles: it refuses to run until they exist and own the tables.
- **Migration** `e2c04a7f3b18`: `app_current_agency()`, the grants and `ALTER DEFAULT PRIVILEGES FOR ROLE hireflow_owner` (tables the owner creates later get `SELECT, INSERT, UPDATE, DELETE` for the app role, sequences `USAGE`), and `app.platform.rls.enable_tenant_rls` on the eight tenant tables. The global allowlist is `app.platform.rls.GLOBAL_TABLES`; `catalogue_problems` (the isolation suite, `python -m app.devtools.check_rls`, CI) fails on any other table without the policies.
- **Start-up check:** `app.platform.rls.check_app_role` in the API's lifespan and the workers' `worker_init` also refuses a role that is a *member* of a table's owner, since a policy granted to a role applies to its members. There is no switch to skip it.
- **Operator commands** use `app.platform.maintenance.owner_session()` (`MIGRATION_DATABASE_URL`); the seed stays on the app role, bound to the default agency.
- Runbook: [tenancy-rls.md](../runbooks/tenancy-rls.md).
