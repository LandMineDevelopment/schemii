#!/bin/sh
set -eu

umask 077
password="${SCHEMII_POSTGRES_PASSWORD:?SCHEMII_POSTGRES_PASSWORD is required}"
escaped_password="$(printf '%s' "$password" | sed 's/\\/\\\\/g; s/:/\\:/g')"
printf '%s:%s:%s:%s:%s\n' "$PGHOST" "$PGPORT" "$PGDATABASE" "$PGUSER" "$escaped_password" > /tmp/pgpass
export PGPASSFILE=/tmp/pgpass
exec psql --set ON_ERROR_STOP=1 --file /seed/001_bookstore.sql
