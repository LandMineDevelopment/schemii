#!/usr/bin/env bash
set -euo pipefail

requested="${1:-ai-docker-db}"
credential_action=""
case "$requested" in
  credentials-backup|credentials-restore|credentials-rotate|legacy-volume-adopt|instance-backup|instance-restore)
    credential_action="$requested"
    mode="ui"
    ;;
  *) mode="$requested" ;;
esac

if [[ "$mode" == "help" || "$mode" == "--help" || "$mode" == "-h" ]]; then
  printf '%s\n' \
    'Usage: bash ./start.sh [mode]' \
    '' \
    'Modes:' \
    '  ai-docker-db  Complete UI, tutorial PostgreSQL, and AI stack (default)' \
    '  ui            Local schema design only' \
    '  docker-db     UI and tutorial PostgreSQL without AI' \
    '  ai            UI and AI without included PostgreSQL' \
    '  local-db      Linux host PostgreSQL without AI' \
    '  ai-local-db   Linux host PostgreSQL with AI' \
    '  schemer       Schemii and Schemer with tutorial PostgreSQL, explicitly without AI' \
    '  schemer-ai    Schemii and Schemer with tutorial PostgreSQL and shared AI' \
    '' \
    'Credential lifecycle:' \
    '  credentials-backup <directory>' \
    '  credentials-restore <directory>' \
    '  credentials-rotate' \
    '  legacy-volume-adopt ADOPT:<exact-instance-name>' \
    '' \
    'Coordinated Schemer recovery (all instance containers must be stopped):' \
    '  instance-backup <directory>' \
    '  instance-restore <directory> RESTORE:<exact-instance-name>' \
    '' \
    'Uninstall: bash ./uninstall.sh' \
    'Setup help: https://github.com/LandMineDevelopment/schemii#install-docker'
  exit 0
fi
case "$mode" in
  ui|local-db|docker-db|ai|ai-local-db|ai-docker-db|schemer|schemer-ai) ;;
  *)
    printf 'Unknown mode: %s\nRun bash ./start.sh --help for available modes.\n' "$mode" >&2
    exit 2
    ;;
esac

script_path="${BASH_SOURCE[0]}"
script_parent="${script_path%/*}"
[[ "$script_parent" == "$script_path" ]] && script_parent=.
script_dir="$(cd -- "$script_parent" && pwd -P)"

if ! command -v docker >/dev/null 2>&1; then
  printf 'Docker was not found. Install and start Docker, then reopen this terminal.\n' >&2
  printf 'Instructions: https://github.com/LandMineDevelopment/schemii#install-docker\n' >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  printf 'Docker is installed, but the daemon is unavailable or your user lacks permission.\n' >&2
  printf 'Start Docker Desktop or the Linux Docker service, then run: docker info\n' >&2
  printf 'Instructions: https://github.com/LandMineDevelopment/schemii#docker-is-installed-but-unavailable\n' >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  printf 'Docker Compose was not found. Update Docker Desktop or install the Compose plugin.\n' >&2
  printf 'Instructions: https://docs.docker.com/compose/install/\n' >&2
  exit 1
fi

