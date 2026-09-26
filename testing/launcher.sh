#!/usr/bin/env bash
# Sourced exclusively by start.sh after its lifecycle and testing leases.
# Requires ROOT_DIR, compose_args, SCHEMII_QA_STATE_DIRECTORY and
# SCHEMII_STARTUP_TIMEOUT; the overlay must already be in compose_args.
testing_database_check_files() {
  local name file
  for name in database-admin-password database-credentials.tsv; do
    file="${SCHEMII_QA_STATE_DIRECTORY}/${name}"
    [[ -f "$file" && ! -L "$file" && -s "$file" ]] || fail "missing private testing database file: ${name}; provision the testing registry first"
    [[ "$(stat -c '%a' "$file")" == 600 ]] || fail "testing database file must have mode 600: ${name}"
  done
  [[ "$(wc -l < "${SCHEMII_QA_STATE_DIRECTORY}/database-admin-password")" == 1 ]] || fail "testing database administrator secret must contain one line"
  LC_ALL=C grep -Eq '^[0-9a-f]{64}$' "${SCHEMII_QA_STATE_DIRECTORY}/database-admin-password" || fail "invalid testing database administrator secret"
}

testing_database_start() {
  testing_database_check_files
  docker "${compose_args[@]}" build qa-postgres || fail "testing database image could not be built"
  docker "${compose_args[@]}" up --detach --force-recreate --wait --wait-timeout "$SCHEMII_STARTUP_TIMEOUT" qa-postgres || fail "testing database did not become ready"
}

testing_database_manage() {
  local action="${1:?testing database action required}" space="${2:-all}"
  case "$action" in prepare|reset|verify|check-reset) ;; *) fail "invalid testing database action" ;; esac
  [[ "$action" != check-reset || "$space" != all ]] || fail "reset smoke check requires one explicit testing space"
  [[ "$space" == all || "$space" =~ ^qa_[a-z][a-z_]*_[0-9]{3}$ ]] || fail "invalid testing database space"
  (( ${#space} <= 63 )) || fail "testing database space exceeds PostgreSQL identifier length"
  testing_database_check_files
  docker "${compose_args[@]}" exec --user root -T qa-postgres /opt/testing/manage.sh "$action" "$space" || fail "testing database ${action} failed for ${space}"
}
