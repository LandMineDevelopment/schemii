#!/usr/bin/env bash
# Runs only inside the dedicated Schemii PRoot userland, through start.sh.
set -Eeuo pipefail
umask 077
NODE_VERSION=22.22.2
NODE_SHA256=e9e1930fd321a470e29bb68f30318bf58e3ecb4acb4f1533fb19c58328a091fe
NODE_ARCHIVE="node-v${NODE_VERSION}-linux-arm64.tar.xz"
IMAGE='debian:trixie-slim@sha256:24a01011d10eba3a023bc8585b9650b8b29021240d4217c05b905e09ef4a5232'
fail() { printf 'Android installer: %s\n' "$*" >&2; exit 1; }
discard_unactivated_environment() {
  local candidate=$1 active_link=$2
  # If activation rollback or signal handling was interrupted, keep any
  # environment still referenced by the stable executable path for repair.
  if [[ ! -L "$active_link" || $(readlink "$active_link") != "$candidate" ]]; then
    rm -rf -- "$candidate"
  fi
}
activate_runtime() {
  python3 - "$1" "$2" "$3" <<'PY'
import hashlib, json, os, pathlib, secrets, subprocess, sys, tempfile
base=pathlib.Path(sys.argv[1])
root=base/'opt/schemii'
opt=base/'opt'
files=['constraints.docker.txt','pyproject.toml','ai/prototype/package-lock.json','ai/prototype/package.json','ai/prototype/server.js']
files.extend(str(path.relative_to(root)) for path in sorted((root/'src').rglob('*')) if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc')
record={'format':'schemii-android-install-v1','debian':'13','architecture':'arm64',
        'postgresql_major':17,'python_minor':'3.13','node':'22.22.2',
        'image':'debian:trixie-slim@sha256:24a01011d10eba3a023bc8585b9650b8b29021240d4217c05b905e09ef4a5232',
        'venv':sys.argv[2], 'source_files':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in files}}
manifest=root/'ANDROID_RELEASE.json'
if manifest.is_file():
    record['release']=json.loads(manifest.read_text())
record['packages']=subprocess.check_output(['dpkg-query','-W','-f=${binary:Package}=${Version}\n'],text=True).splitlines()
path=opt/'schemii-runtime.json'
if path.is_symlink() or (path.exists() and not path.is_file()):
    raise SystemExit('Runtime manifest is not a regular owned file; preserving it')
old_manifest=path.read_bytes() if path.exists() else None
old_mode=path.stat().st_mode & 0o777 if path.exists() else 0o644
staged=[]
switched=[]

def stage_bytes(data, mode):
    fd,name=tempfile.mkstemp(prefix='.schemii-activate-',dir=opt)
    target=pathlib.Path(name); staged.append(target)
    with os.fdopen(fd,'wb') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    target.chmod(mode)
    return target

def stage_link(destination, target):
    temporary=destination.with_name('.'+destination.name+'-'+secrets.token_hex(8))
    temporary.symlink_to(target); staged.append(temporary)
    return temporary

# Complete every fallible manifest construction/write before changing links.
try:
    next_manifest=stage_bytes((json.dumps(record,indent=2)+'\n').encode(),0o644)
    for destination,target in [(opt/'node',sys.argv[3]),(opt/'schemii-venv',sys.argv[2])]:
        if destination.exists() and not destination.is_symlink():
            raise RuntimeError(f'{destination.name} is not a managed symlink')
        previous=os.readlink(destination) if destination.is_symlink() else None
        os.replace(stage_link(destination,target),destination)
        switched.append((destination,previous))
    os.replace(next_manifest,path)
except BaseException:
    rollback_errors=[]
    for destination,previous in reversed(switched):
        try:
            if previous is None:
                destination.unlink(missing_ok=True)
            else:
                os.replace(stage_link(destination,previous),destination)
        except OSError as error:
            rollback_errors.append(f'{destination.name}: {error}')
    # The commit rename is last; normally the old manifest is still in place.
    # Restore it if an exception arrived immediately after that rename.
    try:
        if old_manifest is None:
            path.unlink(missing_ok=True)
        elif not path.exists() or path.read_bytes()!=old_manifest:
            os.replace(stage_bytes(old_manifest,old_mode),path)
    except OSError as error:
        rollback_errors.append(f'manifest: {error}')
    if rollback_errors:
        print('Activation rollback incomplete; environments were preserved. Rerun install to repair: '+'; '.join(rollback_errors),file=sys.stderr)
    raise
finally:
    for temporary in staged:
        temporary.unlink(missing_ok=True)
PY
}

