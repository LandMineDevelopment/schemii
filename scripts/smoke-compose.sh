#!/usr/bin/env bash
set -euo pipefail

product="${1:-}"
topology="${2:-}"
build_mode="${3:---no-build}"

case "$product" in
  schemii)
    files=(compose.yaml compose.postgres.yaml)
    app_services=(schemii)
    app_names=(Schemii)
    app_ports=("${SCHEMII_HOST_PORT:?SCHEMII_HOST_PORT is required}")
    ;;
  schemer)
    files=(compose.yaml compose.postgres.yaml compose.schemer.yaml)
    app_services=(schemii schemer)
    app_names=(Schemii Schemer)
    app_ports=(
      "${SCHEMII_HOST_PORT:?SCHEMII_HOST_PORT is required}"
      "${SCHEMER_HOST_PORT:?SCHEMER_HOST_PORT is required}"
    )
    ;;
  *)
    printf 'Usage: scripts/smoke-compose.sh schemii|schemer no-ai|ai [--no-build]\n' >&2
    exit 2
    ;;
esac

case "$topology" in
  no-ai) ;;
  ai)
    files+=(compose.ai.yaml)
    [[ "$product" == "schemii" ]] || files+=(compose.schemer.ai.yaml)
    ;;
  *)
    printf 'Usage: scripts/smoke-compose.sh schemii|schemer no-ai|ai [--no-build]\n' >&2
    exit 2
    ;;
esac

case "$build_mode" in
  --no-build) ;;
  *)
    printf 'Smoke tests consume prebuilt images; the third argument must be --no-build.\n' >&2
    exit 2
    ;;
esac

project="${SCHEMII_SMOKE_PROJECT:-smoke-${product}-${topology}-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}}"
export SCHEMII_INSTANCE="$project"
application_image="${SCHEMII_IMAGE:-schemii:local}"
metadata_image="${SCHEMII_METADATA_IMAGE:-schemii-metadata-postgres:local}"
opencode_image="${SCHEMII_OPENCODE_IMAGE:-schemii-opencode:1.18.15-local}"
expected_version="${SCHEMII_EXPECTED_VERSION:-}"
expected_revision="${SCHEMII_EXPECTED_REVISION:-}"
if [[ -n "$expected_version" || -n "$expected_revision" ]]; then
  [[ -n "$expected_version" && -n "$expected_revision" ]] || {
    printf 'SCHEMII_EXPECTED_VERSION and SCHEMII_EXPECTED_REVISION must be set together.\n' >&2
    exit 2
  }
fi
compose=(docker compose --project-name "$project")
for file in "${files[@]}"; do
  compose+=(-f "$file")
done

cleanup() {
  "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT HUP INT TERM
cleanup

services="$("${compose[@]}" config --services)"
opencode_count=0
while IFS= read -r service; do
  [[ "$service" != "opencode" ]] || opencode_count=$((opencode_count + 1))
done <<< "$services"
case "
$services
" in
  *$'\nopencode\n'*) has_opencode=1 ;;
  *) has_opencode=0 ;;
esac
if [[ "$topology" == "ai" && ( "$has_opencode" != "1" || "$opencode_count" != "1" ) ]]; then
  printf 'AI topology must contain exactly one OpenCode service.\n' >&2
  exit 1
fi
if [[ "$topology" == "no-ai" && "$has_opencode" != "0" ]]; then
  printf 'No-AI topology unexpectedly contains OpenCode.\n' >&2
  exit 1
fi

"${compose[@]}" up "$build_mode" -d --wait --wait-timeout 240

if [[ "$topology" == "ai" ]]; then
  opencode_id="$("${compose[@]}" ps -q opencode)"
  [[ -n "$opencode_id" && "$(docker inspect --format '{{.State.Health.Status}}' "$opencode_id")" == healthy ]]
  [[ "$(docker inspect --format '{{.Config.Image}}' "$opencode_id")" == "$opencode_image" ]]
  opencode_mounts="$(docker inspect --format '{{range .Mounts}}{{println .Destination "|" .RW}}{{end}}' "$opencode_id")"
  [[ "$opencode_mounts" == *$'/workspace | false'* ]]
  if [[ "$product" == "schemer" ]]; then
    [[ "$opencode_mounts" == *$'/workspace-schemer | false'* ]]
  fi
fi

metadata_id="$("${compose[@]}" ps -q metadata-postgres)"
[[ -n "$metadata_id" ]]
[[ "$(docker inspect --format '{{.Config.Image}}' "$metadata_id")" == "$metadata_image" ]]

for (( index=0; index<${#app_services[@]}; index++ )); do
  service="${app_services[$index]}"
  name="${app_names[$index]}"
  port="${app_ports[$index]}"
  container_id="$("${compose[@]}" ps -q "$service")"
  [[ -n "$container_id" ]] || { printf '%s container is missing.\n' "$name" >&2; exit 1; }
  [[ "$(docker inspect --format '{{.State.Health.Status}}' "$container_id")" == healthy ]]
  [[ "$(docker inspect --format '{{.Config.Image}}' "$container_id")" == "$application_image" ]]
  curl --fail --silent --show-error --output /dev/null "http://127.0.0.1:${port}/"
  curl --fail --silent --show-error --output /dev/null "http://127.0.0.1:${port}/api/session"
  readiness_url="http://127.0.0.1:${port}/api/readiness"
  if [[ -n "$expected_version" ]]; then
    curl --fail --silent --show-error "$readiness_url" | python3 -c \
      'import json, sys; response=json.load(sys.stdin); expected={"version": sys.argv[1], "revision": sys.argv[2]}; assert response.get("build") == expected, (response.get("build"), expected)' \
      "$expected_version" "$expected_revision"
  else
    curl --fail --silent --show-error --output /dev/null "$readiness_url"
  fi
  environment="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$container_id")"
  if [[ "$topology" == "ai" ]]; then
    if [[ "$service" == "schemii" ]]; then
      [[ "$environment" == *$'SCHEMII_OPENCODE_URL=http://opencode:4096'* ]]
    else
      [[ "$environment" == *$'SCHEMER_OPENCODE_URL=http://opencode:4096'* ]]
    fi
  elif [[ "$environment" == *"OPENCODE_URL=http://opencode:4096"* ]]; then
    printf '%s unexpectedly has an OpenCode endpoint in no-AI topology.\n' "$name" >&2
    exit 1
  fi
done
