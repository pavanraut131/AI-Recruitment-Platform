#!/usr/bin/env bash
# Creates the row-level-security roles hireflow_owner and hireflow_app and gives the owner role
# the application database's schema (docker/postgres/roles.sql; EPIC-02 spec §6.2, §7.2).
#
# - A new volume: the postgres image runs it once, from /docker-entrypoint-initdb.d, after it
#   has created POSTGRES_DB.
# - An existing volume (created before E2-04): `make db-roles` runs it in the running
#   container, which also hands the existing tables to hireflow_owner. Runbook:
#   docs/runbooks/tenancy-rls.md.
#
# Idempotent. The passwords come from the container's environment (docker-compose.yml passes
# HIREFLOW_OWNER_PASSWORD and HIREFLOW_APP_PASSWORD, with development defaults).
#
# No `set -u`: the image's entrypoint sources scripts that are not executable, and the option
# would then apply to the rest of the entrypoint.
set -eo pipefail

: "${POSTGRES_USER:?}" "${POSTGRES_DB:?}"
: "${HIREFLOW_OWNER_PASSWORD:?set HIREFLOW_OWNER_PASSWORD}" "${HIREFLOW_APP_PASSWORD:?set HIREFLOW_APP_PASSWORD}"

psql --no-psqlrc --quiet -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v owner_password="$HIREFLOW_OWNER_PASSWORD" \
  -v app_password="$HIREFLOW_APP_PASSWORD" \
  -f /hireflow/roles.sql

echo "hireflow roles: hireflow_owner and hireflow_app are ready in database \"$POSTGRES_DB\""