project="${SCHEMII_INSTANCE:-}"
if [[ -z "$project" ]]; then
  legacy_containers=( $(docker ps -aq --filter label=com.docker.compose.project=schemii --filter label=com.docker.compose.service=schemii) )
  legacy_working_dir=""
  if [[ ${#legacy_containers[@]} -gt 0 ]]; then
    legacy_working_dir="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "${legacy_containers[0]}" 2>/dev/null || true)"
  fi
  if [[ "$legacy_working_dir" == "$script_dir" ]]; then
    project="schemii"
  elif [[ ${#legacy_containers[@]} -eq 0 ]] \
    && docker volume inspect schemii_schemii-config >/dev/null 2>&1 \
    && docker volume inspect schemii_schemii-schemas >/dev/null 2>&1; then
    printf 'Legacy Schemii data volumes were found without a container that identifies their installation directory.\n' >&2
    printf 'To reuse that data, run: SCHEMII_INSTANCE=schemii bash ./start.sh %s\n' "$mode" >&2
    printf 'To start a separate installation, choose a unique name, for example: SCHEMII_INSTANCE=schemii-dev bash ./start.sh %s\n' "$mode" >&2
    exit 2
  else
    read -r instance_key _ <<< "$(printf '%s' "$script_dir" | cksum)"
    project="schemii-${instance_key}"
  fi
fi
if [[ ! "$project" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
  printf 'SCHEMII_INSTANCE must contain only lowercase letters, numbers, hyphens, or underscores.\n' >&2
  exit 2
fi
export SCHEMII_INSTANCE="$project"

credential_root="${SCHEMII_CREDENTIAL_ROOT:-${XDG_DATA_HOME:-${HOME:?HOME is required}/.local/share}/schemii/credentials}"
credential_dir="${SCHEMII_CREDENTIAL_DIR:-$credential_root/$project}"
if [[ "$credential_dir" != /* ]]; then
  printf 'SCHEMII_CREDENTIAL_DIR must be an absolute path.\n' >&2
  exit 2
fi
credential_files=(metadata_bootstrap_password metadata_migration_password metadata_schemii_password metadata_schemer_password opencode_password)
credential_transaction="$credential_dir/.credential-transaction"
credential_commit_cleanup="$credential_dir/.credential-transaction-committed"
credential_lock="${credential_dir}.lock"
credential_lock_token="$$-${RANDOM:-0}-$(date +%s)"
temporary_dir=""
recovery_container=""
recovery_metadata_container=""
recovery_backup_staging=""
owner_only_path() {
  local path="$1" expected_mode="$2" details=""
  if details="$(stat -c '%u|%a' -- "$path" 2>/dev/null)"; then
    :
  elif details="$(stat -f '%u|%Lp' "$path" 2>/dev/null)"; then
    :
  else
    return 1
  fi
  [[ "$details" == "$(id -u)|$expected_mode" ]]
}
read_lock_marker() {
  local value=""
  [[ -f "$1" && ! -L "$1" ]] && IFS= read -r value < "$1" || true
  printf '%s' "$value"
}
release_credential_lock() {
  if [[ -d "$credential_lock" && ! -L "$credential_lock" ]] \
      && [[ "$(read_lock_marker "$credential_lock/token")" == "$credential_lock_token" ]]; then
    rm -rf -- "$credential_lock"
  fi
}
cleanup_launcher() {
  [[ -z "$temporary_dir" ]] || rm -rf -- "$temporary_dir" \
    || printf 'Temporary credential staging could not be removed: %s\n' "$temporary_dir" >&2
  [[ -z "$recovery_backup_staging" ]] || rm -rf -- "$recovery_backup_staging" \
    || printf 'Incomplete recovery backup staging could not be removed: %s\n' "$recovery_backup_staging" >&2
  [[ -z "$recovery_container" ]] || docker rm -f "$recovery_container" >/dev/null 2>&1 || true
  [[ -z "$recovery_metadata_container" ]] || docker stop "$recovery_metadata_container" >/dev/null 2>&1 || true
  release_credential_lock || true
}
trap cleanup_launcher EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
credential_parent="$(dirname -- "$credential_dir")"
(umask 077; mkdir -p "$credential_parent")
for lock_attempt in {1..60}; do
  if (umask 077; mkdir "$credential_lock") 2>/dev/null; then
    chmod 700 "$credential_lock"
    printf '%s\n' "$$" > "$credential_lock/pid"
    printf '%s\n' "$credential_lock_token" > "$credential_lock/token"
    chmod 600 "$credential_lock/pid" "$credential_lock/token"
    break
  fi
  [[ -d "$credential_lock" && ! -L "$credential_lock" ]] || {
    printf 'Credential lock path is not a directory; refusing to continue: %s\n' "$credential_lock" >&2
    exit 1
  }
  lock_pid="$(read_lock_marker "$credential_lock/pid")"
  observed_lock_token="$(read_lock_marker "$credential_lock/token")"
  if [[ "$lock_pid" =~ ^[0-9]+$ ]] && ! kill -0 "$lock_pid" 2>/dev/null; then
    stale_lock="${credential_lock}.stale.${credential_lock_token}"
    if [[ "$(read_lock_marker "$credential_lock/token")" == "$observed_lock_token" ]] \
        && mv -- "$credential_lock" "$stale_lock" 2>/dev/null; then
      rm -rf -- "$stale_lock"
      continue
    fi
  fi
  sleep 1
done
if [[ ! -d "$credential_lock" ]] \
    || [[ "$(read_lock_marker "$credential_lock/token")" != "$credential_lock_token" ]]; then
  printf 'Timed out waiting for another launcher credential operation for %s.\n' "$project" >&2
  exit 1
fi
generate_secret() {
  docker run --rm python:3.12-slim python -c 'import secrets; print(secrets.token_hex(32))'
}
read_single_line() {
  local path="$1" name="$2"
  local value="" extra="" had_lf=0
  [[ -f "$path" ]] || { printf '%s is missing.\n' "$name" >&2; return 1; }
  exec 3< "$path"
  if IFS= read -r value <&3; then had_lf=1; fi
  if [[ "$had_lf" == "1" ]] && { IFS= read -r extra <&3 || [[ -n "$extra" ]]; }; then
    exec 3<&-
    printf '%s must contain exactly one nonempty line.\n' "$name" >&2
    return 1
  fi
  if [[ -z "$value" || "$value" == *$'\r'* ]]; then
    exec 3<&-
    printf '%s must contain exactly one nonempty line.\n' "$name" >&2
    return 1
  fi
  exec 3<&-
  printf '%s' "$value"
}
credential_is_valid() {
  local value="$1"
  [[ ${#value} -ge 16 && ${#value} -le 256 ]] || return 1
  case "$value" in
    *[!A-Za-z0-9_-]*) return 1 ;;
  esac
}
read_credential() {
  local path="$1" name="$2"
  local value="" extra="" had_lf=0
  [[ -f "$path" ]] || { printf '%s is missing.\n' "$name" >&2; return 1; }
  exec 3< "$path"
  if IFS= read -r value <&3; then had_lf=1; fi
  if [[ "$had_lf" == "1" ]] && { IFS= read -r extra <&3 || [[ -n "$extra" ]]; }; then
    exec 3<&-
    printf '%s must be one line containing 16-256 characters from [A-Za-z0-9_-].\n' "$name" >&2
    return 1
  fi
  if ! credential_is_valid "$value"; then
    exec 3<&-
    printf '%s must be one line containing 16-256 characters from [A-Za-z0-9_-].\n' "$name" >&2
    return 1
  fi
  exec 3<&-
  printf '%s' "$value"
}
write_secret() {
  local path="$1" value="$2"
  credential_is_valid "$value" || { printf 'Refusing to write an invalid credential.\n' >&2; return 1; }
  (umask 077; printf '%s\n' "$value" > "$path") \
    || { printf 'Credential could not be written: %s\n' "$path" >&2; return 1; }
  chmod 600 "$path" \
    || { printf 'Credential permissions could not be restricted: %s\n' "$path" >&2; return 1; }
}
write_marker() {
  local path="$1" value="$2"
  [[ -n "$value" && "$value" != *$'\n'* && "$value" != *$'\r'* ]] || return 1
  (umask 077; printf '%s\n' "$value" > "$path") \
    || { printf 'Credential marker could not be written: %s\n' "$path" >&2; return 1; }
  chmod 600 "$path" \
    || { printf 'Credential marker permissions could not be restricted: %s\n' "$path" >&2; return 1; }
}
replace_secret() {
  local path="$1" value="$2" temporary status
  temporary="$(mktemp "$credential_dir/.credential.XXXXXX")" \
    || { printf 'Credential replacement staging file could not be created.\n' >&2; return 1; }
  if write_secret "$temporary" "$value"; then
    :
  else
    status=$?
    rm -f -- "$temporary" \
      || { printf 'Failed credential replacement staging could not be removed: %s\n' "$temporary" >&2; return 1; }
    return "$status"
  fi
  # Preserve the file identity so existing Compose secret bind mounts observe
  # the update. The transaction directory permits recovery from interruption.
  cp "$temporary" "$path" \
    || { printf 'Credential replacement copy failed; staging was retained at %s.\n' "$temporary" >&2; return 1; }
  chmod 600 "$path" \
    || { printf 'Credential replacement permissions failed; staging was retained at %s.\n' "$temporary" >&2; return 1; }
  rm -f -- "$temporary" \
    || { printf 'Credential replacement staging could not be removed: %s\n' "$temporary" >&2; return 1; }
}
container_environment() {
  local container="$1" variable="$2"
  [[ -n "$container" ]] || return 0
  docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$container" 2>/dev/null \
    | while IFS= read -r item; do
        [[ "$item" == "$variable="* ]] && printf '%s' "${item#*=}" && break
      done
}
metadata_volume="${project}_schemii-metadata-postgres"
legacy_metadata=0
if docker volume inspect "$metadata_volume" >/dev/null 2>&1; then legacy_metadata=1; fi
if [[ -e "$credential_dir" || -L "$credential_dir" ]]; then
  [[ -d "$credential_dir" && ! -L "$credential_dir" ]] && owner_only_path "$credential_dir" 700 || {
    printf 'Credential directory must be an owner-only non-symlink directory: %s\n' "$credential_dir" >&2
    exit 1
  }
else
  (umask 077; mkdir -p "$credential_dir")
  chmod 700 "$credential_dir"
fi
if [[ -e "$credential_dir/instance" || -L "$credential_dir/instance" ]]; then
  [[ -f "$credential_dir/instance" && ! -L "$credential_dir/instance" ]] \
    && owner_only_path "$credential_dir/instance" 600 || {
      printf 'Credential instance marker must be an owner-only regular file.\n' >&2
      exit 1
    }
else
  write_marker "$credential_dir/instance" "$project"
fi
if [[ "$(read_single_line "$credential_dir/instance" 'credential instance marker')" != "$project" ]]; then
  printf 'Credential directory belongs to a different instance; refusing to use it.\n' >&2
  exit 2
fi

if [[ "$legacy_metadata" == "1" && ! -f "$credential_dir/metadata_migration_password" ]]; then
  legacy_metadata_containers=( $(docker ps -aq --filter "label=com.docker.compose.project=$project" --filter label=com.docker.compose.service=metadata-postgres) )
  legacy_metadata_container="${legacy_metadata_containers[0]:-}"
  bootstrap_password="$(container_environment "$legacy_metadata_container" POSTGRES_PASSWORD)"
  migration_password="$(container_environment "$legacy_metadata_container" SCHEMII_METADATA_MIGRATION_PASSWORD)"
  schemii_password="$(container_environment "$legacy_metadata_container" SCHEMII_METADATA_SCHEMII_PASSWORD)"
  schemer_password="$(container_environment "$legacy_metadata_container" SCHEMII_METADATA_SCHEMER_PASSWORD)"
  bootstrap_password="${bootstrap_password:-schemii-metadata-bootstrap-local}"
  migration_password="${migration_password:-schemii-metadata-migration-local}"
  schemii_password="${schemii_password:-schemii-metadata-runtime-local}"
  schemer_password="${schemer_password:-schemer-metadata-runtime-local}"
  write_secret "$credential_dir/metadata_bootstrap_password" "$bootstrap_password"
  write_secret "$credential_dir/metadata_migration_password" "$migration_password"
  write_secret "$credential_dir/metadata_schemii_password" "$schemii_password"
  write_secret "$credential_dir/metadata_schemer_password" "$schemer_password"
  printf 'WARNING: Existing metadata volume %s was found without managed credentials.\n' "$metadata_volume" >&2
  printf 'Historical credentials were preserved. Back them up; legacy rotation may first require the reviewed bootstrap-owned function. The volume was not reset.\n' >&2
fi
for secret_name in "${credential_files[@]}"; do
  secret_path="$credential_dir/$secret_name"
  if [[ -e "$secret_path" || -L "$secret_path" ]]; then
    [[ -f "$secret_path" && ! -L "$secret_path" ]] && owner_only_path "$secret_path" 600 || {
      printf '%s must be an owner-only regular credential file.\n' "$secret_name" >&2
      exit 1
    }
  else
    write_secret "$secret_path" "$(generate_secret)"
  fi
  read_credential "$secret_path" "$secret_name" >/dev/null
done
export SCHEMII_CREDENTIAL_DIR="$credential_dir"

legacy_adoption_dir="$credential_dir/legacy-volume-adoptions.v1"
legacy_identity_name=""
legacy_identity_created_at=""
legacy_identity_driver=""
legacy_identity_mountpoint=""
legacy_identity_scope=""
legacy_identity_labels=""
legacy_adoptable_volume() {
  [[ "$project" == "schemii" && ( "$1" == "schemii-config" || "$1" == "schemii-schemas" ) ]]
}
inspect_legacy_volume_identity() {
  local logical="$1" volume="${project}_${1}" details
  if ! details="$(docker volume inspect --format '{{.Name}}|{{.CreatedAt}}|{{.Driver}}|{{.Mountpoint}}|{{.Scope}}|{{json .Labels}}' "$volume" 2>/dev/null)"; then
    return 1
  fi
  IFS='|' read -r legacy_identity_name legacy_identity_created_at legacy_identity_driver \
    legacy_identity_mountpoint legacy_identity_scope legacy_identity_labels <<< "$details"
  [[ "$legacy_identity_name" == "$volume" \
    && -n "$legacy_identity_created_at" \
    && "$legacy_identity_driver" == "local" \
    && -n "$legacy_identity_mountpoint" \
    && "$legacy_identity_scope" == "local" \
    && ( "$legacy_identity_labels" == "null" || "$legacy_identity_labels" == "{}" ) ]]
}
legacy_manifest_body() {
  local logical="$1" name="$2" created_at="$3" driver="$4" mountpoint="$5" scope="$6"
  printf '%s\n' \
    'format=schemii-legacy-volume-adoption-v1' \
    "project=$project" \
    "repository=$script_dir" \
    "logical=$logical" \
    "volume=$name" \
    "created-at=$created_at" \
    "driver=$driver" \
    "mountpoint=$mountpoint" \
    "scope=$scope"
}
legacy_adoption_set_matches() {
  local logical path entry name count=0
  legacy_adoptable_volume schemii-config || return 1
  [[ -d "$legacy_adoption_dir" && ! -L "$legacy_adoption_dir" ]] || return 1
  owner_only_path "$legacy_adoption_dir" 700 || return 1
  for entry in "$legacy_adoption_dir"/* "$legacy_adoption_dir"/.[!.]* "$legacy_adoption_dir"/..?*; do
    [[ -e "$entry" || -L "$entry" ]] || continue
    name="$(basename -- "$entry")"
    case "$name" in
      schemii-config.manifest|schemii-schemas.manifest) ;;
      *) return 1 ;;
    esac
    count=$((count + 1))
  done
  [[ "$count" == "2" ]] || return 1
  for logical in schemii-config schemii-schemas; do
    inspect_legacy_volume_identity "$logical" || return 1
    path="$legacy_adoption_dir/$logical.manifest"
    [[ -f "$path" && ! -L "$path" ]] || return 1
    owner_only_path "$path" 600 || return 1
    cmp -s -- "$path" <(legacy_manifest_body "$logical" "$legacy_identity_name" \
      "$legacy_identity_created_at" "$legacy_identity_driver" "$legacy_identity_mountpoint" "$legacy_identity_scope") \
      || return 1
  done
}
expected_legacy_consumer() {
  local logical="$1" service="$2" destination="$3"
  case "$logical|$service|$destination" in
    'schemii-config|schemii|/data/config'|\
    'schemii-config|schemer|/data/config'|\
    'schemii-config|example-profile-init|/data/config'|\
    'schemii-config|metadata-recovery|/data/config'|\
    'schemii-config|application-recovery-verify|/data/config'|\
    'schemii-schemas|schemii|/data/schemas'|\
    'schemii-schemas|schemer|/data/schemas'|\
    'schemii-schemas|metadata-recovery|/data/schemas'|\
    'schemii-schemas|application-recovery-verify|/data/schemas') return 0 ;;
  esac
  return 1
}
verify_legacy_volume_consumers() {
  local logical="$1" volume="${project}_${1}" output container_id mounts mount_type mount_name destination labels
  local consumer_project consumer_service consumer_working_dir witness=0
  local -a containers=()
  if ! output="$(docker ps -aq)"; then
    printf 'Docker could not enumerate containers while attesting %s. No adoption manifest was written.\n' "$volume" >&2
    return 1
  fi
  containers=( $output )
  for container_id in "${containers[@]}"; do
    if ! mounts="$(docker inspect --format '{{range .Mounts}}{{printf "%s|%s|%s\n" .Type .Name .Destination}}{{end}}' "$container_id" 2>/dev/null)"; then
      printf 'Docker could not inspect all mounts for container %s. No adoption manifest was written.\n' "$container_id" >&2
      return 1
    fi
    while IFS='|' read -r mount_type mount_name destination; do
      [[ -n "$mount_type" || -n "$mount_name" || -n "$destination" ]] || continue
      [[ "$mount_name" == "$volume" ]] || continue
      if ! labels="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}|{{ index .Config.Labels "com.docker.compose.service" }}|{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$container_id" 2>/dev/null)"; then
        printf 'Docker could not inspect ownership labels for volume consumer %s. No adoption manifest was written.\n' "$container_id" >&2
        return 1
      fi
      IFS='|' read -r consumer_project consumer_service consumer_working_dir <<< "$labels"
      if [[ "$mount_type" != "volume" || "$consumer_project" != "$project" \
        || "$consumer_working_dir" != "$script_dir" ]] \
        || ! expected_legacy_consumer "$logical" "$consumer_service" "$destination"; then
        printf 'Volume %s has a foreign or unexpected consumer: %s. No adoption manifest was written.\n' "$volume" "$container_id" >&2
        return 1
      fi
      witness=1
    done <<< "$mounts"
  done
  [[ "$witness" == "1" ]] || {
    printf 'Volume %s has no expected Compose project/service/repository witness. No adoption manifest was written.\n' "$volume" >&2
    return 1
  }
}
ensure_instance_stopped() {
  local container_output container_id running_state
  local -a containers=()
  if ! container_output="$(docker ps -aq --filter "label=com.docker.compose.project=$project")"; then
    printf 'Docker could not enumerate instance %s; legacy adoption or recovery refuses to infer that it is stopped.\n' "$project" >&2
    return 1
  fi
  containers=( $container_output )
  for container_id in "${containers[@]}"; do
    if ! running_state="$(docker inspect --format '{{.State.Running}}' "$container_id")"; then
      printf 'Docker could not inspect instance container %s; legacy adoption or recovery refuses to infer that it is stopped.\n' "$container_id" >&2
      return 1
    fi
    case "$running_state" in
      false) ;;
      true)
        printf 'Stop every container in instance %s before legacy adoption, coordinated backup, or restore. No data was changed.\n' "$project" >&2
        return 1
        ;;
      *)
        printf 'Docker returned an invalid running state for instance container %s; legacy adoption or recovery refuses to continue.\n' "$container_id" >&2
        return 1
        ;;
    esac
  done
}
adopt_legacy_volumes() {
  local confirmation="$1" logical staging path
  [[ "$project" == "schemii" ]] || {
    printf 'Legacy adoption is limited to the historical schemii volume pair.\n' >&2
    return 2
  }
  [[ "$confirmation" == "ADOPT:$project" ]] || {
    printf 'Legacy adoption requires literal confirmation ADOPT:%s. No adoption manifest was written.\n' "$project" >&2
    return 2
  }
  ensure_instance_stopped || return
  for logical in schemii-config schemii-schemas; do
    if ! inspect_legacy_volume_identity "$logical"; then
      printf 'Historical volume %s is missing, labeled, non-local, or lacks stable Docker identity. No adoption manifest was written.\n' "${project}_${logical}" >&2
      return 1
    fi
    verify_legacy_volume_consumers "$logical" || return
  done
  if [[ -e "$legacy_adoption_dir" || -L "$legacy_adoption_dir" ]]; then
    legacy_adoption_set_matches || {
      printf 'Existing legacy adoption evidence is incomplete, changed, or bound to different volumes; refusing to replace it.\n' >&2
      return 1
    }
    printf 'Historical volumes for %s are already attested by unchanged owner-only manifests.\n' "$project"
    return 0
  fi
  staging="$(mktemp -d "$credential_dir/.legacy-volume-adoptions.v1.XXXXXX")" \
    || { printf 'Legacy adoption staging could not be created.\n' >&2; return 1; }
  temporary_dir="$staging"
  chmod 700 "$staging" || return 1
  for logical in schemii-config schemii-schemas; do
    inspect_legacy_volume_identity "$logical" || {
      printf 'Historical volume identity changed during adoption. No adoption manifest was published.\n' >&2
      return 1
    }
    path="$staging/$logical.manifest"
    (umask 077; legacy_manifest_body "$logical" "$legacy_identity_name" "$legacy_identity_created_at" \
      "$legacy_identity_driver" "$legacy_identity_mountpoint" "$legacy_identity_scope" > "$path") \
      || { printf 'Legacy adoption manifest could not be staged.\n' >&2; return 1; }
    chmod 600 "$path" || return 1
  done
  sync || { printf 'Legacy adoption manifests could not be synchronized.\n' >&2; return 1; }
  mv -- "$staging" "$legacy_adoption_dir" \
    || { printf 'Legacy adoption manifests could not be atomically published.\n' >&2; return 1; }
  temporary_dir=""
  sync || { printf 'Legacy adoption publication could not be synchronized.\n' >&2; return 1; }
  legacy_adoption_set_matches || {
    printf 'Published legacy adoption manifests failed verification.\n' >&2
    return 1
  }
  printf 'Attested historical volumes %s and %s without changing their labels or contents.\n' \
    "${project}_schemii-config" "${project}_schemii-schemas"
}

metadata_psql() {
  local container="$1" authentication_password="$2" sql="$3"
  local pgpass=/tmp/schemii-credential-operation.pgpass
  if ! printf '%s\n' "$authentication_password" | docker exec -i -u postgres "$container" sh -c \
      'set -eu; umask 077; IFS= read -r password; printf "127.0.0.1:5432:schemii_metadata:schemii_metadata_migration:%s\n" "$password" > /tmp/schemii-credential-operation.pgpass'; then
    return 1
  fi
  if ! printf '%s\n' "$sql" | docker exec -i -u postgres -e PGPASSFILE="$pgpass" "$container" \
      psql --quiet --set ON_ERROR_STOP=1 --host 127.0.0.1 --username schemii_metadata_migration \
      --dbname schemii_metadata >/dev/null; then
    docker exec -u postgres "$container" rm -f "$pgpass" >/dev/null 2>&1 \
      || printf 'Staged metadata authentication could not be removed after failure.\n' >&2
    return 1
  fi
  docker exec -u postgres "$container" rm -f "$pgpass" >/dev/null \
    || { printf 'Staged metadata authentication could not be removed.\n' >&2; return 1; }
}
metadata_authenticates() {
  metadata_psql "$1" "$2" 'SELECT 1;'
}
wait_for_metadata() {
  local container="$1"
  for _ in {1..30}; do
    if docker exec -u postgres "$container" pg_isready --quiet --host 127.0.0.1 --port 5432 --dbname schemii_metadata; then
      return 0
    fi
    sleep 1
  done
  printf 'Metadata PostgreSQL did not become ready within 30 seconds.\n' >&2
  return 1
}
update_metadata_passwords() {
  local container="$1" authentication_password="$2" migration_password="$3" schemii_password="$4" schemer_password="$5" sql
  sql="$({
    printf '%s\n' "\\prompt '' migration_password" "$migration_password"
    printf '%s\n' "\\prompt '' schemii_password" "$schemii_password"
    printf '%s\n' "\\prompt '' schemer_password" "$schemer_password"
    printf '%s\n' "SELECT schemii_admin.rotate_metadata_passwords(:'migration_password', :'schemii_password', :'schemer_password');"
  })"
  metadata_psql "$container" "$authentication_password" "$sql"
}
restart_credential_consumers() {
  local metadata_container="$1" dependent_output
  local -a dependent_ids=()
  docker restart "$metadata_container" >/dev/null || return 1
  if ! dependent_output="$(docker ps -q --filter "label=com.docker.compose.project=$project")"; then
    printf 'Dependent container lookup failed during credential replacement.\n' >&2
    return 1
  fi
  dependent_ids=( $dependent_output )
  for dependent_id in "${dependent_ids[@]}"; do
    if [[ "$dependent_id" != "$metadata_container" ]]; then
      docker restart "$dependent_id" >/dev/null || return 1
    fi
  done
}
replace_from_transaction() {
  local side="$1" name value
  for name in "${credential_files[@]}"; do
    value="$(read_credential "$credential_transaction/$side/$name" "$side $name")" || return 1
    replace_secret "$credential_dir/$name" "$value" || return 1
  done
}
rollback_credential_transaction() {
  local metadata_container="$1" preserve_transaction="${2:-0}" old_migration new_migration old_schemii old_schemer
  old_migration="$(read_credential "$credential_transaction/old/metadata_migration_password" 'old migration credential')" || return 1
  new_migration="$(read_credential "$credential_transaction/new/metadata_migration_password" 'new migration credential')" || return 1
  old_schemii="$(read_credential "$credential_transaction/old/metadata_schemii_password" 'old Schemii credential')" || return 1
  old_schemer="$(read_credential "$credential_transaction/old/metadata_schemer_password" 'old Schemer credential')" || return 1
  docker start "$metadata_container" >/dev/null || return 1
  wait_for_metadata "$metadata_container" || return 1
  if metadata_authenticates "$metadata_container" "$new_migration"; then
    update_metadata_passwords "$metadata_container" "$new_migration" "$old_migration" "$old_schemii" "$old_schemer" || return 1
  elif ! metadata_authenticates "$metadata_container" "$old_migration"; then
    printf 'Neither staged metadata credential authenticates; transaction recovery requires administrator review.\n' >&2
    return 1
  fi
  replace_from_transaction old || return 1
  restart_credential_consumers "$metadata_container" || return 1
  wait_for_metadata "$metadata_container" || return 1
  metadata_authenticates "$metadata_container" "$old_migration" || return 1
  [[ "$preserve_transaction" == "1" ]] || rm -rf -- "$credential_transaction"
}
run_credential_transaction() {
  local metadata_container="$1" preserve_transaction="${2:-0}" old_migration new_migration new_schemii new_schemer
  old_migration="$(read_credential "$credential_transaction/old/metadata_migration_password" 'old migration credential')" || return 1
  new_migration="$(read_credential "$credential_transaction/new/metadata_migration_password" 'new migration credential')" || return 1
  new_schemii="$(read_credential "$credential_transaction/new/metadata_schemii_password" 'new Schemii credential')" || return 1
  new_schemer="$(read_credential "$credential_transaction/new/metadata_schemer_password" 'new Schemer credential')" || return 1
  wait_for_metadata "$metadata_container" || return 1
  update_metadata_passwords "$metadata_container" "$old_migration" "$new_migration" "$new_schemii" "$new_schemer" || return 1
  replace_from_transaction new || return 1
  restart_credential_consumers "$metadata_container" || return 1
  wait_for_metadata "$metadata_container" || return 1
  metadata_authenticates "$metadata_container" "$new_migration" || return 1
  [[ "$preserve_transaction" == "1" ]] || rm -rf -- "$credential_transaction"
}
stage_credential_transaction() {
  local source="$1" operation="${2:-credential-operation}" name staging current staged value
  if [[ -d "$credential_transaction" ]]; then
    [[ "$(read_single_line "$credential_transaction/operation" 'credential transaction operation')" == "$operation" ]] || return 1
    for name in "${credential_files[@]}"; do
      current="$(read_credential "$source/$name" "new $name")" || return 1
      staged="$(read_credential "$credential_transaction/new/$name" "staged new $name")" || return 1
      [[ "$current" == "$staged" ]] || return 1
    done
    return 0
  fi
  staging="$(mktemp -d "$credential_dir/.credential-transaction-stage.XXXXXX")" || return 1
  mkdir "$staging/old" "$staging/new" || {
    rm -rf -- "$staging" || printf 'Failed credential staging could not be removed: %s\n' "$staging" >&2
    return 1
  }
  for name in "${credential_files[@]}"; do
    value="$(read_credential "$credential_dir/$name" "$name")" \
      || {
        rm -rf -- "$staging" || printf 'Failed credential staging could not be removed: %s\n' "$staging" >&2
        return 1
      }
    write_secret "$staging/old/$name" "$value" || {
      rm -rf -- "$staging" || printf 'Failed credential staging could not be removed: %s\n' "$staging" >&2
      return 1
    }
    value="$(read_credential "$source/$name" "new $name")" \
      || {
        rm -rf -- "$staging" || printf 'Failed credential staging could not be removed: %s\n' "$staging" >&2
        return 1
      }
    write_secret "$staging/new/$name" "$value" || {
      rm -rf -- "$staging" || printf 'Failed credential staging could not be removed: %s\n' "$staging" >&2
      return 1
    }
  done
  write_marker "$staging/instance" "$project" || {
    rm -rf -- "$staging" || printf 'Failed credential staging could not be removed: %s\n' "$staging" >&2
    return 1
  }
  write_marker "$staging/operation" "$operation" || {
    rm -rf -- "$staging" || printf 'Failed credential staging could not be removed: %s\n' "$staging" >&2
    return 1
  }
  mv "$staging" "$credential_transaction" || {
    rm -rf -- "$staging" || printf 'Failed credential staging could not be removed: %s\n' "$staging" >&2
    return 1
  }
}

validate_committed_restore_credentials() {
  local source="$1" name active reviewed staged marker operation
  [[ ! -e "$credential_transaction" || ! -e "$credential_commit_cleanup" ]] \
    || { printf 'Both rollback and committed credential cleanup transactions exist; refusing automatic recovery.\n' >&2; return 1; }
  if [[ -e "$credential_transaction" ]]; then
    [[ -d "$credential_transaction" && ! -L "$credential_transaction" ]] \
      || { printf 'Credential rollback transaction is invalid.\n' >&2; return 1; }
    marker="$(read_single_line "$credential_transaction/instance" 'credential transaction marker')" || return 1
    [[ "$marker" == "$project" ]] || { printf 'Credential transaction belongs to another instance.\n' >&2; return 1; }
    operation="$(read_single_line "$credential_transaction/operation" 'credential transaction operation')" || return 1
    [[ "$operation" == instance-restore ]] || { printf 'Credential transaction is not a coordinated restore transaction.\n' >&2; return 1; }
  fi
  if [[ -e "$credential_commit_cleanup" ]]; then
    [[ -d "$credential_commit_cleanup" && ! -L "$credential_commit_cleanup" ]] \
      || { printf 'Committed credential cleanup transaction is invalid.\n' >&2; return 1; }
  fi
  for name in "${credential_files[@]}"; do
    reviewed="$(read_credential "$source/$name" "backup $name")" || return 1
    active="$(read_credential "$credential_dir/$name" "active $name")" || return 1
    [[ "$active" == "$reviewed" ]] \
      || { printf 'Active credentials do not match the committed restore source; refusing cleanup.\n' >&2; return 1; }
    if [[ -d "$credential_transaction" ]]; then
      staged="$(read_credential "$credential_transaction/new/$name" "staged new $name")" || return 1
      [[ "$staged" == "$reviewed" ]] \
        || { printf 'Staged credentials do not match the committed restore source; refusing cleanup.\n' >&2; return 1; }
    fi
  done
}

cleanup_committed_credential_transaction() {
  if [[ -d "$credential_transaction" ]]; then
    [[ ! -e "$credential_commit_cleanup" ]] \
      || { printf 'Committed credential cleanup destination already exists.\n' >&2; return 1; }
    mv "$credential_transaction" "$credential_commit_cleanup" \
      || { printf 'Credential transaction could not enter forward cleanup.\n' >&2; return 1; }
    sync || { printf 'Credential forward-cleanup transition could not be synchronized.\n' >&2; return 1; }
  fi
  if [[ -e "$credential_commit_cleanup" ]]; then
    [[ -d "$credential_commit_cleanup" && ! -L "$credential_commit_cleanup" ]] \
      || { printf 'Committed credential cleanup transaction is invalid.\n' >&2; return 1; }
    rm -rf -- "$credential_commit_cleanup" \
      || { printf 'Committed credential cleanup could not be completed.\n' >&2; return 1; }
    sync || { printf 'Committed credential cleanup could not be synchronized.\n' >&2; return 1; }
  fi
}

for stale_transaction_stage in "$credential_dir"/.credential-transaction-stage.*; do
  if [[ -e "$stale_transaction_stage" ]]; then
    rm -rf -- "$stale_transaction_stage" \
      || { printf 'Stale unpublished credential staging could not be removed: %s\n' "$stale_transaction_stage" >&2; exit 1; }
  fi
done
if [[ -e "$credential_commit_cleanup" && "$credential_action" != instance-restore ]]; then
  printf 'An interrupted committed restore requires forward cleanup by rerunning instance-restore with its reviewed backup and RESTORE:%s confirmation.\n' "$project" >&2
  exit 1
fi
if [[ -d "$credential_transaction" ]]; then
  recovery_marker="$(read_single_line "$credential_transaction/instance" 'credential transaction marker')" || exit 1
  [[ "$recovery_marker" == "$project" ]] || { printf 'Credential transaction belongs to another instance; refusing recovery.\n' >&2; exit 1; }
  transaction_operation="credential-operation"
  if [[ -f "$credential_transaction/operation" ]]; then
    transaction_operation="$(read_single_line "$credential_transaction/operation" 'credential transaction operation')" || exit 1
  fi
  if [[ "$transaction_operation" == "instance-restore" && "$credential_action" != "instance-restore" ]]; then
    printf 'An interrupted coordinated restore must be resolved by rerunning instance-restore with its reviewed backup and RESTORE:%s confirmation; its durable state determines rollback or forward cleanup.\n' "$project" >&2
    exit 1
  fi
  if [[ "$transaction_operation" == instance-restore ]]; then
    printf 'Retained coordinated credential evidence will follow the durable recovery state for %s.\n' "$project" >&2
  else
    credential_recovery_containers=( $(docker ps -aq --filter "label=com.docker.compose.project=$project" --filter label=com.docker.compose.service=metadata-postgres) )
    credential_recovery_container="${credential_recovery_containers[0]:-}"
    [[ -n "$credential_recovery_container" ]] || { printf 'An incomplete credential transaction needs its metadata container for recovery.\n' >&2; exit 1; }
    printf 'Recovering an incomplete %s transaction for %s.\n' "$transaction_operation" "$project" >&2
    rollback_credential_transaction "$credential_recovery_container" 0 || { printf 'Automatic credential rollback failed; staged old/new values remain in %s.\n' "$credential_transaction" >&2; exit 1; }
  fi
fi

if [[ -n "$credential_action" ]]; then
  case "$credential_action" in
    credentials-backup)
      destination="${2:-}"
      [[ -n "$destination" ]] || { printf 'Usage: bash ./start.sh credentials-backup <directory>\n' >&2; exit 2; }
      mkdir -p "$destination/$project" \
        || { printf 'Credential backup directory could not be created.\n' >&2; exit 1; }
      chmod 700 "$destination/$project" \
        || { printf 'Credential backup directory permissions could not be restricted.\n' >&2; exit 1; }
      for secret_name in instance "${credential_files[@]}"; do
        cp "$credential_dir/$secret_name" "$destination/$project/$secret_name" \
          || { printf 'Credential backup copy failed for %s.\n' "$secret_name" >&2; exit 1; }
        chmod 600 "$destination/$project/$secret_name" \
          || { printf 'Credential backup permissions failed for %s.\n' "$secret_name" >&2; exit 1; }
      done
      printf 'Credential backup created at %s. Protect it like a password vault.\n' "$destination/$project"
      exit 0
      ;;
    credentials-restore)
      source_dir="${2:-}"
      [[ -n "$source_dir" ]] || { printf 'Usage: bash ./start.sh credentials-restore <directory>\n' >&2; exit 2; }
      [[ -d "$source_dir/$project" ]] && source_dir="$source_dir/$project"
      backup_marker="$(read_single_line "$source_dir/instance" 'backup instance marker')" || exit 2
      [[ "$backup_marker" == "$project" ]] || { printf 'Backup instance marker does not exactly match %s.\n' "$project" >&2; exit 2; }
      for secret_name in "${credential_files[@]}"; do
        read_credential "$source_dir/$secret_name" "backup $secret_name" >/dev/null || exit 2
      done
      if [[ "$legacy_metadata" == "1" ]]; then
        restore_containers=( $(docker ps -aq --filter "label=com.docker.compose.project=$project" --filter label=com.docker.compose.service=metadata-postgres) )
        metadata_container="${restore_containers[0]:-}"
        [[ -n "$metadata_container" ]] || { printf 'Start the instance before restoring credentials for its existing metadata volume. No files were changed.\n' >&2; exit 2; }
        docker start "$metadata_container" >/dev/null \
          || { printf 'Metadata container could not be started for credential restore.\n' >&2; exit 1; }
        stage_credential_transaction "$source_dir" \
          || { printf 'Credential restore transaction could not be staged.\n' >&2; exit 1; }
        if ! run_credential_transaction "$metadata_container"; then
          printf 'Credential restore failed; rolling back PostgreSQL, files, and containers.\n' >&2
          rollback_credential_transaction "$metadata_container" || { printf 'Automatic rollback failed; staged old/new values remain in %s.\n' "$credential_transaction" >&2; exit 1; }
          exit 1
        fi
      else
        for secret_name in "${credential_files[@]}"; do
          restored_value="$(read_credential "$source_dir/$secret_name" "backup $secret_name")" || exit 1
          replace_secret "$credential_dir/$secret_name" "$restored_value" || exit 1
        done
      fi
      printf 'Credentials restored for %s and dependent containers restarted.\n' "$project"
      exit 0
      ;;
    credentials-rotate)
      rotate_containers=( $(docker ps -q --filter "label=com.docker.compose.project=$project" --filter label=com.docker.compose.service=metadata-postgres) )
      metadata_container="${rotate_containers[0]:-}"
      [[ -n "$metadata_container" ]] || { printf 'Start the instance before rotating credentials. No files were changed.\n' >&2; exit 2; }
      temporary_dir="$(mktemp -d "$credential_dir/.new.XXXXXX")" \
        || { printf 'Credential rotation staging directory could not be created.\n' >&2; exit 1; }
      bootstrap_value="$(read_credential "$credential_dir/metadata_bootstrap_password" metadata_bootstrap_password)" || exit 1
      write_secret "$temporary_dir/metadata_bootstrap_password" "$bootstrap_value" || exit 1
      for secret_name in metadata_migration_password metadata_schemii_password metadata_schemer_password opencode_password; do
        generated_value="$(generate_secret)" || exit 1
        write_secret "$temporary_dir/$secret_name" "$generated_value" || exit 1
      done
      stage_credential_transaction "$temporary_dir" \
        || { printf 'Credential rotation transaction could not be staged.\n' >&2; exit 1; }
      rm -rf -- "$temporary_dir" \
        || { printf 'Credential rotation staging directory could not be removed.\n' >&2; exit 1; }
      temporary_dir=""
      if ! run_credential_transaction "$metadata_container"; then
        printf 'Credential rotation failed; rolling back PostgreSQL, files, and containers.\n' >&2
        rollback_credential_transaction "$metadata_container" || { printf 'Automatic rollback failed; staged old/new values remain in %s.\n' "$credential_transaction" >&2; exit 1; }
        exit 1
      fi
      printf 'Credentials rotated for %s and dependent containers restarted.\n' "$project"
      exit 0
      ;;
    legacy-volume-adopt)
      adopt_legacy_volumes "${2:-}" || exit $?
      exit 0
      ;;
  esac
fi
if [[ "$credential_action" != "instance-backup" && "$credential_action" != "instance-restore" ]]; then
  release_credential_lock
fi
if [[ "$project" == "schemii" ]]; then
  default_port=8080
  default_schemer_port=8081
else
  read -r instance_number _ <<< "$(printf '%s' "$project" | cksum)"
  default_port=$((12000 + instance_number % 30000))
  default_schemer_port=$((12000 + (instance_number + 1) % 30000))
fi
port="${SCHEMII_HOST_PORT:-$default_port}"
schemer_port="${SCHEMER_HOST_PORT:-$default_schemer_port}"
project_containers=()
while IFS= read -r container_id; do
  [[ -z "$container_id" ]] || project_containers+=("$container_id")
done < <(docker ps -aq --filter "label=com.docker.compose.project=$project" --filter label=com.docker.compose.service=schemii)
schemii_ingress_containers=()
while IFS= read -r container_id; do
  [[ -z "$container_id" ]] || schemii_ingress_containers+=("$container_id")
done < <(docker ps -aq --filter "label=com.docker.compose.project=$project" --filter label=com.docker.compose.service=schemii-ingress)
schemer_containers=()
while IFS= read -r container_id; do
  [[ -z "$container_id" ]] || schemer_containers+=("$container_id")
done < <(docker ps -aq --filter "label=com.docker.compose.project=$project" --filter label=com.docker.compose.service=schemer)
schemer_ingress_containers=()
while IFS= read -r container_id; do
  [[ -z "$container_id" ]] || schemer_ingress_containers+=("$container_id")
done < <(docker ps -aq --filter "label=com.docker.compose.project=$project" --filter label=com.docker.compose.service=schemer-ingress)
port_in_use() { (exec 3<>"/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1; }
if [[ ( ${#project_containers[@]} -gt 0 || ${#schemii_ingress_containers[@]} -gt 0 ) && -z "${SCHEMII_HOST_PORT:-}" ]]; then
  port_container="${schemii_ingress_containers[0]:-${project_containers[0]:-}}"
  existing_port="$(docker inspect --format '{{with index .HostConfig.PortBindings "8080/tcp"}}{{(index . 0).HostPort}}{{end}}' "$port_container" 2>/dev/null || true)"
  if [[ -z "$existing_port" ]]; then
    while IFS= read -r value; do
      [[ "$value" == SCHEMII_PORT=* ]] && existing_port="${value#SCHEMII_PORT=}"
    done < <(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${project_containers[0]:-}" 2>/dev/null || true)
  fi
  [[ "$existing_port" =~ ^[0-9]+$ ]] && port="$existing_port"
elif [[ ${#project_containers[@]} -eq 0 && ${#schemii_ingress_containers[@]} -eq 0 ]]; then
  if [[ -z "${SCHEMII_HOST_PORT:-}" ]]; then
    while port_in_use "$port"; do
      port=$((port + 1))
      [[ "$port" -le 41999 ]] || port=12000
    done
  fi
fi
if [[ ( ${#schemer_containers[@]} -gt 0 || ${#schemer_ingress_containers[@]} -gt 0 ) && -z "${SCHEMER_HOST_PORT:-}" ]]; then
  schemer_port_container="${schemer_ingress_containers[0]:-${schemer_containers[0]:-}}"
  existing_schemer_port="$(docker inspect --format '{{with index .HostConfig.PortBindings "8080/tcp"}}{{(index . 0).HostPort}}{{end}}' "$schemer_port_container" 2>/dev/null || true)"
  if [[ -z "$existing_schemer_port" && ${#schemer_ingress_containers[@]} -eq 0 ]]; then
    existing_schemer_port="$(docker inspect --format '{{with index .HostConfig.PortBindings "8081/tcp"}}{{(index . 0).HostPort}}{{end}}' "${schemer_containers[0]}" 2>/dev/null || true)"
  fi
  [[ "$existing_schemer_port" =~ ^[0-9]+$ ]] && schemer_port="$existing_schemer_port"
elif [[ ${#schemer_containers[@]} -eq 0 && ${#schemer_ingress_containers[@]} -eq 0 && -z "${SCHEMER_HOST_PORT:-}" ]]; then
  while port_in_use "$schemer_port" || [[ "$schemer_port" == "$port" ]]; do
    schemer_port=$((schemer_port + 1))
    [[ "$schemer_port" -le 41999 ]] || schemer_port=12000
  done
fi
export SCHEMII_HOST_PORT="$port"
export SCHEMER_HOST_PORT="$schemer_port"
default_application_image="schemii:${project}"
default_metadata_image="schemii-metadata-postgres:${project}"
default_opencode_image="schemii-opencode:1.18.15-${project}"
release_version="$(tr -d '\r\n' < "$script_dir/VERSION" 2>/dev/null || true)"
release_revision="$(tr -d '\r\n' < "$script_dir/src/schemii/build_revision.txt" 2>/dev/null || true)"
if [[ "$release_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ && "$release_revision" =~ ^[0-9a-f]{40}$ ]]; then
  release_identity="${release_version}-${release_revision}"
  default_application_image="schemii:${release_identity}"
  default_metadata_image="schemii-metadata-postgres:${release_identity}"
  default_opencode_image="schemii-opencode:${release_identity}"
fi
export SCHEMII_IMAGE="${SCHEMII_IMAGE:-$default_application_image}"
export SCHEMII_METADATA_IMAGE="${SCHEMII_METADATA_IMAGE:-$default_metadata_image}"
export SCHEMII_OPENCODE_IMAGE="${SCHEMII_OPENCODE_IMAGE:-$default_opencode_image}"
compose_base=(docker compose --project-name "$project" --project-directory "$script_dir" -f "$script_dir/compose.yaml")

run_required() {
  local message="$1" status
  shift
  if "$@"; then
    return 0
  else
    status=$?
    printf '%s\n' "$message" >&2
    return "$status"
  fi
}

ensure_recovery_volumes() {
  local logical="$1" volume="${project}_${1}" labels
  if ! labels="$(docker volume inspect --format '{{ index .Labels "com.docker.compose.project" }}|{{ index .Labels "com.docker.compose.volume" }}' "$volume" 2>/dev/null)"; then
    printf 'Required reviewed destination volume is missing or belongs to another project: %s\n' "$volume" >&2
    return 1
  fi
  [[ "$labels" == "$project|$logical" ]] && return 0
  if [[ "$labels" == "|" ]] && legacy_adoptable_volume "$logical"; then
    if legacy_adoption_set_matches; then return 0; fi
    printf 'Historical unlabeled volume %s lacks unchanged adoption evidence. With all schemii containers stopped, run: SCHEMII_INSTANCE=schemii bash ./start.sh legacy-volume-adopt ADOPT:schemii\n' "$volume" >&2
    return 1
  fi
  printf 'Required reviewed destination volume is missing or belongs to another project: %s\n' "$volume" >&2
  return 1
}
prepare_recovery_volumes() {
  run_required 'Selected immutable recovery images are not loaded.' \
    docker image inspect "$SCHEMII_IMAGE" "$SCHEMII_METADATA_IMAGE" >/dev/null || return $?
  run_required 'Recovery volumes could not be prepared.' \
    "${recovery_compose[@]}" run --rm --no-deps metadata-recovery prepare || return $?
  ensure_recovery_volumes schemer-dashboards || return $?
  ensure_recovery_volumes schemii-recovery || return $?
}
start_recovery_metadata() {
  run_required 'Metadata PostgreSQL could not be started for recovery.' \
    "${recovery_compose[@]}" up -d metadata-postgres || return $?
  if ! recovery_metadata_container="$("${recovery_compose[@]}" ps -q metadata-postgres)"; then
    printf 'Metadata PostgreSQL container lookup failed during recovery.\n' >&2
    return 1
  fi
  [[ -n "$recovery_metadata_container" ]] \
    || { printf 'Metadata PostgreSQL container was not created for recovery.\n' >&2; return 1; }
  wait_for_metadata "$recovery_metadata_container" || return $?
}
stop_recovery_metadata() {
  if [[ -n "$recovery_metadata_container" ]]; then
    run_required 'Metadata PostgreSQL could not be stopped after recovery.' \
      docker stop "$recovery_metadata_container" >/dev/null || return $?
  fi
  recovery_metadata_container=""
}
read_recovery_state() {
  local output
  if ! output="$("${recovery_compose[@]}" run --rm --no-deps metadata-recovery state)"; then
    printf 'Recovery transaction state could not be determined; refusing automatic rollback.\n' >&2
    return 1
  fi
  recovery_state="${output##*$'\n'}"
  recovery_state="${recovery_state%$'\r'}"
  case "$recovery_state" in
    none|rollback-required|committed-cleanup-required) ;;
    *) printf 'Recovery transaction returned an invalid state: %s\n' "$recovery_state" >&2; return 1 ;;
  esac
}
finish_committed_restore() {
  local credential_source="$1"
  validate_committed_restore_credentials "$credential_source" || return 1
  run_required 'Committed recovery data cleanup could not be completed; rollback remains forbidden.' \
    "${recovery_compose[@]}" run --rm --no-deps metadata-recovery commit || return $?
  cleanup_committed_credential_transaction || return 1
  run_required 'Committed recovery finalization could not be completed; rollback remains forbidden.' \
    "${recovery_compose[@]}" run --rm --no-deps metadata-recovery finalize-commit || return $?
}
run_instance_backup() {
  local destination="$1" destination_dir backup_path
  [[ -n "$destination" ]] || { printf 'Usage: bash ./start.sh instance-backup <directory>\n' >&2; return 2; }
  ensure_instance_stopped || return
  ensure_recovery_volumes schemii-config || return
  ensure_recovery_volumes schemii-schemas || return
  ensure_recovery_volumes schemii-metadata-postgres || return
  prepare_recovery_volumes || return
  backup_path="${destination%/}/$project"
  [[ ! -e "$backup_path" ]] || { printf 'Backup destination already exists; refusing to overwrite it: %s\n' "$backup_path" >&2; return 2; }
  destination_dir="${destination%/}"
  [[ -n "$destination_dir" ]] || destination_dir=/
  (umask 077; mkdir -p "$destination_dir") \
    || { printf 'Backup destination directory could not be created: %s\n' "$destination_dir" >&2; return 1; }
  recovery_backup_staging="${backup_path}.incomplete.$$-${RANDOM:-0}-$(date +%s)"
  [[ ! -e "$recovery_backup_staging" ]] || { printf 'Backup staging path already exists; refusing to overwrite it: %s\n' "$recovery_backup_staging" >&2; return 2; }
  (umask 077; mkdir "$recovery_backup_staging") \
    || { printf 'Backup staging directory could not be created: %s\n' "$recovery_backup_staging" >&2; return 1; }
  start_recovery_metadata || return
  run_required 'Current config or dashboards failed backup validation.' \
    "${recovery_compose[@]}" run --rm --no-deps application-recovery-verify || return $?
  recovery_container="${project}-recovery-backup-$$"
  run_required 'Coordinated backup failed.' \
    "${recovery_compose[@]}" run --name "$recovery_container" --no-deps metadata-recovery backup || return $?
  run_required 'Backup output could not be copied to the destination.' \
    docker cp "$recovery_container:/transaction/output/." "$recovery_backup_staging" || return $?
  run_required 'Recovery backup container could not be removed.' \
    docker rm "$recovery_container" >/dev/null || return $?
  recovery_container=""
  [[ -f "$recovery_backup_staging/complete" ]] || { printf 'Backup copy did not complete; the destination must not be used for restore.\n' >&2; return 1; }
  chmod -R go-rwx "$recovery_backup_staging" \
    || { printf 'Backup destination permissions could not be restricted.\n' >&2; return 1; }
  mv -- "$recovery_backup_staging" "$backup_path" \
    || { printf 'Completed backup could not be published at %s.\n' "$backup_path" >&2; return 1; }
  recovery_backup_staging=""
  stop_recovery_metadata || return $?
  printf 'Coordinated backup for %s created at %s. It contains plaintext credentials and sensitive metadata.\n' "$project" "$backup_path"
}
rollback_instance_restore() {
  local metadata_container="$1"
  if [[ -d "$credential_transaction" ]]; then
    rollback_credential_transaction "$metadata_container" 1 || return 1
  fi
  "${recovery_compose[@]}" run --rm --no-deps metadata-recovery rollback || return 1
  rm -rf -- "$credential_transaction" || return 1
}
run_instance_restore() {
  local source="$1" confirmation="$2" metadata_container verification_status=0 restore_status=0 commit_status=0
  [[ -n "$source" && -n "$confirmation" ]] || { printf 'Usage: bash ./start.sh instance-restore <directory> RESTORE:<exact-instance-name>\n' >&2; return 2; }
  [[ "$confirmation" == "RESTORE:$project" ]] || { printf 'Destructive restore confirmation must exactly equal RESTORE:%s\n' "$project" >&2; return 2; }
  [[ -d "$source/$project" ]] && source="$source/$project"
  [[ -d "$source" ]] || { printf 'Backup directory does not exist: %s\n' "$source" >&2; return 2; }
  if ! source="$(cd -- "$source" && pwd -P)"; then
    printf 'Backup directory could not be resolved: %s\n' "$source" >&2
    return 2
  fi
  [[ "$(read_single_line "$source/instance" 'backup instance marker')" == "$project" ]] || { printf 'Backup instance marker does not exactly match %s.\n' "$project" >&2; return 2; }
  [[ "$(read_single_line "$source/credentials/instance" 'credential backup instance marker')" == "$project" ]] || { printf 'Credential backup instance marker does not exactly match %s.\n' "$project" >&2; return 2; }
  for secret_name in "${credential_files[@]}"; do read_credential "$source/credentials/$secret_name" "backup $secret_name" >/dev/null || return 2; done
  ensure_instance_stopped || return
  ensure_recovery_volumes schemii-config || return
  ensure_recovery_volumes schemii-schemas || return
  ensure_recovery_volumes schemii-metadata-postgres || return
  prepare_recovery_volumes || return
  read_recovery_state || return
  if [[ "$recovery_state" == committed-cleanup-required ]]; then
    finish_committed_restore "$source/credentials" || return
    printf 'Completed forward cleanup for the committed restore of %s. The instance remains stopped for review.\n' "$project"
    return 0
  fi
  start_recovery_metadata || return
  metadata_container="$recovery_metadata_container"
  if [[ "$recovery_state" == rollback-required || -d "$credential_transaction" ]]; then
    if [[ -d "$credential_transaction" ]] \
        && ! stage_credential_transaction "$source/credentials" instance-restore; then
      printf 'Reviewed backup credentials do not match the retained restore staging transaction.\n' >&2
      return 1
    fi
    printf 'Rolling back the incomplete coordinated restore for %s before retry.\n' "$project" >&2
    rollback_instance_restore "$metadata_container" \
      || { printf 'Automatic coordinated rollback failed; recovery evidence remains in the credential and Docker recovery volumes.\n' >&2; return 1; }
  fi
  run_required 'Backup manifest or archive verification failed.' \
    "${recovery_compose[@]}" run --rm --no-deps -v "$source:/backup:ro" metadata-recovery stage-verification \
    || verification_status=$?
  if [[ "$verification_status" == "0" ]]; then
    run_required 'Backup application compatibility validation failed.' \
      "${recovery_compose[@]}" run --rm --no-deps application-recovery-verify backup /transaction/verification \
      || verification_status=$?
  fi
  run_required 'Backup verification staging could not be cleaned.' \
    "${recovery_compose[@]}" run --rm --no-deps metadata-recovery clear-verification \
    || verification_status=$?
  if [[ "$verification_status" != "0" ]]; then
    printf 'Backup compatibility validation failed before destination data was changed.\n' >&2
    return "$verification_status"
  fi
  if ! stage_credential_transaction "$source/credentials" instance-restore; then
    printf 'Reviewed backup credentials do not match the retained restore staging transaction.\n' >&2
    return 1
  fi
  run_required 'Instance data restore command failed.' \
    "${recovery_compose[@]}" run --rm --no-deps -e "SCHEMII_RECOVERY_CONFIRM=RESTORE:$project" -v "$source:/backup:ro" metadata-recovery restore \
    || restore_status=$?
  if [[ "$restore_status" == "0" ]]; then
    run_required 'Restored metadata migration failed.' \
      "${recovery_compose[@]}" run --rm --no-deps metadata-migrate || restore_status=$?
  fi
  if [[ "$restore_status" == "0" ]]; then
    run_required 'Restored metadata security verification failed.' \
      "${recovery_compose[@]}" run --rm --no-deps metadata-recovery verify-metadata || restore_status=$?
  fi
  if [[ "$restore_status" == "0" ]]; then
    run_required 'Restored application data validation failed.' \
      "${recovery_compose[@]}" run --rm --no-deps application-recovery-verify || restore_status=$?
  fi
  if [[ "$restore_status" != "0" ]]; then
    printf 'Instance data restore failed; restoring the reviewed destination snapshot.\n' >&2
    rollback_instance_restore "$metadata_container" || { printf 'Automatic coordinated rollback failed; recovery evidence remains in the credential and Docker recovery volumes.\n' >&2; return 1; }
    return "$restore_status"
  fi
  if ! run_credential_transaction "$metadata_container" 1; then
    printf 'Credential restore failed; rolling back credentials and instance data.\n' >&2
    rollback_instance_restore "$metadata_container" || { printf 'Automatic coordinated rollback failed; recovery evidence remains in the credential and Docker recovery volumes.\n' >&2; return 1; }
    return 1
  fi
  if ! stop_recovery_metadata; then
    printf 'Metadata PostgreSQL could not be stopped after restore; recovery evidence was retained and commit was not attempted.\n' >&2
    return 1
  fi
  "${recovery_compose[@]}" run --rm --no-deps metadata-recovery commit || commit_status=$?
  if [[ "$commit_status" != 0 ]]; then
    if ! read_recovery_state; then
      printf 'Recovery commit outcome is uncertain; evidence was retained and automatic rollback was not attempted.\n' >&2
      return "$commit_status"
    fi
    if [[ "$recovery_state" == committed-cleanup-required ]]; then
      printf 'Recovery commit began and forward cleanup remains required; evidence was retained and rollback was not attempted.\n' >&2
      return "$commit_status"
    fi
    if [[ "$recovery_state" == rollback-required ]]; then
      printf 'Recovery commit failed before publication; rolling back credentials and instance data.\n' >&2
      rollback_instance_restore "$metadata_container" || { printf 'Automatic coordinated rollback failed; recovery evidence remains in the credential and Docker recovery volumes.\n' >&2; return 1; }
      return "$commit_status"
    fi
    printf 'Recovery commit failed without a recoverable state; evidence was retained and automatic rollback was not attempted.\n' >&2
    return "$commit_status"
  fi
  validate_committed_restore_credentials "$source/credentials" || return 1
  cleanup_committed_credential_transaction || return 1
  run_required 'Recovery commit marker could not be finalized; forward cleanup remains required.' \
    "${recovery_compose[@]}" run --rm --no-deps metadata-recovery finalize-commit || return $?
  printf 'Coordinated restore completed for %s. The instance remains stopped for review; rerun the desired launch mode when ready.\n' "$project"
}

if [[ "$credential_action" == "instance-backup" || "$credential_action" == "instance-restore" ]]; then
  recovery_compose=("${compose_base[@]}" -f "$script_dir/compose.recovery.yaml")
  operation_status=0
  if [[ "$credential_action" == "instance-backup" ]]; then
    run_instance_backup "${2:-}" || operation_status=$?
  else
    run_instance_restore "${2:-}" "${3:-}" || operation_status=$?
  fi
  cleanup_status=0
  stop_recovery_metadata || cleanup_status=$?
  if [[ "$operation_status" == "0" && "$cleanup_status" != "0" ]]; then
    operation_status="$cleanup_status"
  fi
  release_credential_lock
  exit "$operation_status"
fi

case "$mode" in
  ui)
    compose=("${compose_base[@]}")
    ;;
  local-db)
    if [[ "$(uname -s)" != "Linux" ]]; then
      printf 'local-db mode is Linux-only. On Docker Desktop, use ui mode and profile host.docker.internal.\n' >&2
      exit 1
    fi
    compose=("${compose_base[@]}" -f "$script_dir/compose.local-db.yaml")
    ;;
  docker-db)
    compose=("${compose_base[@]}" -f "$script_dir/compose.postgres.yaml")
    ;;
  ai)
    compose=("${compose_base[@]}" -f "$script_dir/compose.ai.yaml")
    ;;
  ai-local-db)
    if [[ "$(uname -s)" != "Linux" ]]; then
      printf 'ai-local-db mode is Linux-only. Use ai mode with host.docker.internal on Docker Desktop.\n' >&2
      exit 1
    fi
    compose=("${compose_base[@]}" -f "$script_dir/compose.local-db.yaml" -f "$script_dir/compose.ai.yaml" -f "$script_dir/compose.ai.local-db.yaml")
    ;;
  ai-docker-db)
    compose=("${compose_base[@]}" -f "$script_dir/compose.postgres.yaml" -f "$script_dir/compose.ai.yaml")
    ;;
  schemer)
    compose=("${compose_base[@]}" -f "$script_dir/compose.postgres.yaml" -f "$script_dir/compose.schemer.yaml")
    ;;
  schemer-ai)
    compose=("${compose_base[@]}" -f "$script_dir/compose.postgres.yaml" -f "$script_dir/compose.ai.yaml" -f "$script_dir/compose.schemer.yaml" -f "$script_dir/compose.schemer.ai.yaml")
    ;;
  *)
    printf 'Usage: ./start.sh [ui|local-db|docker-db|ai|ai-local-db|ai-docker-db|schemer|schemer-ai]\n' >&2
    exit 2
    ;;
esac

app_service=schemii
app_name=Schemii
url="http://127.0.0.1:${port}/"
health_services=(metadata-postgres schemii schemii-ingress)
health_names=("metadata PostgreSQL" "Schemii backend" "Schemii ingress")
case "$mode" in
  docker-db|ai-docker-db|schemer|schemer-ai)
    health_services+=(postgres)
    health_names+=("tutorial PostgreSQL")
    ;;
esac
case "$mode" in
  ai|ai-local-db|ai-docker-db|schemer-ai)
    health_services+=(opencode)
    health_names+=(OpenCode)
    ;;
esac
if [[ "$mode" == "schemer" || "$mode" == "schemer-ai" ]]; then
  app_service=schemer
  app_name=Schemer
  url="http://127.0.0.1:${schemer_port}/"
  health_services+=(schemer schemer-ingress)
  health_names+=("Schemer backend" "Schemer ingress")
fi
was_ready=0
if [[ "${SCHEMII_NO_OPEN:-0}" != "1" ]] && command -v curl >/dev/null 2>&1; then
  if curl --fail --silent --max-time 1 "$url" >/dev/null 2>&1; then
    was_ready=1
  fi
fi

printf 'Starting %s instance %s in %s mode.\n' "$app_name" "$project" "$mode"
printf 'Starting the selected immutable application artifacts; pinned dependency images may download on first use.\n'
required_images=("$SCHEMII_IMAGE" "$SCHEMII_METADATA_IMAGE")
if [[ "$mode" == "ai" || "$mode" == "ai-local-db" || "$mode" == "ai-docker-db" || "$mode" == "schemer-ai" ]]; then
  required_images+=("$SCHEMII_OPENCODE_IMAGE")
fi
if ! docker image inspect "${required_images[@]}" >/dev/null; then
  printf 'Selected immutable application images are not loaded. Verify and load the promoted release image archives first.\n' >&2
  exit 1
fi
compose_status=0
"${compose[@]}" up --no-build -d --remove-orphans || compose_status=$?
if [[ "$compose_status" != "0" ]]; then
  exit "$compose_status"
fi
for (( health_index=0; health_index<${#health_services[@]}; health_index++ )); do
  health_service="${health_services[$health_index]}"
  health_name="${health_names[$health_index]}"
  container_id="$("${compose[@]}" ps -q "$health_service")"
  if [[ -z "$container_id" ]]; then
    printf '%s did not start. Review the Docker Compose output above.\n' "$health_name" >&2
    exit 1
  fi
  container_name="$(docker inspect --format '{{.Name}}' "$container_id" 2>/dev/null || true)"
  container_name="${container_name#/}"
  health=""
  for _ in {1..60}; do
    health="$(docker inspect --format '{{.State.Health.Status}}' "$container_id" 2>/dev/null || true)"
    [[ "$health" == "healthy" ]] && break
    if [[ "$health" == "unhealthy" ]]; then
      printf '%s failed its container health check. Run docker logs %s for details.\n' "$health_name" "${container_name:-$container_id}" >&2
      exit 1
    fi
    sleep 1
  done
  if [[ "$health" != "healthy" ]]; then
    printf '%s did not become ready within 60 seconds after startup. Run docker logs %s for details.\n' "$health_name" "${container_name:-$container_id}" >&2
    exit 1
  fi
done
if [[ "$app_service" == "schemer" ]]; then
  printf '\nSchemer is ready at %s\n' "$url"
  printf 'Schemii companion: http://127.0.0.1:%s/\n' "$port"
else
  printf '\nSchemii is ready at %s\n' "$url"
fi
printf 'Mode: %s\n' "$mode"
printf 'Instance: %s\n' "$project"
printf 'Saved data remains in Docker named volumes.\n'

if [[ "${SCHEMII_NO_OPEN:-0}" != "1" && "$was_ready" != "1" ]]; then
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url" >/dev/null 2>&1 || true
  elif command -v open >/dev/null 2>&1; then
    open "$url" >/dev/null 2>&1 || true
  fi
fi
