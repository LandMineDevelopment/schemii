#!/bin/sh
set -eu

instance=${SCHEMII_INSTANCE:?SCHEMII_INSTANCE is required}
version_file=${SCHEMII_RECOVERY_VERSION_FILE:-/opt/schemii-release-version}
release_version=$(readlink -f "$version_file" 2>/dev/null || printf '%s' "$version_file")
release_version=$(sed -n '1p' "$release_version")
config_dir=${SCHEMII_RECOVERY_CONFIG_DIR:-/data/config}
schema_dir=${SCHEMII_RECOVERY_SCHEMA_DIR:-/data/schemas}
dashboard_dir=${SCHEMII_RECOVERY_DASHBOARD_DIR:-/data/dashboards}
transaction_dir=${SCHEMII_RECOVERY_TRANSACTION_DIR:-/transaction}
verification_dir=$transaction_dir/verification
committed_marker=$transaction_dir/committed
output_dir=${SCHEMII_RECOVERY_OUTPUT_DIR:-/output}
backup_dir=${SCHEMII_RECOVERY_BACKUP_DIR:-/backup}
secret_dir=${SCHEMII_RECOVERY_SECRET_DIR:-/run/secrets}
metadata_host=${SCHEMII_RECOVERY_METADATA_HOST:-metadata-postgres}
metadata_database=schemii_metadata
metadata_user=schemii_metadata_migration
metadata_owner=schemii_metadata_owner
security_verification_sql=${SCHEMII_RECOVERY_SECURITY_SQL:-/opt/schemii-recovery/verify_security.sql}
migration_dir=${SCHEMII_RECOVERY_MIGRATION_DIR:-/opt/schemii-recovery/migrations}
minimum_supported_metadata_version=10

credential_files='metadata_bootstrap_password metadata_migration_password metadata_schemii_password metadata_schemer_password opencode_password'
manifest_paths='format
instance
release-version
metadata-version
schemii-config.tar.gz
schemii-schemas.tar.gz
schemer-dashboards.tar.gz
metadata.dump
credentials/instance
credentials/metadata_bootstrap_password
credentials/metadata_migration_password
credentials/metadata_schemii_password
credentials/metadata_schemer_password
credentials/opencode_password'

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

sha256_tool=${SCHEMII_RECOVERY_SHA256_TOOL:-}
if [ -z "$sha256_tool" ]; then
  if command -v sha256sum >/dev/null 2>&1; then
    sha256_tool=sha256sum
  elif command -v shasum >/dev/null 2>&1; then
    sha256_tool=shasum
  else
    fail "A SHA-256 implementation is required (sha256sum or shasum)"
  fi
fi
case "$sha256_tool" in
  sha256sum)
    command -v sha256sum >/dev/null 2>&1 || fail "Configured SHA-256 command is unavailable: sha256sum"
    ;;
  shasum)
    command -v shasum >/dev/null 2>&1 || fail "Configured SHA-256 command is unavailable: shasum"
    ;;
  *) fail "SCHEMII_RECOVERY_SHA256_TOOL must be sha256sum or shasum" ;;
esac

sha256_files() {
  if [ "$sha256_tool" = sha256sum ]; then
    sha256sum "$@"
  else
    shasum -a 256 "$@"
  fi
}

sha256_verify() {
  if [ "$sha256_tool" = sha256sum ]; then
    sha256sum -c "$1"
  else
    shasum -a 256 -c "$1"
  fi
}

