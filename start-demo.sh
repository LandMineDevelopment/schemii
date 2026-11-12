#!/usr/bin/env bash
set -Eeuo pipefail

RELEASE_LAUNCHER="${HOME:?HOME is required}/.local/share/schemii/releases/demo-v0.1.0/dev/demo/published.sh"
if [[ ! -x "$RELEASE_LAUNCHER" ]]; then
  printf 'Schemii demo error: install demo-v0.1.0 using docs/demo-release.md before starting the published demo.\n' >&2
  exit 1
fi

exec "$RELEASE_LAUNCHER" "$@"
