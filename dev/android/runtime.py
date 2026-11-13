#!/usr/bin/env python3
"""Guest runtime for the Android preview. Invoked only through ``start.sh``.

PRoot owns the lifetime of its descendants. The Termux launcher therefore keeps
one login invocation alive around ``run``; this module never daemonizes itself.
All credentials and PostgreSQL files live in the app-private guest filesystem.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import fcntl
import getpass
import importlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import warnings


DEFAULT_STATE = Path("/home/schemii/.local/share/schemii")
SOURCE = Path(__file__).resolve().parents[2]
PYTHON = Path("/opt/schemii-venv/bin/python")
NODE = Path("/opt/node/bin/node")
PG_BIN = Path("/usr/lib/postgresql/17/bin")
SERVICES = ("metadata", "target", "pi", "app", "ingress")
SECRET_NAMES = (
    "metadata_bootstrap_password",
    "target_bootstrap_password",
    "metadata_app_password",
    "target_password",
    "metadata_encryption_key",
    "account_setup_token",
    "opencode_password",
)


class RuntimeFailure(RuntimeError):
    """A safe, operator-facing runtime failure without command output/secrets."""


def private_directory(path: Path) -> None:
    if path.is_symlink():
        raise RuntimeFailure(f"Refusing symlink directory: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def write_private(path: Path, text: str) -> None:
    if path.parent.is_symlink():
        raise RuntimeFailure(f"Refusing symlink output directory: {path.parent}")
    # An export destination can have an existing user-owned parent. Never
    # change that directory's permissions merely to create a private file.
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def write_new_private(path: Path, text: str) -> None:
    """Export without replacing an existing user file, including during races."""
    if path.parent.is_symlink():
        raise RuntimeFailure("The export parent cannot be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
    except FileExistsError as error:
        raise RuntimeFailure("Export destination already exists") from error
    with os.fdopen(descriptor, "w") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


def process_identity(pid: int) -> dict | None:
    """Return Linux birth identity; never authorize a signal by PID alone."""
    try:
        contents = Path(f"/proc/{pid}/stat").read_text()
        fields = contents[contents.rindex(")") + 2 :].split()
        if fields[0] == "Z":
            return None
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if not boot_id:
            return None
        return {
            "pid": pid,
            "start_ticks": int(fields[19]),
            "pgid": int(fields[2]),
            "boot_id": boot_id,
            "ppid": int(fields[1]),
        }
    except (OSError, ValueError, IndexError):
        return None


def is_current(identity: object) -> bool:
    if not isinstance(identity, dict):
        return False
    pid, ticks = identity.get("pid"), identity.get("start_ticks")
    if not isinstance(pid, int) or pid <= 1 or not isinstance(ticks, int):
        return False
    actual = process_identity(pid)
    return (
        actual is not None
        and actual["start_ticks"] == ticks
        and isinstance(identity.get("boot_id"), str)
        and actual.get("boot_id") == identity["boot_id"]
    )


def captured_group_members(identity: dict) -> list[dict]:
    """Capture descendants while the original group leader is still alive."""
    if not is_current(identity):
        return identity.get("members", [])
    members = []
    pending = [identity["pid"]]
    visited = set()
    while pending:
        parent = pending.pop()
        if parent in visited:
            continue
        visited.add(parent)
        try:
            children = (
                Path(f"/proc/{parent}/task/{parent}/children").read_text().split()
            )
        except OSError:
            continue
        for raw_pid in children:
            child = process_identity(int(raw_pid))
            if child and child["pgid"] == identity["pid"]:
                members.append(child)
                pending.append(child["pid"])
    return members


def owns_group(identity: object) -> bool:
    if not isinstance(identity, dict) or identity.get("pgid") != identity.get("pid"):
        return False
    return is_current(identity) or any(
        is_current(member) and member.get("pgid") == identity["pid"]
        for member in identity.get("members", [])
    )


def direct_children(identity: dict) -> list[dict]:
    if not is_current(identity):
        return []
    pid = identity["pid"]
    try:
        values = Path(f"/proc/{pid}/task/{pid}/children").read_text().split()
    except OSError:
        return []
    children = []
    for value in values:
        child = process_identity(int(value))
        if child and child.get("ppid") == pid:
            children.append(child)
    return children


def process_arguments(identity: dict) -> list[str]:
    if not is_current(identity):
        return []
    try:
        return [
            value.decode()
            for value in Path(f"/proc/{identity['pid']}/cmdline")
            .read_bytes()
            .split(b"\0")
            if value
        ]
    except (OSError, UnicodeError):
        return []


def matching_executable(identity: dict, executable: Path) -> bool:
    try:
        return is_current(identity) and os.path.samefile(
            f"/proc/{identity['pid']}/exe", executable
        )
    except OSError:
        return False


def verified_parent_chain(chain: tuple[dict, ...]) -> bool:
    actuals: list[dict] = []
    for item in chain:
        actual = process_identity(item["pid"])
        if actual is None:
            return False
        actuals.append(actual)
    if any(
        actual["start_ticks"] != captured.get("start_ticks")
        or actual.get("boot_id") != captured.get("boot_id")
        for captured, actual in zip(chain, actuals)
    ):
        return False
    return all(
        actual.get("ppid") == parent["pid"]
        for parent, actual in zip(chain, actuals[1:])
    )


def validate_config(tailnet_ip: str, hostname: str) -> dict:
    if not isinstance(tailnet_ip, str) or not isinstance(hostname, str):
        raise RuntimeFailure(
            "Phone network configuration must contain an IP address and DNS hostname"
        )
    try:
        address = ipaddress.IPv4Address(tailnet_ip)
    except ipaddress.AddressValueError as error:
        raise RuntimeFailure(
            "--tailnet-ip must be the phone's Tailscale IPv4 address"
        ) from error
    if address not in ipaddress.IPv4Network("100.64.0.0/10"):
        raise RuntimeFailure("--tailnet-ip must be in Tailscale's 100.64.0.0/10 range")
    hostname = hostname.lower().rstrip(".")
    if len(hostname) > 253 or not re.fullmatch(
        r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
        r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
        hostname,
    ):
        raise RuntimeFailure("--hostname must be the phone's full DNS hostname")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise RuntimeFailure("The remote hostname must differ from localhost")
    return {"schema": 1, "tailnet_ip": str(address), "hostname": hostname}


class LogPump:
    """Drain a child pipe with a finite private on-disk log budget."""

    def __init__(self, pipe, path: Path, maximum: int = 1024 * 1024):
        self.pipe, self.path, self.maximum = pipe, path, maximum
        self.thread = threading.Thread(target=self._drain, daemon=True)
        self.thread.start()

    def _drain(self) -> None:
        private_directory(self.path.parent)
        with self.pipe:
            while chunk := self.pipe.read1(8192):
                append_log(self.path, chunk, self.maximum)


def append_log(path: Path, chunk: bytes, maximum: int = 1024 * 1024) -> None:
    chunk = chunk[-maximum:]
    if path.exists() and path.stat().st_size + len(chunk) > maximum:
        path.with_suffix(".log.2").unlink(missing_ok=True)
        previous = path.with_suffix(".log.1")
        if previous.exists():
            previous.replace(path.with_suffix(".log.2"))
        path.replace(previous)
    with path.open("ab") as output:
        os.chmod(path, 0o600)
        output.write(chunk)


class Runtime:
    def __init__(self, state: Path | None = None, source: Path | None = None):
        self.state = Path(
            state or os.environ.get("SCHEMII_ANDROID_STATE") or DEFAULT_STATE
        )
        if not self.state.is_absolute() or any(c in str(self.state) for c in "\r\n\"'"):
            raise RuntimeFailure(
                "Android state must be an absolute path without quotes or newlines"
            )
        self.source = source or SOURCE
        self.children: dict[str, subprocess.Popen] = {}
        self.identities: dict[str, dict] = {}
        self.recovery_identities: dict[str, dict] = {}
        self.pumps: list[LogPump] = []
        self.shutdown = threading.Event()
        self.started_at = time.time()
        self.startup_stage = "preflight"
        self.helper_identity: dict | None = None
        self._supervising = False

    @contextmanager
    def _file_lock(self, name: str, blocking: bool = False):
        if self.state.parent.is_symlink():
            raise RuntimeFailure("The Android state parent cannot be a symlink")
        self.state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.state.parent / f".{self.state.name}.{name}.lock"
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(
                    descriptor, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
                )
            except BlockingIOError as error:
                stage = read_json(self.state / "run/status.json").get("startup_stage")
                detail = (
                    f" at {stage}"
                    if isinstance(stage, str) and stage.isprintable()
                    else ""
                )
                raise RuntimeFailure(
                    f"Android {name} operation is busy{detail}; use --android status or --android stop"
                ) from error
            yield
        finally:
            os.close(descriptor)

    def lock(self):
        return self._file_lock("lifecycle")

    def recovery_lock(self):
        return self._file_lock("recovery")

    def _prepare(self) -> None:
        for relative in ("", "postgres", "secrets", "tls", "run", "logs"):
            private_directory(self.state / relative)

    def config(self) -> dict:
        config = read_json(self.state / "config.json")
        if not config:
            raise RuntimeFailure(
                "Configure the phone first: ./start.sh --android configure --tailnet-ip IP --hostname NAME"
            )
        return validate_config(config.get("tailnet_ip", ""), config.get("hostname", ""))

    def configure(self, tailnet_ip: str, hostname: str) -> dict:
        config = validate_config(tailnet_ip, hostname)
        with self.recovery_lock(), self.lock():
            self.assert_stopped()
            self._prepare()
            old = read_json(self.state / "config.json")
            write_private(
                self.state / "config.json", json.dumps(config, indent=2) + "\n"
            )
            if old and old != config:
                # Retain the CA and replace only the leaf on the next startup.
                (self.state / "tls/server.crt").unlink(missing_ok=True)
        return {"configured": True, **config, "device_acceptance": "pending"}

    def _secrets(self) -> None:
        initialized = any(
            (self.state / "postgres" / name / "PG_VERSION").exists()
            for name in ("metadata", "target")
        )
        for name in SECRET_NAMES:
            path = self.state / "secrets" / name
            if path.is_symlink():
                raise RuntimeFailure(f"Refusing symlink secret: {path}")
            if not path.exists():
                if initialized:
                    raise RuntimeFailure(
                        f"Retained database is missing its original secret: {path}"
                    )
                value = (
                    base64.b64encode(secrets.token_bytes(32)).decode()
                    if name == "metadata_encryption_key"
                    else secrets.token_hex(32)
                )
                write_private(path, value + "\n")
            value = path.read_text().strip()
            if not value:
                raise RuntimeFailure(f"Empty retained secret: {path}")
            if name == "metadata_encryption_key":
                try:
                    if len(base64.b64decode(value, validate=True)) != 32:
                        raise ValueError("length")
                except ValueError as error:
                    raise RuntimeFailure(
                        "The original metadata encryption key is invalid"
                    ) from error
            path.chmod(0o600)

    def _secret(self, name: str) -> str:
        return (self.state / "secrets" / name).read_text().strip()

    def _checked(
        self,
        command: list[str],
        *,
        input_text: str | None = None,
        env: dict | None = None,
        timeout: float = 120,
    ) -> None:
        if self._helper(command, input_text=input_text, env=env, timeout=timeout):
            raise RuntimeFailure(
                f"{Path(command[0]).name} failed; inspect the private setup log"
            )

    def _helper(
        self,
        command: list[str],
        *,
        input_text: str | None = None,
        env: dict | None = None,
        timeout: float = 120,
    ) -> int:
        """Run one owned startup helper with responsive cancellation and a deadline."""
        if self.shutdown.is_set():
            raise RuntimeFailure("Startup interrupted; retained data was not reset")
        with tempfile.TemporaryFile() as input_stream:
            if input_text is not None:
                input_stream.write(input_text.encode())
                input_stream.seek(0)
            process = subprocess.Popen(
                command,
                stdin=input_stream,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            identity = None
            pump = None
            original_error: BaseException | None = None
            try:
                identity = process_identity(process.pid)
                if identity is None or identity.get("pgid") != process.pid:
                    if process.poll() is not None:
                        pump = LogPump(process.stdout, self.state / "logs/setup.log")
                        return process.returncode
                    raise RuntimeFailure("Cannot establish startup helper ownership")
                self.helper_identity = identity
                # Receipt/log setup can fail, including disk errors. They must
                # be inside the same cleanup boundary as helper execution.
                pump = LogPump(process.stdout, self.state / "logs/setup.log")
                if self._supervising:
                    self._record("starting")
                deadline = time.monotonic() + timeout
                while process.poll() is None:
                    members = captured_group_members(identity)
                    if members != identity.get("members"):
                        identity["members"] = members
                        if self._supervising:
                            self._record("starting")
                    if self.shutdown.is_set():
                        raise RuntimeFailure(
                            "Startup interrupted; retained data was not reset"
                        )
                    if time.monotonic() >= deadline:
                        raise RuntimeFailure(
                            f"{Path(command[0]).name} timed out after {timeout:g}s at {self.startup_stage}; inspect the private setup log"
                        )
                    self.shutdown.wait(min(0.1, max(0, deadline - time.monotonic())))
                return process.wait(timeout=1)
            except BaseException as error:
                original_error = error
                try:
                    self._cleanup_helper_process(process, identity)
                except BaseException:
                    # Preserve the original failure. A still-live helper keeps
                    # its captured identity for a later safe stop attempt.
                    pass
                raise
            finally:
                if pump is not None:
                    pump.thread.join(timeout=2)
                if not owns_group(identity):
                    self.helper_identity = None
                if self._supervising:
                    try:
                        self._record("starting")
                    except BaseException:
                        # A cleanup receipt cannot replace the actual startup
                        # error. If this is the first failure, clean up any
                        # captured descendants before propagating it.
                        if original_error is None:
                            try:
                                self._cleanup_helper_process(process, identity)
                            except BaseException:
                                pass
                            if not owns_group(identity):
                                self.helper_identity = None
                            raise

    def _cleanup_helper_process(
        self, process: subprocess.Popen, identity: object
    ) -> None:
        if owns_group(identity):
            self._stop_helper(identity)
        elif process.poll() is None:
            # Popen still owns this unreaped direct child, so its PID cannot
            # have been reused. This fallback never guesses a group or signals
            # descendants when /proc birth identity could not be established.
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        process.wait(timeout=3)

    def _stage(self, name: str) -> None:
        self.startup_stage = name
        if self._supervising:
            self._record("starting")
            print(f"Android startup: {name}", flush=True)

    def _tls(self, config: dict) -> None:
        tls = self.state / "tls"
        ca_key, ca = tls / "ca.key", tls / "ca.crt"
        leaf_key, leaf = tls / "server.key", tls / "server.crt"
        if ca_key.exists() != ca.exists():
            raise RuntimeFailure(
                "Incomplete retained TLS CA; restore its original key and certificate"
            )
        if not ca.exists():
            self._checked(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:3072",
                    "-nodes",
                    "-sha256",
                    "-days",
                    "3650",
                    "-subj",
                    "/CN=Schemii Android private CA",
                    "-addext",
                    "basicConstraints=critical,CA:TRUE,pathlen:0",
                    "-addext",
                    "keyUsage=critical,keyCertSign,cRLSign",
                    "-keyout",
                    str(ca_key),
                    "-out",
                    str(ca),
                ]
            )
        if leaf.exists() and leaf_key.exists():
            current = self._helper(
                ["openssl", "x509", "-in", str(leaf), "-noout", "-checkend", "604800"],
                timeout=10,
            )
            if current == 0:
                return
        extensions = tls / "server.ext"
        write_private(
            extensions,
            "basicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature,keyEncipherment\n"
            "extendedKeyUsage=serverAuth\n"
            f"subjectAltName=DNS:localhost,DNS:{config['hostname']},"
            f"IP:127.0.0.1,IP:{config['tailnet_ip']}\n",
        )
        csr = tls / "server.csr"
        self._checked(
            [
                "openssl",
                "req",
                "-new",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-subj",
                "/CN=Schemii Android",
                "-keyout",
                str(leaf_key),
                "-out",
                str(csr),
            ]
        )
        self._checked(
            [
                "openssl",
                "x509",
                "-req",
                "-in",
                str(csr),
                "-CA",
                str(ca),
                "-CAkey",
                str(ca_key),
                "-set_serial",
                str(secrets.randbits(128)),
                "-days",
                "365",
                "-sha256",
                "-extfile",
                str(extensions),
                "-out",
                str(leaf),
            ]
        )
        for path in tls.iterdir():
            path.chmod(0o600)
        csr.unlink(missing_ok=True)

    def _cluster(self, name: str, port: int) -> None:
        directory = self.state / "postgres" / name
        if directory.is_symlink():
            raise RuntimeFailure("PostgreSQL data directories cannot be symlinks")
        version = directory / "PG_VERSION"
        if version.exists():
            if version.read_text().strip() != "17":
                raise RuntimeFailure(
                    "Retained databases require PostgreSQL 17; no automatic major upgrade is allowed"
                )
            if not (directory / "global/pg_control").is_file():
                raise RuntimeFailure(
                    f"Incomplete PostgreSQL initialization is missing global/pg_control; retained files were not changed: {directory}"
                )
        else:
            self._stage(f"{name} init")
            if directory.exists() and any(directory.iterdir()):
                raise RuntimeFailure(
                    f"Incomplete PostgreSQL initialization retained for inspection: {directory}"
                )
            self._checked(
                [
                    str(PG_BIN / "initdb"),
                    "-D",
                    str(directory),
                    "--username",
                    "schemii_bootstrap",
                    "--pwfile",
                    str(self.state / "secrets" / f"{name}_bootstrap_password"),
                    "--auth-local=scram-sha-256",
                    "--auth-host=scram-sha-256",
                    "--encoding=UTF8",
                    "--locale=C.UTF-8",
                ]
            )
        self._stage(f"{name} start")
        socket_dir = self.state / "run" / f"{name}-socket"
        private_directory(socket_dir)
        configuration = (
            "listen_addresses = '127.0.0.1'\n"
            f"port = {port}\n"
            f"unix_socket_directories = '{socket_dir}'\n"
            "max_connections = 24\nshared_buffers = '32MB'\nwork_mem = '2MB'\n"
            "maintenance_work_mem = '32MB'\nmax_parallel_workers = 0\n"
            "max_parallel_workers_per_gather = 0\nmax_wal_senders = 0\n"
            "fsync = on\nsynchronous_commit = on\nfull_page_writes = on\n"
            "password_encryption = 'scram-sha-256'\nlog_min_messages = warning\n"
        )
        write_private(directory / "postgresql.conf", configuration)
        self._spawn(name, [str(PG_BIN / "postgres"), "-D", str(directory)])
        self._wait(lambda: self._postgres_ready(port), name)

    def _postgres_ready(self, port: int) -> bool:
        result = self._helper(
            [str(PG_BIN / "pg_isready"), "-h", "127.0.0.1", "-p", str(port), "-t", "2"],
            timeout=5,
        )
        return result == 0

    def _bootstrap(self, name: str, port: int, role: str, password_name: str) -> None:
        self._stage(f"{name} bootstrap")
        self._checked(
            [
                str(PYTHON),
                str(self.source / "dev/android/runtime.py"),
                "internal-bootstrap",
                name,
            ],
            env={**os.environ, "SCHEMII_ANDROID_STATE": str(self.state)},
            timeout=180,
        )
        if name == "target":
            self._stage("target fixtures")
            self._seed_targets(role, port)

    def _bootstrap_database(
        self, name: str, port: int, role: str, password_name: str
    ) -> None:
        import psycopg
        from psycopg import sql

        with psycopg.connect(
            host="127.0.0.1",
            port=port,
            user="schemii_bootstrap",
            password=self._secret(f"{name}_bootstrap_password"),
            dbname="postgres",
            autocommit=True,
            connect_timeout=5,
            options="-c statement_timeout=30000 -c lock_timeout=5000",
        ) as connection:
            if not connection.execute(
                "SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)
            ).fetchone():
                connection.execute(
                    sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role))
                )
            connection.execute(
                sql.SQL(
                    "ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT NOREPLICATION PASSWORD {}"
                ).format(sql.Identifier(role), sql.Literal(self._secret(password_name)))
            )
            databases = (
                ("schemii",)
                if name == "metadata"
                else ("schemii", "schemii_migration_demo", "organization")
            )
            for database in databases:
                if not connection.execute(
                    "SELECT 1 FROM pg_database WHERE datname=%s", (database,)
                ).fetchone():
                    connection.execute(
                        sql.SQL("CREATE DATABASE {} OWNER {}").format(
                            sql.Identifier(database), sql.Identifier(role)
                        )
                    )

    def _seed_targets(self, role: str, port: int) -> None:
        marker = self.state / "postgres/target/.schemii-fixtures-v1"
        if marker.exists():
            return
        password_file = self.state / "run/target.pgpass"
        write_private(
            password_file,
            f"127.0.0.1:{port}:*:{role}:{self._secret('target_password')}\n",
        )
        env = {
            **os.environ,
            "PGHOST": "127.0.0.1",
            "PGPORT": str(port),
            "PGUSER": role,
            "PGPASSFILE": str(password_file),
            "PGCONNECT_TIMEOUT": "5",
            "PGOPTIONS": "-c statement_timeout=30000 -c lock_timeout=5000",
        }
        try:
            for database, fixture in (
                ("schemii", "seed.sql"),
                ("schemii_migration_demo", "migration-demo.sql"),
                ("organization", "organization.sql"),
            ):
                self._checked(
                    [
                        str(PG_BIN / "psql"),
                        "-X",
                        "--dbname",
                        database,
                        "--set",
                        "ON_ERROR_STOP=1",
                        "--file",
                        str(self.source / "dev/postgres" / fixture),
                    ],
                    env=env,
                )
        finally:
            password_file.unlink(missing_ok=True)
        write_private(marker, "1\n")

    def _environment(self, config: dict) -> dict:
        env = dict(os.environ)
        # Do not inherit desktop deployment configuration or user-supplied DSNs.
        for key in list(env):
            if key.startswith(("SCHEMII_", "SCHEMER_")) or key in (
                "PGPASSWORD",
                "PGPASSFILE",
                "PGHOST",
                "PGPORT",
                "PGUSER",
                "PGDATABASE",
            ):
                env.pop(key)
        secret = self.state / "secrets"
        env.update(
            {
                "PATH": f"/opt/node/bin:/opt/schemii-venv/bin:{PG_BIN}:/usr/bin:/bin",
                "PYTHONUNBUFFERED": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "SCHEMII_DEPLOYMENT_MODE": "authenticated",
                "SCHEMII_AUTH_ENABLED": "1",
                "SCHEMII_STORAGE_MODE": "postgresql",
                "SCHEMII_DEVELOPER_INSPECTION": "1",
                "SCHEMII_METADATA_DSN": "host=127.0.0.1 port=5433 dbname=schemii user=schemii_metadata_app",
                "SCHEMII_METADATA_PASSWORD_FILE": str(secret / "metadata_app_password"),
                "SCHEMII_METADATA_ENCRYPTION_KEY_FILE": str(
                    secret / "metadata_encryption_key"
                ),
                "SCHEMII_SETUP_TOKEN_FILE": str(secret / "account_setup_token"),
                "SCHEMII_METADATA_TARGET_HOST_ALIASES": "localhost,127.0.0.1,::1",
                "SCHEMII_METADATA_MAXIMUM_CONNECTIONS": "4",
                "SCHEMII_TARGET_EGRESS_MODE": "internal-only",
                "SCHEMII_ALLOWED_TARGET_HOSTS": "127.0.0.1,localhost",
                "SCHEMII_CONFIG_FILE": str(self.state / "run/schemii.toml"),
                "SCHEMII_PI_PROTOTYPE_URL": "http://127.0.0.1:4097",
                "SCHEMII_PI_PASSWORD_FILE": str(secret / "opencode_password"),
                "SCHEMII_PI_HOST": "127.0.0.1",
                "SCHEMII_TRUSTED_ORIGINS": ",".join(
                    (
                        "https://localhost:8001",
                        "https://127.0.0.1:8001",
                        f"https://{config['hostname']}:8001",
                        f"https://{config['tailnet_ip']}:8001",
                    )
                ),
                "SCHEMER_PREVIEW_ROWS": "10000",
                "SCHEMER_PREVIEW_BYTES": "8388608",
                "SCHEMER_DASHBOARD_PREVIEW_ROWS": "50000",
                "SCHEMER_DASHBOARD_PREVIEW_BYTES": "16777216",
            }
        )
        return env

    def _write_runtime_config(self, config: dict) -> None:
        app_config = (self.source / "dev/schemii.toml").read_text()
        replacements = {
            "maximum_total = 20": "maximum_total = 8",
            "maximum_per_identity = 4": "maximum_per_identity = 2",
            "maximum_live_read_sessions = 12": "maximum_live_read_sessions = 4",
            "maximum_concurrent_turns = 4": "maximum_concurrent_turns = 1",
            "maximum_concurrent_turns_per_user = 2": "maximum_concurrent_turns_per_user = 1",
        }
        for old, new in replacements.items():
            app_config = app_config.replace(old, new)
        write_private(self.state / "run/schemii.toml", app_config)
        template = (self.source / "dev/android/nginx.conf.in").read_text()
        for key, value in (
            ("STATE", str(self.state)),
            ("TAILNET_IP", config["tailnet_ip"]),
            ("HOSTNAME", config["hostname"]),
        ):
            template = template.replace(f"@{key}@", value)
        write_private(self.state / "run/nginx.conf", template)

    def _record(self, state: str, error: str | None = None) -> None:
        for identity in self.identities.values():
            identity["members"] = captured_group_members(identity)
        write_private(
            self.state / "run/status.json",
            json.dumps(
                {
                    "schema": 1,
                    "state": state,
                    "ready": state == "ready",
                    "supervisor": process_identity(os.getpid()),
                    "services": self.identities,
                    "recovery_services": self.recovery_identities,
                    "started_at": self.started_at,
                    "updated_at": time.time(),
                    "error": error,
                    "startup_stage": self.startup_stage,
                    "helper": self.helper_identity,
                    "device_acceptance": "pending",
                },
                indent=2,
            )
            + "\n",
        )

    def _spawn(self, name: str, command: list[str], env: dict | None = None) -> None:
        process = subprocess.Popen(
            command,
            cwd=self.source,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.children[name] = process
        identity = process_identity(process.pid)
        if identity is None or identity["pgid"] != process.pid:
            raise RuntimeFailure(
                f"Cannot establish private process ownership for {name}"
            )
        self.identities[name] = identity
        self.pumps.append(LogPump(process.stdout, self.state / "logs" / f"{name}.log"))
        self._record("starting")

    def _wait(self, predicate, name: str, seconds: int = 180) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.shutdown.is_set():
                raise RuntimeFailure("Startup interrupted; retained data was not reset")
            for service, process in self.children.items():
                if process.poll() is not None:
                    raise RuntimeFailure(
                        f"{service} exited during startup; inspect its private log"
                    )
            try:
                if predicate():
                    return
            except (OSError, ssl.SSLError, urllib.error.URLError):
                pass
            self.shutdown.wait(0.3)
        raise RuntimeFailure(
            f"{name} did not become ready within {seconds}s; inspect its private log"
        )

    def _request(self, url: str, *, token: bool = False, tls: bool = False) -> bool:
        handlers: list[urllib.request.BaseHandler] = [urllib.request.ProxyHandler({})]
        if tls:
            handlers.append(
                urllib.request.HTTPSHandler(
                    context=ssl.create_default_context(
                        cafile=str(self.state / "tls/ca.crt")
                    )
                )
            )
        request = urllib.request.Request(url)
        if token:
            request.add_header(
                "Authorization", "Bearer " + self._secret("opencode_password")
            )
        with urllib.request.build_opener(*handlers).open(
            request, timeout=2
        ) as response:
            return response.status == 200

    def _ready(self, config: dict) -> bool:
        return (
            self._request("https://localhost:8001/api/v1/readiness", tls=True)
            and self._request(
                f"https://{config['tailnet_ip']}:8001/api/v1/readiness", tls=True
            )
            and self._request("http://127.0.0.1:4097/health", token=True)
        )

    def run(self) -> None:
        with self._file_lock("supervisor", blocking=False):
            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, lambda _signum, _frame: self.shutdown.set())
            # Preflight failure must leave the previous ownership receipt intact.
            # In particular, do not replace orphan references with this process.
            with self.recovery_lock(), self.lock():
                self._assert_startable()
                self._prepare()
                previous = read_json(self.state / "run/status.json")
                for name in ("metadata", "target"):
                    if (self.state / "postgres" / name / "postmaster.pid").exists():
                        self.recovery_identities[name] = previous.get(
                            "services", {}
                        ).get(name) or previous.get("recovery_services", {}).get(
                            name, {}
                        )
            try:
                with self.recovery_lock(), self.lock():
                    self._assert_startable()
                    config = self.config()
                    for binary in (
                        PYTHON,
                        NODE,
                        PG_BIN / "postgres",
                        Path("/usr/sbin/nginx"),
                    ):
                        if not binary.is_file():
                            raise RuntimeFailure(
                                f"Missing runtime dependency: {binary}; run Android install"
                            )
                    # Bind validation proves VPN startup order without widening interfaces.
                    for address, port in (
                        (config["tailnet_ip"], 8001),
                        ("127.0.0.1", 8001),
                        ("127.0.0.1", 8000),
                        ("127.0.0.1", 4097),
                        ("127.0.0.1", 5433),
                        ("127.0.0.1", 5434),
                    ):
                        with socket.socket() as probe:
                            try:
                                probe.bind((address, port))
                            except OSError as error:
                                raise RuntimeFailure(
                                    f"Cannot bind {address}:{port}; connect Tailscale and check existing listeners"
                                ) from error
                    self._supervising = True
                    self._stage("secrets")
                    self._secrets()
                    self._stage("TLS certificates")
                    self._tls(config)
                    self._stage("runtime configuration")
                    self._write_runtime_config(config)
                    self._cluster("metadata", 5433)
                    self._bootstrap(
                        "metadata",
                        5433,
                        "schemii_metadata_app",
                        "metadata_app_password",
                    )
                    self._cluster("target", 5434)
                    self._bootstrap("target", 5434, "schemii_target", "target_password")
                    environment = self._environment(config)
                    self._stage("Pi sidecar start")
                    self._spawn(
                        "pi",
                        [str(NODE), str(self.source / "ai/prototype/server.js")],
                        environment,
                    )
                    self._wait(
                        lambda: self._request(
                            "http://127.0.0.1:4097/health", token=True
                        ),
                        "pi",
                    )
                    self._stage("application start and metadata migrations")
                    self._spawn(
                        "app",
                        [
                            str(PYTHON),
                            "-m",
                            "uvicorn",
                            "schemii.main:app",
                            "--host",
                            "127.0.0.1",
                            "--port",
                            "8000",
                            "--no-access-log",
                        ],
                        environment,
                    )
                    self._wait(
                        lambda: self._request("http://127.0.0.1:8000/api/v1/readiness"),
                        "app",
                    )
                    self._stage("HTTPS configuration")
                    self._checked(
                        [
                            "/usr/sbin/nginx",
                            "-t",
                            "-c",
                            str(self.state / "run/nginx.conf"),
                        ]
                    )
                    self._stage("HTTPS ingress start")
                    self._spawn(
                        "ingress",
                        [
                            "/usr/sbin/nginx",
                            "-c",
                            str(self.state / "run/nginx.conf"),
                            "-g",
                            "daemon off;",
                        ],
                        environment,
                    )
                    self._wait(lambda: self._ready(config), "HTTPS ingress")
                    self.startup_stage = "ready"
                    self._record("ready")
                while not self.shutdown.wait(0.5):
                    for name, process in self.children.items():
                        if process.poll() is not None:
                            raise RuntimeFailure(
                                f"{name} stopped; inspect its private log before restarting"
                            )
                    changed = False
                    for identity in self.identities.values():
                        members = captured_group_members(identity)
                        if members != identity.get("members"):
                            identity["members"] = members
                            changed = True
                    if changed:
                        self._record("ready")
            except Exception as error:
                message = (
                    str(error)
                    if isinstance(error, RuntimeFailure)
                    else f"Startup failed ({type(error).__name__}); inspect private logs"
                )
                self._record("failed", message)
                raise RuntimeFailure(message) from None
            finally:
                self._stop_children(self.identities)
                for process in self.children.values():
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
                for pump in self.pumps:
                    pump.thread.join(timeout=1)
                old = read_json(self.state / "run/status.json")
                if old.get("state") != "failed":
                    self._record("stopped")

    @staticmethod
    def _stop_helper(identity: object) -> None:
        if not isinstance(identity, dict) or not owns_group(identity):
            return
        try:
            os.killpg(identity["pid"], signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 3
        while owns_group(identity) and time.monotonic() < deadline:
            time.sleep(0.05)
        if owns_group(identity):
            # This is a captured transient helper group, never a database
            # service group. An operational PostgreSQL gets graceful SIGINT.
            try:
                os.killpg(identity["pid"], signal.SIGKILL)
            except ProcessLookupError:
                return

    def _cancel_legacy_initdb_check(self, status: dict, supervisor: dict) -> bool:
        """Cancel only the proven unfinished initdb probe in an older preview.

        The original preview didn't record transient helpers. Reconstruct the
        exact live parent chain, executable inodes and data path before sending
        SIGTERM to the --check leaf. initdb retains ownership of its cleanup.
        A cluster with pg_control or any managed service is never eligible.
        """
        if (
            status.get("state") != "starting"
            or status.get("ready") is True
            or status.get("services")
            or status.get("helper")
        ):
            return False
        initdb_binary, postgres_binary = PG_BIN / "initdb", PG_BIN / "postgres"
        for initdb in direct_children(supervisor):
            arguments = process_arguments(initdb)
            if (
                not matching_executable(initdb, initdb_binary)
                or not arguments
                or arguments[0] != str(initdb_binary)
            ):
                continue
            try:
                directory = Path(arguments[arguments.index("-D") + 1])
                username = arguments[arguments.index("--username") + 1]
                password_file = Path(arguments[arguments.index("--pwfile") + 1])
            except (ValueError, IndexError):
                continue
            names = {
                self.state / "postgres" / name: name for name in ("metadata", "target")
            }
            name = names.get(directory)
            if (
                not name
                or username != "schemii_bootstrap"
                or password_file
                != self.state / "secrets" / f"{name}_bootstrap_password"
            ):
                continue
            if directory.is_symlink() or (directory / "global/pg_control").exists():
                continue
            for shell in direct_children(initdb):
                if not matching_executable(shell, Path("/bin/sh")):
                    continue
                for probe in direct_children(shell):
                    probe_arguments = process_arguments(probe)
                    if (
                        not matching_executable(probe, postgres_binary)
                        or not probe_arguments
                        or probe_arguments[0] != str(postgres_binary)
                        or "--check" not in probe_arguments
                    ):
                        continue
                    chain = (supervisor, initdb, shell, probe)
                    if not verified_parent_chain(chain):
                        continue
                    if (directory / "global/pg_control").exists():
                        continue
                    try:
                        # initdb catches SIGTERM and owns cleanup once its probe
                        # returns. Mark it interrupted first, so it won't start
                        # another memory probe after the check leaf exits.
                        os.kill(initdb["pid"], signal.SIGTERM)
                        if not verified_parent_chain(chain):
                            continue
                        os.kill(probe["pid"], signal.SIGTERM)
                    except ProcessLookupError:
                        continue
                    print(
                        "Requested graceful cancellation of verified unfinished initdb and its --check probe; initdb owns cleanup",
                        file=sys.stderr,
                        flush=True,
                    )
                    return True
        return False

    @staticmethod
    def _stop_children(identities: dict) -> None:
        for name in reversed(SERVICES):
            identity = identities.get(name)
            if not isinstance(identity, dict) or not owns_group(identity):
                continue
            # PostgreSQL fast shutdown rolls back active transactions cleanly.
            sig = signal.SIGINT if name in ("metadata", "target") else signal.SIGTERM
            try:
                os.killpg(identity["pid"], sig)
            except ProcessLookupError:
                continue
            deadline = time.monotonic() + (20 if name in ("metadata", "target") else 10)
            while owns_group(identity) and time.monotonic() < deadline:
                time.sleep(0.1)
            if owns_group(identity):
                raise RuntimeFailure(
                    f"{name} did not stop gracefully; its owned processes and retained data were preserved"
                )

    def stop(self, timeout: float = 90) -> dict:
        # A starting supervisor owns lifecycle/recovery while initializing.
        # Signal that validated owner before asking for either lock. An older
        # preview may remain inside an unbounded helper; never hide this behind
        # a blocked flock or kill an unidentified descendant to force progress.
        status = read_json(self.state / "run/status.json")
        supervisor = status.get("supervisor")
        if isinstance(supervisor, dict) and is_current(supervisor):
            if supervisor["pid"] == os.getpid():
                raise RuntimeFailure(
                    "A supervisor cannot stop itself through the external stop command"
                )
            print(
                f"Requesting graceful Android shutdown (up to {timeout:g}s); retained data will be preserved",
                file=sys.stderr,
                flush=True,
            )
            try:
                os.kill(supervisor["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
            self._cancel_legacy_initdb_check(status, supervisor)
            deadline = time.monotonic() + timeout
            while is_current(supervisor) and time.monotonic() < deadline:
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
            if is_current(supervisor):
                raise RuntimeFailure(
                    "Supervisor did not stop within the graceful deadline; it may be an older preview blocked in a startup helper. Retained state and ownership were preserved; inspect status and the private launcher/setup logs"
                )
        with self.lock():
            status = read_json(self.state / "run/status.json")
            if is_current(status.get("supervisor")):
                raise RuntimeFailure(
                    "Another supervisor started during stop; its state was preserved"
                )
            self._stop_helper(status.get("helper"))
            self._stop_children(status.get("services", {}))
            self.assert_stopped()
            if status:
                status.update(state="stopped", ready=False, stopped_at=time.time())
                write_private(
                    self.state / "run/status.json", json.dumps(status, indent=2) + "\n"
                )
            return {"state": "stopped", "ready": False}

    def assert_stopped(self) -> None:
        status = read_json(self.state / "run/status.json")
        identities = [
            status.get("supervisor"),
            status.get("helper"),
            *status.get("services", {}).values(),
        ]
        if any(is_current(identity) or owns_group(identity) for identity in identities):
            raise RuntimeFailure(
                "The Android stack is active; stop it before changing deployment state"
            )
        for name in ("metadata", "target"):
            if (self.state / "postgres" / name / "postmaster.pid").exists():
                raise RuntimeFailure(
                    f"PostgreSQL {name} has an uncleared postmaster.pid; inspect retained state before recovery"
                )

    def _assert_startable(self) -> None:
        """Admit PostgreSQL's own stale-lock/WAL recovery only with ownership proof."""
        status = read_json(self.state / "run/status.json")
        identities = [
            status.get("supervisor"),
            status.get("helper"),
            *status.get("services", {}).values(),
        ]
        if any(is_current(identity) or owns_group(identity) for identity in identities):
            raise RuntimeFailure(
                "The Android stack or captured orphan services are active; stop it first"
            )
        for name in ("metadata", "target"):
            directory = self.state / "postgres" / name
            pidfile = directory / "postmaster.pid"
            if not pidfile.exists():
                continue
            try:
                lines = pidfile.read_text().splitlines()
                pid = int(lines[0])
                recorded = status.get("services", {}).get(name, {})
                if recorded.get("pid") != pid:
                    recorded = status.get("recovery_services", {}).get(name, {})
                valid = (
                    pid > 1
                    and Path(lines[1]).resolve() == directory.resolve()
                    and recorded.get("pid") == pid
                    and isinstance(recorded.get("start_ticks"), int)
                    and process_identity(pid) is None
                )
            except (OSError, ValueError, IndexError):
                valid = False
            if not valid:
                raise RuntimeFailure(
                    f"Unverified PostgreSQL {name} lock ownership; retained files were not changed"
                )
            # Deliberately do not remove the file. PostgreSQL checks/removes its
            # own stale lock and performs normal WAL recovery during startup.

    def status(self) -> dict:
        value = read_json(self.state / "run/status.json")
        if not value:
            return {
                "schema": 1,
                "state": "stopped",
                "ready": False,
                "services": {},
                "device_acceptance": "pending",
            }
        live = is_current(value.get("supervisor"))
        services = {
            name: {**identity, "running": is_current(identity)}
            for name, identity in value.get("services", {}).items()
        }
        ready = (
            live
            and value.get("ready") is True
            and len(services) == len(SERVICES)
            and all(item["running"] for item in services.values())
        )
        if ready:
            try:
                ready = self._ready(self.config())
            except (OSError, ValueError, RuntimeFailure, urllib.error.URLError):
                ready = False
        return {
            **value,
            "ready": ready,
            "services": services,
            "supervisor_alive": live,
            "state": value.get("state", "stopped")
            if live
            else (
                "failed" if value.get("state") not in ("stopped", None) else "stopped"
            ),
        }

    def doctor(self) -> dict:
        dependencies = {}
        for module in (
            "psycopg",
            "pglast",
            "pydantic",
            "cryptography",
            "fastapi",
            "uvicorn",
        ):
            try:
                imported = importlib.import_module(module)
                dependencies[module] = {
                    "imported": True,
                    "version": getattr(imported, "__version__", "unknown"),
                }
            except ImportError:
                dependencies[module] = {"imported": False}
        config = read_json(self.state / "config.json")
        return {
            "schema": 1,
            "configured": bool(config),
            "runtime": "debian13-pg17",
            "python": sys.version.split()[0],
            "dependencies": dependencies,
            "state_directory": str(self.state),
            "status": self.status(),
            "physical_device_acceptance": "pending",
            "security_profile": "trusted-owner",
            "pending": [
                "authenticated API family acceptance",
                "provider operation with owned credentials",
                "locked-screen and Doze endurance",
                "VPN reconnect",
                "reboot after first unlock",
                "crash recovery and fresh restore on the Moto",
            ],
        }

    def export_ca(self, destination: Path) -> dict:
        if destination.exists():
            raise RuntimeFailure("CA export destination already exists")
        ca = self.state / "tls/ca.crt"
        if not ca.is_file():
            raise RuntimeFailure("Start the configured runtime once to create its CA")
        write_new_private(destination, ca.read_text())
        return {"ca_certificate": str(destination), "private_key_exported": False}

    def credentials(self, destination: Path) -> dict:
        if destination.exists():
            raise RuntimeFailure(
                "Credential destination already exists; preserve the existing private file"
            )
        self.config()
        value = {
            "account_setup_token": self._secret("account_setup_token"),
            "target_connection": {
                "host": "127.0.0.1",
                "port": 5434,
                "database": "schemii",
                "username": "schemii_target",
                "password": self._secret("target_password"),
            },
        }
        write_new_private(destination, json.dumps(value, indent=2) + "\n")
        return {"credentials_file": str(destination), "sensitive": True}

    def _password_reset_store(self):
        import psycopg
        from psycopg.rows import dict_row
        from schemii.common.auth.store import AuthStore

        def connect():
            return psycopg.connect(
                host="127.0.0.1",
                port=5433,
                dbname="schemii",
                user="schemii_metadata_app",
                password=self._secret("metadata_app_password"),
                connect_timeout=5,
                options="-c statement_timeout=30000 -c lock_timeout=5000",
                row_factory=dict_row,
            )

        return AuthStore(connect)

    def reset_password(self, username: str) -> dict:
        """Recover an existing enabled account through this device's terminal."""
        if not sys.stdin.isatty():
            raise RuntimeFailure(
                "Password recovery requires an interactive Termux terminal; "
                "run schemii reset-password USERNAME without pipes or redirects"
            )
        if not re.fullmatch(r"[a-zA-Z0-9_.@-]{3,64}", username):
            raise RuntimeFailure("Enter an existing account username")
        username = username.casefold()
        import psycopg
        from schemii.common.auth.service import password_hash

        store = self._password_reset_store()
        try:
            # Close this read transaction before terminal input or expensive
            # password work; never hold the authentication write lock at a prompt.
            with store.transaction() as state:
                account = next(
                    (
                        user
                        for user in state["users"].values()
                        if user["username"] == username
                    ),
                    None,
                )
                if account is None or account["disabled"]:
                    raise RuntimeFailure(
                        "Password recovery requires an existing enabled account"
                    )
                user_id = account["id"]
            try:
                with warnings.catch_warnings():
                    # getpass must not fall back to echoing a password.
                    warnings.simplefilter("error", getpass.GetPassWarning)
                    password = getpass.getpass("New password: ")
                    confirmation = getpass.getpass("Confirm new password: ")
            except (getpass.GetPassWarning, EOFError, KeyboardInterrupt):
                raise RuntimeFailure(
                    "Password recovery cancelled; hidden terminal input is required"
                ) from None
            if not 12 <= len(password) <= 256:
                raise RuntimeFailure("Use a password between 12 and 256 characters")
            if password != confirmation:
                raise RuntimeFailure("Passwords did not match; account unchanged")
            hashed = password_hash(password)
            password = confirmation = ""
            with store.transaction(write=True) as state:
                account = state["users"].get(user_id)
                if (
                    account is None
                    or account["disabled"]
                    or account["username"] != username
                ):
                    raise RuntimeFailure(
                        "Account changed during recovery; no reset was applied"
                    )
                account["password_hash"] = hashed
                previous_count = len(state["sessions"])
                state["sessions"] = {
                    key: session
                    for key, session in state["sessions"].items()
                    if session["user_id"] != user_id
                }
                revoked = previous_count - len(state["sessions"])
                state["attempts"].pop(username, None)
                state["audit"].append((None, "account.password.reset.local", user_id))
            return {
                "password_reset": True,
                "username": username,
                "sessions_revoked": revoked,
            }
        except psycopg.Error:
            raise RuntimeFailure(
                "Local metadata is unavailable or the reset could not be confirmed. "
                "Run schemii start, then retry password recovery"
            ) from None


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    arguments = list(sys.argv[1:] if argv is None else argv)
    if (
        arguments
        and arguments[0] == "reset-password"
        and not (
            (
                len(arguments) == 2
                and (
                    not arguments[1].startswith("-") or arguments[1] in ("-h", "--help")
                )
            )
            or (len(arguments) == 3 and arguments[1] == "--")
        )
    ):
        print(
            "Usage: schemii reset-password USERNAME; enter the password only at the hidden prompts",
            file=sys.stderr,
        )
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    configure = subcommands.add_parser("configure")
    configure.add_argument("--tailnet-ip", required=True)
    configure.add_argument("--hostname", required=True)
    subcommands.add_parser("reset-password").add_argument("username")
    for command in ("run", "start", "stop", "status", "doctor", "assert-stopped"):
        subcommands.add_parser(command)
    logs = subcommands.add_parser("logs")
    logs.add_argument("service", nargs="?", default="app", choices=(*SERVICES, "setup"))
    for command in ("backup", "restore", "verify-backup", "export-ca", "credentials"):
        subcommands.add_parser(command).add_argument("path", type=Path)
    internal_bootstrap = subcommands.add_parser(
        "internal-bootstrap", help=argparse.SUPPRESS
    )
    internal_bootstrap.add_argument("cluster", choices=("metadata", "target"))
    args = parser.parse_args(arguments)
    runtime = Runtime()
    try:
        if args.command == "configure":
            result = runtime.configure(args.tailnet_ip, args.hostname)
        elif args.command in ("run", "start"):
            runtime.run()
            return 0
        elif args.command == "stop":
            result = runtime.stop()
        elif args.command == "assert-stopped":
            with runtime.recovery_lock(), runtime.lock():
                runtime.assert_stopped()
            result = {"state": "stopped", "ready": False}
        elif args.command == "internal-bootstrap":
            if args.cluster == "metadata":
                runtime._bootstrap_database(
                    "metadata", 5433, "schemii_metadata_app", "metadata_app_password"
                )
            else:
                runtime._bootstrap_database(
                    "target", 5434, "schemii_target", "target_password"
                )
            result = {"bootstrap": args.cluster, "completed": True}
        elif args.command == "status":
            result = runtime.status()
            print(json.dumps(result, indent=2))
            return 0 if result["ready"] else 1
        elif args.command == "doctor":
            result = runtime.doctor()
        elif args.command == "logs":
            path = runtime.state / "logs" / f"{args.service}.log"
            if path.is_file():
                with path.open("rb") as stream:
                    stream.seek(max(0, path.stat().st_size - 32768))
                    print(stream.read().decode("utf8", errors="replace"))
            else:
                print(f"No retained {args.service} log: {path}")
            return 0
        elif args.command == "export-ca":
            result = runtime.export_ca(args.path)
        elif args.command == "credentials":
            result = runtime.credentials(args.path)
        elif args.command == "reset-password":
            result = runtime.reset_password(args.username)
        else:
            import recovery

            operation = {
                "backup": recovery.backup,
                "restore": recovery.restore,
                "verify-backup": lambda _runtime, path: recovery.verify(path),
            }[args.command]
            result = operation(runtime, args.path)
        print(json.dumps(result, indent=2))
        return 0
    except (RuntimeFailure, OSError, ValueError) as error:
        print(f"Android runtime: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
