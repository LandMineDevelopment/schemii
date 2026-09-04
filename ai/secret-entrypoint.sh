#!/bin/sh
set -eu
secret_file="${OPENCODE_SERVER_PASSWORD_FILE:-/run/secrets/opencode_password}"
[ -r "$secret_file" ] || { echo 'OpenCode password file is not readable' >&2; exit 1; }
OPENCODE_SERVER_PASSWORD="$(cat "$secret_file")"
[ "${#OPENCODE_SERVER_PASSWORD}" -ge 16 ] || { echo 'OpenCode password is invalid' >&2; exit 1; }
export OPENCODE_SERVER_PASSWORD
exec opencode "$@"