load_packaged_migrations() {
  expected=1
  separator=
  migration_expectation_sql='CREATE TEMP TABLE expected_metadata_migrations (version integer PRIMARY KEY, name text NOT NULL, checksum text NOT NULL); INSERT INTO expected_metadata_migrations (version, name, checksum) VALUES '
  for path in "$migration_dir"/[0-9][0-9][0-9][0-9]_*.sql; do
    [ -f "$path" ] && [ ! -L "$path" ] || fail "Packaged metadata migrations are missing or invalid"
    name=${path##*/}
    prefix=${name%%_*}
    [ "$prefix" = "$(printf '%04d' "$expected")" ] \
      || fail "Packaged metadata migrations are not a contiguous prefix"
    stem=${name#????_}
    case "$stem" in
      *.sql) ;;
      *) fail "Packaged metadata migration name is invalid: $name" ;;
    esac
    stem=${stem%.sql}
    case "$stem" in
      ''|*[!a-z0-9_]*) fail "Packaged metadata migration name is invalid: $name" ;;
    esac
    digest=$(sha256_files "$path")
    digest=${digest%% *}
    case "$digest" in
      *[!0-9a-f]*|'') fail "Packaged metadata migration checksum is invalid: $name" ;;
    esac
    [ "${#digest}" -eq 64 ] || fail "Packaged metadata migration checksum is invalid: $name"
    migration_expectation_sql="$migration_expectation_sql$separator($expected, '$name', '$digest')"
    separator=', '
    expected=$((expected + 1))
  done
  current_metadata_version=$((expected - 1))
  [ "$current_metadata_version" -ge "$minimum_supported_metadata_version" ] \
    || fail "Packaged metadata migration history does not reach the minimum supported version"
  migration_expectation_sql="$migration_expectation_sql;"
}

load_packaged_migrations

read_one_line() {
  path=$1
  name=$2
  [ -f "$path" ] || fail "$name is missing"
  value=
  extra=
  {
    IFS= read -r value || [ -n "$value" ]
    if IFS= read -r extra || [ -n "$extra" ]; then
      fail "$name must contain exactly one nonempty line"
    fi
  } < "$path"
  [ -n "$value" ] || fail "$name must contain exactly one nonempty line"
  printf '%s' "$value"
}

read_credential() {
  value=$(read_one_line "$1" "$2")
  case "$value" in
    *[!A-Za-z0-9_-]*) fail "$2 has an invalid credential format" ;;
  esac
  [ "${#value}" -ge 16 ] && [ "${#value}" -le 256 ] || fail "$2 has an invalid credential length"
  printf '%s' "$value"
}

metadata_password() {
  read_credential "$secret_dir/metadata_migration_password" metadata_migration_password
}

metadata_psql() {
  password=$(metadata_password)
  PGPASSWORD=$password psql --quiet --tuples-only --no-align --set ON_ERROR_STOP=1 \
    --host "$metadata_host" --username "$metadata_user" --dbname "$metadata_database" "$@"
}

metadata_runtime_psql() {
  application=$1
  case "$application" in schemii|schemer) ;; *) fail "Invalid metadata runtime application" ;; esac
  password=$(read_credential "$secret_dir/metadata_${application}_password" "metadata_${application}_password")
  PGPASSWORD=$password psql --quiet --tuples-only --no-align --set ON_ERROR_STOP=1 \
    --host "$metadata_host" --username "schemii_metadata_$application" --dbname "$metadata_database" "$@"
}

metadata_dump() {
  destination=$1
  password=$(metadata_password)
  PGPASSWORD=$password pg_dump --host "$metadata_host" --username "$metadata_user" \
    --dbname "$metadata_database" --role "$metadata_owner" --enable-row-security \
    --format=custom --schema=public --file "$destination" || return 1
  pg_restore --list "$destination" >/dev/null || return 1
}

metadata_restore() {
  archive=$1
  password=$(metadata_password)
  PGPASSWORD=$password pg_restore --host "$metadata_host" --username "$metadata_user" \
    --dbname "$metadata_database" --role "$metadata_owner" --clean --if-exists \
    --exit-on-error --single-transaction "$archive"
}

metadata_restore_clean() {
  archive=$1
  expected_version=$2
  validate_supported_metadata_version "$expected_version"
  metadata_psql --command 'DROP SCHEMA IF EXISTS public CASCADE;' >/dev/null
  if [ "$expected_version" -lt 12 ]; then
    metadata_psql --command \
      'ALTER DEFAULT PRIVILEGES FOR ROLE schemii_metadata_owner GRANT EXECUTE ON FUNCTIONS TO PUBLIC;' >/dev/null
  else
    metadata_psql --command \
      'ALTER DEFAULT PRIVILEGES FOR ROLE schemii_metadata_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;' >/dev/null
  fi
  metadata_restore "$archive"
}

