"""Row-level security (EPIC-02 spec §6.2, ADR 0009): the two database roles, the policies every
tenant table gets, and the start-up check of the application's own role.

**Roles.** :data:`OWNER_ROLE` owns the schema and every table; Alembic and the operator
commands that span agencies connect as it (``MIGRATION_DATABASE_URL``,
:mod:`app.platform.maintenance`). :data:`APP_ROLE` is what the API and the Celery workers
connect as (``DATABASE_URL``, ``SYNC_DATABASE_URL``): not a superuser, no ``BYPASSRLS``, owns
nothing. ``docker/postgres/roles.sql`` creates both; the migrations grant and enforce.

**Policies** (:func:`enable_tenant_rls`). Every tenant table has ``ENABLE`` and ``FORCE ROW
LEVEL SECURITY`` and two policies:

- ``tenant_isolation`` for :data:`APP_ROLE`: ``USING`` and ``WITH CHECK`` ``agency_id = (SELECT
  app_current_agency())``. ``app_current_agency()`` reads the transaction-local setting
  ``app.agency_id`` (set by :mod:`app.platform.tenancy` at every ``BEGIN`` of a bound session)
  and is ``NULL`` when it is empty, so an unbound session reads nothing and writes nothing.
- ``maintenance`` for :data:`OWNER_ROLE`: ``USING (true) WITH CHECK (true)``. ``FORCE`` makes
  the owner subject to policies too, so its access is this visible, named policy instead of a
  silent bypass; any other role sees nothing.

**Migrations** call :func:`enable_tenant_rls` for a new tenant table (and grant nothing: the
owner's default privileges give the application role ``SELECT, INSERT, UPDATE, DELETE`` on every
table it creates). The statements are part of the migrations' history: change them only
together with a migration that changes the existing policies.

**Global tables** (spec §6.4, :data:`GLOBAL_TABLES`) are read before a tenant is known, so they
are not under agency RLS. Every other table of the schema must be: :func:`catalogue_problems`
(the isolation suite, ``python -m app.devtools.check_rls``, the CI ``migrations`` job) fails on a
table that is neither under the policies nor on the allowlist with a reason.

**Start-up check** (:func:`check_app_role`). The API (``app.main`` lifespan) and the workers
(``celery_app``'s ``worker_init``) refuse to run when their database user is a superuser, has
``BYPASSRLS``, owns (or is a member of the owner of) a table in the schema: each of those would
switch row-level security off for it, silently. There is no switch to skip it, in any
environment.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from sqlalchemy import Connection, text

OWNER_ROLE: Final = "hireflow_owner"
APP_ROLE: Final = "hireflow_app"

TENANT_POLICY: Final = "tenant_isolation"
OWNER_POLICY: Final = "maintenance"

CURRENT_AGENCY_FUNCTION: Final = "app_current_agency"

GLOBAL_TABLES: Final[Mapping[str, str]] = {
    # table -> why it is not under agency row-level security (spec §6.4). A new table either
    # gets enable_tenant_rls in its migration or joins this list with a reason, in review.
    "agencies": "the tenants themselves: read before or across tenants (login, the switcher)",
    "legacy_candidate_map": "id pairs read only by /api/v1 (removed in E2-12); the "
    "applications they lead to are under RLS",
    "alembic_version": "migration bookkeeping; the application role has no grant on it",
}

TABLE_PRIVILEGES: Final = ("SELECT", "INSERT", "UPDATE", "DELETE")
"""What the application role may do on a table (spec §6.2); never ``TRUNCATE``, ``REFERENCES``
or ``TRIGGER``."""

APP_PRIVILEGE_EXCEPTIONS: Final[Mapping[str, tuple[str, ...]]] = {
    "alembic_version": (),
    "audit_events": ("SELECT", "INSERT"),  # append-only by grant (E2-05, spec §4.7)
}

# NULL when the setting is unset or empty: `agency_id = NULL` is never true (fail closed). An
# invalid value fails the statement with a cast error, which is louder still.
CREATE_CURRENT_AGENCY_FUNCTION: Final = f"""
CREATE OR REPLACE FUNCTION {CURRENT_AGENCY_FUNCTION}() RETURNS uuid
LANGUAGE sql STABLE PARALLEL SAFE AS
$$ SELECT nullif(current_setting('app.agency_id', true), '')::uuid $$
"""
DROP_CURRENT_AGENCY_FUNCTION: Final = f"DROP FUNCTION IF EXISTS {CURRENT_AGENCY_FUNCTION}()"

# `(SELECT app_current_agency())` is an init plan: evaluated once per statement, not per row.
_CURRENT: Final = f"(SELECT {CURRENT_AGENCY_FUNCTION}())"


def enable_tenant_rls(table: str, column: str = "agency_id") -> list[str]:
    """The statements that put ``table`` under agency row-level security (spec §6.2)."""
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {TENANT_POLICY} ON {table} AS PERMISSIVE FOR ALL TO {APP_ROLE} "
        f"USING ({column} = {_CURRENT}) WITH CHECK ({column} = {_CURRENT})",
        f"CREATE POLICY {OWNER_POLICY} ON {table} AS PERMISSIVE FOR ALL TO {OWNER_ROLE} "
        "USING (true) WITH CHECK (true)",
    ]


def disable_tenant_rls(table: str) -> list[str]:
    """The reverse of :func:`enable_tenant_rls` (a downgrade)."""
    return [
        f"DROP POLICY IF EXISTS {OWNER_POLICY} ON {table}",
        f"DROP POLICY IF EXISTS {TENANT_POLICY} ON {table}",
        f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY",
    ]


# ── The start-up check ────────────────────────────────────────
class UnsafeDatabaseRole(RuntimeError):
    """The process connects as a role that row-level security would not restrict."""


_ROLE = text(
    """
    SELECT r.rolname, r.rolsuper, r.rolbypassrls
    FROM pg_roles r
    WHERE r.rolname = current_user
    """
)
# Tables (and other relations) of the schema that the current user owns, or whose owner it is a
# member of: either way it would be the owner as far as RLS is concerned.
_OWNED = text(
    """
    SELECT c.relname, pg_get_userbyid(c.relowner) AS owner
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
      AND pg_has_role(current_user, c.relowner, 'MEMBER')
    ORDER BY c.relname
    LIMIT 5
    """
)


def role_problems(connection: Connection) -> list[str]:
    """What makes the connection's role unsafe to run the application as (empty: nothing)."""
    name, superuser, bypassrls = connection.execute(_ROLE).one()
    problems = []
    if superuser:
        problems.append(f"{name} is a superuser")
    if bypassrls:
        problems.append(f"{name} has BYPASSRLS")
    owned = connection.execute(_OWNED).all()
    if owned:
        listed = ", ".join(f"{rel} (owner {owner})" for rel, owner in owned)
        problems.append(f"{name} owns, or is a member of the owner of, {listed}")
    return problems


