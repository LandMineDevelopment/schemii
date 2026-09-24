# Sourced only by start.sh after its lifecycle lock and Docker access checks.
recovery_main() {
  command -v python3 >/dev/null 2>&1 || fail "Python 3 is required for recovery bundle validation"
  umask 077
  if [[ "$SCHEMII_LAUNCH_ACTION" == "backup" ]]; then
    recovery_backup
  elif [[ "$SCHEMII_LAUNCH_ACTION" == "restore-backup-new" ]]; then
    recovery_restore_new
  else
    recovery_verify
  fi
}

recovery_backup() {
  local bundle
  # Trap state must survive function unwinding on an error.
  bundle="${SCHEMII_RECOVERY_DIRECTORY:-${ROOT_DIR}/.schemii/backups/$(date -u +%Y%m%dT%H%M%SZ)-$$}"
  [[ ! -e "$bundle" && ! -L "$bundle" ]] || fail "backup destination already exists; choose a new directory"
  mkdir -p -- "$(dirname -- "$bundle")"
  temporary="$(mktemp -d "$(dirname -- "$bundle")/.backup-incomplete.XXXXXX")"
  # Failure retains no misleading completed bundle or copied secret material.
  trap 'rm -rf -- "$temporary"' EXIT
  python3 "$ROOT_DIR/dev/recovery/bundle.py" prepare "$temporary" "$SCHEMII_SECRET_DIRECTORY" \
    "$SCHEMII_TEST_POSTGRES_DB" "$SCHEMII_TEST_POSTGRES_USER" "$SCHEMII_METADATA_APP_USER"
  docker "${compose_args[@]}" exec -T metadata-postgres sh -ec \
    'test "$POSTGRES_DB" = "$1"; test "$POSTGRES_USER" = "$2"; export PGPASSWORD="$(cat "$POSTGRES_PASSWORD_FILE")"; exec pg_dump --format=custom --no-owner --no-privileges --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"' \
    sh "$SCHEMII_TEST_POSTGRES_DB" "$SCHEMII_TEST_POSTGRES_USER" \
    > "$temporary/metadata.dump"
  docker "${compose_args[@]}" exec -T schemii python - \
    < "$ROOT_DIR/dev/recovery/key_probe.py" > "$temporary/key-probe.json"
  python3 "$ROOT_DIR/dev/recovery/bundle.py" source-identity "$temporary"
  python3 "$ROOT_DIR/dev/recovery/bundle.py" seal "$temporary"
  mv -T -- "$temporary" "$bundle"
  trap - EXIT
  printf 'Private metadata backup created: %s\nRun ./start.sh --verify-backup with this directory before relying on it. Target database rows are not included.\n' "$bundle"
}

recovery_verify() {
  local recovery_project
  SCHEMII_RECOVERY_BUNDLE="$(realpath -e -- "$SCHEMII_RECOVERY_DIRECTORY")"
  python3 "$ROOT_DIR/dev/recovery/bundle.py" validate "$SCHEMII_RECOVERY_BUNDLE"
  # Use current source's migration/crypto code, not an arbitrarily stale image.
  docker "${compose_args[@]}" build schemii
  recovery_project="schemii-verify-$(date -u +%Y%m%d%H%M%S)-$$"
  export SCHEMII_RECOVERY_BUNDLE
  SCHEMII_RECOVERY_UID="$(id -u)"
  SCHEMII_RECOVERY_GID="$(id -g)"
  export SCHEMII_RECOVERY_UID SCHEMII_RECOVERY_GID
  recovery_compose=(compose --project-name "$recovery_project"
    --project-directory "$ROOT_DIR" --file "$ROOT_DIR/dev/recovery/compose.yaml")
  # Only this newly generated project is ever removed. No live service/volume is
  # attached to the verifier, and neither local overrides nor ports are used.
  trap 'docker "${recovery_compose[@]}" down --volumes --remove-orphans >/dev/null' EXIT
  docker "${recovery_compose[@]}" up --detach --wait --wait-timeout 60 database
  docker "${recovery_compose[@]}" exec -T database psql --username=verifier --dbname=schemii_verify --set=ON_ERROR_STOP=1 \
    --command='CREATE ROLE restore_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION; ALTER DATABASE schemii_verify OWNER TO restore_app;' >/dev/null
  if ! docker "${recovery_compose[@]}" exec -T database \
    pg_restore --username=verifier --dbname=schemii_verify --role=restore_app --no-owner --no-privileges --exit-on-error --single-transaction \
    < "$SCHEMII_RECOVERY_BUNDLE/metadata.dump" 2>/dev/null; then
    fail "isolated database restore failed; database error detail suppressed to protect saved data"
  fi
  docker "${recovery_compose[@]}" run --rm --no-deps -T verifier
  docker "${recovery_compose[@]}" down --volumes --remove-orphans >/dev/null
  trap - EXIT
  printf 'Backup restore, migration, and credential verification passed. Live data was not modified.\n'
}

