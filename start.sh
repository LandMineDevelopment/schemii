#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${ROOT_DIR}/compose.test.yaml"
ORIGINAL_ARGUMENTS=("$@")

fail() {
  printf 'Schemii startup error: %s\n' "$1" >&2
  exit 1
}

ensure_docker_access() {
  local current_groups account_name account_groups restart_command restart_argument escaped_restart_argument
  command -v docker >/dev/null 2>&1 || fail "Docker is not installed or is not on PATH"
  if ! docker info >/dev/null 2>&1; then
    current_groups=" $(id -nG 2>/dev/null) " || fail "could not determine current group membership"
    account_name="$(id -un 2>/dev/null)" || fail "could not determine the current account"
    account_groups=" $(id -nG "$account_name" 2>/dev/null) " || fail "could not determine account group membership"
    if [[ "$current_groups" != *" docker "* && "$account_groups" == *" docker "* ]]; then
      command -v newgrp >/dev/null 2>&1 || fail "the account has Docker access, but this session is stale and newgrp is unavailable; start a new login session"
      printf "Refreshing this process with the account's Docker group membership...\n" >&2
      printf -v restart_command 'exec %q' "${ROOT_DIR}/start.sh"
      for restart_argument in "${ORIGINAL_ARGUMENTS[@]}"; do
        printf -v escaped_restart_argument ' %q' "$restart_argument"
        restart_command+="$escaped_restart_argument"
      done
      exec newgrp docker -c "$restart_command"
    fi
    fail "Docker is unavailable; confirm that the daemon is running and the current account belongs to the docker group"
  fi
}

# Exact-container diagnostics do not enter stack configuration or lifecycle
# preflight. Reported ownership labels are opaque metadata, never paths to open.
if [[ "${1-}" == --inspect-runtime || "${1-}" == --stop-retired-runtime ]]; then
  runtime_action="$1"
  shift
  (( $# >= 1 && $# <= 8 )) || { printf 'Schemii runtime inspection requires 1 through 8 distinct full lowercase container IDs\n' >&2; exit 2; }
  declare -A runtime_inspection_seen=()
  for runtime_inspection_id in "$@"; do
    [[ "$runtime_inspection_id" =~ ^[0-9a-f]{64}$ ]] || { printf 'Schemii runtime inspection requires 1 through 8 distinct full lowercase container IDs\n' >&2; exit 2; }
    [[ -z "${runtime_inspection_seen[$runtime_inspection_id]+present}" ]] || { printf 'Schemii runtime inspection requires 1 through 8 distinct full lowercase container IDs\n' >&2; exit 2; }
    runtime_inspection_seen[$runtime_inspection_id]=1
  done
  command -v python3 >/dev/null 2>&1 || fail "Python 3 is required for runtime inspection"
  ensure_docker_access
  exec python3 - "$runtime_action" "$ROOT_DIR" "$@" <<'PY'
from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path
import select
import stat
import subprocess
import sys
import time

TEMPLATE = '{"id":{{json .Id}},"name":{{json .Name}},"status":{{json .State.Status}},"exitCode":{{json .State.ExitCode}},"restartCount":{{json .RestartCount}},"restartPolicy":{{json .HostConfig.RestartPolicy.Name}},"restartRetryLimit":{{json .HostConfig.RestartPolicy.MaximumRetryCount}},"composeProject":{{json (index .Config.Labels "com.docker.compose.project")}},"composeService":{{json (index .Config.Labels "com.docker.compose.service")}},"composeWorkingDirectory":{{json (index .Config.Labels "com.docker.compose.project.working_dir")}},"composeConfigFiles":{{json (index .Config.Labels "com.docker.compose.project.config_files")}}}'
FIELDS = ("id", "name", "status", "exitCode", "restartCount", "restartPolicy",
          "restartRetryLimit", "composeProject", "composeService",
          "composeWorkingDirectory", "composeConfigFiles")
LABELS = FIELDS[7:]
MAX_RECORD_BYTES = 16_384
MODE, ROOT, *IDENTIFIERS = sys.argv[1:]


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def inspect(identifier):
    try:
        raw = bounded_command(["docker", "container", "inspect", "--format", TEMPLATE, identifier])
        value = json.loads(raw, object_pairs_hook=unique_object)
        if type(value) is not dict or set(value) != set(FIELDS) or value["id"] != identifier:
            raise ValueError()
        if type(value["name"]) is not str or not value["name"].startswith("/"):
            raise ValueError()
        if value["status"] not in {"created", "running", "paused", "restarting", "removing", "exited", "dead"}:
            raise ValueError()
        if value["restartPolicy"] not in {"", "no", "always", "unless-stopped", "on-failure"}:
            raise ValueError()
        for key in ("exitCode", "restartCount", "restartRetryLimit"):
            if type(value[key]) is not int or not 0 <= value[key] <= 2**63 - 1:
                raise ValueError()
        for key in LABELS:
            if value[key] is not None and type(value[key]) is not str:
                raise ValueError()
            if not value[key]:
                value[key] = None
        encoded = json.dumps({key: value[key] for key in FIELDS}, separators=(",", ":"), ensure_ascii=True)
        if len(encoded) > MAX_RECORD_BYTES * 6:
            raise ValueError()
        return encoded
    except Exception:
        # CLI stderr and parser exceptions can contain secrets or target data.
        sys.stderr.write(f"Schemii runtime inspection failed for container {identifier}\n")
        raise SystemExit(1) from None


def bounded_command(arguments, *, timeout=10, limit=MAX_RECORD_BYTES):
    """Capture only bounded output, including failed or wedged CLI attempts."""
    process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        raw = bytearray()
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                raise ValueError()
            chunk = os.read(process.stdout.fileno(), limit + 1 - len(raw))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > limit:
                raise ValueError()
        if process.wait(timeout=max(0.01, deadline - time.monotonic())) != 0:
            raise ValueError()
        return raw.decode("utf-8")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


def lease(stack, common, filename, variable, descriptor):
    path = common / filename
    inherited = os.environ.get(variable)
    if inherited is not None:
        if inherited != str(descriptor):
            raise ValueError()
        info = path.lstat()
        current = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError()
        stream = stack.enter_context(os.fdopen(os.dup(descriptor), "a"))
    else:
        stream = stack.enter_context(os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600), "a"))
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError()
    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def retired_identity(record, primary):
    service = record["composeService"]
    configs = ",".join(str(primary / name) for name in (
        "compose.yaml", "compose.postgres.yaml", "compose.ai.yaml",
        "compose.schemer.yaml", "compose.schemer.ai.yaml", "compose.tailnet.yaml",
    ))
    return (
        record["composeProject"] == "schemii"
        and service in {"schemii", "schemer"}
        and record["name"] == f"/schemii-{service}-1"
        and record["composeWorkingDirectory"] == str(primary)
        and record["composeConfigFiles"] == configs
        and record["restartPolicy"] == "on-failure"
        and record["restartRetryLimit"] == 0
    )