def check_app_role(connection: Connection, process: str) -> str:
    """Raise :class:`UnsafeDatabaseRole` unless the connection's role is restricted by
    row-level security (spec §6.2). Returns the role's name. ``process`` names the caller in
    the message ("the API", "the Celery worker")."""
    problems = role_problems(connection)
    if problems:
        raise UnsafeDatabaseRole(
            f"Refusing to start {process}: its database user would bypass row-level security "
            f"({'; '.join(problems)}). Connect as {APP_ROLE}: DATABASE_URL and SYNC_DATABASE_URL "
            f"must use it, MIGRATION_DATABASE_URL the owner {OWNER_ROLE} "
            "(docs/runbooks/tenancy-rls.md)."
        )
    name: str = connection.execute(text("SELECT current_user")).scalar_one()
    return name


# ── The catalogue (spec §11.2 test_catalogue; the CI migrations job) ─────
_ALL_PRIVILEGES = (*TABLE_PRIVILEGES, "TRUNCATE", "REFERENCES", "TRIGGER")


def _role_attributes(connection: Connection, role: str) -> tuple[bool, bool] | None:
    row = connection.execute(
        text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :role"), {"role": role}
    ).one_or_none()
    return None if row is None else (bool(row[0]), bool(row[1]))


def catalogue_problems(connection: Connection) -> list[str]:
    """Everything about the schema's row-level security that differs from spec §6.2 (empty:
    nothing). Read-only, and readable by any role: it queries the catalogue only."""
    problems: list[str] = []

    # Roles.
    for role in (OWNER_ROLE, APP_ROLE):
        attributes = _role_attributes(connection, role)
        if attributes is None:
            problems.append(f"role {role} does not exist")
            continue
        superuser, bypassrls = attributes
        if superuser or bypassrls:
            problems.append(f"role {role} is a superuser or has BYPASSRLS")
    if problems:
        return problems
    if connection.execute(
        text("SELECT pg_has_role(:app, :owner, 'MEMBER')"), {"app": APP_ROLE, "owner": OWNER_ROLE}
    ).scalar_one():
        problems.append(f"{APP_ROLE} is a member of {OWNER_ROLE} (its policies would apply)")

    tables = {
        name: (owner, enabled, forced)
        for name, owner, enabled, forced in connection.execute(
            text(
                "SELECT c.relname, pg_get_userbyid(c.relowner), c.relrowsecurity, "
                "c.relforcerowsecurity FROM pg_class c "
                "WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')"
            )
        ).tuples()
    }
    for name in sorted(set(GLOBAL_TABLES) - set(tables)):
        problems.append(f"{name} is on the global allowlist but does not exist")

    # Ownership: the owner role owns every relation; the app role owns nothing.
    for name, owner in connection.execute(
        text(
            "SELECT c.relname, pg_get_userbyid(c.relowner) FROM pg_class c "
            "WHERE c.relnamespace = 'public'::regnamespace "
            "AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S') AND pg_get_userbyid(c.relowner) <> :owner "
            "ORDER BY 1"
        ),
        {"owner": OWNER_ROLE},
    ).tuples():
        problems.append(f"{name} is owned by {owner}, not {OWNER_ROLE}")

    # Policies.
    policies: dict[str, dict[str, tuple[str, list[str], str | None, str | None, str]]] = {}
    for table, name, command, roles, qual, check, permissive in connection.execute(
        text(
            "SELECT tablename, policyname, cmd, roles::text[], qual, with_check, permissive "
            "FROM pg_policies WHERE schemaname = 'public'"
        )
    ).tuples():
        policies.setdefault(table, {})[name] = (command, list(roles), qual, check, permissive)

    current = f"{CURRENT_AGENCY_FUNCTION}()"
    for name, (_, enabled, forced) in sorted(tables.items()):
        on_table = policies.get(name, {})
        if name in GLOBAL_TABLES:
            if enabled or on_table:
                problems.append(f"{name} is a global table but has row-level security")
            continue
        if not (enabled and forced):
            problems.append(f"{name}: row-level security is not enabled and forced")
        tenant = on_table.get(TENANT_POLICY)
        if tenant is None:
            problems.append(f"{name}: no {TENANT_POLICY} policy")
        else:
            command, roles, qual, check, permissive = tenant
            if (command, roles, permissive) != ("ALL", [APP_ROLE], "PERMISSIVE") or not all(
                clause and "agency_id" in clause and current in clause for clause in (qual, check)
            ):
                problems.append(
                    f"{name}: {TENANT_POLICY} is not FOR ALL TO {APP_ROLE} with "
                    f"USING and WITH CHECK agency_id = {current}"
                )
        owner_policy = on_table.get(OWNER_POLICY)
        if owner_policy is None:
            problems.append(f"{name}: no {OWNER_POLICY} policy")
        elif owner_policy[:4] != ("ALL", [OWNER_ROLE], "true", "true"):
            problems.append(f"{name}: {OWNER_POLICY} is not FOR ALL TO {OWNER_ROLE} USING (true)")
        others = sorted(set(on_table) - {TENANT_POLICY, OWNER_POLICY})
        if others:
            problems.append(f"{name}: unexpected policies {others}")

    # Grants to the app role, table by table, and on every sequence.
    for name in sorted(tables):
        expected = set(APP_PRIVILEGE_EXCEPTIONS.get(name, TABLE_PRIVILEGES))
        granted = {
            privilege
            for privilege in _ALL_PRIVILEGES
            if connection.execute(
                text("SELECT has_table_privilege(:role, :table, :privilege)"),
                {"role": APP_ROLE, "table": f"public.{name}", "privilege": privilege},
            ).scalar_one()
        }
        if granted != expected:
            problems.append(
                f"{name}: {APP_ROLE} has {sorted(granted) or 'nothing'}, "
                f"expected {sorted(expected) or 'nothing'}"
            )
    for (sequence,) in connection.execute(
        text(
            "SELECT c.relname FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace "
            "AND c.relkind = 'S' AND NOT CASE WHEN c.relkind = 'S' "
            "THEN has_sequence_privilege(:role, c.oid, 'USAGE') END ORDER BY 1"
        ),
        {"role": APP_ROLE},
    ).tuples():
        problems.append(f"sequence {sequence}: {APP_ROLE} has no USAGE")

    # Default privileges: what the owner creates later gets the same grants.
    defaults = {
        kind: privileges
        for kind, privileges in connection.execute(
            text(
                "SELECT d.defaclobjtype, array_agg(a.privilege_type::text ORDER BY 1) "
                "FROM pg_default_acl d, aclexplode(d.defaclacl) a "
                "WHERE pg_get_userbyid(d.defaclrole) = :owner "
                "AND d.defaclnamespace = 'public'::regnamespace "
                "AND pg_get_userbyid(a.grantee) = :app GROUP BY d.defaclobjtype"
            ),
            {"owner": OWNER_ROLE, "app": APP_ROLE},
        ).tuples()
    }
    if sorted(defaults.get("r", [])) != sorted(TABLE_PRIVILEGES):
        problems.append(
            f"default privileges: tables {OWNER_ROLE} creates do not get "
            f"{', '.join(TABLE_PRIVILEGES)} for {APP_ROLE}"
        )
    if list(defaults.get("S", [])) != ["USAGE"]:
        problems.append(
            f"default privileges: sequences {OWNER_ROLE} creates do not get USAGE for {APP_ROLE}"
        )

    if not connection.execute(
        text("SELECT to_regprocedure(:function) IS NOT NULL"),
        {"function": f"public.{CURRENT_AGENCY_FUNCTION}()"},
    ).scalar_one():
        problems.append(f"function {CURRENT_AGENCY_FUNCTION}() does not exist")
    return problems


def tenant_tables(connection: Connection) -> list[str]:
    """Every table of the schema that is not on :data:`GLOBAL_TABLES` (sorted)."""
    names = connection.execute(
        text(
            "SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace "
            "AND relkind IN ('r', 'p') ORDER BY 1"
        )
    ).scalars()
    return [name for name in names if name not in GLOBAL_TABLES]
