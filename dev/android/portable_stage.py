"""Termux-only staging of a trusted portable bundle. No activation path."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

if __package__:
    from . import recovery, runtime as guest
else:
    import recovery
    import runtime as guest


BUNDLE_BIND = Path("/mnt/schemii-portable-bundle")
CONTROL_BIND = Path("/mnt/schemii-control")
RECEIPT = "inspection.json"
ATTEMPT_FORMAT = "schemii-termux-portable-attempt-v1"
UNRESOLVED_SHUTDOWN = "Staged PostgreSQL shutdown is unresolved"
SHUTDOWN_TIMEOUT_SECONDS = 30
PORTABLE_TOOLS = ("initdb", "postgres", "psql", "pg_restore", "pg_controldata")
PASSWORD_FILE = {"metadata": "metadata_app_password", "target": "target_password"}


class StageError(recovery.RecoveryError):
    """A sanitized refusal or failure during portable staging."""


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _private_directory(path: Path) -> None:
    recovery._plain_parents(path)
    if stat.S_IMODE(recovery._private_path(path, directory=True).st_mode) != 0o700:
        raise StageError("The portable bundle directory must have mode 0700")


def check_host_bundle(
    raw: str, home: Path, prefix: Path, source: Path, control: Path
) -> None:
    """Validate the single host path allowed through the extra PRoot bind."""
    bundle = Path(raw)
    home = home.resolve(strict=True)
    if (
        raw != str(bundle)
        or not bundle.is_absolute()
        or ".." in bundle.parts
        or any(char in raw for char in ":\r\n")
        or home not in bundle.parents
        or any(
            _overlaps(bundle, reserved)
            for reserved in (
                prefix.resolve(),
                source,
                control,
                home / ".local/share/schemii",
            )
        )
    ):
        raise StageError(
            "Use a separate absolute bundle directory in Termux private home"
        )
    _private_directory(bundle)
    if bundle.stat().st_uid != os.getuid():
        raise StageError("The portable bundle directory must belong to the Termux user")


def _owner_identity_stopped(identity: object, *, runner: bool = False) -> None:
    if not isinstance(identity, dict):
        raise StageError("Destination process ownership is unverifiable")
    pid = identity.get("pid")
    boot = identity.get("boot_id")
    ticks = identity.get("start_tick" if runner else "start_ticks")
    if runner and isinstance(ticks, str) and ticks.isascii() and ticks.isdecimal():
        ticks = int(ticks)
    members = identity.get("members", [])
    if (
        type(pid) is not int
        or pid <= 1
        or type(ticks) is not int
        or ticks <= 0
        or not isinstance(boot, str)
        or not boot
        or not isinstance(members, list)
    ):
        raise StageError("Destination process ownership is unverifiable")
    actual = guest.process_identity(pid)
    if actual is None:
        # process_identity also returns None for an unreadable /proc entry.
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise StageError("Destination process ownership is unverifiable")
    elif actual["start_ticks"] == ticks and actual["boot_id"] == boot:
        raise StageError("The Android destination is active; stop it before staging")
    for member in members:
        if not isinstance(member, dict) or "members" in member:
            raise StageError("Destination process ownership is unverifiable")
        _owner_identity_stopped(member)


def _owner_record(path: Path) -> dict | None:
    try:
        result = json.loads(recovery._read_private(path, 1024 * 1024))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise StageError("Destination ownership record is unverifiable") from None
    if not isinstance(result, dict) or not result:
        raise StageError("Destination ownership record is unverifiable")
    return result


def _assert_prior_stages_resolved(parent: Path) -> None:
    """Block on retained uncertainty without consulting or signaling a recorded PID."""
    for root in parent.glob(".schemii-portable-*"):
        if root.is_symlink() or not root.is_dir():
            continue
        try:
            attempt = _owner_record(root / "attempt.json")
        except StageError:
            attempt = None
        postmasters = (
            root / f"{plane}-pgdata" / "postmaster.pid"
            for plane in recovery.PORTABLE_CLUSTERS
        )
        if (
            attempt is None
            or attempt.get("format") != ATTEMPT_FORMAT
            or attempt.get("status") not in ("quarantined", "verified")
            or attempt.get("processes") != {}
            or attempt.get("reason") == UNRESOLVED_SHUTDOWN
            or any(path.exists() or path.is_symlink() for path in postmasters)
        ):
            raise StageError(f"Unresolved custody in prior portable stage: {root}")


def _assert_destination_stopped(runtime: guest.Runtime, control: Path) -> None:
    recovery._plain_parents(runtime.state / "run")
    _private_directory(control)
    if (control / "runner.tmp").exists() or (control / "runner.tmp").is_symlink():
        raise StageError("An incomplete Termux runner record is preserved")
    runner = _owner_record(control / "runner.json")
    if runner is not None:
        _owner_identity_stopped(runner, runner=True)
    for path, names in (
        (control / "host.json", ("owner", "proxy", "guest")),
        (runtime.state / "run/native-tailnet.json", ("owner", "proxy", "guest")),
        (runtime.state / "run/status.json", ("supervisor", "helper")),
    ):
        record = _owner_record(path)
        if record is None:
            continue
        if record.get("schema") != 1 or record.get("state") not in {
            "stopped",
            "failed",
        }:
            raise StageError(
                "The Android destination has no verified terminal owner record"
            )
        for name in names:
            identity = record[name]
            if name in ("supervisor", "owner") or identity not in (None, {}):
                _owner_identity_stopped(identity)
        for key in ("services", "recovery_services") if "supervisor" in names else ():
            for identity in record.get(key, {}).values():
                _owner_identity_stopped(identity)
    for plane in recovery.PORTABLE_CLUSTERS:
        recovery._plain_parents(runtime.state / "postgres" / plane / "postmaster.pid")
    runtime.assert_stopped()


def _copy_bundle(bundle: Path, copy: Path, manifest: dict) -> str:
    """Replay only an exclusively owned, byte-verified copy."""
    copy.mkdir(mode=0o700)
    folders = (*recovery.PORTABLE_CLUSTERS, "secrets")
    for folder in folders:
        _private_directory(bundle / folder)
        (copy / folder).mkdir(mode=0o700)
    for name in ("manifest.json", *manifest["sha256"]):
        source = bundle / name
        recovery._plain_parents(source)
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as reader:
            info = os.fstat(reader.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                raise StageError(
                    "Portable bundle payloads must be regular mode 0600 files"
                )
            with (copy / name).open("xb") as writer:
                os.fchmod(writer.fileno(), 0o600)
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
                writer.flush()
                os.fsync(writer.fileno())
    if recovery.verify_portable(copy) != manifest:
        raise StageError("The portable bundle changed during staging")
    for folder in folders:
        recovery._sync_directory(copy / folder)
    recovery._sync_directory(copy)
    return recovery._digest(copy / "manifest.json")


def _pgpass_line(role: str, secret: Path) -> str:
    password = secret.read_text(encoding="utf-8").removesuffix("\n")
    escaped = password.replace("\\", "\\\\").replace(":", "\\:")
    return f"*:*:*:{role}:{escaped}\n"


@contextmanager
def _interrupts(runtime: guest.Runtime):
    def interrupt(_signum, _frame):
        runtime.shutdown.set()

    previous = {
        sig: signal.signal(sig, interrupt) for sig in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


class Stage:
    def __init__(self, runtime: guest.Runtime, root: Path, pg_bin: Path):
        self.runtime = runtime
        self.root = root
        self.pg_bin = pg_bin
        self.environment = {"HOME": str(root), "LC_ALL": "C", "PATH": "/usr/bin:/bin"}
        self.phase = "copy"
        self.processes: dict[str, dict] = {}

    def _check_interrupted(self) -> None:
        if self.runtime.shutdown.is_set():
            raise StageError("Portable stage interrupted")

    def _tool(self, tool: str, *args, env=None, data=None, timeout=30, probe=False):
        self._check_interrupted()
        process = subprocess.Popen(
            [self.pg_bin / tool, *args],
            env=self.environment if env is None else env,
            stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        identity = guest.process_identity(process.pid)
        deadline = time.monotonic() + timeout
        try:
            while True:
                self._check_interrupted()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise StageError(f"{tool} timed out")
                try:
                    stdout, _ = process.communicate(data, timeout=min(remaining, 0.25))
                    break
                except subprocess.TimeoutExpired:
                    # communicate resumes its original input on subsequent calls.
                    data = None
        except BaseException:
            if identity is not None and identity["pgid"] == process.pid:
                identity["members"] = guest.captured_group_members(identity)
            self.runtime._cleanup_helper_process(process, identity)
            raise
        if process.returncode:
            if probe:
                return None
            raise StageError(f"{tool} exited with status {process.returncode}")
        return stdout.decode("utf-8").strip()

    def require_tools(self) -> dict[str, str]:
        versions = {}
        for tool in PORTABLE_TOOLS:
            version = self._tool(tool, "--version")
            if not re.fullmatch(
                rf"{tool} \(PostgreSQL\) 17\.\d+(?: \([^\r\n]+\))?", version
            ):
                raise StageError("Staging requires the installed PostgreSQL 17 tools")
            versions[tool] = version
        return versions

    @contextmanager
    def _postgres(self, plane: str, data: Path, sockets: Path, port: int):
        process = subprocess.Popen(
            [
                self.pg_bin / "postgres",
                "-D",
                data,
                "-c",
                "listen_addresses=",
                "-c",
                f"unix_socket_directories={sockets}",
                "-c",
                "unix_socket_permissions=0700",
                "-c",
                f"port={port}",
                "-c",
                "shared_buffers=32MB",
                "-c",
                "max_connections=24",
            ],
            env=self.environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        custody = {
            "observed_pid": process.pid,
            "spawn_phase": self.phase,
            "birth_identity_status": "pending",
            "birth_identity": None,
            "shutdown_status": "pending",
            "shutdown_timeout_seconds": SHUTDOWN_TIMEOUT_SECONDS,
            "signal_authority": "none",
        }
        self.processes[plane] = custody
        try:
            # Record the direct child before asking /proc to identify it.
            self.record_attempt("in-progress")
            identity = guest.process_identity(process.pid)
            custody["birth_identity"] = identity
            if identity is None:
                custody["birth_identity_status"] = "unavailable"
            elif identity.get("pid") == identity.get("pgid") == process.pid:
                custody["birth_identity_status"] = "verified"
            else:
                custody["birth_identity_status"] = "mismatched"
            self.record_attempt("in-progress")
            if custody["birth_identity_status"] != "verified":
                raise StageError("Cannot establish staged PostgreSQL process ownership")
            yield process
        finally:
            # This Popen owns the original unreaped direct child. Do not use
            # pg_ctl or signal a PID obtained from a data directory.
            exit_code = process.poll()
            if exit_code is None:
                custody["shutdown_status"] = "requested"
                process.send_signal(signal.SIGINT)
                try:
                    exit_code = process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    custody["shutdown_status"] = "timed-out"
                    raise StageError(UNRESOLVED_SHUTDOWN) from None
            self.processes.pop(plane, None)
            if exit_code != 0:
                raise StageError(f"Staged PostgreSQL exited with status {exit_code}")

    def _psql(self, env: dict, *args, **options):
        return self._tool(
            "psql",
            "-X",
            "--no-password",
            "--set",
            "ON_ERROR_STOP=1",
            "--no-align",
            "--tuples-only",
            "--quiet",
            *args,
            env=env,
            **options,
        )

    def restore_plane(self, plane: str, identity: dict, port: int) -> str:
        bundle = self.root / "bundle"
        bootstrap, app = identity["bootstrap_user"], identity["app_user"]
        password = bundle / "secrets" / f"{plane}_bootstrap_password"
        data, sockets = self.root / f"{plane}-pgdata", self.root / plane[0]
        sockets.mkdir(mode=0o700)
        self.phase = f"{plane}-initdb"
        self._tool(
            "initdb",
            "--pgdata",
            data,
            "--username",
            bootstrap,
            "--pwfile",
            password,
            "--auth-local=scram-sha-256",
            "--auth-host=reject",
            "--encoding=UTF8",
            "--locale=C.UTF-8",
            "--no-instructions",
            timeout=180,
        )
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".pgpass", dir=self.root
        ) as passfile:
            passfile.write(_pgpass_line(bootstrap, password))
            passfile.write(_pgpass_line(app, bundle / "secrets" / PASSWORD_FILE[plane]))
            passfile.flush()
            admin = {
                **self.environment,
                "PGHOST": str(sockets),
                "PGPORT": str(port),
                "PGUSER": bootstrap,
                "PGDATABASE": "postgres",
                "PGCONNECT_TIMEOUT": "3",
                "PGPASSFILE": passfile.name,
                "PGREQUIREAUTH": "scram-sha-256",
            }
            self.phase = f"{plane}-startup"
            with self._postgres(plane, data, sockets, port) as server:
                identifier = self._restore_running(plane, identity, server, admin)
            self.check_shutdown(plane, identifier)
        return identifier

    def _restore_running(
        self, plane: str, identity: dict, server: subprocess.Popen, admin: dict
    ) -> str:
        bundle = self.root / "bundle"
        bootstrap, app = identity["bootstrap_user"], identity["app_user"]
        primary = identity["primary_database"]
        deadline = time.monotonic() + 45
        while self._psql(admin, "--command", "SELECT 1", timeout=5, probe=True) != "1":
            if server.poll() is not None or time.monotonic() >= deadline:
                raise StageError(
                    "The staged cluster did not accept its bootstrap login"
                )
            self.runtime.shutdown.wait(0.2)

        self.phase = f"{plane}-roles"
        roles = recovery.portable_roles_for_restore(
            (bundle / plane / "roles.sql").read_bytes(), bootstrap
        )
        self._psql(
            admin, "--single-transaction", "--file", "-", data=roles, timeout=180
        )
        self.phase = f"{plane}-archives"
        for database in identity["databases"]:
            self._tool(
                "pg_restore",
                "--create",
                "--exit-on-error",
                "--no-password",
                "--dbname",
                "postgres",
                bundle / plane / f"{database}.dump",
                env=admin,
                timeout=600,
            )

        self.phase = f"{plane}-readback"
        inventory = self._psql(admin, "--command", "SELECT datname FROM pg_database")
        if set(inventory.splitlines()) != recovery.MAINTENANCE_DATABASES.union(
            identity["databases"]
        ):
            raise StageError("Restored database inventory differs from the manifest")
        # Manifest identifiers are validated lowercase PostgreSQL identifiers.
        scram = self._psql(
            admin,
            "--command",
            "SELECT count(*) FROM pg_authid WHERE rolcanlogin "
            f"AND ((oid = 10 AND rolname = '{bootstrap}') OR rolname = '{app}') "
            "AND rolpassword LIKE 'SCRAM-SHA-256$%'",
        )
        identifier = self._psql(
            admin, "--command", "SELECT system_identifier FROM pg_control_system()"
        )
        app_env = {
            **admin,
            "PGUSER": app,
            "PGDATABASE": primary,
        }
        login = self._psql(
            app_env,
            "--command",
            "SELECT current_user, current_database() WHERE inet_server_addr() IS NULL",
        )
        if (
            scram != "2"
            or login != f"{app}|{primary}"
            or not re.fullmatch(r"[0-9]+", identifier)
        ):
            raise StageError("Restored PostgreSQL identities or original logins differ")
        self.phase = f"{plane}-shutdown"
        return identifier

    def check_shutdown(self, plane: str, identifier: str) -> None:
        data = self.root / f"{plane}-pgdata"
        pid_file = data / "postmaster.pid"
        info = self._tool("pg_controldata", data)
        if (
            pid_file.exists()
            or pid_file.is_symlink()
            or not re.search(
                r"^Database cluster state:\s+shut down$", info, re.MULTILINE
            )
            or not re.search(
                rf"^Database system identifier:\s+{identifier}$", info, re.MULTILINE
            )
        ):
            raise StageError(
                "Staged PostgreSQL clean shutdown or physical identity is unproved"
            )

    def write_receipt(self, name: str, value: dict) -> None:
        guest.write_private(self.root / name, json.dumps(value, indent=2) + "\n")
        recovery._sync_directory(self.root)

    def record_attempt(self, status: str, reason: str | None = None) -> None:
        self.write_receipt(
            "attempt.json",
            {
                "format": ATTEMPT_FORMAT,
                "status": status,
                "phase": self.phase,
                "processes": self.processes,
                "reason": reason,
            },
        )


def _stage(runtime: guest.Runtime, bundle: Path, control: Path, pg_bin: Path) -> dict:
    state, source = runtime.state, runtime.source
    if (
        not bundle.is_absolute()
        or ".." in bundle.parts
        or _overlaps(bundle, state)
        or _overlaps(bundle, source)
        or _overlaps(state.parent, source)
        or _overlaps(bundle, control)
    ):
        raise StageError("Portable input, active state and release must be separate")
    recovery._plain_parents(state)
    with (
        runtime._file_lock("native-host"),
        runtime._file_lock("supervisor"),
        runtime.recovery_lock(),
        runtime.lock(),
        _interrupts(runtime),
    ):
        _assert_destination_stopped(runtime, control)
        _assert_prior_stages_resolved(state.parent)
        fingerprint = recovery._runtime_fingerprint()
        _private_directory(bundle)
        manifest = recovery.verify_portable(bundle)
        sizes = sum((bundle / name).stat().st_size for name in manifest["sha256"])
        if shutil.disk_usage(state.parent).free < sizes * 2 + 64 * 1024 * 1024:
            raise StageError("Insufficient free storage for a private portable stage")
        root = Path(tempfile.mkdtemp(prefix=".schemii-portable-", dir=state.parent))
        stage = Stage(runtime, root, pg_bin)
        try:
            recovery._sync_directory(root.parent)
            stage.record_attempt("in-progress")
            bundle_digest = _copy_bundle(bundle, root / "bundle", manifest)
            stage.phase = "tool-versions"
            versions = stage.require_tools()
            clusters = {}
            for plane in recovery.PORTABLE_CLUSTERS:
                identity = manifest["clusters"][plane]
                identifier = stage.restore_plane(
                    plane, identity, getattr(runtime.endpoints, plane)
                )
                clusters[plane] = {**identity, "system_identifier": identifier}
            stage.phase = "pair-verification"
            if (
                clusters["metadata"]["system_identifier"]
                == clusters["target"]["system_identifier"]
            ):
                raise StageError(
                    "The staged PostgreSQL cluster identities must be distinct"
                )
            for plane, cluster in clusters.items():
                stage.check_shutdown(plane, cluster["system_identifier"])
            stage.phase = "receipt"
            stage.record_attempt("verified")
            stage._check_interrupted()
            stage.write_receipt(
                RECEIPT,
                {
                    "format": "schemii-termux-portable-inspection-v1",
                    "bundle_manifest_sha256": bundle_digest,
                    "installed_runtime": fingerprint,
                    "postgresql_tools": versions,
                    "clusters": clusters,
                    "clean_shutdown": True,
                },
            )
        except BaseException as error:
            reason = (
                str(error) if isinstance(error, StageError) else type(error).__name__
            )
            try:
                # If publication failed after rename, withdraw the success marker.
                (root / RECEIPT).unlink(missing_ok=True)
                stage.record_attempt("quarantined", reason)
            except (OSError, ValueError):
                reason += "; quarantine record could not be flushed"
            raise StageError(
                f"Portable stage failed during {stage.phase}: {reason}. Private stage: {root}"
            ) from None
        return {"inspection_receipt": str(root / RECEIPT)}


def stage_portable(runtime: guest.Runtime, bundle: Path) -> dict:
    if (
        runtime.profile != "termux"
        or runtime.state != guest.DEFAULT_STATE
        or bundle != BUNDLE_BIND
    ):
        raise StageError(
            "Portable staging requires the installed Termux launcher and its explicit bundle bind"
        )
    try:
        return _stage(runtime, bundle, CONTROL_BIND, guest.PG_BIN)
    except recovery.RecoveryError as error:
        raise StageError(str(error)) from None
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise StageError(
            "Portable stage preflight refused the destination or bundle"
        ) from None


if __name__ == "__main__":
    try:
        raw, home, prefix, source, control = sys.argv[1:]
        check_host_bundle(raw, Path(home), Path(prefix), Path(source), Path(control))
    except StageError as error:
        sys.exit(f"Android launcher: {error}")
    except (OSError, ValueError):
        sys.exit("Android launcher: The portable bundle path could not be verified.")
