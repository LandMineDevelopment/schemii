#!/bin/sh
set -eu

target_password="$(sed -n '1p' "${SCHEMII_DEMO_TARGET_PASSWORD_FILE:?}")"
test -n "$target_password"

psql --set ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  --set target_user="$SCHEMII_DEMO_TARGET_USER" \
  --set target_password="$target_password" \
  --set target_database="$SCHEMII_DEMO_TARGET_DATABASE" <<'SQL'
SELECT format(
  'CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION PASSWORD %L',
  :'target_user', :'target_password'
) \gexec
SELECT format(
  'CREATE DATABASE %I OWNER %I', :'target_database', :'target_user'
) \gexec
SQL
