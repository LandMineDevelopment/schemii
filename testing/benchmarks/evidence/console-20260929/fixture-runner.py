import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time

workspace = Path(__file__).resolve().parents[4]
evidence = workspace / "testing/benchmarks/evidence/console-20260929"
evidence.mkdir(parents=True, exist_ok=True)
report = {
    "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=workspace, text=True
    ).strip(),
    "tracked_source_dirty": bool(
        subprocess.check_output(
            ["git", "diff", "--name-only", "HEAD"], cwd=workspace, text=True
        ).strip()
    ),
    "topology": "Shared host; private disposable PostgreSQL18 loopback TCP/socket; no application deployment",
    "hardware": {
        "cpu": next(
            line.split(":", 1)[1].strip()
            for line in Path("/proc/cpuinfo").read_text().splitlines()
            if line.startswith("model name")
        ),
        "logical_cpus": os.cpu_count(),
        "ram_kib": int(
            next(
                line.split()[1]
                for line in Path("/proc/meminfo").read_text().splitlines()
                if line.startswith("MemTotal:")
            )
        ),
    },
    "attempts": [],
}


def process_identity(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
        return {"pid": pid, "ppid": int(fields[1]), "start_ticks": int(fields[19])}
    except (FileNotFoundError, ProcessLookupError):
        return None


def owned_processes(root_pid):
    candidates = [
        process_identity(int(path.name))
        for path in Path("/proc").iterdir()
        if path.name.isdecimal()
    ]
    selected = {root_pid}
    while True:
        children = {
            item["pid"] for item in candidates if item and item["ppid"] in selected
        }
        if children <= selected:
            return [item for item in candidates if item and item["pid"] in selected]
        selected |= children


def interrupted(signum, frame):
    raise InterruptedError("Owned console fixture interrupted")


signal.signal(signal.SIGTERM, interrupted)
owned = None
try:
    with tempfile.TemporaryDirectory(prefix="schemii-console-pg-") as disposable:
        owned = disposable
        root = Path(owned)
        root.chmod(0o700)
        data = root / "data"
        sockets = root / "socket"
        sockets.mkdir(mode=0o700)
        password_file = root / "password"
        password = secrets.token_urlsafe(32)
        password_file.write_text(password)
        password_file.chmod(0o600)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        cluster = False
        postmaster_pid = None
        try:
            started = time.monotonic()
            subprocess.run(
                [
                    "initdb",
                    "-D",
                    str(data),
                    "-U",
                    "console_fixture",
                    "--pwfile",
                    str(password_file),
                    "--auth=scram-sha-256",
                    "--no-locale",
                    "--encoding=UTF8",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            report["initdb_seconds"] = time.monotonic() - started
            with (data / "postgresql.conf").open("a") as config:
                config.write(
                    f"\nlisten_addresses = '127.0.0.1'\nport = {port}\nunix_socket_directories = '{sockets}'\nmax_connections = 30\n"
                )
            subprocess.run(
                [
                    "pg_ctl",
                    "-D",
                    str(data),
                    "-l",
                    str(root / "postgres.log"),
                    "-w",
                    "start",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            cluster = True
            postmaster_pid = int((data / "postmaster.pid").read_text().splitlines()[0])
            report["fixture"] = {
                "postmaster_pid": postmaster_pid,
                "port": port,
                "owned_directory": owned,
            }
            environment = dict(
                os.environ,
                PYTHONPATH="src",
                SCHEMII_TEST_METADATA_DSN=f"host=127.0.0.1 port={port} dbname=postgres user=console_fixture",
                SCHEMII_TEST_METADATA_PASSWORD=password,
            )

            def run(name, command):
                started = time.monotonic()
                attempt = subprocess.run(
                    command,
                    cwd=workspace,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                elapsed = time.monotonic() - started
                (evidence / f"{name}.log").write_text(attempt.stdout + attempt.stderr)
                report["attempts"].append(
                    {"name": name, "seconds": elapsed, "returncode": attempt.returncode}
                )
                (evidence / "report.json").write_text(json.dumps(report, indent=2))
                print(json.dumps(report["attempts"][-1]), flush=True)
                if attempt.returncode:
                    print(attempt.stdout[-14000:] + attempt.stderr[-3000:], flush=True)
                    raise RuntimeError(f"{name} failed")
                return attempt.stdout

            python = str(workspace / ".venv/bin/python")
            run(
                "real-postgres",
                [
                    python,
                    "-m",
                    "pytest",
                    "-v",
                    "tests/integration/test_raw_session_lifecycle.py",
                    "tests/integration/test_postgres_gateway_execution.py",
                    "tests/integration/test_query_cancellation_postgres.py",
                ],
            )
            output = run(
                "named-narrow-latency",
                [
                    python,
                    "scripts/console_memory_probe.py",
                    "--page-bytes",
                    "65536",
                    "--named-latency",
                ],
            )
            (evidence / "named-narrow-latency.json").write_text(output)
            plans = (
                []
                if "--latency-only" in sys.argv
                else [
                    (
                        "rss-narrow-select",
                        [
                            "--rows",
                            "10000",
                            "50000",
                            "100000",
                            "--width",
                            "128",
                            "--path",
                            "select",
                        ],
                    ),
                    (
                        "rss-wide-select",
                        [
                            "--rows",
                            "1000",
                            "10000",
                            "30000",
                            "--width",
                            "8192",
                            "--path",
                            "select",
                        ],
                    ),
                    (
                        "rss-wide-returning",
                        [
                            "--rows",
                            "1000",
                            "10000",
                            "30000",
                            "--width",
                            "8192",
                            "--path",
                            "returning",
                        ],
                    ),
                    (
                        "rss-fixed-named",
                        [
                            "--rows",
                            "1000",
                            "10000",
                            "100000",
                            "--fixed-scalar",
                            "--mode",
                            "named",
                        ],
                    ),
                    (
                        "rss-wide-named",
                        [
                            "--rows",
                            "1000",
                            "10000",
                            "30000",
                            "--width",
                            "8192",
                            "--mode",
                            "named",
                        ],
                    ),
                    (
                        "rss-single-cell",
                        ["--rows", "1", "--width", "2097152", "--mode", "incremental"],
                    ),
                ]
            )
            for name, arguments in plans:
                output = run(
                    name,
                    [
                        python,
                        "scripts/console_memory_probe.py",
                        "--page-bytes",
                        "65536",
                        *arguments,
                    ],
                )
                (evidence / f"{name}.json").write_text(output)
        finally:
            if cluster:
                identities = owned_processes(postmaster_pid)
                stopped = subprocess.run(
                    ["pg_ctl", "-D", str(data), "-m", "fast", "-w", "stop"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                remaining = [
                    identity
                    for identity in identities
                    if (live := process_identity(identity["pid"])) is not None
                    and live["start_ticks"] == identity["start_ticks"]
                ]
                report["cleanup"] = {
                    "pg_ctl_returncode": stopped.returncode,
                    "owned_process_identities": identities,
                    "owned_processes_remaining": remaining,
                    "postmaster_alive": process_identity(postmaster_pid) is not None,
                    "stop_stdout": stopped.stdout,
                }
                (evidence / "postgres-server.log").write_text(
                    (root / "postgres.log").read_text()
                )
                if stopped.returncode or remaining:
                    report["cleanup"]["failure"] = stopped.stderr[-1000:]
                    raise RuntimeError("Owned PostgreSQL cleanup failed")
            else:
                report["cleanup"] = {"cluster_started": False}
finally:
    report.setdefault("cleanup", {})["owned_directory_exists_after_context_exit"] = (
        Path(owned).exists() if owned else False
    )
    (evidence / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"cleanup": report["cleanup"]}), flush=True)