validate_supported_metadata_version() {
  version=$1
  case "$version" in ''|*[!0-9]*) fail "Metadata schema version is invalid: $version" ;; esac
  case "$version" in 0|[1-9]|[1-9][0-9]*) ;; *) fail "Metadata schema version is invalid: $version" ;; esac
  [ "$version" -ge "$minimum_supported_metadata_version" ] \
    || fail "Metadata schema version $version is older than the minimum supported version $minimum_supported_metadata_version"
  [ "$version" -le "$current_metadata_version" ] \
    || fail "Metadata schema version $version is newer than the packaged version $current_metadata_version"
}

metadata_version() {
  metadata_psql --command 'SELECT COALESCE(max(version), 0) FROM metadata_schema_migrations;'
}

verify_metadata_security() {
  expected_version=$1
  validate_supported_metadata_version "$expected_version"
  [ -f "$security_verification_sql" ] || fail "Metadata security verification SQL is missing"
  result=$(metadata_psql --set "expected_metadata_version=$expected_version" \
    --command "$migration_expectation_sql" --file "$security_verification_sql")
  [ "$result" = verified ] || fail "Metadata ownership or ACL verification failed: $result"
  for application in schemii schemer; do
    visible=$(metadata_runtime_psql "$application" \
      --command "SELECT COALESCE(string_agg(application_id, ',' ORDER BY application_id), '') FROM metadata_applications;")
    [ "$visible" = "$application" ] \
      || fail "Metadata row-level isolation verification failed for $application"
  done
}

archive_directory() {
  source=$1
  destination=$2
  tar --numeric-owner -czf "$destination" -C "$source" . || return 1
  tar -tzf "$destination" >/dev/null || return 1
}

validate_manifest() {
  root=$1
  manifest=$root/checksums.sha256
  [ -f "$manifest" ] && [ ! -L "$manifest" ] || fail "Backup checksum manifest is missing or invalid"
  seen=
  count=0
  while IFS= read -r line || [ -n "$line" ]; do
    hash=${line%% *}
    path=${line#"$hash  "}
    [ "$path" != "$line" ] && [ -n "$path" ] || fail "Backup checksum manifest has an invalid entry"
    [ "${#hash}" -eq 64 ] || fail "Backup checksum manifest has an invalid hash"
    case "$hash" in *[!0-9a-f]*) fail "Backup checksum manifest has an invalid hash" ;; esac
    case "$path" in
      format|instance|release-version|metadata-version|schemii-config.tar.gz|schemii-schemas.tar.gz|schemer-dashboards.tar.gz|metadata.dump|credentials/instance|credentials/metadata_bootstrap_password|credentials/metadata_migration_password|credentials/metadata_schemii_password|credentials/metadata_schemer_password|credentials/opencode_password) ;;
      *) fail "Backup checksum manifest contains an unexpected or unsafe path: $path" ;;
    esac
    case "
$seen
" in *"
$path
"*) fail "Backup checksum manifest contains a duplicate path: $path" ;; esac
    seen="$seen
$path"
    count=$((count + 1))
  done < "$manifest"
  [ "$count" -eq 14 ] || fail "Backup checksum manifest does not contain the exact required path set"
  for path in $manifest_paths; do
    case "
$seen
" in *"
$path
"*) ;;
      *) fail "Backup checksum manifest is missing required path: $path" ;;
    esac
    [ -f "$root/$path" ] && [ ! -L "$root/$path" ] || fail "Backup manifest path is missing or not a regular file: $path"
  done
  (cd "$root" && sha256_verify checksums.sha256 >/dev/null) || fail "Backup checksum verification failed"
}

