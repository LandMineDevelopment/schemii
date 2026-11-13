#!/usr/bin/env bash
# Android lifecycle entry point. Invoke through ./start.sh --android.
set -Eeuo pipefail
umask 077

SCHEMII_ANDROID_ALIAS=schemii-android
SCHEMII_ANDROID_IMAGE='debian:trixie-slim@sha256:24a01011d10eba3a023bc8585b9650b8b29021240d4217c05b905e09ef4a5232'

fail() { printf 'Android launcher: %s\n' "$*" >&2; return 1; }
usage() {
  cat <<'HELP'
Usage: ./start.sh --android COMMAND [ARGUMENTS]
  install                         Install the dedicated Debian 13 ARM64 runtime
  configure --tailnet-ip IP --hostname NAME
  start | stop | status | doctor  Manage or inspect the phone backend
  logs [SERVICE]                 Read bounded private logs
  export-ca DEST | credentials DEST  Export CA or private setup references
  verify-backup DIRECTORY         Verify a private backup without restoring
  backup DIRECTORY               Make a private cold backup (stops the backend)
  restore DIRECTORY              Restore only into a fresh empty runtime state
  enable-boot | disable-boot     Manage only Schemii's Termux:Boot script
Install Termux and Termux:Boot from the same official source. Keep this release
inside Termux's private home, and connect the official Tailscale Android app.
The preview requires on-device acceptance; it is for trusted-owner SQL use.
HELP
}
require_termux() {
  [[ ${PREFIX:-} == /data/data/com.termux/files/usr ]] || fail 'Run this command inside the official Termux app, not a desktop shell or PRoot guest.'
  [[ $(uname -m) == aarch64 ]] || fail 'This release requires an ARM64 (aarch64) Android device.'
  [[ $(id -u) != 0 ]] || fail 'Run as the normal unrooted Termux app user.'
  command -v getprop >/dev/null || fail 'Android getprop is unavailable; this is not a supported Termux host.'
  [[ $(getprop ro.build.version.sdk) =~ ^[0-9]+$ ]] || fail 'Cannot determine the Android API level.'
  local actual_root actual_home
  actual_root=$(realpath -- "$SCHEMII_ANDROID_ROOT")
  actual_home=$(realpath -- "$HOME")
  [[ "$actual_root" == "$actual_home/"* ]] || fail 'Extract the release inside Termux private home, not shared storage or a memory card.'
  [[ "$actual_root" != *:* && "$actual_root" != *$'\n'* ]] || fail 'Release path cannot contain colons or newlines.'
}
require_proot() {
  command -v proot-distro >/dev/null || fail 'Run ./start.sh --android install first (proot-distro is missing).'
  local install_help login_help
  # PRoot-Distro 5.10.3 deliberately renders help on stderr, even on success.
  # Inspect both streams without displaying pages of help during installation.
  if ! install_help=$(proot-distro install --help 2>&1); then
    fail 'Could not inspect PRoot-Distro install options. Run proot-distro install --help to inspect the failure.'
    return 1
  fi
  if ! login_help=$(proot-distro login --help 2>&1); then
    fail 'Could not inspect PRoot-Distro login options. Run proot-distro login --help to inspect the failure.'
    return 1
  fi
  [[ "$install_help" == *--name* && "$login_help" == *--isolated* && "$login_help" == *--bind* && "$login_help" == *--user* && "$login_help" == *--work-dir* ]] || fail 'This preview requires the current OCI-capable PRoot-Distro 5.x CLI. Update the proot-distro Termux package, then rerun install; older distro-alias releases are unsupported.'
}
guest() {
  local guest_user=$1; shift
  proot-distro login --isolated --user "$guest_user" \
    --bind "$SCHEMII_ANDROID_ROOT:/opt/schemii" \
    --bind "$SCHEMII_ANDROID_CONTROL:/mnt/schemii-control" \
    --work-dir /opt/schemii "$SCHEMII_ANDROID_ALIAS" -- "$@"
}
runtime() { guest schemii /opt/schemii-venv/bin/python /opt/schemii/dev/android/runtime.py "$@"; }
require_owned() {
  local rootfs="$PREFIX/var/lib/proot-distro/containers/$SCHEMII_ANDROID_ALIAS/rootfs"
  [[ -d "$rootfs" && ! -L "$rootfs" ]] || fail 'The dedicated runtime is missing. Run ./start.sh --android install.'
  [[ -f "$rootfs/opt/schemii-install-owned" && ! -L "$rootfs/opt/schemii-install-owned" ]] || fail 'Existing schemii-android userland has no Schemii ownership marker; preserving it. Choose another alias manually before installation.'
  [[ $(cat "$rootfs/opt/schemii-install-owned") == "$SCHEMII_ANDROID_IMAGE" ]] || fail 'The dedicated runtime image identity differs from this release. Preserve it and its backup; automatic replacement is refused.'
}
require_installed_source() {
  guest schemii /usr/bin/python3 -c '
import hashlib,json,pathlib,sys
root=pathlib.Path("/opt/schemii")
try:
 record=json.loads(pathlib.Path("/opt/schemii-runtime.json").read_text())
 expected=record["source_files"]
 names=["constraints.docker.txt","pyproject.toml","ai/prototype/package-lock.json","ai/prototype/package.json","ai/prototype/server.js"]
 names.extend(str(p.relative_to(root)) for p in sorted((root/"src").rglob("*")) if p.is_file() and "__pycache__" not in p.parts and p.suffix!=".pyc")
 valid=set(names)==set(expected) and all(hashlib.sha256((root/name).read_bytes()).hexdigest()==expected[name] for name in names)
except (OSError,ValueError,KeyError,TypeError):
 valid=False
if not valid:
 print("Installed dependencies/source do not match this release. Stop the old runtime and run ./start.sh --android install.",file=sys.stderr)
 sys.exit(1)
'
}
safe_control_paths() {
  local name
  for name in launcher.lock launcher.log launcher.log.1 runner.json runner.tmp status.json; do
    [[ ! -L "$SCHEMII_ANDROID_CONTROL/$name" && ( ! -e "$SCHEMII_ANDROID_CONTROL/$name" || -f "$SCHEMII_ANDROID_CONTROL/$name" ) ]] || fail "Control file is not a regular owned file: $name"
  done
}
host_running() {
  python - "$SCHEMII_ANDROID_CONTROL/runner.json" <<'PY'
import json, pathlib, sys
try:
    record=json.loads(pathlib.Path(sys.argv[1]).read_text())
    pid=int(record['pid'])
    stat=pathlib.Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
    boot=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    alive=stat[0] != 'Z' and stat[19] == str(record['start_tick']) and record.get('boot_id') == boot
except (OSError,ValueError,KeyError,TypeError):
    alive=False
raise SystemExit(0 if alive else 1)
PY
}
record_runner() {
  python - "$SCHEMII_ANDROID_CONTROL/runner.json" "$1" <<'PY'
import json, os, pathlib, sys
pid=int(sys.argv[2]); path=pathlib.Path(sys.argv[1])
stat=pathlib.Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
tmp=path.with_suffix('.tmp')
boot=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
if not boot: raise SystemExit('Cannot establish Android boot identity')
tmp.write_text(json.dumps({'pid':pid,'start_tick':stat[19],'boot_id':boot})+'\n'); tmp.chmod(0o600)
os.replace(tmp,path)
PY
}
install_runtime() {
  (($# == 0)) || fail 'install takes no arguments. Configure the tailnet address afterward.'
  command -v pkg >/dev/null || fail 'Termux pkg is unavailable.'
  if [[ -f "$SCHEMII_ANDROID_CONTROL/runner.json" ]]; then
    host_running && fail 'The backend supervisor is running. Stop it before installing or updating.'
  fi
  local rootfs="$PREFIX/var/lib/proot-distro/containers/$SCHEMII_ANDROID_ALIAS/rootfs"
  local legacy="$PREFIX/var/lib/proot-distro/installed-rootfs/$SCHEMII_ANDROID_ALIAS"
  [[ ! -e "$legacy" ]] || fail 'An older PRoot runtime already uses the dedicated alias. It will not be removed or overwritten.'
  [[ ! -L "${rootfs%/rootfs}" ]] || fail 'Dedicated runtime directory cannot be a symlink.'
  if [[ -e "$rootfs" ]]; then
    require_proot
    require_owned
    if [[ -f "$rootfs/opt/schemii-runtime.json" ]]; then
      # The guest verifies supervisor birth identity, its lifetime lock and both
      # postmasters even if the outer host runner record was lost.
      runtime assert-stopped
    fi
  fi
  pkg install -y proot-distro python curl tar coreutils util-linux
  require_proot
  host_running && fail 'The backend supervisor is running. Stop it before installing or updating.'
  if [[ -e "$rootfs" ]]; then
    require_owned
  else
    proot-distro install --name "$SCHEMII_ANDROID_ALIAS" --architecture aarch64 "$SCHEMII_ANDROID_IMAGE"
    [[ -d "$rootfs/opt" && ! -e "$rootfs/opt/schemii-install-owned" ]] || fail 'Fresh image layout or ownership marker is unexpected.'
    printf '%s\n' "$SCHEMII_ANDROID_IMAGE" > "$rootfs/opt/schemii-install-owned"
    chmod 600 "$rootfs/opt/schemii-install-owned"
  fi
  guest root /bin/bash /opt/schemii/dev/android/install-guest.sh
  printf 'Installed Android preview runtime. Next: ./start.sh --android configure --tailnet-ip PHONE_IP --hostname PHONE_NAME\n'
}
start_runtime() {
  (($# == 0)) || fail 'start takes no arguments; use configure first.'
  if runtime status > "$SCHEMII_ANDROID_CONTROL/status.json" 2>/dev/null; then
    cat "$SCHEMII_ANDROID_CONTROL/status.json"; return
  fi
  host_running && fail 'The supervisor is active but not ready. Inspect logs, then stop it before starting again.'
  local logfile="$SCHEMII_ANDROID_CONTROL/launcher.log"
  if [[ -f "$logfile" ]] && (( $(wc -c < "$logfile") > 1048576 )); then
    mv -f -- "$logfile" "$logfile.1"
  fi
  command -v termux-wake-lock >/dev/null && termux-wake-lock
  # All guest services remain children of one foreground PRoot session.
  nohup proot-distro login --isolated --user schemii \
    --bind "$SCHEMII_ANDROID_ROOT:/opt/schemii" \
    --bind "$SCHEMII_ANDROID_CONTROL:/mnt/schemii-control" \
    --work-dir /opt/schemii "$SCHEMII_ANDROID_ALIAS" -- \
    /opt/schemii-venv/bin/python /opt/schemii/dev/android/runtime.py run \
    </dev/null >>"$logfile" 2>&1 9>&- &
  local runner=$!
  record_runner "$runner"
  local deadline=$((SECONDS + 180))
  while ((SECONDS < deadline)); do
    if runtime status > "$SCHEMII_ANDROID_CONTROL/status.json" 2>/dev/null; then
      cat "$SCHEMII_ANDROID_CONTROL/status.json"; return
    fi
    host_running || fail "The supervisor exited before readiness. Inspect private log: $logfile"
    sleep 2
  done
  fail "Readiness timed out after 180 seconds. The supervisor is retained for diagnosis; inspect logs and run stop. Private launcher log: $logfile"
}
manage_boot() {
  local action=$1 target="$HOME/.termux/boot/schemii"
  if [[ "$action" == disable-boot ]]; then
    if [[ -e "$target" ]]; then
      [[ ! -L "$target" ]] && head -n2 "$target" | grep -Fq '# Schemii Android preview boot v1' || fail 'Existing boot file is not owned by Schemii; preserving it.'
      rm -- "$target"
    fi
    printf 'Schemii boot startup disabled.\n'; return
  fi
  mkdir -p -- "$HOME/.termux/boot"
  if [[ -e "$target" ]]; then
    [[ ! -L "$target" ]] && head -n2 "$target" | grep -Fq '# Schemii Android preview boot v1' || fail 'Existing boot file is not owned by Schemii; preserving it.'
  fi
  [[ ! -e "$target.tmp" && ! -L "$target.tmp" ]] || fail 'Existing boot temporary file is preserved; inspect it before retrying.'
  {
    printf '#!%s/bin/bash\n# Schemii Android preview boot v1\n' "$PREFIX"
    printf 'exec %q %q --android start\n' "$PREFIX/bin/bash" "$SCHEMII_ANDROID_ROOT/start.sh"
  } > "$target.tmp"
  chmod 700 "$target.tmp"
  mv -f -- "$target.tmp" "$target"
  printf 'Schemii boot script enabled. Install/open Termux:Boot from the same source as Termux. Enable Tailscale always-on VPN and battery exemptions for both apps; first unlock and physical idle/reboot tests remain required.\n'
}
main() {
  local action=${1:---help}
  case "$action" in --help|-h|help) usage; return;; esac
  shift
  case "$action" in install|configure|start|stop|status|doctor|logs|backup|restore|verify-backup|export-ca|credentials|enable-boot|disable-boot) ;; *) fail "Unknown command: $action"; return 1;; esac
  SCHEMII_ANDROID_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
  require_termux
  SCHEMII_ANDROID_CONTROL="$HOME/.local/share/schemii-android"
  [[ ! -L "$SCHEMII_ANDROID_CONTROL" ]] || fail 'Control directory must not be a symlink.'
  mkdir -p -- "$SCHEMII_ANDROID_CONTROL"
  chmod 700 "$SCHEMII_ANDROID_CONTROL"
  [[ $(realpath -- "$SCHEMII_ANDROID_CONTROL") == "$(realpath -- "$HOME")/"* ]] || fail 'Control directory must stay in Termux private home.'
  safe_control_paths
  # Serialize host lifecycle mutations; read-only commands never wait on a start.
  case "$action" in status|doctor|logs) ;; *) exec 9>"$SCHEMII_ANDROID_CONTROL/launcher.lock"; flock -n 9 || fail 'Another Android lifecycle action is running.';; esac
  if [[ "$action" == install ]]; then install_runtime "$@"; return; fi
  require_proot
  require_owned
  case "$action" in start|configure|enable-boot) require_installed_source;; esac
  case "$action" in
    start) start_runtime "$@";;
    enable-boot|disable-boot) (($# == 0)) || fail "$action takes no arguments."; manage_boot "$action";;
    *) runtime "$action" "$@";;
  esac
}
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then main "$@"; fi
