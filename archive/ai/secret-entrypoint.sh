#!/bin/sh
set -eu

secret_file="${OPENCODE_SERVER_PASSWORD_FILE:-/run/secrets/opencode_password}"
if [ ! -r "$secret_file" ]; then
  printf '%s\n' 'OpenCode password file is not readable' >&2
  exit 1
fi
OPENCODE_SERVER_PASSWORD=
extra=
{
  IFS= read -r OPENCODE_SERVER_PASSWORD || [ -n "$OPENCODE_SERVER_PASSWORD" ]
  if IFS= read -r extra; then
    printf '%s\n' 'OpenCode password must contain exactly one line' >&2
    exit 1
  fi
} < "$secret_file"
case "$OPENCODE_SERVER_PASSWORD" in
  *[!A-Za-z0-9_-]*) valid=0 ;;
  *) valid=1 ;;
esac
if [ "$valid" -ne 1 ] || [ "${#OPENCODE_SERVER_PASSWORD}" -lt 16 ] || [ "${#OPENCODE_SERVER_PASSWORD}" -gt 256 ]; then
  printf '%s\n' 'OpenCode password must contain 16-256 characters from [A-Za-z0-9_-]' >&2
  exit 1
fi
export OPENCODE_SERVER_PASSWORD
exec opencode "$@"
