#!/bin/sh
set -eu

runtime_dir="$(mktemp -d /tmp/schemii-secrets.XXXXXX)"
chmod 700 "$runtime_dir"
for setting in SCHEMII_METADATA_PASSWORD_FILE SCHEMII_OPENCODE_PASSWORD_FILE SCHEMER_OPENCODE_PASSWORD_FILE; do
  source_path="$(printenv "$setting" 2>/dev/null || true)"
  if [ -n "$source_path" ]; then
    value=
    extra=
    {
      IFS= read -r value || [ -n "$value" ]
      if IFS= read -r extra; then
        printf '%s\n' "$setting must contain exactly one line" >&2
        exit 1
      fi
    } < "$source_path"
    case "$value" in
      *[!A-Za-z0-9_-]*) valid=0 ;;
      *) valid=1 ;;
    esac
    if [ "$valid" -ne 1 ] || [ "${#value}" -lt 16 ] || [ "${#value}" -gt 256 ]; then
      printf '%s\n' "$setting must contain 16-256 characters from [A-Za-z0-9_-]" >&2
      exit 1
    fi
    destination="$runtime_dir/$(basename "$source_path")"
    printf '%s\n' "$value" > "$destination"
    chmod 400 "$destination"
    chown 10001:10001 "$destination"
    export "$setting=$destination"
  fi
done
chown 10001:10001 "$runtime_dir"
export HOME=/home/schemii
exec setpriv --reuid=10001 --regid=10001 --init-groups \
  --bounding-set=-all --inh-caps=-all --ambient-caps=-all "$@"
