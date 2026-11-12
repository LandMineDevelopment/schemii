# Sourced only by start.sh. This is the only supported pressure lifecycle entry.
pressure_main() {
  command -v git >/dev/null 2>&1 || fail "Git is required for pressure qualification"
  command -v flock >/dev/null 2>&1 || fail "flock is required for pressure qualification"
  local common primary gate lease
  common="$(git -C "$ROOT_DIR" rev-parse --path-format=absolute --git-common-dir)" || fail "could not resolve pressure deployment leases"
  primary="$(git --git-dir="$common" worktree list --porcelain | sed -n '1s/^worktree //p')" || fail "could not resolve the primary checkout"
  [[ -d "$common" && "$common" == /* && -d "$primary" && "$primary" == /* ]] || fail "pressure checkout identity is unavailable"
  gate="$common/qa-startup.lock"
  lease="$common/qa-deployment.lock"
  [[ ! -L "$gate" && ! -L "$lease" ]] || fail "pressure leases must not be symlinks"
  if [[ -n "${SCHEMII_QA_GATE_FD+x}" ]]; then
    [[ "$SCHEMII_QA_GATE_FD" == 4 && "$(readlink -- /proc/$$/fd/4 2>/dev/null || true)" == "$gate" ]] || fail "pressure startup gate does not match"
  else
    exec 4>"$gate"
  fi
  flock --nonblock 4 || fail "another launch or QA preparation holds the startup gate"
  if [[ -n "${SCHEMII_QA_LEASE_FD+x}" ]]; then
    [[ "$SCHEMII_QA_LEASE_FD" == 3 && "${SCHEMII_QA_LEASE_MODE-exclusive}" == exclusive && "$(readlink -- /proc/$$/fd/3 2>/dev/null || true)" == "$lease" ]] || fail "pressure requires the canonical exclusive deployment lease"
  else
    exec 3>"$lease"
  fi
  flock --nonblock 3 || fail "deployment is leased by another launch or QA run"
  export SCHEMII_QA_GATE_FD=4 SCHEMII_QA_LEASE_FD=3 SCHEMII_QA_LEASE_MODE=exclusive
  ensure_docker_access
  exec python3 "$ROOT_DIR/dev/pressure/controller.py" --root "$ROOT_DIR" --primary "$primary" --common "$common" -- "$@"
}
