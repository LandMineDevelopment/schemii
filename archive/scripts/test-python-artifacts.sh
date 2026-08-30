#!/usr/bin/env bash
set -euo pipefail

package_dir=${1:?Package directory is required}
source_root=${2:?Extracted source root is required}
expected_version=${3:?Expected version is required}
expected_revision=${4:?Expected revision is required}
work_root=${5:?Clean virtual-environment directory is required}

wheel_files=("$package_dir"/*.whl)
sdist_files=("$package_dir"/*.tar.gz)
[[ ${#wheel_files[@]} -eq 1 && -f "${wheel_files[0]}" ]] || { printf 'Expected exactly one wheel.\n' >&2; exit 1; }
[[ ${#sdist_files[@]} -eq 1 && -f "${sdist_files[0]}" ]] || { printf 'Expected exactly one sdist.\n' >&2; exit 1; }

rm -rf -- "$work_root"
mkdir -p "$work_root"
index=0
for artifact in "${wheel_files[0]}" "${sdist_files[0]}"; do
  index=$((index + 1))
  environment="$work_root/$index"
  python -m venv "$environment"
  "$environment/bin/python" -m pip install --disable-pip-version-check --quiet setuptools==84.0.0
  if [[ "$artifact" == *.whl ]]; then
    "$environment/bin/python" -m pip install --disable-pip-version-check --quiet --no-deps "$artifact"
  else
    "$environment/bin/python" -m pip install --disable-pip-version-check --quiet --no-deps --no-build-isolation "$artifact"
  fi
  (cd / && "$environment/bin/python" "$source_root/scripts/verify-installed-package.py" "$expected_version" "$expected_revision" "$source_root")
done
