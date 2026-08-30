#!/usr/bin/env python3
"""Run an isolated, synthetic Schemii and Schemer development environment."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "schemii" / "dev-test"
ROOT = Path(os.environ.get("SCHEMII_DEV_ROOT", DEFAULT_ROOT)).expanduser().resolve()
MARKER = ROOT / "synthetic-environment.json"
SEED = REPOSITORY / "examples/postgres/001_bookstore.sql"
PORTS = {
    "postgres": int(os.environ.get("SCHEMII_DEV_POSTGRES_PORT", "55432")),
    "schemii": int(os.environ.get("SCHEMII_DEV_SCHEMII_PORT", "18080")),
    "schemer": int(os.environ.get("SCHEMII_DEV_SCHEMER_PORT", "18081")),
}
ADMIN_ROLE = "schemii_metadata_bootstrap"
TARGET_ROLE = "schemii"
TARGET_DATABASE = "schemii"


def fail(message: str) -> None:
    raise SystemExit(message)


def validate_configuration() -> None:
    if ROOT == REPOSITORY or REPOSITORY in ROOT.parents:
        fail("SCHEMII_DEV_ROOT must be outside the repository so development data cannot enter a release.")
    for name, port in PORTS.items():
        if not 1024 <= port <= 65535:
            fail(f"{name} development port must be between 1024 and 65535")
    if len(set(PORTS.values())) != len(PORTS):
        fail("Development PostgreSQL, Schemii, and Schemer ports must be distinct")
    for command in ("initdb", "pg_ctl", "psql", "createdb"):
        if shutil.which(command) is None:
            fail(f"{command} is required for the native development environment")


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def write_private(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)


def marker_payload() -> dict:
    return {
        "version": 1,
        "purpose": "synthetic-development-and-test-only",
        "seed": "examples/postgres/001_bookstore.sql",
        "repository": str(REPOSITORY),
    }


def ensure_root() -> None:
    ensure_directory(ROOT)
    if MARKER.exists():
        try:
            current = json.loads(MARKER.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            fail(f"Development marker is unreadable: {error}")
        if current != marker_payload():
            fail(f"Refusing to use unrecognized development data directory: {ROOT}")
    else:
        write_private(MARKER, json.dumps(marker_payload(), indent=2) + "\n")
    for name in ("config", "schemas", "dashboards", "credentials", "logs", "run"):
        ensure_directory(ROOT / name)


def ensure_venv() -> Path:
    venv = ROOT / "venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    requirements = (REPOSITORY / "requirements.txt").read_bytes()
    fingerprint = hashlib.sha256(requirements + sys.version.encode()).hexdigest()
    marker = venv / ".requirements-sha256"
    if not python.exists():
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    if not marker.exists() or marker.read_text(encoding="utf-8").strip() != fingerprint:
        subprocess.run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(REPOSITORY / "requirements.txt")],
            check=True,
        )
        write_private(marker, fingerprint + "\n")
    return python


def secret_path(name: str) -> Path:
    return ROOT / "credentials" / name


def ensure_secrets() -> None:
    for name in (
        "metadata_bootstrap_password", "metadata_migration_password",
        "metadata_schemii_password", "metadata_schemer_password", "target_password",
    ):
        path = secret_path(name)
        if not path.exists():
            write_private(path, secrets.token_urlsafe(32) + "\n")


def read_secret(name: str) -> str:
    value = secret_path(name).read_text(encoding="utf-8").strip()
    if not value or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for character in value):
        fail(f"Development secret {name} is malformed")
    return value


def pgdata() -> Path:
    return ROOT / "postgres"


def pg_environment(database: str = "postgres", user: str = ADMIN_ROLE) -> dict[str, str]:
    return {
        **os.environ,
        "PGHOST": str(ROOT / "run"),
        "PGPORT": str(PORTS["postgres"]),
        "PGDATABASE": database,
        "PGUSER": user,
    }


def postgres_running() -> bool:
    if not pgdata().exists():
        return False
    return subprocess.run(["pg_ctl", "-D", str(pgdata()), "status"], capture_output=True).returncode == 0


def initialize_cluster() -> None:
    if (pgdata() / "PG_VERSION").exists():
        return
    admin_password = secret_path("metadata_bootstrap_password")
    subprocess.run([
        "initdb", "-D", str(pgdata()), "-U", ADMIN_ROLE,
        "--auth-local=trust", "--auth-host=scram-sha-256", "--encoding=UTF8",
        "--no-locale", f"--pwfile={admin_password}",
    ], check=True)


def start_postgres() -> None:
    if postgres_running():
        return
    subprocess.run([
        "pg_ctl", "-D", str(pgdata()), "-l", str(ROOT / "logs/postgres.log"),
        "-o", f"-h 127.0.0.1 -p {PORTS['postgres']} -k {ROOT / 'run'}", "start", "-w",
    ], check=True)


def psql(sql: str, *, database: str = "postgres", user: str = ADMIN_ROLE, variables: dict[str, str] | None = None) -> None:
    command = ["psql", "--set", "ON_ERROR_STOP=1"]
    for name, value in (variables or {}).items():
        command.extend(["--set", f"{name}={value}"])
    subprocess.run(command, env=pg_environment(database, user), input=sql, text=True, check=True)


def initialize_databases(python: Path) -> None:
    initialized = ROOT / "run/initialized-v1"
    if not initialized.exists():
        subprocess.run(["createdb", "schemii_metadata"], env=pg_environment(), check=True)
        roles_env = {
            **pg_environment("schemii_metadata"),
            "POSTGRES_USER": ADMIN_ROLE,
            "POSTGRES_DB": "schemii_metadata",
            "SCHEMII_METADATA_SECRET_DIR": str(ROOT / "credentials"),
        }
        subprocess.run(["sh", str(REPOSITORY / "docker/metadata/001_roles.sh")], env=roles_env, check=True)
        psql(
            "CREATE ROLE schemii LOGIN PASSWORD :'target_password';\n",
            variables={"target_password": read_secret("target_password")},
        )
        subprocess.run(["createdb", "--owner", TARGET_ROLE, TARGET_DATABASE], env=pg_environment(), check=True)
        subprocess.run(
            ["psql", "--set", "ON_ERROR_STOP=1", "--file", str(SEED)],
            env=pg_environment(TARGET_DATABASE, TARGET_ROLE), check=True,
        )
        subprocess.run(
            ["psql", "--set", "ON_ERROR_STOP=1", "--file", str(REPOSITORY / "docker/metadata/002_rotation_function.sql")],
            env=pg_environment("schemii_metadata"), check=True,
        )
        write_private(initialized, "initialized\n")

    common = app_environment(python)
    migrate_env = {
        **common,
        "SCHEMII_METADATA_DSN": metadata_dsn("schemii_metadata_migration", owner=True),
        "SCHEMII_METADATA_APPLICATION_NAME": "schemii-dev-migrate",
        "SCHEMII_METADATA_PASSWORD_FILE": str(secret_path("metadata_migration_password")),
    }
    subprocess.run([str(python), "-m", "schemii.metadata_migrate"], env=migrate_env, check=True)
    profile_env = {
        **common,
        "SCHEMII_CONFIG_DIR": str(ROOT / "config"),
        "SCHEMII_EXAMPLE_POSTGRES_HOST": "127.0.0.1",
        "SCHEMII_EXAMPLE_POSTGRES_PORT": str(PORTS["postgres"]),
        "SCHEMII_EXAMPLE_POSTGRES_DB": TARGET_DATABASE,
        "SCHEMII_EXAMPLE_POSTGRES_USER": TARGET_ROLE,
        "SCHEMII_EXAMPLE_POSTGRES_PASSWORD": read_secret("target_password"),
    }
    subprocess.run([str(python), "-m", "schemii.example_profile_init"], env=profile_env, check=True)


def metadata_dsn(role: str, *, owner: bool = False) -> str:
    options = " options='-c role=schemii_metadata_owner'" if owner else ""
    return f"host=127.0.0.1 port={PORTS['postgres']} dbname=schemii_metadata user={role}{options}"


def app_environment(python: Path) -> dict[str, str]:
    return {
        **os.environ,
        "PYTHONPATH": str(REPOSITORY / "src"),
        "PYTHONUNBUFFERED": "1",
        "SCHEMII_DEV_PYTHON": str(python),
    }


def pid_path(name: str) -> Path:
    return ROOT / "run" / f"{name}.pid"


def process_pid(name: str) -> int | None:
    path = pid_path(name)
    if not path.exists():
        return None
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        path.unlink(missing_ok=True)
        return None


def port_available(port: int) -> bool:
    with socket.socket() as current:
        try:
            current.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def start_app(name: str, python: Path) -> None:
    if process_pid(name) is not None:
        return
    port = PORTS[name]
    if not port_available(port):
        fail(f"Development port {port} is already in use and is not owned by this environment")
    environment = {
        **app_environment(python),
        "SCHEMII_CONFIG_DIR": str(ROOT / "config"),
        "SCHEMII_SCHEMA_DIR": str(ROOT / "schemas"),
        "SCHEMER_DASHBOARD_DIR": str(ROOT / "dashboards"),
        "SCHEMII_EXAMPLES": "all",
        "SCHEMII_EXAMPLE_POSTGRES_HOST": "127.0.0.1",
        "SCHEMII_EXAMPLE_POSTGRES_PORT": str(PORTS["postgres"]),
        "SCHEMII_EXAMPLE_POSTGRES_DB": TARGET_DATABASE,
        "SCHEMII_EXAMPLE_POSTGRES_USER": TARGET_ROLE,
        "SCHEMII_EXAMPLE_POSTGRES_PASSWORD": read_secret("target_password"),
    }
    if name == "schemii":
        environment.update({
            "SCHEMII_HOST": "127.0.0.1", "SCHEMII_PORT": str(port),
            "SCHEMII_METADATA_DSN": metadata_dsn("schemii_metadata_schemii"),
            "SCHEMII_METADATA_PASSWORD_FILE": str(secret_path("metadata_schemii_password")),
        })
        module = "schemii.server"
    else:
        environment.update({
            "SCHEMER_HOST": "127.0.0.1", "SCHEMER_PORT": str(port),
            "SCHEMER_CONFIG_DIR": str(ROOT / "config"),
            "SCHEMII_METADATA_DSN": metadata_dsn("schemii_metadata_schemer"),
            "SCHEMII_METADATA_PASSWORD_FILE": str(secret_path("metadata_schemer_password")),
        })
        module = "schemii.schemer_server"
    log = (ROOT / "logs" / f"{name}.log").open("ab", buffering=0)
    process = subprocess.Popen(
        [str(python), "-m", module], env=environment, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    write_private(pid_path(name), f"{process.pid}\n")


def wait_ready(name: str) -> None:
    url = f"http://127.0.0.1:{PORTS[name]}/api/readiness"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process_pid(name) is None:
            fail(f"{name} exited during startup; review {ROOT / 'logs' / f'{name}.log'}")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.25)
    fail(f"{name} did not become ready; review {ROOT / 'logs' / f'{name}.log'}")


def start() -> None:
    ensure_root()
    ensure_secrets()
    python = ensure_venv()
    initialize_cluster()
    start_postgres()
    initialize_databases(python)
    start_app("schemii", python)
    start_app("schemer", python)
    wait_ready("schemii")
    wait_ready("schemer")
    print(f"Schemii dev/test: http://127.0.0.1:{PORTS['schemii']}/")
    print(f"Schemer dev/test: http://127.0.0.1:{PORTS['schemer']}/")
    print(f"Synthetic data root: {ROOT}")


def stop_process(name: str) -> None:
    pid = process_pid(name)
    if pid is None:
        return
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.1)
    else:
        fail(f"{name} did not stop cleanly; refusing to force termination")
    pid_path(name).unlink(missing_ok=True)


def stop() -> None:
    if not MARKER.exists():
        print("Synthetic dev/test environment is not initialized.")
        return
    stop_process("schemer")
    stop_process("schemii")
    if postgres_running():
        subprocess.run(["pg_ctl", "-D", str(pgdata()), "stop", "-m", "fast", "-w"], check=True)
    print("Synthetic dev/test environment stopped.")


def reset() -> None:
    if not MARKER.exists():
        fail(f"Refusing to reset unmarked directory: {ROOT}")
    expected = marker_payload()
    if json.loads(MARKER.read_text(encoding="utf-8")) != expected:
        fail(f"Refusing to reset unrecognized directory: {ROOT}")
    stop()
    shutil.rmtree(ROOT)
    print(f"Synthetic dev/test data removed: {ROOT}")


def status() -> None:
    print(f"Data root: {ROOT}")
    print(f"PostgreSQL: {'running' if postgres_running() else 'stopped'}")
    for name in ("schemii", "schemer"):
        state = "running" if process_pid(name) is not None else "stopped"
        print(f"{name.capitalize()}: {state} · http://127.0.0.1:{PORTS[name]}/")


def main() -> None:
    validate_configuration()
    action = sys.argv[1] if len(sys.argv) == 2 else ""
    if action == "start":
        start()
    elif action == "stop":
        stop()
    elif action == "reset":
        reset()
    elif action == "status":
        status()
    else:
        fail("Usage: python3 scripts/dev-test.py start|stop|status|reset")


if __name__ == "__main__":
    main()