def eligible(record, primary):
    return (retired_identity(record, primary) and record["status"] == "restarting"
            and record["exitCode"] == 1 and record["restartCount"] > 0)


def stop_retired():
    # Only Git-derived paths are opened. Container labels remain opaque values.
    common = Path(bounded_command(["git", "-C", ROOT, "rev-parse", "--path-format=absolute", "--git-common-dir"]).strip())
    if not common.is_absolute() or not common.is_dir():
        raise ValueError()
    listed = bounded_command(["git", f"--git-dir={common}", "worktree", "list", "--porcelain", "-z"])
    first = listed.split("\0", 1)[0]
    if not first.startswith("worktree "):
        raise ValueError()
    primary = Path(first[len("worktree "):])
    if not primary.is_absolute() or not primary.is_dir() or primary.resolve() != primary:
        raise ValueError()
    with ExitStack() as stack:
        if os.environ.get("SCHEMII_QA_LEASE_MODE", "exclusive") != "exclusive":
            raise ValueError()
        lease(stack, common, "qa-startup.lock", "SCHEMII_QA_GATE_FD", 4)
        lease(stack, common, "qa-deployment.lock", "SCHEMII_QA_LEASE_FD", 3)
        records = [json.loads(inspect(identifier)) for identifier in IDENTIFIERS]
        if not all(eligible(record, primary) for record in records):
            raise ValueError()
        stopped = []
        for index, identifier in enumerate(IDENTIFIERS):
            stage = "revalidation"
            try:
                current = json.loads(inspect(identifier))
                if not eligible(current, primary):
                    raise ValueError()
                stage = "stop-unconfirmed"
                output = bounded_command(["docker", "container", "stop", "--time", "10", identifier], timeout=15, limit=256)
                if output.strip() != identifier:
                    raise ValueError()
                stage = "post-stop-unconfirmed"
                after = json.loads(inspect(identifier))
                if not retired_identity(after, primary) or after["status"] != "exited":
                    raise ValueError()
                stopped.append(identifier)
            except (Exception, SystemExit):
                sys.stdout.write(json.dumps({"outcome": "incomplete", "stopped": stopped,
                    "unconfirmed": identifier, "stage": stage,
                    "unattempted": IDENTIFIERS[index + 1:]}, separators=(",", ":")) + "\n")
                sys.stderr.write("Schemii retired runtime stop incomplete; inspect the reported exact IDs before another action\n")
                return 1
        sys.stdout.write(json.dumps({"outcome": "stopped", "stopped": stopped}, separators=(",", ":")) + "\n")
        return 0


