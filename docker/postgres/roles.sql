-- The two database roles of row-level security (EPIC-02 spec §6.2, §7.2; ADR 0009), and the
-- ownership of the current database's `public` schema.
--
--   hireflow_owner  LOGIN, not a superuser, no BYPASSRLS. Owns the schema and every table:
--                   Alembic and the operator commands that span agencies connect as it
--                   (MIGRATION_DATABASE_URL). Its access to tenant rows is the named
--                   `maintenance` policy.
--   hireflow_app    LOGIN, not a superuser, no BYPASSRLS, owns nothing, member of nothing. The
--                   API and the Celery workers connect as it (DATABASE_URL, SYNC_DATABASE_URL);
--                   the `tenant_isolation` policies limit it to the transaction's agency.
--
-- Run by psql as a superuser, connected to the application's database, with two variables:
--
--   psql -v ON_ERROR_STOP=1 -v owner_password=... -v app_password=... -f roles.sql
--
-- Callers: docker/postgres/init/10-roles.sh (a new Compose volume, at initdb time), the same
-- script through `make db-roles` (an existing volume), the test harness (tests/postgres.py) and
-- the CI `migrations` job. Runbook: docs/runbooks/tenancy-rls.md.
--
-- Idempotent: it creates what is missing and resets what exists (attributes, passwords,
-- ownership), so running it again is always safe. It changes no data, no grant on a table and
-- no policy: those belong to the migrations, which run as hireflow_owner.
\set ON_ERROR_STOP on

-- ── Roles (cluster-wide) ────────────────────────────────────
SELECT 'CREATE ROLE hireflow_owner'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'hireflow_owner') \gexec
SELECT 'CREATE ROLE hireflow_app'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'hireflow_app') \gexec

-- Attributes are (re)set on every run: a role edited by hand gets back to what RLS relies on.
SELECT format(
  'ALTER ROLE hireflow_owner WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE '
  'NOREPLICATION PASSWORD %L', :'owner_password') \gexec
SELECT format(
  'ALTER ROLE hireflow_app WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE '
  'NOREPLICATION PASSWORD %L', :'app_password') \gexec

-- A policy granted to a role applies to its members: the app role must never be a member of
-- the owner role (the API and workers refuse to start if it is).
SELECT 'REVOKE hireflow_owner FROM hireflow_app'
WHERE pg_has_role('hireflow_app', 'hireflow_owner', 'MEMBER') \gexec

-- ── This database ───────────────────────────────────────────
SELECT format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO hireflow_owner, hireflow_app',
              current_database()) \gexec

-- The owner role owns the schema, so it can create tables in it; the app role may only use it.
ALTER SCHEMA public OWNER TO hireflow_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO hireflow_app;

-- An existing volume (created before E2-04): the tables, views, sequences, functions and types
-- in `public` belong to the bootstrap superuser (POSTGRES_USER). Hand them to the owner role.
-- Sequences that belong to a column (serial, identity) follow their table; extension objects
-- stay with the extension.
SELECT format('ALTER %s %I.%I OWNER TO hireflow_owner',
              CASE c.relkind WHEN 'v' THEN 'VIEW'
                             WHEN 'm' THEN 'MATERIALIZED VIEW'
                             WHEN 'f' THEN 'FOREIGN TABLE'
                             ELSE 'TABLE' END,
              n.nspname, c.relname)
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND c.relowner <> 'hireflow_owner'::regrole
  AND NOT EXISTS (SELECT FROM pg_depend d
                  WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
ORDER BY c.relname \gexec

SELECT format('ALTER SEQUENCE %I.%I OWNER TO hireflow_owner', n.nspname, c.relname)
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind = 'S'
  AND c.relowner <> 'hireflow_owner'::regrole
  AND NOT EXISTS (SELECT FROM pg_depend d
                  WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid
                    AND d.deptype IN ('a', 'i', 'e'))
ORDER BY c.relname \gexec

SELECT format('ALTER ROUTINE %s OWNER TO hireflow_owner', p.oid::regprocedure)
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
  AND p.prokind IN ('f', 'p')
  AND p.proowner <> 'hireflow_owner'::regrole
  AND NOT EXISTS (SELECT FROM pg_depend d
                  WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e')
ORDER BY 1 \gexec

SELECT format('ALTER TYPE %s OWNER TO hireflow_owner', t.oid::regtype)
FROM pg_type t
JOIN pg_namespace n ON n.oid = t.typnamespace
WHERE n.nspname = 'public'
  AND t.typtype IN ('e', 'd', 'r')
  AND t.typowner <> 'hireflow_owner'::regrole
  AND NOT EXISTS (SELECT FROM pg_depend d
                  WHERE d.classid = 'pg_type'::regclass AND d.objid = t.oid AND d.deptype = 'e')
ORDER BY 1 \gexec
