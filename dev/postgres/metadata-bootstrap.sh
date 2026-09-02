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

# Reconcile ownership on every launch. This makes interrupted upgrades
# repairable and keeps the runtime role's privileges explicit. The bootstrap
# credential is mounted only into this short-lived job, never the app.
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
SELECT format('ALTER SCHEMA metadata OWNER TO %I', :'app_user')
WHERE to_regnamespace('metadata') IS NOT NULL \gexec
SELECT format('ALTER SCHEMA schemii OWNER TO %I', :'app_user')
WHERE to_regnamespace('schemii') IS NOT NULL \gexec
SELECT format(
  'ALTER %s %I.%I OWNER TO %I',
  CASE object.relkind
    WHEN 'S' THEN 'SEQUENCE'
    WHEN 'v' THEN 'VIEW'
    WHEN 'm' THEN 'MATERIALIZED VIEW'
    WHEN 'f' THEN 'FOREIGN TABLE'
    ELSE 'TABLE'
  END,
  namespace.nspname,
  object.relname,
  :'app_user'
)
FROM pg_class AS object
JOIN pg_namespace AS namespace ON namespace.oid = object.relnamespace
WHERE namespace.nspname IN ('metadata', 'schemii')
  AND object.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
  AND (
    object.relkind <> 'S'
    OR NOT EXISTS (
      SELECT 1
      FROM pg_depend AS dependency
      WHERE dependency.classid = 'pg_class'::regclass
        AND dependency.objid = object.oid
        AND dependency.deptype IN ('a', 'i')
    )
  )
ORDER BY object.relkind, namespace.nspname, object.relname \gexec
SELECT format(
  'ALTER ROUTINE %I.%I(%s) OWNER TO %I',
  namespace.nspname,
  routine.proname,
  pg_get_function_identity_arguments(routine.oid),
  :'app_user'
)
FROM pg_proc AS routine
JOIN pg_namespace AS namespace ON namespace.oid = routine.pronamespace
WHERE namespace.nspname IN ('metadata', 'schemii')
ORDER BY namespace.nspname, routine.proname, routine.oid \gexec
SELECT format('GRANT USAGE, CREATE ON SCHEMA metadata TO %I', :'app_user')
WHERE to_regnamespace('metadata') IS NOT NULL \gexec
SELECT format('GRANT USAGE, CREATE ON SCHEMA schemii TO %I', :'app_user')
WHERE to_regnamespace('schemii') IS NOT NULL \gexec
SQL