recovery_restore_new() {
  local existing_volumes existing_containers
  # Strictly fresh canonical installation only. Never infer that a retained
  # volume is disposable, even if its database happens to look empty.
  existing_volumes="$(docker volume ls --format '{{.Name}}' --filter 'name=^schemii-test_schemii-test-(postgres|demo-postgres)$')"
  existing_containers="$(docker ps --all --quiet --filter label=com.docker.compose.project=schemii-test)"
  [[ -z "$existing_volumes" && -z "$existing_containers" ]] || fail "fresh restore refuses existing Schemii containers or database volumes; use a fresh host/install"
  [[ ! -e "$SCHEMII_SECRET_DIRECTORY" && ! -L "$SCHEMII_SECRET_DIRECTORY" ]] || fail "fresh restore refuses an existing secrets directory"
  [[ ! -f "$ROOT_DIR/.schemii/compose.local.yaml" ]] || fail "fresh restore refuses local Compose overrides; restore on a clean installation first"
  SCHEMII_RECOVERY_BUNDLE="$(realpath -e -- "$SCHEMII_RECOVERY_DIRECTORY")"
  python3 "$ROOT_DIR/dev/recovery/bundle.py" validate "$SCHEMII_RECOVERY_BUNDLE"
  python3 "$ROOT_DIR/dev/recovery/bundle.py" identity "$SCHEMII_RECOVERY_BUNDLE" \
    "$SCHEMII_TEST_POSTGRES_DB" "$SCHEMII_TEST_POSTGRES_USER" "$SCHEMII_METADATA_APP_USER"
  recovery_verify
  # The successful verifier has removed only its disposable project. Restore
  # original identities and keys before the canonical database is initialized.
  mkdir -m 750 -- "$SCHEMII_SECRET_DIRECTORY"
  for recovery_secret in metadata_password metadata_app_password metadata_encryption_key demo_admin_password demo_target_password account_setup_token opencode_password; do
    install -m 640 -- "$SCHEMII_RECOVERY_BUNDLE/secrets/$recovery_secret" "$SCHEMII_SECRET_DIRECTORY/$recovery_secret"
  done
  SCHEMII_SECRET_READER_GID="$(stat -c '%g' "$SCHEMII_SECRET_DIRECTORY/metadata_password")"
  export SCHEMII_SECRET_READER_GID SCHEMII_TEST_POSTGRES_DB SCHEMII_TEST_POSTGRES_USER SCHEMII_METADATA_APP_USER
  export SCHEMII_METADATA_BOOTSTRAP_PASSWORD_SECRET_FILE SCHEMII_METADATA_APP_PASSWORD_SECRET_FILE
  docker "${compose_args[@]}" up --detach --wait --wait-timeout "$SCHEMII_STARTUP_TIMEOUT" metadata-postgres
  # Establish the original application role first; every restored object is
  # owned by it, including Schemoo/Schemer objects outside the bootstrap schemas.
  docker "${compose_args[@]}" run --rm --no-deps -T metadata-bootstrap
  if ! docker "${compose_args[@]}" exec -T metadata-postgres sh -ec \
    'export PGPASSWORD="$(cat "$POSTGRES_PASSWORD_FILE")"; exec pg_restore --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --role="$1" --no-owner --no-privileges --exit-on-error --single-transaction' \
    sh "$SCHEMII_METADATA_APP_USER" < "$SCHEMII_RECOVERY_BUNDLE/metadata.dump" 2>/dev/null; then
    fail "fresh restore failed and its import transaction rolled back; new installation state retained for inspection, original backup unchanged"
  fi
  printf 'Metadata restored into a fresh installation with original role identities and encryption key.\nRun ./start.sh with the same identity settings to build/start the application. External target databases require their own backups.\n'
}