if MODE == "--inspect-runtime":
    records = [inspect(identifier) for identifier in IDENTIFIERS]
    sys.stdout.write("\n".join(records) + "\n")
else:
    try:
        outcome = stop_retired()
    except (Exception, SystemExit):
        sys.stderr.write("Schemii retired runtime stop refused before mutation: ownership, state or exclusive deployment lease could not be verified\n")
        outcome = 1
    raise SystemExit(outcome)
PY
fi

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
SCHEMII_LAUNCH_LOCK_FILE="${SCHEMII_LAUNCH_LOCK_FILE-${ROOT_DIR}/.schemii/start.lock}"
SCHEMII_RESET_MIGRATION_DEMO="${SCHEMII_RESET_MIGRATION_DEMO-0}"
SCHEMII_DEMO_SCENARIO="${SCHEMII_DEMO_SCENARIO-baseline}"
SCHEMII_DEMO_SOURCE_REVISION="${SCHEMII_DEMO_SOURCE_REVISION-unknown+dirty}"
SCHEMII_DEMO_STATE_DIRECTORY="${SCHEMII_DEMO_STATE_DIRECTORY-}"
SCHEMII_LAUNCH_ACTION=start
SCHEMII_RECOVERY_DIRECTORY=
SCHEMII_TESTING_SPACE=all
SCHEMII_QA_STATE_DIRECTORY="${SCHEMII_QA_STATE_DIRECTORY-${ROOT_DIR}/.schemii/testing}"
SCHEMII_LOG_SERVICE=
SCHEMII_RUNTIME_RECEIPT="${SCHEMII_RUNTIME_RECEIPT-}"
SCHEMII_PI_PROTOTYPE_URL=http://ai-prototype-runtime:4097

