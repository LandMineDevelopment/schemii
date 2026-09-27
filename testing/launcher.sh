#!/usr/bin/env bash
# Sourced exclusively by start.sh after its lifecycle and testing leases.
# Requires ROOT_DIR, compose_args, SCHEMII_QA_STATE_DIRECTORY and
# SCHEMII_STARTUP_TIMEOUT; the overlay must already be in compose_args.
testing_database_init_writable_registry() {
  local file="${SCHEMII_QA_STATE_DIRECTORY}/writable-credentials.tsv"
  [[ -d "$SCHEMII_QA_STATE_DIRECTORY" && ! -L "$SCHEMII_QA_STATE_DIRECTORY" ]] || fail "testing state directory is missing or symbolic"
  [[ "$(stat -c '%a' "$SCHEMII_QA_STATE_DIRECTORY")" == 700 ]] || fail "testing state directory must have mode 700"
  [[ ! -L "$file" ]] || fail "writable testing credential registry must not be symbolic"
  if [[ ! -e "$file" ]]; then
    ( umask 077; : > "$file" ) || fail "could not create writable testing credential registry"
  fi
  [[ -f "$file" && "$(stat -c '%a' "$file")" == 600 ]] || fail "writable testing credential registry must be a mode-600 file"
}

testing_database_prepare_writable_credential() {
  local account="$1" file="${SCHEMII_QA_STATE_DIRECTORY}/writable-credentials.tsv" role password
  case "$account" in qa_designer_004|qa_designer_010|qa_designer_011) ;; *) fail "unknown writable testing account" ;; esac
  testing_database_init_writable_registry
  [[ -f "${SCHEMII_QA_STATE_DIRECTORY}/database-credentials.tsv" ]] || fail "registered testing credentials are missing"
  awk -F '\t' -v wanted="$account" '$1 == wanted && $2 == wanted { found=1 } END { exit !found }' "${SCHEMII_QA_STATE_DIRECTORY}/database-credentials.tsv" || fail "writable account is not in the retained testing registry"
  awk -F '\t' '
    NF != 4 || ($4 != "qa_designer_004" && $4 != "qa_designer_010" && $4 != "qa_designer_011") ||
    $1 != "qa_write_" substr($4,4) || $2 != $1 || length($3)!=64 || $3 !~ /^[0-9a-f]+$/ || seen[$4]++ { bad=1 }
    END { exit bad }
  ' "$file" || fail "writable testing credential registry is invalid"
  if ! awk -F '\t' -v wanted="$account" '$4 == wanted { found=1 } END { exit !found }' "$file"; then
    role="qa_write_${account#qa_}"
    password="$(openssl rand -hex 32)" || fail "could not generate writable testing credential"
    [[ "$password" =~ ^[0-9a-f]{64}$ ]] || fail "generated writable testing credential is invalid"
    printf '%s\t%s\t%s\t%s\n' "$role" "$role" "$password" "$account" >> "$file" || fail "could not preserve writable testing credential"
  fi
}

testing_database_check_files() {
  local name file
  for name in database-admin-password database-credentials.tsv; do
    file="${SCHEMII_QA_STATE_DIRECTORY}/${name}"
    [[ -f "$file" && ! -L "$file" && -s "$file" ]] || fail "missing private testing database file: ${name}; provision the testing registry first"
    [[ "$(stat -c '%a' "$file")" == 600 ]] || fail "testing database file must have mode 600: ${name}"
  done
  [[ "$(wc -l < "${SCHEMII_QA_STATE_DIRECTORY}/database-admin-password")" == 1 ]] || fail "testing database administrator secret must contain one line"
  LC_ALL=C grep -Eq '^[0-9a-f]{64}$' "${SCHEMII_QA_STATE_DIRECTORY}/database-admin-password" || fail "invalid testing database administrator secret"
  testing_database_init_writable_registry
}

testing_database_start() {
  testing_database_check_files
  docker "${compose_args[@]}" build qa-postgres || fail "testing database image could not be built"
  docker "${compose_args[@]}" up --detach --force-recreate --wait --wait-timeout "$SCHEMII_STARTUP_TIMEOUT" qa-postgres || fail "testing database did not become ready"
}

testing_database_manage() {
  local action="${1:?testing database action required}" space="${2:-all}"
  case "$action" in prepare|reset|verify|check-reset|writer-prepare|writer-reset|writer-verify) ;; *) fail "invalid testing database action" ;; esac
  [[ "$action" != check-reset || "$space" != all ]] || fail "reset smoke check requires one explicit testing space"
  if [[ "$action" == writer-* ]]; then
    [[ "$space" == qa_designer_004 || "$space" == qa_designer_010 || "$space" == qa_designer_011 ]] || fail "invalid writable testing account"
  else
    [[ "$space" == all || "$space" =~ ^qa_[a-z][a-z_]*_[0-9]{3}$ ]] || fail "invalid testing database space"
  fi
  (( ${#space} <= 63 )) || fail "testing database space exceeds PostgreSQL identifier length"
  testing_database_check_files
  docker "${compose_args[@]}" exec --user root -T qa-postgres /opt/testing/manage.sh "$action" "$space" || fail "testing database ${action} failed for ${space}"
}
