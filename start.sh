#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${ROOT_DIR}/compose.test.yaml"

# Local Compose deployment configuration. Environment values may override these defaults.
SCHEMII_TEST_APP_PORT="${SCHEMII_TEST_APP_PORT-8001}"
SCHEMII_TEST_POSTGRES_DB="${SCHEMII_TEST_POSTGRES_DB-schemii_test}"
SCHEMII_TEST_POSTGRES_USER="${SCHEMII_TEST_POSTGRES_USER-schemii}"
SCHEMII_TEST_POSTGRES_PASSWORD="${SCHEMII_TEST_POSTGRES_PASSWORD-schemii-local-test}"
SCHEMII_STARTUP_TIMEOUT="${SCHEMII_STARTUP_TIMEOUT-120}"
SCHEMII_TLS_DIRECTORY="${SCHEMII_TLS_DIRECTORY-${ROOT_DIR}/.schemii/tls}"
SCHEMII_TLS_CERTIFICATE_DAYS="${SCHEMII_TLS_CERTIFICATE_DAYS-365}"

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
[[ -f "$COMPOSE_FILE" ]] || fail "Compose definition is missing at ${COMPOSE_FILE}"
command -v docker >/dev/null 2>&1 || fail "Docker is not installed or is not on PATH"
docker compose version >/dev/null 2>&1 || fail "the Docker Compose plugin is unavailable"
command -v openssl >/dev/null 2>&1 || fail "OpenSSL is required to create the local HTTPS certificate"

SCHEMII_TEST_TLS_CERTIFICATE="${SCHEMII_TLS_DIRECTORY}/localhost.crt"
SCHEMII_TEST_TLS_PRIVATE_KEY="${SCHEMII_TLS_DIRECTORY}/localhost.key"

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
    exec newgrp docker -c "$restart_command"
  fi
  fail "Docker is unavailable; confirm that the daemon is running and the current account belongs to the docker group"
fi

if ! certificate_is_current; then
  printf 'Creating a persistent local HTTPS certificate for localhost and 127.0.0.1...\n'
  create_local_certificate
fi
SCHEMII_TLS_READER_GID="$(stat -c '%g' "$SCHEMII_TEST_TLS_PRIVATE_KEY")"

export SCHEMII_TEST_APP_PORT
export SCHEMII_TEST_POSTGRES_DB
export SCHEMII_TEST_POSTGRES_USER
export SCHEMII_TEST_POSTGRES_PASSWORD
export SCHEMII_TEST_TLS_CERTIFICATE
export SCHEMII_TEST_TLS_PRIVATE_KEY
export SCHEMII_TLS_READER_GID

compose_args=(
  compose
  --project-directory "$ROOT_DIR"
  --file "$COMPOSE_FILE"
)

# Nginx loads its file-mounted configuration and certificate only at process start.
docker "${compose_args[@]}" rm --stop --force ingress

printf 'Building and starting the Schemii HTTPS deployment on 127.0.0.1:%s...\n' "$SCHEMII_TEST_APP_PORT"
docker "${compose_args[@]}" up --build --detach --wait --wait-timeout "$SCHEMII_STARTUP_TIMEOUT"
docker "${compose_args[@]}" ps
printf 'Schemii is ready at https://localhost:%s/\n' "$SCHEMII_TEST_APP_PORT"
printf 'API map: https://localhost:%s/api-map\n' "$SCHEMII_TEST_APP_PORT"
printf 'DB call map: https://localhost:%s/db-map\n' "$SCHEMII_TEST_APP_PORT"