usage() {
  printf 'Usage: %s [--inspect-runtime ID [ID ...] | --stop-retired-runtime ID [ID ...] | --demo | --prepare-testing | --reset-testing SPACE | --check-testing-reset SPACE | --verify-testing [SPACE] | --prepare-testing-writable ACCOUNT | --reset-testing-writable ACCOUNT | --verify-testing-writable ACCOUNT | --backup [NEW_DIRECTORY] | --verify-backup DIRECTORY | --restore-backup-new DIRECTORY | --ai-prototype | --reset-demo [SCENARIO] | --reset-migration-demo | --list-demo-scenarios | --logs SERVICE | --test-ai-prototype | --test-ai-metadata | --remove-legacy-ai-data]\n' "$0"
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
    --demo)
      (( $# == 1 )) || { usage >&2; exit 2; }
      SCHEMII_LAUNCH_ACTION=demo
      # A demo launch always preserves existing source data and saved work.
      SCHEMII_RESET_MIGRATION_DEMO=0
      ;;
    --prepare-testing)
      (( $# == 1 )) || { usage >&2; exit 2; }
      SCHEMII_LAUNCH_ACTION=prepare-testing
      ;;
    --reset-testing|--verify-testing|--check-testing-reset)
      (( $# <= 2 )) || { usage >&2; exit 2; }
      [[ "$1" != "--reset-testing" || $# == 2 ]] || { usage >&2; exit 2; }
      SCHEMII_LAUNCH_ACTION="${1#--}"
      SCHEMII_TESTING_SPACE="${2-all}"
      [[ "$SCHEMII_TESTING_SPACE" == "all" || "$SCHEMII_TESTING_SPACE" =~ ^qa_[a-z_]+_[0-9]{3}$ ]] || { printf 'Invalid testing space\n' >&2; exit 2; }
      ;;
    --prepare-testing-writable|--reset-testing-writable|--verify-testing-writable)
      (( $# == 2 )) || { usage >&2; exit 2; }
      SCHEMII_LAUNCH_ACTION="${1#--}"
      SCHEMII_TESTING_SPACE="$2"
      [[ "$SCHEMII_TESTING_SPACE" == qa_designer_004 || "$SCHEMII_TESTING_SPACE" == qa_designer_010 || "$SCHEMII_TESTING_SPACE" == qa_designer_011 ]] || { printf 'Invalid writable testing account\n' >&2; exit 2; }
      ;;
    --backup)
      (( $# <= 2 )) || { usage >&2; exit 2; }
      SCHEMII_LAUNCH_ACTION=backup
      SCHEMII_RECOVERY_DIRECTORY="${2-}"
      ;;
    --verify-backup|--restore-backup-new)
      (( $# == 2 )) || { usage >&2; exit 2; }
      SCHEMII_LAUNCH_ACTION="${1#--}"
      SCHEMII_RECOVERY_DIRECTORY="$2"
      ;;
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
    --test-ai-metadata)
      (( $# == 1 )) || { usage >&2; exit 2; }
      SCHEMII_LAUNCH_ACTION=test-ai-metadata
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
        schemii|ai-prototype-runtime|ingress|metadata-bootstrap|demo-bootstrap|postgres-seed|qa-postgres)
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

# Timing is opt-in for normal starts only. Disabled starts invoke no additional
# external programs. EXIT observes the original status; RETURN cleanup traps in
# certificate/secret helpers remain untouched. Only fixed phase names reach the
# writer, never command arguments, errors, service output or secret values.
SCHEMII_START_TIMING_ACTIVE=0
startup_timing_exit() {
  local launch_status="$?" outcome=failed
  trap - EXIT
  if (( SCHEMII_START_TIMING_ACTIVE )); then
    if (( launch_status == 0 )); then outcome=passed
    elif (( launch_status >= 128 )); then outcome=cancelled
    fi
    if ! python3 "$ROOT_DIR/scripts/ci/startup_timing.py" finish \
        "$SCHEMII_START_TIMING_FILE" "$SCHEMII_START_TIMING_TOKEN" \
        "$SCHEMII_START_TIMING_PHASE" "$outcome" "$SCHEMII_START_TIMING_PHASE_START" >/dev/null; then
      printf 'Schemii startup error: launcher timing receipt could not be completed\n' >&2
      # Keep a lifecycle failure's original status. A successful launch with
      # unusable requested evidence is explicitly unsuccessful to its caller.
      if (( launch_status == 0 )); then exit 1; fi
    fi
  fi
  # Returning from EXIT retains Bash's original exit/signal behavior.
  return 0
}

startup_timing_next() {
  if (( SCHEMII_START_TIMING_ACTIVE )); then
    local next_clock
    if ! next_clock="$(python3 "$ROOT_DIR/scripts/ci/startup_timing.py" phase \
        "$SCHEMII_START_TIMING_FILE" "$SCHEMII_START_TIMING_TOKEN" \
        "$SCHEMII_START_TIMING_PHASE" "$2" "$SCHEMII_START_TIMING_PHASE_START")"; then
      SCHEMII_START_TIMING_ACTIVE=0
      fail "launcher timing phase could not be recorded"
    fi
    SCHEMII_START_TIMING_PHASE_START="$next_clock"
    SCHEMII_START_TIMING_PHASE="$1"
  fi
}

# A QA coordinator holds this lease for its whole run, including manual testing.
# Resolve through Git so all linked worktrees protect the same deployment.
# FD 3 is deliberately inherited across the launcher's stale-group re-exec.
if [[ "$SCHEMII_LAUNCH_ACTION" != "logs" ]]; then
  command -v git >/dev/null 2>&1 || fail "Git is required to resolve the shared deployment lease"
  command -v flock >/dev/null 2>&1 || fail "flock is required to serialize local application lifecycle changes"
  SCHEMII_QA_LOCK_DIRECTORY="$(git -C "$ROOT_DIR" rev-parse --path-format=absolute --git-common-dir)" || fail "could not resolve the shared deployment lease directory"
  if [[ -z "${SCHEMII_SECRET_DIRECTORY+x}" || "$SCHEMII_LAUNCH_ACTION" == demo ]]; then
    # Linked worktrees share the fixed Compose project and its persistent volumes.
    # Keep their secrets with Git's primary worktree so the first launch from any
    # linked checkout uses one stable credential owner. Git lists the primary
    # worktree first, even when this worktree's .git entry is a file.
    SCHEMII_PRIMARY_WORKTREE_ROOT="$(git --git-dir="$SCHEMII_QA_LOCK_DIRECTORY" worktree list --porcelain \
      | sed -n '1s/^worktree //p')" || fail "could not resolve the primary Git worktree for shared stack secrets"
    [[ -n "$SCHEMII_PRIMARY_WORKTREE_ROOT" && -d "$SCHEMII_PRIMARY_WORKTREE_ROOT" ]] \
      || fail "the primary Git worktree is unavailable; set SCHEMII_SECRET_DIRECTORY to the retained stack secrets"
    if [[ -z "${SCHEMII_SECRET_DIRECTORY+x}" ]]; then
      SCHEMII_SECRET_DIRECTORY="${SCHEMII_PRIMARY_WORKTREE_ROOT}/.schemii/secrets"
    fi
    if [[ "$SCHEMII_LAUNCH_ACTION" == demo ]]; then
      SCHEMII_DEMO_STATE_DIRECTORY="${SCHEMII_DEMO_STATE_DIRECTORY:-${SCHEMII_PRIMARY_WORKTREE_ROOT}/.schemii/demo}"
      [[ "$SCHEMII_DEMO_STATE_DIRECTORY" == /* ]] || fail "demo state directory must be an absolute path"
      [[ -f "$ROOT_DIR/dev/demo/prepare.py" ]] || fail "demo preparation helper is missing"
    fi
  fi
  SCHEMII_QA_LOCK_FILE="${SCHEMII_QA_LOCK_DIRECTORY}/qa-deployment.lock"
  SCHEMII_QA_GATE_FILE="${SCHEMII_QA_LOCK_DIRECTORY}/qa-startup.lock"
  if [[ -n "${SCHEMII_QA_GATE_FD+x}" ]]; then
    [[ "$SCHEMII_QA_GATE_FD" == "4" && "$(readlink -- /proc/$$/fd/4 2>/dev/null || true)" == "$SCHEMII_QA_GATE_FILE" ]] || fail "inherited QA startup gate does not match this repository"
    flock --nonblock 4 || fail "QA startup gate is unavailable"
  else
    exec 4>"$SCHEMII_QA_GATE_FILE"
    flock --nonblock 4 || fail "another launch or QA preparation holds the startup gate"
    export SCHEMII_QA_GATE_FD=4
  fi
  if [[ -n "${SCHEMII_QA_LEASE_FD+x}" ]]; then
    [[ "${SCHEMII_QA_LEASE_MODE-exclusive}" == "exclusive" ]] || fail "a shared QA run cannot rebuild or reset the deployment"
    [[ "$SCHEMII_QA_LEASE_FD" == "3" ]] || fail "SCHEMII_QA_LEASE_FD must identify inherited descriptor 3"
    [[ "$(readlink -- "/proc/$$/fd/3" 2>/dev/null || true)" == "$SCHEMII_QA_LOCK_FILE" ]] || fail "inherited QA deployment lease does not match this repository"
    flock --nonblock 3 || fail "inherited QA deployment lease is not available"
  else
    exec 3>"$SCHEMII_QA_LOCK_FILE"
    flock --nonblock 3 || fail "the deployment is leased by another launch or QA run; finish that run before rebuilding or resetting"
    export SCHEMII_QA_LEASE_FD=3
    export SCHEMII_QA_LEASE_MODE=exclusive
  fi
fi

# The logs-only action historically needs neither Git nor stack secrets.
if [[ -z "${SCHEMII_SECRET_DIRECTORY+x}" ]]; then
  SCHEMII_SECRET_DIRECTORY="${ROOT_DIR}/.schemii/secrets"
fi

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
if [[ -n "$SCHEMII_RUNTIME_RECEIPT" ]] &&
   [[ "$SCHEMII_LAUNCH_ACTION" == start || "$SCHEMII_LAUNCH_ACTION" == prepare-testing ]]; then
  [[ "$SCHEMII_RUNTIME_RECEIPT" == /* ]] || fail "SCHEMII_RUNTIME_RECEIPT must be an absolute private path"
  [[ ! -e "$SCHEMII_RUNTIME_RECEIPT" && ! -L "$SCHEMII_RUNTIME_RECEIPT" ]] || fail "the runtime receipt already exists; use a new task-owned path"
  command -v node >/dev/null 2>&1 || fail "Node is required for the optional runtime observation receipt"
fi

SCHEMII_TEST_TLS_CERTIFICATE="${SCHEMII_TLS_DIRECTORY}/localhost.crt"
SCHEMII_TEST_TLS_PRIVATE_KEY="${SCHEMII_TLS_DIRECTORY}/localhost.key"
SCHEMII_METADATA_BOOTSTRAP_PASSWORD_SECRET_FILE="${SCHEMII_SECRET_DIRECTORY}/metadata_password"
SCHEMII_METADATA_APP_PASSWORD_SECRET_FILE="${SCHEMII_SECRET_DIRECTORY}/metadata_app_password"
SCHEMII_DEMO_ADMIN_PASSWORD_SECRET_FILE="${SCHEMII_SECRET_DIRECTORY}/demo_admin_password"
SCHEMII_DEMO_TARGET_PASSWORD_SECRET_FILE="${SCHEMII_SECRET_DIRECTORY}/demo_target_password"
SCHEMII_METADATA_ENCRYPTION_KEY_SECRET_FILE="${SCHEMII_SECRET_DIRECTORY}/metadata_encryption_key"
SCHEMII_OPENCODE_PASSWORD_SECRET_FILE="${SCHEMII_SECRET_DIRECTORY}/opencode_password"
SCHEMII_ACCOUNT_SETUP_TOKEN_SECRET_FILE="${SCHEMII_SECRET_DIRECTORY}/account_setup_token"

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

ensure_docker_access

# Preflight (arguments, deployment lease, configuration and runtime access) is
# deliberately outside the receipt. Start only after stale-group re-exec has
# resolved, so no timing state changes the supported exec or signal contract.
if [[ "$SCHEMII_LAUNCH_ACTION" == start && -n "${SCHEMII_START_TIMING_FILE-}" ]]; then
  command -v python3 >/dev/null 2>&1 || fail "Python 3 is required only when launcher timing is enabled"
  SCHEMII_START_TIMING_TOKEN="$(python3 "$ROOT_DIR/scripts/ci/startup_timing.py" begin \
    "$SCHEMII_START_TIMING_FILE" "$ROOT_DIR")" || fail "launcher timing receipt could not be created"
  SCHEMII_START_TIMING_PHASE_START="${SCHEMII_START_TIMING_TOKEN##*:}"
  SCHEMII_START_TIMING_PHASE=preparation
  SCHEMII_START_TIMING_ACTIVE=1
  trap startup_timing_exit EXIT
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
# Include the QA service on every launch once initialized; normal --remove-orphans
# must not remove a portable testing database between runs.
if [[ -f "${SCHEMII_QA_STATE_DIRECTORY}/registry.json" ]]; then
  source "$ROOT_DIR/testing/launcher.sh"
  testing_database_init_writable_registry
  compose_args+=(--file "${ROOT_DIR}/testing/compose.yaml")
fi
export SCHEMII_QA_STATE_DIRECTORY
# Optional machine-local target networks; never part of the portable base stack.
if [[ -f "${ROOT_DIR}/.schemii/compose.local.yaml" ]]; then
  compose_args+=(--file "${ROOT_DIR}/.schemii/compose.local.yaml")
fi

if [[ "$SCHEMII_LAUNCH_ACTION" == "backup" || "$SCHEMII_LAUNCH_ACTION" == "verify-backup" || "$SCHEMII_LAUNCH_ACTION" == "restore-backup-new" ]]; then
  # Recovery deliberately runs before secret generation or normal stack mutation.
  source "$ROOT_DIR/dev/recovery/recovery.sh"
  recovery_main
  exit 0
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
ensure_random_secret "$SCHEMII_ACCOUNT_SETUP_TOKEN_SECRET_FILE" "account-setup-token"
if ! metadata_encryption_key_is_valid; then
  if [[ -e "$SCHEMII_METADATA_ENCRYPTION_KEY_SECRET_FILE" ]]; then
    fail "the existing metadata encryption key is invalid; restore the original 256-bit base64 key"
  fi
  printf 'Creating a persistent metadata credential encryption key...\n'
  create_metadata_encryption_key
fi
SCHEMII_SECRET_READER_GID="$(stat -c '%g' "$SCHEMII_METADATA_ENCRYPTION_KEY_SECRET_FILE")"
# A stale-group recovery shell may create a new secret with another primary group.
chgrp --reference="$SCHEMII_METADATA_ENCRYPTION_KEY_SECRET_FILE" "$SCHEMII_ACCOUNT_SETUP_TOKEN_SECRET_FILE"

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
export SCHEMII_ACCOUNT_SETUP_TOKEN_SECRET_FILE
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

if [[ -f "${SCHEMII_QA_STATE_DIRECTORY}/registry.json" ]]; then
  # Resolve overlays first so adding the QA target never drops a machine's
  # existing allowed targets (for example organization-postgres).
  SCHEMII_QA_EFFECTIVE_ALLOWED_TARGET_HOSTS="$(docker "${compose_args[@]}" config --format json | python -c 'import json,sys; raw=json.load(sys.stdin)["services"]["schemii"]["environment"].get("SCHEMII_ALLOWED_TARGET_HOSTS", ""); hosts=[h.strip() for h in str(raw).split(",") if h.strip()]; print(",".join(dict.fromkeys([*hosts,"qa-postgres"])))')" || fail "could not resolve the testing target allowlist"
  export SCHEMII_QA_EFFECTIVE_ALLOWED_TARGET_HOSTS
  compose_args+=(--file "$ROOT_DIR/testing/egress.yaml")
fi

if [[ "$SCHEMII_LAUNCH_ACTION" == *-testing || "$SCHEMII_LAUNCH_ACTION" == *-testing-writable || "$SCHEMII_LAUNCH_ACTION" == "check-testing-reset" ]]; then
  source "$ROOT_DIR/testing/launcher.sh"
  # A crashed coordinator can leave account reservations even after its OS lock
  # closes. Do not reset data until its recorded browsers have been cleaned up.
  if [[ "$SCHEMII_LAUNCH_ACTION" == "reset-testing" || "$SCHEMII_LAUNCH_ACTION" == "reset-testing-writable" || "$SCHEMII_LAUNCH_ACTION" == "check-testing-reset" ]]; then
    shopt -s nullglob
    qa_reservations=("${SCHEMII_QA_LOCK_DIRECTORY}/qa-account-leases/"*.json)
    shopt -u nullglob
    (( ${#qa_reservations[@]} == 0 )) || fail "testing accounts remain reserved; clean up their owning runs before resetting data"
    if [[ "$SCHEMII_LAUNCH_ACTION" == "check-testing-reset" ]]; then
      [[ "$SCHEMII_TESTING_SPACE" != all ]] || fail "reset check needs a single testing space"
      testing_database_manage check-reset "$SCHEMII_TESTING_SPACE"
    elif [[ "$SCHEMII_LAUNCH_ACTION" == "reset-testing-writable" ]]; then
      testing_database_manage writer-reset "$SCHEMII_TESTING_SPACE"
    else
      testing_database_manage reset "$SCHEMII_TESTING_SPACE"
    fi
  elif [[ "$SCHEMII_LAUNCH_ACTION" == "verify-testing" ]]; then
    testing_database_manage verify "$SCHEMII_TESTING_SPACE"
  elif [[ "$SCHEMII_LAUNCH_ACTION" == "verify-testing-writable" ]]; then
    testing_database_manage writer-verify "$SCHEMII_TESTING_SPACE"
  elif [[ "$SCHEMII_LAUNCH_ACTION" == "prepare-testing-writable" ]]; then
    testing_database_prepare_writable_credential "$SCHEMII_TESTING_SPACE"
    testing_database_start
    testing_database_manage writer-prepare "$SCHEMII_TESTING_SPACE"
  else
    testing_database_start
    testing_database_manage prepare all
  fi
  if [[ "$SCHEMII_LAUNCH_ACTION" != "prepare-testing" ]]; then exit 0; fi
fi

if [[ "$SCHEMII_LAUNCH_ACTION" == "logs" ]]; then
  exec docker "${compose_args[@]}" logs --no-color --tail 200 "$SCHEMII_LOG_SERVICE"
fi

if [[ "$SCHEMII_LAUNCH_ACTION" == "test-ai-metadata" ]]; then
  printf 'Verifying AI authority records against local PostgreSQL with an isolated test owner...\n'
  exec docker "${compose_args[@]}" exec -T schemii python - < "$ROOT_DIR/tests/integration/ai_operation_contract.py"
fi

printf 'Building the current Schemii application image...\n'
startup_timing_next build passed
if [[ -n "$SCHEMII_PI_PROTOTYPE_URL" ]]; then
  if ! docker "${compose_args[@]}" build schemii ai-prototype-runtime; then
    fail "the application or AI prototype runtime image could not be built; the running deployment was left unchanged"
  fi
elif ! docker "${compose_args[@]}" build schemii ai-prototype-runtime; then
  fail "the application image could not be built; the running deployment was left unchanged"
fi

startup_timing_next replacement passed
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
startup_timing_next readiness passed
if ! docker "${compose_args[@]}" up --detach --remove-orphans --wait --wait-timeout "$SCHEMII_STARTUP_TIMEOUT"; then
  startup_timing_next post-start failed
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
startup_timing_next post-start passed
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
# This operator-only command prepares one account's demo resources after every
# service is healthy. Secret input travels over stdin, never command arguments.
if [[ "$SCHEMII_LAUNCH_ACTION" == demo ]]; then
  printf 'Preparing the demo administrator and connected bookstore workspace...\n'
  if ! demo_result="$(python3 "$ROOT_DIR/dev/demo/prepare.py" request \
      --state-directory "$SCHEMII_DEMO_STATE_DIRECTORY" \
      --target-database "$SCHEMII_TEST_POSTGRES_DB" \
      --target-username "$SCHEMII_TEST_POSTGRES_USER" \
      --target-password-file "$SCHEMII_DEMO_TARGET_PASSWORD_SECRET_FILE" \
    | docker "${compose_args[@]}" exec -T schemii python \
      -c "$(cat "$ROOT_DIR/dev/demo/prepare.py")" provision)"; then
    fail "demo account and database preparation failed"
  fi
  if ! printf '%s\n' "$demo_result" | python3 "$ROOT_DIR/dev/demo/prepare.py" verify \
      --state-directory "$SCHEMII_DEMO_STATE_DIRECTORY" \
      --origin "https://localhost:${SCHEMII_TEST_APP_PORT}" \
      --preview-origin "https://omarchy.taile4f57f.ts.net"; then
    fail "demo readiness verification failed"
  fi
fi
# Export only selected nonsecret ownership fields after successful health checks.
# This optional action stays inside the launcher's existing deployment lease.
if [[ -n "$SCHEMII_RUNTIME_RECEIPT" ]] &&
   [[ "$SCHEMII_LAUNCH_ACTION" == start || "$SCHEMII_LAUNCH_ACTION" == prepare-testing || "$SCHEMII_LAUNCH_ACTION" == demo ]]; then
  runtime_revision="$(git -C "$ROOT_DIR" rev-parse HEAD)" || fail "could not identify the observed source revision"
  if [[ -n "$(git -C "$ROOT_DIR" status --porcelain --untracked-files=normal)" ]]; then runtime_revision+="+dirty"; fi
  runtime_container_ids="$(docker ps --quiet --filter label=com.docker.compose.project=schemii-test --filter status=running)" || fail "could not identify runtime containers"
  [[ -n "$runtime_container_ids" ]] || fail "runtime containers were not found"
  if ! {
    while IFS= read -r runtime_container_id; do
      [[ "$runtime_container_id" =~ ^[0-9a-f]+$ ]] || exit 1
      docker inspect --format '{"id":{{json .Id}},"image":{{json .Image}},"pid":{{.State.Pid}},"running":{{.State.Running}},"project":{{json (index .Config.Labels "com.docker.compose.project")}},"service":{{json (index .Config.Labels "com.docker.compose.service")}},"memoryBytes":{{.HostConfig.Memory}},"nanoCpus":{{.HostConfig.NanoCpus}}}' "$runtime_container_id" || exit 1
    done <<< "$runtime_container_ids"
  } | node "$ROOT_DIR/testing/load/runtime.mjs" export "$SCHEMII_RUNTIME_RECEIPT" "$runtime_revision"; then
    fail "the healthy deployment's runtime ownership receipt could not be exported"
  fi
fi
printf 'Schemii is ready at https://localhost:%s/\n' "$SCHEMII_TEST_APP_PORT"
printf 'API map: https://localhost:%s/api-map\n' "$SCHEMII_TEST_APP_PORT"
printf 'DB call map: https://localhost:%s/db-map\n' "$SCHEMII_TEST_APP_PORT"

printf 'Accounts: https://localhost:%s/login (first setup token is stored in %s)\n' "$SCHEMII_TEST_APP_PORT" "$SCHEMII_ACCOUNT_SETUP_TOKEN_SECRET_FILE"
