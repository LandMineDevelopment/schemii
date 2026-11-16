#!/usr/bin/env python3
"""Real disposable guest probes. Not a production supervisor or ownership acceptance."""

import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request

STATE = Path("/state")
PG = Path("/usr/lib/postgresql/17/bin")
ENDPOINTS = json.loads((STATE / "runtime-interface.json").read_text())["endpoints"]
HOST = ENDPOINTS["host"]
METADATA_PORT = ENDPOINTS["metadata_postgres"]
TARGET_PORT = ENDPOINTS["target_postgres"]
PI_PORT = ENDPOINTS["pi"]
CONTRACT = json.loads(Path("/shared/dev/runtime-contract.json").read_text())
REPORT = {"schema": 1, "stack_acceptance": False, "checks": [], "retained_children": []}
CHILDREN = []
RETAINED = set()
STOP = False


def stopping(signum, frame):
    global STOP
    STOP = True
    raise InterruptedError("Probe cancelled")


def command(args, timeout=60, **kwargs):
    name = "tool-" + Path(str(args[0])).name + "-" + str(len(REPORT["checks"]))
    path = STATE / (name + ".txt")
    log = path.open("ab", buffering=0)
    process = subprocess.Popen(
        [str(x) for x in args],
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        **kwargs,
    )
    entry = (name, process, log)
    CHILDREN.append(entry)
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        stop_child(
            entry
        )  # One bounded graceful stop only, never subprocess.run's implicit SIGKILL.
        raise TimeoutError(name + " deadline; inspect private tool log/state")
    code = process.returncode
    stop_child(entry)
    if code != 0:
        raise RuntimeError(name + " exited " + str(code) + "; inspect private tool log")
    return path.read_text().strip()


def check(name, action):
    started = time.monotonic()
    try:
        observed = action()
        REPORT["checks"].append(
            {
                "name": name,
                "status": "passed",
                "observed": observed,
                "seconds": round(time.monotonic() - started, 3),
            }
        )
        persist()
        return observed
    except BaseException as error:
        location = traceback.extract_tb(error.__traceback__)[-1]
        REPORT["checks"].append(
            {
                "name": name,
                "status": "failed",
                "error": str(error)[:2048],
                "error_type": type(error).__name__,
                "location": {"function": location.name, "line": location.lineno},
                "seconds": round(time.monotonic() - started, 3),
            }
        )
        persist()
        raise


