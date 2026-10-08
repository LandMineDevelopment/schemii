#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$ROOT_DIR"
# The checkout's constrained environment is preferred; an explicit interpreter
# also permits linked worktrees to use an already prepared environment.
if [[ -n "${SCHEMII_CI_PYTHON:-}" ]]; then
  exec "$SCHEMII_CI_PYTHON" scripts/ci/local_ci.py "$@"
elif [[ -x .venv/bin/python ]]; then
  exec .venv/bin/python scripts/ci/local_ci.py "$@"
else
  exec python3 scripts/ci/local_ci.py "$@"
fi
