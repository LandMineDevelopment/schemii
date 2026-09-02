#!/bin/sh
set -eu

umask 077
bootstrap_password="$(sed -n '1p' "${SCHEMII_METADATA_BOOTSTRAP_PASSWORD_FILE:?}")"
app_password="$(sed -n '1p' "${SCHEMII_METADATA_APP_PASSWORD_FILE:?}")"
test -n "$bootstrap_password"
test -n "$app_password"
escaped_password="$(printf '%s' "$bootstrap_password" | sed 's/\\/\\\\/g; s/:/\\:/g')"
escaped_app_password="$(printf '%s' "$app_password" | sed 's/\\/\\\\/g; s/:/\\:/g')"
printf '%s:%s:*:%s:%s\n' "$PGHOST" "$PGPORT" "$PGUSER" "$escaped_password" > /tmp/pgpass
printf '%s:%s:*:%s:%s\n' "$PGHOST" "$PGPORT" "$SCHEMII_METADATA_APP_USER" "$escaped_app_password" >> /tmp/pgpass
export PGPASSFILE=/tmp/pgpass

# After the one-time ownership handoff, ordinary restarts prove the runtime
# role can connect and never re-enable the privileged bootstrap login.
if psql --username "$SCHEMII_METADATA_APP_USER" \
    --set ON_ERROR_STOP=1 \
    --tuples-only --no-align \
    --command "SELECT 1" 2>/dev/null | grep -qx 1; then
  exit 0
fi

psql --set ON_ERROR_STOP=1 \
  --set app_user="$SCHEMII_METADATA_APP_USER" \
  --set app_password="$app_password" \
  --set bootstrap_user="$PGUSER" \
  --set database_name="$PGDATABASE" <<'SQL'
SELECT format(
  'CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION PASSWORD %L',
  :'app_user', :'app_password'
)
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'app_user') \gexec
SELECT format(
  'ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION PASSWORD %L',
  :'app_user', :'app_password'
) \gexec
SELECT format('ALTER DATABASE %I OWNER TO %I', :'database_name', :'app_user') \gexec
SELECT format('REASSIGN OWNED BY %I TO %I', :'bootstrap_user', :'app_user') \gexec
SELECT format('ALTER ROLE %I NOLOGIN', :'bootstrap_user') \gexec
SQL
