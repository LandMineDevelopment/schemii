#!/bin/sh
set -eu

umask 077
password="${SCHEMII_TEST_POSTGRES_PASSWORD:?SCHEMII_TEST_POSTGRES_PASSWORD is required}"
escaped_password="$(printf '%s' "$password" | sed 's/\\/\\\\/g; s/:/\\:/g')"
printf '%s:%s:*:%s:%s\n' "$PGHOST" "$PGPORT" "$PGUSER" "$escaped_password" > /tmp/pgpass
export PGPASSFILE=/tmp/pgpass

psql --set ON_ERROR_STOP=1 --file /seed/seed.sql

demo_database="schemii_migration_demo"
reset_demo="${SCHEMII_RESET_MIGRATION_DEMO:-0}"
if [ "$reset_demo" = "1" ]; then
  printf 'Resetting the isolated %s fixture database...\n' "$demo_database"
  dropdb --if-exists --force "$demo_database"
  createdb "$demo_database"
elif ! psql --dbname postgres --tuples-only --no-align \
    --command "SELECT 1 FROM pg_database WHERE datname = '${demo_database}'" \
    | grep -qx 1; then
  createdb "$demo_database"
fi
exec psql --dbname "$demo_database" --set ON_ERROR_STOP=1 --file /seed/migration-demo.sql
