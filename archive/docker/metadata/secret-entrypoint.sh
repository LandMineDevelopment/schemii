#!/bin/sh
set -eu

runtime_dir=/tmp/schemii-metadata-secrets
install -d -m 700 -o postgres -g postgres "$runtime_dir"
for name in metadata_bootstrap_password metadata_migration_password metadata_schemii_password metadata_schemer_password; do
  source_path="/run/secrets/$name"
  if [ ! -r "$source_path" ]; then
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
  } < "$source_path"
  case "$value" in
    *[!A-Za-z0-9_-]*) valid=0 ;;
    *) valid=1 ;;
  esac
  if [ "$valid" -ne 1 ] || [ "${#value}" -lt 16 ] || [ "${#value}" -gt 256 ]; then
    printf '%s\n' "$name must contain 16-256 characters from [A-Za-z0-9_-]" >&2
    exit 1
  fi
  printf '%s\n' "$value" > "$runtime_dir/$name"
  chown postgres:postgres "$runtime_dir/$name"
  chmod 400 "$runtime_dir/$name"
done
password="$(cat "$runtime_dir/metadata_migration_password")"
escaped_password="$(printf '%s' "$password" | sed 's/\\/\\\\/g; s/:/\\:/g')"
printf '127.0.0.1:5432:schemii_metadata:schemii_metadata_migration:%s\n' "$escaped_password" > "$runtime_dir/metadata_migration_password.pgpass"
chown postgres:postgres "$runtime_dir/metadata_migration_password.pgpass"
chmod 400 "$runtime_dir/metadata_migration_password.pgpass"
exec docker-entrypoint.sh "$@"
