#!/bin/sh
set -eu

read_secret() {
  path="$1"
  name="$2"
  if [ ! -r "$path" ]; then
    printf '%s\n' "$name secret file is not readable" >&2
    exit 1
  fi
  value=
  extra=
  {
    IFS= read -r value || [ -n "$value" ]
    if IFS= read -r extra; then
      printf '%s\n' "$name must contain exactly one line" >&2
      exit 1
    fi
  } < "$path"
  case "$value" in
    *[!A-Za-z0-9_-]*) valid=0 ;;
    *) valid=1 ;;
  esac
  if [ "$valid" -ne 1 ] || [ "${#value}" -lt 16 ] || [ "${#value}" -gt 256 ]; then
    printf '%s\n' "$name must contain 16-256 characters from [A-Za-z0-9_-]" >&2
    exit 1
  fi
  printf '%s' "$value"
}

secret_dir="${SCHEMII_METADATA_SECRET_DIR:-/tmp/schemii-metadata-secrets}"
migration_password="$(read_secret "$secret_dir/metadata_migration_password" metadata_migration_password)"
schemii_password="$(read_secret "$secret_dir/metadata_schemii_password" metadata_schemii_password)"
schemer_password="$(read_secret "$secret_dir/metadata_schemer_password" metadata_schemer_password)"

psql --set ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set migration_password="$migration_password" \
  --set schemii_password="$schemii_password" \
  --set schemer_password="$schemer_password" <<-'SQL'
CREATE ROLE schemii_metadata_owner NOLOGIN;
CREATE ROLE schemii_metadata_migration LOGIN PASSWORD :'migration_password';
CREATE ROLE schemii_metadata_schemii LOGIN PASSWORD :'schemii_password';
CREATE ROLE schemii_metadata_schemer LOGIN PASSWORD :'schemer_password';

ALTER DATABASE schemii_metadata OWNER TO schemii_metadata_owner;
REVOKE ALL ON DATABASE schemii_metadata FROM PUBLIC;
GRANT CONNECT ON DATABASE schemii_metadata TO schemii_metadata_migration;
GRANT CONNECT ON DATABASE schemii_metadata TO schemii_metadata_schemii;
GRANT CONNECT ON DATABASE schemii_metadata TO schemii_metadata_schemer;
GRANT schemii_metadata_owner TO schemii_metadata_migration;

REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO schemii_metadata_owner;
GRANT USAGE ON SCHEMA public TO schemii_metadata_schemii, schemii_metadata_schemer;
ALTER DEFAULT PRIVILEGES FOR ROLE schemii_metadata_owner
  REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE schemii_metadata_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO schemii_metadata_schemii, schemii_metadata_schemer;
ALTER DEFAULT PRIVILEGES FOR ROLE schemii_metadata_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO schemii_metadata_schemii, schemii_metadata_schemer;
SQL