validate_archive_paths() {
  archive=$1
  tar -tvzf "$archive" | while IFS= read -r entry; do
    case "$entry" in
      d*|-*) ;;
      *) fail "Archive contains a link or unsupported entry type" ;;
    esac
  done
  tar -tzf "$archive" | while IFS= read -r entry; do
    case "/$entry/" in
      /*/../*|/*/./../*|//*|/./../*) fail "Archive contains an unsafe path: $entry" ;;
    esac
    case "$entry" in
      /*) fail "Archive contains an absolute path: $entry" ;;
    esac
  done
}

clear_directory() {
  target=$1
  for entry in "$target"/.[!.]* "$target"/..?* "$target"/*; do
    [ -e "$entry" ] || [ -L "$entry" ] || continue
    rm -rf -- "$entry"
  done
}

restore_directory() {
  archive=$1
  target=$2
  validate_archive_paths "$archive"
  clear_directory "$target"
  tar --numeric-owner -xzf "$archive" -C "$target"
}

transaction_exists() {
  [ -f "$transaction_dir/instance" ]
}

verify_transaction_marker() {
  marker=$(read_one_line "$transaction_dir/instance" 'Recovery transaction instance marker')
  [ "$marker" = "$instance" ] || fail "Recovery transaction belongs to another instance"
}

verify_committed_marker() {
  [ -f "$committed_marker" ] && [ ! -L "$committed_marker" ] \
    || fail "Recovery committed marker is missing or invalid"
  marker=$(read_one_line "$committed_marker" 'Recovery committed marker')
  [ "$marker" = "$instance" ] || fail "Committed recovery transaction belongs to another instance"
}

rollback_evidence_exists() {
  for name in config.tar.gz schemas.tar.gz dashboards.tar.gz metadata.dump metadata-version rollback-checksums.sha256 phase instance .instance.pending; do
    [ ! -e "$transaction_dir/$name" ] && [ ! -L "$transaction_dir/$name" ] || return 0
  done
  return 1
}

determine_transaction_state() {
  if [ -e "$committed_marker" ] || [ -L "$committed_marker" ]; then
    verify_committed_marker
    transaction_state=committed-cleanup-required
  elif transaction_exists; then
    verify_transaction_marker
    transaction_state=rollback-required
  elif rollback_evidence_exists; then
    fail "Recovery transaction evidence is incomplete and has no durable state marker; refusing automatic rollback"
  else
    transaction_state=none
  fi
}

publish_committed_marker() {
  pending=$transaction_dir/.committed.pending
  [ ! -e "$committed_marker" ] && [ ! -L "$committed_marker" ] \
    || fail "Recovery committed marker already exists"
  rm -f -- "$pending" || fail "Stale recovery commit marker staging could not be removed"
  printf '%s\n' "$instance" > "$pending" || fail "Recovery committed marker could not be staged"
  chmod 600 "$pending" || fail "Recovery committed marker permissions could not be restricted"
  sync || fail "Recovery committed marker staging could not be synchronized"
  mv "$pending" "$committed_marker" || fail "Recovery committed marker could not be published"
  sync || fail "Recovery committed marker publication could not be synchronized"
}

cleanup_committed_transaction() {
  verify_committed_marker
  for name in config.tar.gz schemas.tar.gz dashboards.tar.gz metadata.dump metadata-version rollback-checksums.sha256 phase instance .instance.pending; do
    rm -f -- "$transaction_dir/$name" \
      || fail "Committed recovery cleanup failed for $name; forward cleanup remains required"
    sync || fail "Committed recovery cleanup could not be synchronized for $name; forward cleanup remains required"
  done
}

commit_transaction() {
  determine_transaction_state
  case "$transaction_state" in
    committed-cleanup-required) ;;
    rollback-required)
      phase=$(read_one_line "$transaction_dir/phase" 'Recovery transaction phase')
      [ "$phase" = data-restored ] || fail "Recovery transaction is not ready to commit"
      publish_committed_marker
      ;;
    none) fail "No recovery transaction is pending" ;;
  esac
  cleanup_committed_transaction
}

finalize_committed_transaction() {
  determine_transaction_state
  [ "$transaction_state" = committed-cleanup-required ] \
    || fail "No committed recovery transaction is pending finalization"
  rollback_evidence_exists \
    && fail "Committed recovery rollback evidence has not been fully cleaned"
  rm -f -- "$committed_marker" || fail "Committed recovery marker could not be finalized"
  sync || fail "Committed recovery marker finalization could not be synchronized"
}

rollback_transaction() {
  determine_transaction_state
  case "$transaction_state" in
    none) return 0 ;;
    committed-cleanup-required)
      fail "Recovery commit has begun; rollback is forbidden and forward cleanup must complete"
      ;;
    rollback-required) ;;
  esac
  [ -f "$transaction_dir/config.tar.gz" ] || fail "Recovery transaction config snapshot is missing"
  [ -f "$transaction_dir/schemas.tar.gz" ] || fail "Recovery transaction schema snapshot is missing"
  [ -f "$transaction_dir/dashboards.tar.gz" ] || fail "Recovery transaction dashboard snapshot is missing"
  [ -f "$transaction_dir/metadata.dump" ] || fail "Recovery transaction metadata snapshot is missing"
  rollback_metadata_version=$(read_one_line "$transaction_dir/metadata-version" 'Recovery transaction metadata version')
  validate_supported_metadata_version "$rollback_metadata_version"
  (cd "$transaction_dir" && sha256_verify rollback-checksums.sha256 >/dev/null) \
    || fail "Recovery transaction snapshot checksum verification failed"
  validate_archive_paths "$transaction_dir/config.tar.gz"
  validate_archive_paths "$transaction_dir/schemas.tar.gz"
  validate_archive_paths "$transaction_dir/dashboards.tar.gz"
  pg_restore --list "$transaction_dir/metadata.dump" >/dev/null \
    || fail "Recovery transaction metadata snapshot verification failed"
  restore_directory "$transaction_dir/config.tar.gz" "$config_dir"
  restore_directory "$transaction_dir/schemas.tar.gz" "$schema_dir"
  restore_directory "$transaction_dir/dashboards.tar.gz" "$dashboard_dir"
  metadata_restore_clean "$transaction_dir/metadata.dump" "$rollback_metadata_version"
  verify_metadata_security "$rollback_metadata_version"
  clear_directory "$transaction_dir"
  printf 'Rolled back the incomplete recovery transaction for %s.\n' "$instance" >&2
}

verify_backup() {
  [ -d "$backup_dir/credentials" ] && [ ! -L "$backup_dir/credentials" ] \
    || fail "Backup credential directory is missing or invalid"
  [ -f "$backup_dir/complete" ] && [ ! -L "$backup_dir/complete" ] \
    || fail "Backup completion marker is missing or invalid"
  complete=$(read_one_line "$backup_dir/complete" 'Backup completion marker')
  [ "$complete" = complete ] || fail "Backup is incomplete"
  validate_manifest "$backup_dir"
  format=$(read_one_line "$backup_dir/format" 'Backup format')
  [ "$format" = 1 ] || fail "Backup format is unsupported"
  marker=$(read_one_line "$backup_dir/instance" 'Backup instance marker')
  [ "$marker" = "$instance" ] || fail "Backup instance marker does not exactly match $instance"
  backup_metadata_version=$(read_one_line "$backup_dir/metadata-version" 'Backup metadata version')
  validate_supported_metadata_version "$backup_metadata_version"
  validate_archive_paths "$backup_dir/schemii-config.tar.gz"
  validate_archive_paths "$backup_dir/schemii-schemas.tar.gz"
  validate_archive_paths "$backup_dir/schemer-dashboards.tar.gz"
  pg_restore --list "$backup_dir/metadata.dump" >/dev/null || fail "Metadata archive verification failed"
  credential_marker=$(read_one_line "$backup_dir/credentials/instance" 'Credential backup instance marker')
  [ "$credential_marker" = "$instance" ] || fail "Credential backup instance marker does not exactly match $instance"
  for name in $credential_files; do
    read_credential "$backup_dir/credentials/$name" "Backup $name" >/dev/null
  done
}

stage_verification() {
  verify_backup
  clear_directory "$verification_dir"
  mkdir -p "$verification_dir"
  chmod 700 "$verification_dir"
  for name in instance release-version metadata-version schemii-config.tar.gz schemii-schemas.tar.gz schemer-dashboards.tar.gz; do
    cp "$backup_dir/$name" "$verification_dir/$name"
    chmod 600 "$verification_dir/$name"
  done
  chown -R 10001:10001 "$verification_dir"
}

clear_verification() {
  [ -d "$verification_dir" ] || return 0
  clear_directory "$verification_dir"
  rmdir "$verification_dir"
}

create_backup() {
  determine_transaction_state
  [ "$transaction_state" = none ] || fail "A recovery transaction must be completed before backup"
  clear_directory "$output_dir"
  mkdir -p "$output_dir/credentials"
  chmod 700 "$output_dir" "$output_dir/credentials"
  archive_directory "$config_dir" "$output_dir/schemii-config.tar.gz"
  archive_directory "$schema_dir" "$output_dir/schemii-schemas.tar.gz"
  archive_directory "$dashboard_dir" "$output_dir/schemer-dashboards.tar.gz"
  metadata_version=$(metadata_version)
  verify_metadata_security "$metadata_version"
  metadata_dump "$output_dir/metadata.dump"
  printf '%s\n' 1 > "$output_dir/format"
  printf '%s\n' "$instance" > "$output_dir/instance"
  printf '%s\n' "$release_version" > "$output_dir/release-version"
  printf '%s\n' "$metadata_version" > "$output_dir/metadata-version"
  printf '%s\n' "$instance" > "$output_dir/credentials/instance"
  for name in $credential_files; do
    value=$(read_credential "$secret_dir/$name" "$name")
    printf '%s\n' "$value" > "$output_dir/credentials/$name"
  done
  for path in "$output_dir"/*; do
    [ -f "$path" ] && chmod 600 "$path"
  done
  chmod 600 "$output_dir/credentials"/*
  chmod 700 "$output_dir/credentials"
  (cd "$output_dir" && sha256_files $manifest_paths > checksums.sha256)
  chmod 600 "$output_dir/checksums.sha256"
  validate_manifest "$output_dir"
  sync
  printf '%s\n' complete > "$output_dir/complete"
  chmod 600 "$output_dir/complete"
  sync
}

cleanup_unpublished_transaction() {
  determine_transaction_state
  [ "$transaction_state" = none ] || fail "Published recovery transaction must be resolved before cleanup"
  clear_directory "$transaction_dir"
}

prepare_rollback_snapshot() {
  stage=$transaction_dir/.rollback-stage.$$
  cleanup_unpublished_transaction
  mkdir "$stage" || return 1
  chmod 700 "$stage" || { rm -rf -- "$stage"; return 1; }
  rollback_metadata_version=$(metadata_version) || { rm -rf -- "$stage"; return 1; }
  verify_metadata_security "$rollback_metadata_version" || { rm -rf -- "$stage"; return 1; }
  if ! archive_directory "$config_dir" "$stage/config.tar.gz" \
      || ! archive_directory "$schema_dir" "$stage/schemas.tar.gz" \
      || ! archive_directory "$dashboard_dir" "$stage/dashboards.tar.gz" \
      || ! metadata_dump "$stage/metadata.dump"; then
    rm -rf -- "$stage"
    return 1
  fi
  if ! validate_archive_paths "$stage/config.tar.gz" \
      || ! validate_archive_paths "$stage/schemas.tar.gz" \
      || ! validate_archive_paths "$stage/dashboards.tar.gz" \
      || ! pg_restore --list "$stage/metadata.dump" >/dev/null; then
    rm -rf -- "$stage"
    return 1
  fi
  printf '%s\n' "$rollback_metadata_version" > "$stage/metadata-version" || { rm -rf -- "$stage"; return 1; }
  (cd "$stage" && sha256_files config.tar.gz schemas.tar.gz dashboards.tar.gz metadata.dump metadata-version > rollback-checksums.sha256) \
    || { rm -rf -- "$stage"; return 1; }
  (cd "$stage" && sha256_verify rollback-checksums.sha256 >/dev/null) \
    || { rm -rf -- "$stage"; return 1; }
  printf '%s\n' ready > "$stage/phase" || { rm -rf -- "$stage"; return 1; }
  printf '%s\n' "$instance" > "$stage/instance" || { rm -rf -- "$stage"; return 1; }
  chmod 600 "$stage"/* || { rm -rf -- "$stage"; return 1; }
  sync || { rm -rf -- "$stage"; return 1; }
  for path in config.tar.gz schemas.tar.gz dashboards.tar.gz metadata.dump metadata-version rollback-checksums.sha256 phase; do
    mv "$stage/$path" "$transaction_dir/$path" || { rm -rf -- "$stage"; cleanup_unpublished_transaction; return 1; }
  done
  sync || { rm -rf -- "$stage"; cleanup_unpublished_transaction; return 1; }
  mv "$stage/instance" "$transaction_dir/.instance.pending" \
    || { rm -rf -- "$stage"; cleanup_unpublished_transaction; return 1; }
  rmdir "$stage" || return 1
  sync || { cleanup_unpublished_transaction; return 1; }
  restoring=1
  trap 'status=$?; trap - EXIT HUP INT TERM; if [ "${restoring:-0}" = 1 ]; then rollback_transaction || true; fi; exit "$status"' EXIT HUP INT TERM
  mv "$transaction_dir/.instance.pending" "$transaction_dir/instance" \
    || { trap - EXIT HUP INT TERM; restoring=0; cleanup_unpublished_transaction; return 1; }
  sync
}

restore_backup() {
  confirmation=${SCHEMII_RECOVERY_CONFIRM:-}
  [ "$confirmation" = "RESTORE:$instance" ] || fail "Destructive restore confirmation must exactly equal RESTORE:$instance"
  determine_transaction_state
  case "$transaction_state" in
    rollback-required) rollback_transaction ;;
    committed-cleanup-required) fail "Committed recovery cleanup must complete before another restore" ;;
  esac
  verify_backup
  prepare_rollback_snapshot || fail "Could not durably stage and verify the destination rollback snapshot"

  printf '%s\n' mutating > "$transaction_dir/phase"
  chmod 600 "$transaction_dir/phase"
  sync
  restore_directory "$backup_dir/schemii-config.tar.gz" "$config_dir"
  restore_directory "$backup_dir/schemii-schemas.tar.gz" "$schema_dir"
  restore_directory "$backup_dir/schemer-dashboards.tar.gz" "$dashboard_dir"
  metadata_restore_clean "$backup_dir/metadata.dump" "$backup_metadata_version"
  verify_metadata_security "$backup_metadata_version"
  printf '%s\n' data-restored > "$transaction_dir/phase"
  chmod 600 "$transaction_dir/phase"
  sync
  restoring=0
  trap - EXIT HUP INT TERM
}

case "${1:-}" in
  prepare)
    # A no-op run lets Compose create the optional dashboard and transaction
    # volumes before the host launcher verifies their exact ownership labels.
    ;;
  stage-verification)
    stage_verification
    ;;
  clear-verification)
    clear_verification
    ;;
  backup)
    create_backup
    ;;
  restore)
    restore_backup
    ;;
  rollback)
    rollback_transaction
    ;;
  state)
    determine_transaction_state
    printf '%s\n' "$transaction_state"
    ;;
  commit)
    commit_transaction
    ;;
  finalize-commit)
    finalize_committed_transaction
    ;;
  verify)
    verify_backup
    ;;
  verify-metadata)
    verify_metadata_security "$current_metadata_version"
    ;;
  *)
    fail 'Usage: recovery.sh prepare|stage-verification|clear-verification|backup|restore|rollback|state|commit|finalize-commit|verify|verify-metadata'
    ;;
esac
