#!/usr/bin/env bash
set -Eeuo pipefail

VERSION=demo-v0.1.0
RELEASE_URL="https://github.com/LandMineDevelopment/schemii/releases/tag/${VERSION}"
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"

usage() {
  printf 'Usage: schemii-demo [--version | --help]\n'
  printf 'Start the frozen %s interview demo through start.sh --demo.\n' "$VERSION"
  printf 'The launcher prints workspace links and the private administrator credentials file.\n'
  printf 'Release: %s\n' "$RELEASE_URL"
}

fail() {
  printf 'Schemii published demo error: %s\n' "$1" >&2
  exit 1
}

(( $# <= 1 )) || { usage >&2; exit 2; }
case "${1-}" in
  --help) usage; exit 0 ;;
  ''|--version) ;;
  *) usage >&2; exit 2 ;;
esac

command -v git >/dev/null 2>&1 || fail 'Git is required to verify the published demo checkout.'
checkout_root="$(git -C "$ROOT_DIR" rev-parse --show-toplevel 2>/dev/null)" \
  || fail 'The published demo checkout is unavailable. Reinstall the published release.'
[[ "$checkout_root" == "$ROOT_DIR" ]] \
  || fail 'This directory is not the published demo checkout root. Use the installed schemii-demo command.'
head="$(git -C "$ROOT_DIR" rev-parse --verify 'HEAD^{commit}' 2>/dev/null)" \
  || fail 'The published demo commit cannot be verified. Reinstall the published release.'
release_head="$(git -C "$ROOT_DIR" rev-parse --verify "refs/tags/${VERSION}^{commit}" 2>/dev/null)" \
  || fail "The ${VERSION} tag is missing. Fetch the published release tag before running schemii-demo."
[[ "$head" == "$release_head" ]] \
  || fail "This checkout is not ${VERSION}. Use the frozen release checkout; no source was changed."
changes="$(git -C "$ROOT_DIR" status --porcelain=v1 --untracked-files=all)" \
  || fail 'The published demo source cannot be checked. Reinstall the published release.'
[[ -z "$changes" ]] \
  || fail 'The published demo checkout contains source changes. Preserve them and reinstall the release before launching.'

if [[ "${1-}" == --version ]]; then
  printf 'Schemii %s\nCommit: %s\nRelease: %s\n' "$VERSION" "$head" "$RELEASE_URL"
  exit 0
fi

[[ -x "$ROOT_DIR/start.sh" ]] || fail 'The published start.sh launcher is missing or not executable.'
exec "$ROOT_DIR/start.sh" --demo
