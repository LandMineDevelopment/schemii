#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${ROOT_DIR}/compose.test.yaml"
ORIGINAL_ARGUMENTS=("$@")

# Local Compose deployment configuration. Environment values may override these defaults.
SCHEMII_TEST_POSTGRES_PASSWORD_WAS_SET="${SCHEMII_TEST_POSTGRES_PASSWORD+x}"
SCHEMII_TEST_APP_PORT="${SCHEMII_TEST_APP_PORT-8001}"
SCHEMII_TEST_POSTGRES_DB="${SCHEMII_TEST_POSTGRES_DB-schemii_test}"
SCHEMII_TEST_POSTGRES_USER="${SCHEMII_TEST_POSTGRES_USER-schemii}"
SCHEMII_TEST_POSTGRES_PASSWORD="${SCHEMII_TEST_POSTGRES_PASSWORD-schemii-local-test}"
SCHEMII_METADATA_APP_USER="${SCHEMII_METADATA_APP_USER-schemii_metadata_app}"
SCHEMII_STARTUP_TIMEOUT="${SCHEMII_STARTUP_TIMEOUT-120}"
SCHEMII_TLS_DIRECTORY="${SCHEMII_TLS_DIRECTORY-${ROOT_DIR}/.schemii/tls}"
SCHEMII_TLS_CERTIFICATE_DAYS="${SCHEMII_TLS_CERTIFICATE_DAYS-365}"
SCHEMII_SECRET_DIRECTORY="${SCHEMII_SECRET_DIRECTORY-${ROOT_DIR}/.schemii/secrets}"
SCHEMII_LAUNCH_LOCK_FILE="${SCHEMII_LAUNCH_LOCK_FILE-${ROOT_DIR}/.schemii/start.lock}"
SCHEMII_RESET_MIGRATION_DEMO="${SCHEMII_RESET_MIGRATION_DEMO-0}"
SCHEMII_DEMO_SCENARIO="${SCHEMII_DEMO_SCENARIO-baseline}"
SCHEMII_DEMO_SOURCE_REVISION="${SCHEMII_DEMO_SOURCE_REVISION-unknown+dirty}"
SCHEMII_LAUNCH_ACTION=start
SCHEMII_LOG_SERVICE=
SCHEMII_PI_PROTOTYPE_URL=http://ai-prototype-runtime:4097

usage() {
  printf 'Usage: %s [--ai-prototype | --reset-demo [SCENARIO] | --reset-migration-demo | --list-demo-scenarios | --logs SERVICE | --test-ai-prototype | --remove-legacy-ai-data]\n' "$0"
}

list_demo_scenarios() {
  local manifest scenario title
  for manifest in "${ROOT_DIR}"/dev/postgres/demo-scenarios/*/manifest.json; do
    [[ -f "$manifest" ]] || continue
    scenario="$(basename -- "$(dirname -- "$manifest")")"
    title="$(sed -n 's/^[[:space:]]*"title":[[:space:]]*"\([^"]*\)".*/\1/p' "$manifest")"
    printf '%-24s %s\n' "$scenario" "$title"
  done
}