prepare_python_source() {
  # setuptools may reuse build/lib from an earlier install. Build only from
  # manifest-listed source in a new directory, never the reused release tree.
  python3 - "$1" "$2" <<'SOURCE'
import hashlib, json, os, pathlib, shutil, stat, sys
root, destination = map(pathlib.Path, sys.argv[1:])
manifest = json.loads((root / 'ANDROID_RELEASE.json').read_text())
if manifest.get('schema') != 1 or not isinstance(manifest.get('files'), dict):
    raise SystemExit('The verified Android release manifest is required for installation')
selected = {}
for name, record in manifest['files'].items():
    if name not in {'pyproject.toml', 'README.md', 'LICENSE', 'constraints.docker.txt'} and not name.startswith('src/'):
        continue
    parts = name.split('/')
    if any(part in {'', '.', '..'} for part in parts):
        raise SystemExit('Unsafe release package source path')
    source = root / name
    if any(root.joinpath(*parts[:i]).is_symlink() for i in range(1, len(parts) + 1)):
        raise SystemExit('Release package source must not be symlinked')
    if not stat.S_ISREG(source.stat().st_mode):
        raise SystemExit('Release package source must be a regular file')
    if hashlib.sha256(source.read_bytes()).hexdigest() != record['sha256']:
        raise SystemExit('Release package source checksum changed; installation refused')
    selected[name] = source
required = {'pyproject.toml', 'README.md', 'LICENSE', 'constraints.docker.txt', 'src/schemii/main.py'}
if not required <= selected.keys():
    raise SystemExit('Release package source is incomplete')
destination.mkdir(mode=0o700)
for name, source in selected.items():
    target = destination / name
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copyfile(source, target)
    os.chmod(target, 0o600)
SOURCE
}

