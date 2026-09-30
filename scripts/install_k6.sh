#!/usr/bin/env bash
# Optional host development tool; never changes the application image.
set -Eeuo pipefail
LOAD_K6_VERSION=2.3.0
LOAD_K6_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
LOAD_K6_DEST="${1:-$LOAD_K6_ROOT/.schemii/tools/k6/$LOAD_K6_VERSION}"
[[ "$(uname -s)" == Linux ]] || { echo 'Pinned installer supports Linux hosts only.' >&2; exit 2; }
case "$(uname -m)" in
  x86_64) LOAD_K6_ARCH=amd64; LOAD_K6_SHA=39c3117b6af817592dcd0ce4242105c0a7af10948c2a425306f0be8f7a8a8ab1 ;;
  aarch64|arm64) LOAD_K6_ARCH=arm64; LOAD_K6_SHA=5ca3433e8201da72a284aaa241a1bb5fb47f4abb4e384d39410ddd8062f49b90 ;;
  *) echo 'Unsupported host architecture for pinned k6.' >&2; exit 2 ;;
esac
[[ ! -e "$LOAD_K6_DEST" ]] || { echo 'Destination exists; preserve it and select a fresh directory.' >&2; exit 2; }
LOAD_K6_TEMP="$(mktemp -d)"
trap 'rm -rf -- "$LOAD_K6_TEMP"' EXIT
LOAD_K6_ASSET="k6-v$LOAD_K6_VERSION-linux-$LOAD_K6_ARCH.tar.gz"
curl --fail --location --proto '=https' --tlsv1.2 --output "$LOAD_K6_TEMP/$LOAD_K6_ASSET" \
  "https://github.com/grafana/k6/releases/download/v$LOAD_K6_VERSION/$LOAD_K6_ASSET"
printf '%s  %s\n' "$LOAD_K6_SHA" "$LOAD_K6_TEMP/$LOAD_K6_ASSET" | sha256sum --check --status
tar -xzf "$LOAD_K6_TEMP/$LOAD_K6_ASSET" -C "$LOAD_K6_TEMP"
mkdir -p -- "$(dirname -- "$LOAD_K6_DEST")"
mkdir -- "$LOAD_K6_DEST"
install -m 0755 "$LOAD_K6_TEMP/k6-v$LOAD_K6_VERSION-linux-$LOAD_K6_ARCH/k6" "$LOAD_K6_DEST/k6"
"$LOAD_K6_DEST/k6" version
printf '%s\n' "$LOAD_K6_DEST/k6"
