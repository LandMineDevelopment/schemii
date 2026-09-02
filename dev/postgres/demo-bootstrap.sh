#!/bin/sh
set -eu

umask 077
admin_password="$(sed -n '1p' "${SCHEMII_DEMO_ADMIN_PASSWORD_FILE:?}")"
target_password="$(sed -n '1p' "${SCHEMII_DEMO_TARGET_PASSWORD_FILE:?}")"
test -n "$admin_password"
test -n "$target_password"

escaped_admin_password="$(printf '%s' "$admin_password" | sed 's/\\/\\\\/g; s/:/\\:/g')"
printf '%s:%s:*:%s:%s\n' \
  "$PGHOST" "$PGPORT" "$SCHEMII_DEMO_ADMIN_USER" "$escaped_admin_password" \
  > /tmp/pgpass
export PGPASSFILE=/tmp/pgpass

# This is deliberately an every-start reconciliation step rather than a
# one-time image initialization hook. The disposable demo role must remain in
# sync with the launcher's durable secret even after an interrupted upgrade.
psql --username "$SCHEMII_DEMO_ADMIN_USER" \
  --dbname postgres \
  --set ON_ERROR_STOP=1 \
  --set target_user="$SCHEMII_DEMO_TARGET_USER" \
  --set target_password="$target_password" <<'SQL'
SELECT format(
  'CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION PASSWORD %L',
  :'target_user', :'target_password'
)
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'target_user') \gexec
SELECT format(
  'ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION PASSWORD %L',
  :'target_user', :'target_password'
) \gexec
SQL

psql --username "$SCHEMII_DEMO_ADMIN_USER" \
  --dbname postgres \
  --set ON_ERROR_STOP=1 \
  --set target_user="$SCHEMII_DEMO_TARGET_USER" \
  --set target_database="$SCHEMII_DEMO_TARGET_DATABASE" <<'SQL'
SELECT format(
  'CREATE DATABASE %I OWNER %I', :'target_database', :'target_user'
)
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'target_database') \gexec
SELECT format(
  'ALTER DATABASE %I OWNER TO %I', :'target_database', :'target_user'
) \gexec
SQL