install_guest() {
[[ $(id -u) == 0 ]] || fail 'Guest installation requires the dedicated PRoot root identity.'
[[ -f /opt/schemii-install-owned && $(cat /opt/schemii-install-owned) == "$IMAGE" ]] || fail 'Dedicated runtime ownership does not match this release.'
[[ -f /etc/os-release ]] || fail 'Debian identification is missing.'
# shellcheck disable=SC1091
. /etc/os-release
[[ $ID == debian && $VERSION_ID == 13 ]] || fail 'The runtime must be Debian 13.'
[[ $(dpkg --print-architecture) == arm64 ]] || fail 'The guest architecture must be ARM64.'
[[ -f /opt/schemii/pyproject.toml && -f /opt/schemii/constraints.docker.txt ]] || fail 'The complete release source is not mounted at /opt/schemii.'
[[ -f /opt/schemii/ANDROID_RELEASE.json ]] || fail 'Install from the verified versioned Android release archive, not an unpackaged source checkout.'

# Package installation must never start system services, nor initialize the
# distribution's unused default PostgreSQL cluster. Our runtime owns both DBs.
work=$(mktemp -d /opt/.schemii-install.XXXXXXXX)
policy=/usr/sbin/policy-rc.d
had_policy=0
environment=
environment_committed=0
if [[ -e "$policy" ]]; then
  [[ ! -L "$policy" && -f "$policy" ]] || fail 'Existing package service policy is not a regular file.'
  cp -p -- "$policy" "$work/policy-rc.d"; had_policy=1
fi
cleanup() {
  if ((had_policy)); then cp -p -- "$work/policy-rc.d" "$policy"; else rm -f -- "$policy"; fi
  rm -rf -- "$work"
  if [[ -n "$environment" && "$environment_committed" == 0 ]]; then
    discard_unactivated_environment "$environment" /opt/schemii-venv
  fi
}
trap cleanup EXIT
printf '#!/bin/sh\nexit 101\n' > "$policy"
chmod 755 "$policy"
mkdir -p /etc/postgresql-common
printf 'create_main_cluster = false\n' > /etc/postgresql-common/createcluster.conf
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip postgresql-17 postgresql-contrib-17 \
  nginx openssl ca-certificates curl xz-utils passwd
[[ $(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])') == 3.13 ]] || fail 'Debian Python must remain on the supported 3.13 minor.'
[[ $(/usr/lib/postgresql/17/bin/postgres --version) == 'postgres (PostgreSQL) 17.'* ]] || fail 'PostgreSQL 17 is required.'

if ! id schemii >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash schemii
fi
[[ $(getent passwd schemii | cut -d: -f6) == /home/schemii ]] || fail 'Existing schemii user has an unexpected home directory.'
install -d -m700 -o schemii -g schemii /home/schemii/.local /home/schemii/.local/share

# Node's release digest is compiled into this installer, rather than trusting a
# second unpinned network response as the expected checksum.
node_directory="/opt/schemii-node/node-v${NODE_VERSION}-${NODE_SHA256:0:16}"
mkdir -p /opt/schemii-node
if [[ -e "$node_directory" ]]; then
  [[ -f "$node_directory/.schemii-owned" && $(cat "$node_directory/.schemii-owned") == "$NODE_SHA256" ]] || fail 'Existing Node directory is not owned by this runtime.'
else
  curl --fail --show-error --location --proto '=https' --tlsv1.2 \
    "https://nodejs.org/download/release/v${NODE_VERSION}/${NODE_ARCHIVE}" -o "$work/$NODE_ARCHIVE"
  printf '%s  %s\n' "$NODE_SHA256" "$work/$NODE_ARCHIVE" | sha256sum --check --status || fail 'Node archive checksum mismatch; installation refused.'
  mkdir "$work/node"
  tar --extract --xz --file "$work/$NODE_ARCHIVE" --directory "$work/node" --strip-components=1 --no-same-owner
  [[ $("$work/node/bin/node" --version) == "v$NODE_VERSION" ]] || fail 'Downloaded Node runtime version is unexpected.'
  printf '%s\n' "$NODE_SHA256" > "$work/node/.schemii-owned"
  mv "$work/node" "$node_directory"
fi
[[ $("$node_directory/bin/node" --version) == "v$NODE_VERSION" ]] || fail 'Installed Node version is unexpected.'
[[ ! -e /opt/node || -L /opt/node ]] || fail 'Existing /opt/node is not a managed symlink; preserving it.'
export PATH="$node_directory/bin:/usr/lib/postgresql/17/bin:$PATH"

# Build directly at its final unique path: virtual-environment script shebangs
# contain absolute paths. Failed installation never replaces the previous venv.
mkdir -p /opt/schemii-environments
environment=$(mktemp -d /opt/schemii-environments/env.XXXXXXXX)
python3 -m venv "$environment"
prepare_python_source /opt/schemii "$work/python-source"
"$environment/bin/python" -m pip install --disable-pip-version-check --only-binary=:all: \
  --constraint "$work/python-source/constraints.docker.txt" "$work/python-source"
"$environment/bin/python" -m pip check
"$environment/bin/python" - <<'IMPORTS'
import cryptography, fastapi, pglast, psycopg, pydantic, sqlglot, uvicorn
assert pglast.parse_sql('SELECT 1')
assert pydantic.TypeAdapter(int).validate_python('1') == 1
IMPORTS
printf 'schemii-python-environment-v1\n' > "$environment/.schemii-owned"
if [[ -e /opt/schemii-venv && ! -L /opt/schemii-venv ]]; then fail 'Existing venv path is not a Schemii symlink; preserving it.'; fi

# Match the reviewed container build: lockfile integrity, no lifecycle scripts.
cd /opt/schemii/ai/prototype
npm ci --omit=dev --ignore-scripts

activate_runtime / "$environment" "$node_directory"
environment_committed=1
printf 'Guest dependencies installed; on-device PostgreSQL, HTTPS, API and recovery acceptance remains pending.\n'
}
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then install_guest "$@"; fi