if (( $# > 0 )); then
  case "$1" in
    --ai-prototype)
      (( $# == 1 )) || { usage >&2; exit 2; }
      SCHEMII_PI_PROTOTYPE_URL=http://ai-prototype-runtime:4097
      ;;
    --reset-demo)
      SCHEMII_RESET_MIGRATION_DEMO=1
      if (( $# == 2 )); then
        SCHEMII_DEMO_SCENARIO="$2"
      elif (( $# > 2 )); then
        usage >&2
        exit 2
      fi
      ;;
    --reset-migration-demo)
      (( $# == 1 )) || { usage >&2; exit 2; }
      SCHEMII_RESET_MIGRATION_DEMO=1
      SCHEMII_DEMO_SCENARIO=baseline
      ;;
    --list-demo-scenarios)
      (( $# == 1 )) || { usage >&2; exit 2; }
      list_demo_scenarios
      exit 0
      ;;
    --test-ai-prototype)
      (( $# == 1 )) || { usage >&2; exit 2; }
      SCHEMII_LAUNCH_ACTION=test-ai-prototype
      ;;
    --remove-legacy-ai-data)
      (( $# == 1 )) || { usage >&2; exit 2; }
      SCHEMII_LAUNCH_ACTION=remove-legacy-ai-data
      ;;
    --logs)
      (( $# == 2 )) || { usage >&2; exit 2; }
      case "$2" in
        schemii|ai-prototype-runtime|ingress|metadata-bootstrap|demo-bootstrap|postgres-seed)
          SCHEMII_LAUNCH_ACTION=logs
          SCHEMII_LOG_SERVICE="$2"
          ;;
        *) printf 'Unknown log service: %s\n' "$2" >&2; usage >&2; exit 2 ;;
      esac
      ;;
    -h|--help)
      (( $# == 1 )) || { usage >&2; exit 2; }
      usage
      exit 0
      ;;
    *) usage >&2; exit 2 ;;
  esac
fi

fail() {
  printf 'Schemii startup error: %s\n' "$1" >&2
  exit 1
}

if [[ ! "$SCHEMII_TEST_APP_PORT" =~ ^[0-9]+$ ]] || (( 10#$SCHEMII_TEST_APP_PORT < 1024 || 10#$SCHEMII_TEST_APP_PORT > 65535 )); then
  fail "SCHEMII_TEST_APP_PORT must be an integer from 1024 through 65535"
fi
if [[ ! "$SCHEMII_STARTUP_TIMEOUT" =~ ^[0-9]+$ ]] || (( 10#$SCHEMII_STARTUP_TIMEOUT < 1 || 10#$SCHEMII_STARTUP_TIMEOUT > 600 )); then
  fail "SCHEMII_STARTUP_TIMEOUT must be an integer from 1 through 600 seconds"
fi
if [[ ! "$SCHEMII_TLS_CERTIFICATE_DAYS" =~ ^[0-9]+$ ]] || (( 10#$SCHEMII_TLS_CERTIFICATE_DAYS < 1 || 10#$SCHEMII_TLS_CERTIFICATE_DAYS > 3650 )); then
  fail "SCHEMII_TLS_CERTIFICATE_DAYS must be an integer from 1 through 3650"
fi
[[ -n "$SCHEMII_TEST_POSTGRES_DB" ]] || fail "SCHEMII_TEST_POSTGRES_DB must not be empty"
[[ -n "$SCHEMII_TEST_POSTGRES_USER" ]] || fail "SCHEMII_TEST_POSTGRES_USER must not be empty"
[[ -n "$SCHEMII_TEST_POSTGRES_PASSWORD" ]] || fail "SCHEMII_TEST_POSTGRES_PASSWORD must not be empty"
[[ "$SCHEMII_TEST_POSTGRES_DB" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || fail "SCHEMII_TEST_POSTGRES_DB must be a lowercase PostgreSQL identifier"
[[ "$SCHEMII_TEST_POSTGRES_USER" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || fail "SCHEMII_TEST_POSTGRES_USER must be a lowercase PostgreSQL identifier"
[[ "$SCHEMII_METADATA_APP_USER" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || fail "SCHEMII_METADATA_APP_USER must be a lowercase PostgreSQL identifier"
[[ "$SCHEMII_METADATA_APP_USER" != "$SCHEMII_TEST_POSTGRES_USER" ]] || fail "SCHEMII_METADATA_APP_USER must differ from the metadata bootstrap user"
[[ "$SCHEMII_TEST_POSTGRES_USER" != "schemii_demo_admin" ]] || fail "SCHEMII_TEST_POSTGRES_USER must differ from the demo bootstrap user"
[[ "$SCHEMII_TEST_POSTGRES_PASSWORD" != *$'\n'* ]] || fail "SCHEMII_TEST_POSTGRES_PASSWORD must be a single line"
[[ -n "$SCHEMII_SECRET_DIRECTORY" ]] || fail "SCHEMII_SECRET_DIRECTORY must not be empty"
[[ "$SCHEMII_LAUNCH_LOCK_FILE" == /* ]] || fail "SCHEMII_LAUNCH_LOCK_FILE must be an absolute path"
[[ "$SCHEMII_RESET_MIGRATION_DEMO" == "0" || "$SCHEMII_RESET_MIGRATION_DEMO" == "1" ]] || fail "SCHEMII_RESET_MIGRATION_DEMO must be 0 or 1"
[[ "$SCHEMII_DEMO_SCENARIO" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] || fail "demo scenario must be a kebab-case identifier"
[[ -f "${ROOT_DIR}/dev/postgres/demo-scenarios/${SCHEMII_DEMO_SCENARIO}/manifest.json" ]] || fail "unknown demo scenario: ${SCHEMII_DEMO_SCENARIO}"
[[ -f "$COMPOSE_FILE" ]] || fail "Compose definition is missing at ${COMPOSE_FILE}"
command -v docker >/dev/null 2>&1 || fail "Docker is not installed or is not on PATH"
docker compose version >/dev/null 2>&1 || fail "the Docker Compose plugin is unavailable"
command -v openssl >/dev/null 2>&1 || fail "OpenSSL is required to create the local HTTPS certificate"
command -v flock >/dev/null 2>&1 || fail "flock is required to serialize local application lifecycle changes"

SCHEMII_TEST_TLS_CERTIFICATE="${SCHEMII_TLS_DIRECTORY}/localhost.crt"
SCHEMII_TEST_TLS_PRIVATE_KEY="${SCHEMII_TLS_DIRECTORY}/localhost.key"
SCHEMII_METADATA_BOOTSTRAP_PASSWORD_SECRET_FILE="${SCHEMII_SECRET_DIRECTORY}/metadata_password"
SCHEMII_METADATA_APP_PASSWORD_SECRET_FILE="${SCHEMII_SECRET_DIRECTORY}/metadata_app_password"
SCHEMII_DEMO_ADMIN_PASSWORD_SECRET_FILE="${SCHEMII_SECRET_DIRECTORY}/demo_admin_password"
SCHEMII_DEMO_TARGET_PASSWORD_SECRET_FILE="${SCHEMII_SECRET_DIRECTORY}/demo_target_password"
SCHEMII_METADATA_ENCRYPTION_KEY_SECRET_FILE="${SCHEMII_SECRET_DIRECTORY}/metadata_encryption_key"
SCHEMII_OPENCODE_PASSWORD_SECRET_FILE="${SCHEMII_SECRET_DIRECTORY}/opencode_password"

certificate_is_current() {
  local certificate_text certificate_modulus key_modulus
  [[ -f "$SCHEMII_TEST_TLS_CERTIFICATE" && -f "$SCHEMII_TEST_TLS_PRIVATE_KEY" ]] || return 1
  openssl x509 -in "$SCHEMII_TEST_TLS_CERTIFICATE" -noout -checkend 604800 >/dev/null 2>&1 || return 1
  certificate_text="$(openssl x509 -in "$SCHEMII_TEST_TLS_CERTIFICATE" -noout -text 2>/dev/null)" || return 1
  [[ "$certificate_text" == *"DNS:localhost, IP Address:127.0.0.1"* ]] || return 1
  [[ "$certificate_text" == *"CA:FALSE"* ]] || return 1
  [[ "$certificate_text" == *"TLS Web Server Authentication"* ]] || return 1
  certificate_modulus="$(openssl x509 -in "$SCHEMII_TEST_TLS_CERTIFICATE" -noout -modulus 2>/dev/null)" || return 1
  key_modulus="$(openssl rsa -in "$SCHEMII_TEST_TLS_PRIVATE_KEY" -noout -modulus 2>/dev/null)" || return 1
  [[ "$certificate_modulus" == "$key_modulus" ]]
}

create_local_certificate() {
  mkdir -p -- "$SCHEMII_TLS_DIRECTORY"
  chmod 750 "$SCHEMII_TLS_DIRECTORY"
  local temporary_directory
  temporary_directory="$(mktemp -d "${SCHEMII_TLS_DIRECTORY}/.generate.XXXXXX")"
  trap 'rm -rf -- "$temporary_directory"' RETURN
  openssl req -x509 -newkey rsa:2048 -sha256 -nodes \
    -days "$SCHEMII_TLS_CERTIFICATE_DAYS" \
    -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
    -addext "basicConstraints=critical,CA:FALSE" \
    -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
    -addext "extendedKeyUsage=serverAuth" \
    -keyout "${temporary_directory}/localhost.key" \
    -out "${temporary_directory}/localhost.crt" >/dev/null 2>&1
  chmod 640 "${temporary_directory}/localhost.key"
  chmod 644 "${temporary_directory}/localhost.crt"
  mv -- "${temporary_directory}/localhost.key" "$SCHEMII_TEST_TLS_PRIVATE_KEY"
  mv -- "${temporary_directory}/localhost.crt" "$SCHEMII_TEST_TLS_CERTIFICATE"
  rmdir -- "$temporary_directory"
  trap - RETURN
}

if ! docker info >/dev/null 2>&1; then
  current_groups=" $(id -nG) "
  account_name="$(id -un)"
  account_groups=" $(id -nG "$account_name") "
  if [[ "$current_groups" != *" docker "* && "$account_groups" == *" docker "* ]]; then
    command -v newgrp >/dev/null 2>&1 || fail "the account has Docker access, but this session is stale and newgrp is unavailable; start a new login session"
    printf "Refreshing this process with the account's Docker group membership...\n"
    printf -v restart_command 'exec %q' "${ROOT_DIR}/start.sh"
    for restart_argument in "${ORIGINAL_ARGUMENTS[@]}"; do
      printf -v escaped_restart_argument ' %q' "$restart_argument"
      restart_command+="$escaped_restart_argument"
    done
    exec newgrp docker -c "$restart_command"
  fi
  fail "Docker is unavailable; confirm that the daemon is running and the current account belongs to the docker group"
fi

mkdir -p -m 700 -- "$(dirname -- "$SCHEMII_LAUNCH_LOCK_FILE")"
[[ ! -L "$SCHEMII_LAUNCH_LOCK_FILE" ]] || fail "SCHEMII_LAUNCH_LOCK_FILE must not be a symbolic link"
exec {SCHEMII_LAUNCH_LOCK_FD}>"$SCHEMII_LAUNCH_LOCK_FILE"
chmod 600 "$SCHEMII_LAUNCH_LOCK_FILE"
if ! flock --nonblock "$SCHEMII_LAUNCH_LOCK_FD"; then
  fail "another ./start.sh lifecycle operation is already running"
fi

compose_args=(
  compose
  --project-name schemii-test
  --project-directory "$ROOT_DIR"
  --file "$COMPOSE_FILE"
)
# Optional machine-local target networks; never part of the portable base stack.
if [[ -f "${ROOT_DIR}/.schemii/compose.local.yaml" ]]; then
  compose_args+=(--file "${ROOT_DIR}/.schemii/compose.local.yaml")
fi

if [[ "$SCHEMII_LAUNCH_ACTION" == "remove-legacy-ai-data" ]]; then
  # Exact former Compose volume only; never prune or remove active mounted data.
  legacy_ai_volume=schemii-test_schemii-test-opencode-data
  legacy_ai_match="$(docker volume ls --filter "name=^${legacy_ai_volume}$" --format '{{.Name}}')"
  if [[ -z "$legacy_ai_match" ]]; then
    printf 'No unused OpenCode data volume remains.\n'
    exit 0
  fi
  [[ "$legacy_ai_match" == "$legacy_ai_volume" ]] || fail "ambiguous legacy AI volume identity"
  legacy_ai_labels="$(docker volume inspect --format '{{index .Labels "com.docker.compose.project"}}/{{index .Labels "com.docker.compose.volume"}}' "$legacy_ai_volume")"
  [[ "$legacy_ai_labels" == 'schemii-test/schemii-test-opencode-data' ]] || fail "legacy AI volume ownership labels do not match"
  legacy_ai_users="$(docker ps --all --filter "volume=${legacy_ai_volume}" --format '{{.ID}}')"
  [[ -z "$legacy_ai_users" ]] || fail "legacy AI volume is still attached to a container; refusing removal"
  docker volume rm "$legacy_ai_volume"
  printf 'Removed unused OpenCode credentials/history. This cannot be undone without a separate backup. Current Pi and database data were not touched.\n'
  exit 0
fi

if [[ "$SCHEMII_LAUNCH_ACTION" == "test-ai-prototype" ]]; then
  printf 'Building the isolated AI prototype test image...\n'
  if ! docker "${compose_args[@]}" --profile ai-prototype build ai-prototype; then
    fail "the AI prototype test image could not be built"
  fi
  printf 'Running AI prototype tests without network access...\n'
  exec docker "${compose_args[@]}" --profile ai-prototype run --rm --no-deps -T ai-prototype
fi

if [[ -n "$SCHEMII_PI_PROTOTYPE_URL" ]]; then
  compose_args+=(--profile ai-prototype-runtime)
fi

if ! certificate_is_current; then
  printf 'Creating a persistent local HTTPS certificate for localhost and 127.0.0.1...\n'
  create_local_certificate
fi
SCHEMII_TLS_READER_GID="$(stat -c '%g' "$SCHEMII_TEST_TLS_PRIVATE_KEY")"

write_secret() {
  local destination="$1"
  local value="$2"
  local temporary_stem="$3"
  mkdir -p -- "$SCHEMII_SECRET_DIRECTORY"
  chmod 750 "$SCHEMII_SECRET_DIRECTORY"
  local temporary_file
  temporary_file="$(mktemp "${SCHEMII_SECRET_DIRECTORY}/.${temporary_stem}.XXXXXX")"
  trap 'rm -f -- "$temporary_file"' RETURN
  printf '%s\n' "$value" > "$temporary_file"
  chmod 640 "$temporary_file"
  mv -- "$temporary_file" "$destination"
  trap - RETURN
}

secret_is_valid() {
  local secret_file="$1"
  [[ -f "$secret_file" ]] || return 1
  [[ "$(wc -l < "$secret_file")" == "1" ]] || return 1
  [[ -n "$(sed -n '1p' "$secret_file")" ]]
}

ensure_configured_secret() {
  local secret_file="$1"
  local configured_value="$2"
  local explicitly_configured="$3"
  local label="$4"
  if [[ -e "$secret_file" ]]; then
    secret_is_valid "$secret_file" || fail "the existing ${label} secret is invalid"
    chmod 640 "$secret_file"
    if [[ "$explicitly_configured" == "x" ]] \
        && [[ "$(sed -n '1p' "$secret_file")" != "$configured_value" ]]; then
      fail "SCHEMII_TEST_POSTGRES_PASSWORD does not match the persisted ${label} secret; restore the prior value before starting"
    fi
    return
  fi
  write_secret "$secret_file" "$configured_value" "$label"
}

ensure_random_secret() {
  local secret_file="$1"
  local label="$2"
  if [[ -e "$secret_file" ]]; then
    secret_is_valid "$secret_file" || fail "the existing ${label} secret is invalid"
    chmod 640 "$secret_file"
    return
  fi
  write_secret "$secret_file" "$(openssl rand -hex 32)" "$label"
}

metadata_encryption_key_is_valid() {
  [[ -f "$SCHEMII_METADATA_ENCRYPTION_KEY_SECRET_FILE" ]] || return 1
  local decoded_file decoded_size
  decoded_file="$(mktemp "${SCHEMII_SECRET_DIRECTORY}/.decoded-key.XXXXXX")"
  if ! openssl base64 -d -A \
      -in "$SCHEMII_METADATA_ENCRYPTION_KEY_SECRET_FILE" \
      -out "$decoded_file" 2>/dev/null; then
    rm -f -- "$decoded_file"
    return 1
  fi
  decoded_size="$(stat -c '%s' "$decoded_file")"
  rm -f -- "$decoded_file"
  [[ "$decoded_size" == "32" ]]
}

create_metadata_encryption_key() {
  local temporary_file
  temporary_file="$(mktemp "${SCHEMII_SECRET_DIRECTORY}/.metadata-key.XXXXXX")"
  trap 'rm -f -- "$temporary_file"' RETURN
  openssl rand -base64 32 | tr -d '\n' > "$temporary_file"
  printf '\n' >> "$temporary_file"
  chmod 640 "$temporary_file"
  mv -- "$temporary_file" "$SCHEMII_METADATA_ENCRYPTION_KEY_SECRET_FILE"
  trap - RETURN
}

mkdir -p -- "$SCHEMII_SECRET_DIRECTORY"
chmod 750 "$SCHEMII_SECRET_DIRECTORY"
ensure_random_secret \
  "$SCHEMII_METADATA_BOOTSTRAP_PASSWORD_SECRET_FILE" \
  "metadata-bootstrap-password"
ensure_configured_secret \
  "$SCHEMII_DEMO_TARGET_PASSWORD_SECRET_FILE" \
  "$SCHEMII_TEST_POSTGRES_PASSWORD" \
  "$SCHEMII_TEST_POSTGRES_PASSWORD_WAS_SET" \
  "demo-target-password"
ensure_random_secret "$SCHEMII_METADATA_APP_PASSWORD_SECRET_FILE" "metadata-app-password"
ensure_random_secret "$SCHEMII_DEMO_ADMIN_PASSWORD_SECRET_FILE" "demo-admin-password"
ensure_random_secret "$SCHEMII_OPENCODE_PASSWORD_SECRET_FILE" "opencode-password"
if ! metadata_encryption_key_is_valid; then
  if [[ -e "$SCHEMII_METADATA_ENCRYPTION_KEY_SECRET_FILE" ]]; then
    fail "the existing metadata encryption key is invalid; restore the original 256-bit base64 key"
  fi
  printf 'Creating a persistent metadata credential encryption key...\n'
  create_metadata_encryption_key
fi
SCHEMII_SECRET_READER_GID="$(stat -c '%g' "$SCHEMII_METADATA_ENCRYPTION_KEY_SECRET_FILE")"

export SCHEMII_TEST_APP_PORT
export SCHEMII_TEST_POSTGRES_DB
export SCHEMII_TEST_POSTGRES_USER
export SCHEMII_TEST_POSTGRES_PASSWORD
export SCHEMII_METADATA_APP_USER
export SCHEMII_TEST_TLS_CERTIFICATE
export SCHEMII_TEST_TLS_PRIVATE_KEY
export SCHEMII_TLS_READER_GID
export SCHEMII_METADATA_BOOTSTRAP_PASSWORD_SECRET_FILE
export SCHEMII_METADATA_APP_PASSWORD_SECRET_FILE
export SCHEMII_DEMO_ADMIN_PASSWORD_SECRET_FILE
export SCHEMII_DEMO_TARGET_PASSWORD_SECRET_FILE
export SCHEMII_METADATA_ENCRYPTION_KEY_SECRET_FILE
export SCHEMII_OPENCODE_PASSWORD_SECRET_FILE
export SCHEMII_PI_PROTOTYPE_URL
export SCHEMII_SECRET_READER_GID
export SCHEMII_RESET_MIGRATION_DEMO
if [[ "$SCHEMII_RESET_MIGRATION_DEMO" == "1" ]]; then
  if command -v git >/dev/null 2>&1 \
      && SCHEMII_DEMO_SOURCE_REVISION="$(git -C "$ROOT_DIR" rev-parse HEAD 2>/dev/null)"; then
    if [[ -n "$(git -C "$ROOT_DIR" status --porcelain --untracked-files=normal)" ]]; then
      SCHEMII_DEMO_SOURCE_REVISION+="+dirty"
    fi
  else
    SCHEMII_DEMO_SOURCE_REVISION="unknown+dirty"
  fi
fi
export SCHEMII_DEMO_SCENARIO
export SCHEMII_DEMO_SOURCE_REVISION

if [[ "$SCHEMII_LAUNCH_ACTION" == "logs" ]]; then
  exec docker "${compose_args[@]}" logs --no-color --tail 200 "$SCHEMII_LOG_SERVICE"
fi

printf 'Building the current Schemii application image...\n'
if [[ -n "$SCHEMII_PI_PROTOTYPE_URL" ]]; then
  if ! docker "${compose_args[@]}" build schemii ai-prototype-runtime; then
    fail "the application or AI prototype runtime image could not be built; the running deployment was left unchanged"
  fi
elif ! docker "${compose_args[@]}" build schemii ai-prototype-runtime; then
  fail "the application image could not be built; the running deployment was left unchanged"
fi

# The launcher is also the restart boundary. Build first so a compilation
# failure does not interrupt the last known-good HTTP processes.
docker "${compose_args[@]}" rm --stop --force \
  ingress schemii metadata-bootstrap demo-bootstrap
# Remove a previous optional runtime even when this launch disables it.
docker "${compose_args[@]}" --profile ai-prototype-runtime rm --stop --force ai-prototype-runtime

# The former single PostgreSQL service used the control-plane volume now owned
# by metadata-postgres. Remove only that exact legacy container before mounting
# its retained volume under the split topology.
if ! legacy_postgres_container="$(docker ps --all --quiet \
    --filter label=com.docker.compose.project=schemii-test \
    --filter label=com.docker.compose.service=postgres)"; then
  fail "the legacy PostgreSQL container check failed"
fi
if [[ "$legacy_postgres_container" == *$'\n'* ]]; then
  fail "multiple legacy PostgreSQL containers matched the exact deployment labels"
fi
if [[ -n "$legacy_postgres_container" ]]; then
  [[ "$legacy_postgres_container" =~ ^[0-9a-f]+$ ]] || fail "the legacy PostgreSQL container ID was malformed"
  printf 'Stopping the legacy combined PostgreSQL container; its volume is retained...\n'
  docker rm --force "$legacy_postgres_container"
fi
if [[ "$SCHEMII_RESET_MIGRATION_DEMO" == "1" ]]; then
  docker "${compose_args[@]}" --profile demo-fixture rm --stop --force \
    postgres-seed demo-bootstrap demo-fixture
fi

printf 'Building and starting the Schemii HTTPS deployment on 127.0.0.1:%s...\n' "$SCHEMII_TEST_APP_PORT"
if ! docker "${compose_args[@]}" up --detach --remove-orphans --wait --wait-timeout "$SCHEMII_STARTUP_TIMEOUT"; then
  printf 'Schemii did not become healthy. Current service state:\n' >&2
  docker "${compose_args[@]}" --profile demo-fixture ps --all >&2 || true
  printf 'Schemii service logs:\n' >&2
  docker "${compose_args[@]}" logs --no-color --tail 200 schemii >&2 || true
  if [[ -n "$SCHEMII_PI_PROTOTYPE_URL" ]]; then
    printf 'Pi prototype service logs:\n' >&2
    docker "${compose_args[@]}" logs --no-color --tail 200 ai-prototype-runtime >&2 || true
  fi
  printf 'Metadata bootstrap logs:\n' >&2
  docker "${compose_args[@]}" logs --no-color --tail 200 metadata-bootstrap >&2 || true
  printf 'Demo seed logs:\n' >&2
  docker "${compose_args[@]}" logs --no-color --tail 200 postgres-seed >&2 || true
  printf 'Demo role bootstrap logs:\n' >&2
  docker "${compose_args[@]}" logs --no-color --tail 200 demo-bootstrap >&2 || true
  if [[ "$SCHEMII_RESET_MIGRATION_DEMO" == "1" ]]; then
    printf 'Demo fixture logs:\n' >&2
    docker "${compose_args[@]}" --profile demo-fixture logs --no-color --tail 200 demo-fixture >&2 || true
  fi
  fail "the application service did not become healthy"
fi
docker "${compose_args[@]}" ps
if [[ "$SCHEMII_RESET_MIGRATION_DEMO" == "1" ]]; then
  if ! fixture_output="$(docker "${compose_args[@]}" --profile demo-fixture run --rm --no-deps demo-fixture 2>&1)"; then
    printf '%s\n' "$fixture_output" >&2
    fail "demo metadata fixture failed"
  fi
  printf '%s\n' "$fixture_output"
  demo_workspace_id="$(printf '%s\n' "$fixture_output" | sed -n 's/^SCHEMII_DEMO_WORKSPACE_ID=//p' | tail -n 1)"
  [[ "$demo_workspace_id" =~ ^ws_[0-9a-f]{32}$ ]] || fail "demo metadata fixture did not report a workspace ID"
  printf 'Demo workspace: https://localhost:%s/?workspace=%s\n' "$SCHEMII_TEST_APP_PORT" "$demo_workspace_id"
  printf 'Remote demo workspace: https://omarchy.taile4f57f.ts.net/?workspace=%s\n' "$demo_workspace_id"
fi
printf 'Schemii is ready at https://localhost:%s/\n' "$SCHEMII_TEST_APP_PORT"
printf 'API map: https://localhost:%s/api-map\n' "$SCHEMII_TEST_APP_PORT"
printf 'DB call map: https://localhost:%s/db-map\n' "$SCHEMII_TEST_APP_PORT"
