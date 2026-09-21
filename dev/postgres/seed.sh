#!/bin/sh
set -eu

umask 077
target_password="$(sed -n '1p' "${SCHEMII_TEST_POSTGRES_PASSWORD_FILE:?SCHEMII_TEST_POSTGRES_PASSWORD_FILE is required}")"
admin_password="$(sed -n '1p' "${SCHEMII_DEMO_ADMIN_PASSWORD_FILE:?SCHEMII_DEMO_ADMIN_PASSWORD_FILE is required}")"
test -n "$target_password"
test -n "$admin_password"
escaped_target_password="$(printf '%s' "$target_password" | sed 's/\\/\\\\/g; s/:/\\:/g')"
escaped_admin_password="$(printf '%s' "$admin_password" | sed 's/\\/\\\\/g; s/:/\\:/g')"
printf '%s:%s:*:%s:%s\n' "$PGHOST" "$PGPORT" "$PGUSER" "$escaped_target_password" > /tmp/pgpass
printf '%s:%s:*:%s:%s\n' "$PGHOST" "$PGPORT" "$SCHEMII_DEMO_ADMIN_USER" "$escaped_admin_password" >> /tmp/pgpass
export PGPASSFILE=/tmp/pgpass

psql --set ON_ERROR_STOP=1 --file /seed/seed.sql

demo_database="schemii_migration_demo"
reset_demo="${SCHEMII_RESET_MIGRATION_DEMO:-0}"
if [ "$reset_demo" = "1" ]; then
  printf 'Resetting the isolated %s fixture database...\n' "$demo_database"
  dropdb --username "$SCHEMII_DEMO_ADMIN_USER" --if-exists --force "$demo_database"
  createdb --username "$SCHEMII_DEMO_ADMIN_USER" --owner "$PGUSER" "$demo_database"
elif ! psql --username "$SCHEMII_DEMO_ADMIN_USER" --dbname postgres --tuples-only --no-align \
    --command "SELECT 1 FROM pg_database WHERE datname = '${demo_database}'" \
    | grep -qx 1; then
  createdb --username "$SCHEMII_DEMO_ADMIN_USER" --owner "$PGUSER" "$demo_database"
fi
psql --dbname "$demo_database" --set ON_ERROR_STOP=1 --file /seed/migration-demo.sql

# The browser suite owns this synthetic HR source on the isolated demo server.
# Never connect to or modify an externally configured organization database.
if ! psql --username "$SCHEMII_DEMO_ADMIN_USER" --dbname postgres --tuples-only --no-align \
    --command "SELECT 1 FROM pg_database WHERE datname = 'organization'" \
    | grep -qx 1; then
  createdb --username "$SCHEMII_DEMO_ADMIN_USER" --owner "$PGUSER" organization
fi
exec psql --dbname organization --set ON_ERROR_STOP=1 --file /seed/organization.sql
