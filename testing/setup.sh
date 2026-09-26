#!/usr/bin/env bash
# Lifecycle serialization for the complete credential/database/account setup.
# All container operations remain exclusively in ./start.sh.
set -Eeuo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
(( $# == 3 )) || { printf 'Internal usage: setup.sh COPIES ADMIN_FILE STATE_DIRECTORY\n' >&2; exit 2; }
QA_COPIES="$1"
QA_ADMIN_FILE="$2"
export SCHEMII_QA_STATE_DIRECTORY="$3"
QA_COMMON_DIR="$(git -C "$ROOT_DIR" rev-parse --path-format=absolute --git-common-dir)"
exec 4>"${QA_COMMON_DIR}/qa-startup.lock"
flock --nonblock 4 || { printf 'Testing setup blocked: another startup owns the gate.\n' >&2; exit 4; }
exec 3>"${QA_COMMON_DIR}/qa-deployment.lock"
flock --nonblock 3 || { printf 'Testing setup blocked: stop active testing runs before changing fixtures or identities.\n' >&2; exit 4; }
export SCHEMII_QA_GATE_FD=4 SCHEMII_QA_LEASE_FD=3 SCHEMII_QA_LEASE_MODE=exclusive
cd "$ROOT_DIR"
python testing/provision.py init --copies "$QA_COPIES" --state-dir "$SCHEMII_QA_STATE_DIRECTORY"
./start.sh --prepare-testing
python testing/provision.py app --admin-credentials "$QA_ADMIN_FILE" --state-dir "$SCHEMII_QA_STATE_DIRECTORY"