def persist():
    temporary = STATE / "guest-report.tmp"
    with temporary.open("w") as stream:
        json.dump(REPORT, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(STATE / "guest-report.json")


def reserve_ports():
    sockets = []
    try:
        assert HOST == "127.0.0.1", "Diagnostic backends must be loopback-only"
        ports = [
            ENDPOINTS[name]
            for name in ["metadata_postgres", "target_postgres", "pi", "app", "ingress"]
        ]
        assert len(set(ports)) == 5 and all(
            type(p) is int and 1024 < p < 65536 for p in ports
        )
        for port in ports:
            s = socket.socket()
            sockets.append(s)
            s.bind((HOST, port))
        return "all five proposed disposable loopback endpoints available before init"
    finally:
        for s in sockets:
            s.close()


def filesystem_probe():
    directory = STATE / "filesystem-controls"
    directory.mkdir()
    source = directory / "source"
    target = directory / "target"
    source.write_bytes(b"new")
    target.write_bytes(b"old")
    os.replace(source, target)
    assert target.read_bytes() == b"new" and not source.exists()
    link = directory / "link"
    link.symlink_to("target")
    os.rename(link, directory / "moved-link")
    assert (
        os.readlink(directory / "moved-link") == "target"
        and target.read_bytes() == b"new"
    )
    child = directory / "directory"
    child.mkdir()
    previous = Path.cwd()
    try:
        os.chdir(child)
        os.rename(directory / "directory", directory / "renamed-directory")
        assert Path.cwd() == directory / "renamed-directory"
    finally:
        os.chdir(previous)
    return {
        "atomic_replace": True,
        "rename_symlink": True,
        "rename_current_directory": True,
    }


def python_extensions():
    assert sys.version_info[:2] == (3, 13), sys.version
    for name in [
        "psycopg",
        "psycopg_binary",
        "pydantic_core",
        "cryptography",
        "pglast",
        "cffi",
    ]:
        importlib.import_module(name)
    for name, version in CONTRACT["dependencies"]["python"].items():
        assert importlib.metadata.version(name) == version, name
    import psycopg
    from cryptography.fernet import Fernet
    import pglast
    import pydantic_core

    assert psycopg.pq.__impl__ == "binary"
    key = Fernet.generate_key()
    assert (
        Fernet(key).decrypt(Fernet(key).encrypt(b"APK-disposable")) == b"APK-disposable"
    )
    assert pglast.parse_sql("SELECT 1")[0]
    assert pydantic_core.from_json(b'{"native":true}') == {"native": True}
    return {
        "python": sys.version.split()[0],
        "psycopg_impl": psycopg.pq.__impl__,
        "libpq": psycopg.pq.version(),
        "packages": CONTRACT["dependencies"]["python"],
    }


def shared_app_import(password):
    from cryptography.fernet import Fernet

    for name, value in CONTRACT["shared_environment"].items():
        os.environ[name] = str(value)
    for name, value in CONTRACT["platforms"]["android"]["environment"].items():
        os.environ[name] = str(value)
    password_file = STATE / "metadata-password"
    key_file = STATE / "metadata-key"
    password_file.write_text(password)
    key_file.write_bytes(Fernet.generate_key())
    password_file.chmod(0o600)
    key_file.chmod(0o600)
    os.environ.update(
        SCHEMII_ALLOWED_TARGET_HOSTS=HOST,
        SCHEMII_METADATA_DSN=f"host={HOST} port={METADATA_PORT} dbname=schemii user=apk_app",
        SCHEMII_METADATA_PASSWORD_FILE=str(password_file),
        SCHEMII_METADATA_ENCRYPTION_KEY_FILE=str(key_file),
    )
    setup_token = STATE / "setup-token"
    setup_token.write_text(secrets.token_urlsafe(32))
    setup_token.chmod(0o600)
    os.environ["SCHEMII_SETUP_TOKEN_FILE"] = str(setup_token)
    pi_password = STATE / "pi-password"
    pi_password.write_text(secrets.token_urlsafe(32))
    pi_password.chmod(0o600)
    os.environ.update(
        SCHEMII_PI_PROTOTYPE_URL=f"http://{HOST}:{PI_PORT}",
        SCHEMII_PI_PASSWORD_FILE=str(pi_password),
    )
    import schemii.main

    assert schemii.main.runtime_config.deployment_mode.value == "authenticated"
    assert schemii.main.runtime_config.target_egress_mode.value == "internal-only"
    assert schemii.main.app.state.services.metadata.durable
    schemii.main.app.state.services.metadata.check_readiness()
    return {
        "module": "schemii.main",
        "durable_config": True,
        "metadata_port": METADATA_PORT,
        "authenticated": True,
        "lifespan_and_server_acceptance": "pending",
    }


def start_pg(name, port, password, initialize):
    data = STATE / name
    if initialize:
        if data.exists():
            raise RuntimeError("Refusing existing disposable cluster: " + name)
        pwfile = STATE / ("bootstrap-" + name)
        pwfile.write_text(password)
        pwfile.chmod(0o600)
        try:
            command(
                [
                    PG / "initdb",
                    "-D",
                    data,
                    "--encoding=UTF8",
                    "--locale=C.UTF-8",
                    "--auth-local=scram-sha-256",
                    "--auth-host=scram-sha-256",
                    "--username=apk_probe_admin",
                    "--pwfile=" + str(pwfile),
                ],
                timeout=120,
            )
        finally:
            pwfile.unlink(missing_ok=True)
        with (data / "postgresql.conf").open("a") as stream:
            stream.write(
                f"\nlisten_addresses='{HOST}'\nport={port}\nunix_socket_directories=''\n"
                "shared_buffers='16MB'\nmax_connections=24\nwork_mem='2MB'\n"
                "maintenance_work_mem='16MB'\nmax_parallel_workers=0\nmax_worker_processes=2\n"
                "dynamic_shared_memory_type=posix\nfsync=on\nsynchronous_commit=on\n"
                "full_page_writes=on\njit=off\n"
            )
    logfile = (STATE / (name + "-postgres.txt")).open("ab", buffering=0)
    process = subprocess.Popen(
        [str(PG / "postgres"), "-D", str(data)],
        stdout=logfile,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    CHILDREN.append((name, process, logfile))
    import psycopg

    deadline = time.monotonic() + 45
    last = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(name + " PostgreSQL exited; inspect private service log")
        try:
            with psycopg.connect(
                host=HOST,
                port=port,
                dbname="postgres",
                user="apk_probe_admin",
                password=password,
                connect_timeout=2,
                autocommit=True,
            ) as connection:
                connection.execute("SELECT 1").fetchone()
            return process
        except psycopg.OperationalError as error:
            last = type(error).__name__
            time.sleep(0.15)
    raise RuntimeError(name + " PostgreSQL readiness deadline: " + str(last))


def stop_child(entry):
    name, process, log = entry
    if process in RETAINED:
        return False
    if process.poll() is None:
        # Popen retains the unreaped exact child; no PID-only adoption or SIGKILL.
        process.send_signal(signal.SIGINT if "postgres" in name else signal.SIGTERM)
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            RETAINED.add(process)
            REPORT["retained_children"].append(
                {
                    "service": name,
                    "pid": process.pid,
                    "reason": "graceful stop deadline",
                }
            )
            return False
    log.close()
    CHILDREN.remove(entry)
    return True


def sql_probe(password):
    import psycopg

    for name, port in [
        ("metadata-postgres", METADATA_PORT),
        ("target-postgres", TARGET_PORT),
    ]:
        check(
            "initdb-start-" + name,
            lambda n=name, p=port: bool(start_pg(n, p, password, True)),
        )

    def connect(port, user="apk_probe_admin", pwd=password):
        return psycopg.connect(
            host=HOST,
            port=port,
            dbname="postgres",
            user=user,
            password=pwd,
            connect_timeout=3,
            autocommit=True,
        )

    ids = []
    for port in [METADATA_PORT, TARGET_PORT]:
        with connect(port) as c:
            version = c.execute("SHOW server_version_num").fetchone()[0]
            assert int(version) // 10000 == int(
                CONTRACT["dependencies"]["postgresql_major"]
            )
            ids.append(
                c.execute(
                    "SELECT system_identifier::text FROM pg_control_system()"
                ).fetchone()[0]
            )
            assert c.execute("SHOW fsync").fetchone()[0] == "on"
            assert c.execute("SHOW synchronous_commit").fetchone()[0] == "on"
            assert c.execute("SHOW full_page_writes").fetchone()[0] == "on"
            c.execute("CREATE EXTENSION pgcrypto")
            assert (
                c.execute("SELECT length(digest('real','sha256'))").fetchone()[0] == 32
            )
            c.execute("CREATE TABLE apk_durability(value text PRIMARY KEY)")
            c.execute(
                "INSERT INTO apk_durability VALUES (%s)",
                ("retained-after-graceful-restart",),
            )
            c.execute(
                psycopg.sql.SQL(
                    "CREATE ROLE apk_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}"
                ).format(psycopg.sql.Literal(password))
            )
            c.execute("GRANT SELECT ON apk_durability TO apk_app")
            # Same ownership boundary as canonical Android bootstrap: app owns its database, never superuser.
            c.execute("CREATE DATABASE schemii OWNER apk_app")
        with connect(port, "apk_app") as c:
            assert (
                c.execute(
                    "SELECT rolsuper FROM pg_roles WHERE rolname=current_user"
                ).fetchone()[0]
                is False
            )
            assert c.execute("SELECT count(*) FROM apk_durability").fetchone()[0] == 1
    assert ids[0] != ids[1], "Clusters share system identifier"
    with (
        connect(METADATA_PORT) as a,
        connect(METADATA_PORT) as b,
        connect(TARGET_PORT) as target,
    ):
        a.execute("SELECT pg_advisory_lock(264263)")
        assert b.execute("SELECT pg_try_advisory_lock(264263)").fetchone()[0] is False
        assert (
            target.execute("SELECT pg_try_advisory_lock(264263)").fetchone()[0] is True
        )
        a.execute("SELECT pg_advisory_unlock(264263)")
        assert b.execute("SELECT pg_try_advisory_lock(264263)").fetchone()[0] is True
    for entry in list(CHILDREN):
        if not stop_child(entry):
            raise RuntimeError("PostgreSQL retained after stop deadline")
    for name, port in [
        ("metadata-postgres", METADATA_PORT),
        ("target-postgres", TARGET_PORT),
    ]:
        start_pg(name, port, password, False)
        with connect(port) as c:
            assert (
                c.execute("SELECT value FROM apk_durability").fetchone()[0]
                == "retained-after-graceful-restart"
            )
    return {
        "separate_system_identifiers": True,
        "pgcrypto": True,
        "nosuperuser_roles": True,
        "real_advisory_lock_isolation": True,
        "fsync_wal_restart_persistence": True,
        "ports": [METADATA_PORT, TARGET_PORT],
        "locale": "C.UTF-8 diagnostic; not product locale acceptance",
    }


def node_pi_probe():
    node = "/opt/node/bin/node"
    installed_pi = json.loads(
        Path("/node_modules/@earendil-works/pi-ai/package.json").read_text()
    )
    assert installed_pi["version"] == CONTRACT["dependencies"]["pi"]
    version = command([node, "--version"])
    assert version == "v" + CONTRACT["dependencies"]["node"], version
    script = STATE / "pi-probe.mjs"
    script.write_text("""
import {createServer} from 'node:http';
import {createHandler} from '/shared/ai/prototype/server.js';
const server=createServer(createHandler({secret:process.env.APK_PI_PROBE_SECRET}));
server.listen(Number(process.env.APK_PI_PROBE_PORT),process.env.APK_PI_PROBE_HOST);
process.on('SIGTERM',()=>server.close(()=>process.exit(0)));
""")
    token = (STATE / "pi-password").read_text().strip()
    env = os.environ.copy()
    env["APK_PI_PROBE_SECRET"] = token
    env["APK_PI_PROBE_HOST"] = HOST
    env["APK_PI_PROBE_PORT"] = str(PI_PORT)
    log = (STATE / "pi-private.txt").open("ab", buffering=0)
    process = subprocess.Popen(
        [node, str(script)],
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    entry = ("pi", process, log)
    CHILDREN.append(entry)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Real Pi exited; inspect private Pi log")
        try:
            request = urllib.request.Request(
                f"http://{HOST}:{PI_PORT}/health",
                headers={"Authorization": "Bearer " + token},
            )
            with urllib.request.urlopen(request, timeout=2) as response:
                assert json.load(response)["status"] == "ok"
            break
        except OSError:
            time.sleep(0.15)
    else:
        raise RuntimeError("Real Pi health deadline")
    request = urllib.request.Request(
        f"http://{HOST}:{PI_PORT}/models", headers={"Authorization": "Bearer " + token}
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        models = json.load(response)
    shared = importlib.import_module("schemii.main")
    assert shared.app.state.pi_client.call("/health")["status"] == "ok"
    check("shared-http-lifespan", shared_http_probe)
    if not stop_child(entry):
        raise RuntimeError("Pi graceful stop deadline")
    assert process.returncode == 0, "Pi did not exit cleanly"
    return {
        "node": version,
        "pi": CONTRACT["dependencies"]["pi"],
        "canonical_handler_health": True,
        "canonical_models_endpoint": isinstance(models, (dict, list)),
        "canonical_python_pi_transport": True,
        "port": PI_PORT,
        "graceful_stop": True,
    }


def shared_http_probe():
    """Exercise the canonical ASGI lifecycle, without replacing its implementation."""
    port = ENDPOINTS["app"]
    origin = f"http://{HOST}:{port}"
    logpath = STATE / "http-private.txt"
    log = logpath.open("ab", buffering=0)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "schemii.main:app",
            "--host",
            HOST,
            "--port",
            str(port),
            "--lifespan",
            "on",
            "--no-access-log",
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    entry = ("http", process, log)
    CHILDREN.append(entry)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Shared HTTP app exited; inspect private HTTP log")
        try:
            with urllib.request.urlopen(
                origin + "/api/v1/readiness", timeout=2
            ) as response:
                ready = json.load(response)
            assert ready == {
                "ready": True,
                "metadata": "postgresql",
                "persistence": "durable",
            }, ready
            break
        except OSError:
            time.sleep(0.15)
    else:
        raise RuntimeError("Shared HTTP readiness deadline")
    with urllib.request.urlopen(origin + "/api/v1/auth/status", timeout=5) as response:
        assert json.load(response) == {
            "enabled": True,
            "setup_required": True,
            "authenticated": False,
        }
        assert response.headers["Cache-Control"] == "no-store"
    try:
        urllib.request.urlopen(origin + "/api/v1/session", timeout=5)
    except urllib.error.HTTPError as error:
        assert error.code == 401, error.code
        error.close()
    else:
        raise AssertionError("Unauthenticated session request was admitted")
    if not stop_child(entry):
        raise RuntimeError("Shared HTTP graceful lifespan stop deadline")
    lifecycle = logpath.read_text()
    termination = {
        "exit": process.returncode,
        "startup_complete": "Application startup complete." in lifecycle,
        "shutdown_complete": "Application shutdown complete." in lifecycle,
        "uvicorn": importlib.metadata.version("uvicorn"),
    }
    REPORT["http_lifecycle"] = termination
    persist()
    # The pinned Uvicorn Server.capture_signals restores the previous handler
    # and re-raises SIGTERM *after* lifespan shutdown. Signal status alone cannot
    # prove graceful cleanup: require both actual lifecycle markers as well.
    assert process.returncode in (0, -signal.SIGTERM), termination
    assert termination["startup_complete"] and termination["shutdown_complete"], (
        termination
    )
    with socket.socket() as control:
        control.settimeout(2)
        assert control.connect_ex((HOST, port)) != 0, (
            "HTTP listener retained after stop"
        )
    return {
        "port": port,
        "canonical_readiness": ready,
        "authentication_enabled": True,
        "anonymous_session_denied": True,
        "lifespan_start_and_graceful_shutdown": True,
        "termination": termination,
        "listener_closed": True,
        "external_https_acceptance": "pending",
    }


def main():
    os.umask(0o077)
    signal.signal(signal.SIGTERM, stopping)
    signal.signal(signal.SIGINT, stopping)
    STATE.mkdir(exist_ok=True)
    REPORT["run_id"] = os.environ["APK_PROBE_RUN_ID"]
    REPORT["source_commit"] = os.environ["APK_SOURCE_COMMIT"]
    REPORT["native_provenance_sha256"] = os.environ["APK_NATIVE_PROVENANCE_SHA256"]
    try:
        check("coexistence-ports-before-init", reserve_ports)
        check("filesystem-atomic-rename-semantics", filesystem_probe)
        check("python-native-extensions", python_extensions)
        password = secrets.token_urlsafe(32)
        check("postgresql-two-clusters", lambda: sql_probe(password))
        check(
            "shared-app-authenticated-durable-import",
            lambda: shared_app_import(password),
        )
        check("node-pi-canonical-source", node_pi_probe)
    except BaseException:
        REPORT["outcome"] = "failed; inspect original private report/logs"
    else:
        REPORT["outcome"] = (
            "guest probes passed; whole-stack/owner-loss/Moto acceptance pending"
        )
    finally:
        # Preserve database state and any unresponsive masters; never kill them away.
        for entry in list(reversed(CHILDREN)):
            stop_child(entry)
        persist()
    print(json.dumps(REPORT))
    return int(
        any(c["status"] != "passed" for c in REPORT["checks"])
        or bool(REPORT["retained_children"])
    )


if __name__ == "__main__":
    sys.exit(main())
