"""Actual ownership gauges, authorization and the launcher's export boundary."""

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from schemii.common.api.routes import router
from schemii.common.auth.middleware import AuthenticationMiddleware
from schemii.common.auth.routes import router as auth_router
from schemii.common.auth.service import AuthService
from schemii.common.connections.models import ResolvedPostgresConnection
from schemii.common.postgres.gateway import PsycopgPostgresGateway
from schemii.common.postgres.errors import PostgresConnectionError
from schemii.common.postgres.console.gateway import PsycopgConsoleReadSession
from schemii.schemii.console.raw_session import RawSessionService
from schemii.schemii.console.service import ConsoleService

ROOT = Path(__file__).resolve().parents[1]


def target():
    return ResolvedPostgresConnection(
        id="pg_" + "a" * 32,
        revision=1,
        name="Contract",
        owner_id="private-owner",
        host="unused",
        port=5432,
        database="unused",
        username="unused",
        ssl_mode="require",
        connect_timeout=1,
    )


def test_source_snapshot_distinguishes_connecting_permit_and_live_native_handle():
    samples = []
    native = SimpleNamespace(close=lambda: None)

    def connect(**_):
        samples.append(gateway.observation_snapshot()["ordinary"])
        return native

    gateway = PsycopgPostgresGateway(connect_factory=connect)
    connection = gateway._connect(target(), retained=True)
    assert samples[0]["permits"] == 1 and samples[0]["connections"] == 0
    live = gateway.observation_snapshot()["ordinary"]
    assert (live["ordinaryPermits"], live["retainedPermits"], live["connections"]) == (
        0,
        1,
        1,
    )
    connection.close()
    connection.close()
    assert gateway.observation_snapshot()["ordinary"]["permits"] == 0
    assert gateway.observation_snapshot()["ordinary"]["connections"] == 0
    assert "private-owner" not in json.dumps(gateway.observation_snapshot())


def test_failed_dial_releases_permit_and_does_not_claim_an_established_connection():
    def fail(**_):
        raise RuntimeError("private failure")

    gateway = PsycopgPostgresGateway(connect_factory=fail)
    with pytest.raises(PostgresConnectionError):
        gateway._connect(target())
    snapshot = gateway.observation_snapshot()["ordinary"]
    assert snapshot["permits"] == snapshot["connections"] == 0


def cursor_session():
    session = PsycopgConsoleReadSession.__new__(PsycopgConsoleReadSession)
    session._lock = threading.RLock()
    session._closed = False
    session._database_connection = SimpleNamespace(close=lambda: None)
    session._readers = {
        0: SimpleNamespace(
            cursor=SimpleNamespace(closed=False, close=lambda: None),
            export_cursor=SimpleNamespace(closed=False, close=lambda: None),
        ),
        1: SimpleNamespace(
            cursor=SimpleNamespace(closed=True, close=lambda: None), export_cursor=None
        ),
    }
    return session


def test_native_cursor_snapshot_counts_owned_open_cursors_and_shutdown():
    session = cursor_session()
    assert session.observation_snapshot() == {"status": "available", "nativeCursors": 2}
    session.close()
    assert session.observation_snapshot() == {"status": "available", "nativeCursors": 0}


def test_native_cursor_snapshot_never_waits_for_a_source_operation():
    session = cursor_session()
    entered, release = threading.Event(), threading.Event()

    def busy():
        with session._lock:
            entered.set()
            assert release.wait(2)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(busy)
        assert entered.wait(1)
        try:
            started = time.monotonic()
            assert session.observation_snapshot() == {"status": "busy"}
            assert time.monotonic() - started < 0.1
        finally:
            release.set()
        pending.result(timeout=1)


def console_owner():
    console = ConsoleService.__new__(ConsoleService)
    console._transient_results_lock = threading.RLock()
    console._active_targets_lock = threading.RLock()
    console._active_transactions_lock = threading.RLock()
    console._active_read_sessions = OrderedDict(
        {"private-id": SimpleNamespace(postgres=cursor_session())}
    )
    console._result_cursors = {"private-token": object()}
    console._transient_results = {"private-result": object()}
    console._active_targets = {"private-target": object()}
    console._active_transactions = {"private-transaction": object()}
    return console


def test_console_tokens_are_distinct_from_native_cursors_and_unavailable_adapter():
    console = console_owner()
    counts = console.observation_snapshot()
    assert counts["readSessionHandles"] == counts["resultPageTokens"] == 1
    assert counts["nativeCursors"] == 2
    assert "private" not in json.dumps(counts)
    console._active_read_sessions["private-id"].postgres = object()
    counts = console.observation_snapshot()
    assert counts["nativeCursorStatus"] == "unavailable_or_busy"
    assert "nativeCursors" not in counts


def test_raw_snapshot_reads_registered_and_opening_handles_without_source_queries():
    owner = RawSessionService(None, None, None)
    owner.sessions["secret-session"] = object()
    owner.opening.add(("secret-owner", "secret-console"))
    assert owner.observation_snapshot() == {
        "registryStatus": "available",
        "registeredSessions": 1,
        "openingSessions": 1,
    }


def test_raw_snapshot_does_not_wait_for_late_native_close_during_shutdown(monkeypatch):
    from contextlib import nullcontext

    from schemii.common.api.errors import ApiProblem
    from schemii.schemii.console import raw_session as module

    target = SimpleNamespace(
        connection_id="private-profile",
        connection_owner_id="private-owner",
        connection_revision=1,
        database="unused",
        namespace="public",
    )
    console = SimpleNamespace(
        _workspace_target=lambda *_: (None, target),
        _validate_settings_revision=lambda *_: None,
        _maximum_live_read_sessions=2,
        _maximum_live_read_sessions_per_identity=2,
        settings=lambda _: SimpleNamespace(row_page_size=100),
        _page_memory_bytes=4096,
    )
    resolved = SimpleNamespace(owner_id="private-owner", revision=1, database="unused")
    connecting, finish_connect, closing, finish_close, shutting_down = (
        threading.Event() for _ in range(5)
    )
    closed = []

    def close():
        closing.set()
        assert finish_close.wait(3)
        closed.append(True)

    native = SimpleNamespace(close=close)

    def connect(*_, **__):
        connecting.set()
        assert finish_connect.wait(3)
        return native

    owner = RawSessionService(
        console,
        SimpleNamespace(use=lambda *_: nullcontext(resolved)),
        SimpleNamespace(_connect=connect),
    )
    monkeypatch.setattr(module, "RawSession", lambda *_, **__: native)
    wait_for = owner.opening_finished.wait_for

    def wait_for_opening(predicate):
        shutting_down.set()
        return wait_for(predicate)

    monkeypatch.setattr(owner.opening_finished, "wait_for", wait_for_opening)
    body = SimpleNamespace(
        console_id="private-console",
        expected_workspace_revision=1,
        expected_settings_revision=1,
    )
    with ThreadPoolExecutor(max_workers=3) as pool:
        opening = pool.submit(owner.create, "private-owner", "private-workspace", body)
        try:
            assert connecting.wait(1)
            shutdown = pool.submit(owner.close)
            assert shutting_down.wait(1)
            finish_connect.set()
            # create retires its late connection under the registry lock.
            assert closing.wait(1)
            snapshot = pool.submit(owner.observation_snapshot)
            assert snapshot.result(timeout=0.5) == {"registryStatus": "busy"}
            assert owner.sessions == {}
            assert owner.opening == {("private-owner", "private-console")}
            assert not opening.done() and not shutdown.done()
            assert closed == []
        finally:
            finish_connect.set()
            finish_close.set()
        with pytest.raises(ApiProblem) as caught:
            opening.result(timeout=1)
        assert caught.value.status_code == 503
        shutdown.result(timeout=1)
    assert closed == [True]
    assert owner.observation_snapshot() == {
        "registryStatus": "available",
        "registeredSessions": 0,
        "openingSessions": 0,
    }


@dataclass
class MetadataCounts:
    active: int = 2
    rejected: int = 3


def test_runtime_snapshot_preserves_busy_raw_counts_and_other_known_domains():
    from schemii.common.auth.routes import runtime_observation

    raw = RawSessionService(None, None, None)
    raw.sessions["private-session"] = object()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                services=SimpleNamespace(
                    postgres=PsycopgPostgresGateway(),
                    console=console_owner(),
                    metadata=SimpleNamespace(
                        connection_factory=SimpleNamespace(
                            admission_snapshot=lambda: MetadataCounts()
                        )
                    ),
                ),
                raw_console=raw,
            )
        )
    )
    entered, release = threading.Event(), threading.Event()

    def busy():
        with raw.lock:
            entered.set()
            assert release.wait(3)

    with ThreadPoolExecutor(max_workers=2) as pool:
        holder = pool.submit(busy)
        try:
            assert entered.wait(1)
            result = pool.submit(runtime_observation, request, actor="test-admin")
            document = result.result(timeout=0.5)
            assert document["sources"]["raw"] == {
                "status": "available",
                "counts": {"registryStatus": "busy"},
            }
            assert document["sources"]["console"]["counts"]["nativeCursors"] == 2
            assert document["sources"]["metadata"]["counts"] == {
                "active": 2,
                "rejected": 3,
            }
            assert "private-" not in json.dumps(document)
            assert len(raw.sessions) == 1
        finally:
            release.set()
        holder.result(timeout=1)


def test_runtime_snapshot_requires_authenticated_administrator_and_discloses_no_handles():
    application = FastAPI()
    application.state.auth = AuthService(enabled=True, setup_token="contract-setup")
    application.state.services = SimpleNamespace(
        postgres=PsycopgPostgresGateway(),
        console=console_owner(),
        metadata=SimpleNamespace(
            connection_factory=SimpleNamespace(
                admission_snapshot=lambda: MetadataCounts()
            )
        ),
    )
    application.state.raw_console = RawSessionService(None, None, None)
    application.include_router(auth_router)
    application.include_router(router)
    application.add_middleware(AuthenticationMiddleware)
    with TestClient(
        application,
        base_url="https://localhost:8001",
        headers={"Origin": "https://localhost:8001"},
    ) as client:
        path = "/api/v1/admin/runtime-observation"
        assert client.get(path).status_code == 401
        assert (
            client.post(
                "/api/v1/auth/setup",
                json={
                    "username": "admin",
                    "display_name": "Admin",
                    "password": "contract-password-123",
                    "setup_token": "contract-setup",
                },
            ).status_code
            == 200
        )
        result = client.get(path)
        assert (
            result.status_code == 200 and result.headers["Cache-Control"] == "no-store"
        )
        document = result.json()
        assert document["scope"] == "process_aggregate"
        assert document["sources"]["metadata"]["counts"] == {"active": 2, "rejected": 3}
        assert "private-" not in result.text
        assert document["databaseVisibility"]["status"] == "not_sampled"
        assert document["runOwnedBackends"]["status"] == "unavailable"
        assert (
            client.post(
                "/api/v1/admin/accounts",
                json={
                    "username": "viewer",
                    "display_name": "Viewer",
                    "password": "viewer-password-123",
                },
            ).status_code
            == 201
        )
        assert (
            client.post(
                "/api/v1/auth/login",
                json={"username": "viewer", "password": "viewer-password-123"},
            ).status_code
            == 200
        )
        assert client.get(path).status_code == 403


def test_memory_and_missing_adapters_are_unavailable_even_for_an_authorized_snapshot():
    from schemii.common.auth.routes import runtime_observation

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                services=SimpleNamespace(
                    postgres=object(),
                    console=object(),
                    metadata=SimpleNamespace(connection_factory=None),
                ),
                raw_console=None,
            )
        )
    )
    output = runtime_observation(request, actor="test-admin")
    assert all(
        source["status"] == "unavailable" for source in output["sources"].values()
    )
    assert all("counts" not in source for source in output["sources"].values())


def launcher_helpers():
    spec = importlib.util.spec_from_file_location(
        "load_observer_launcher_contract_helpers", ROOT / "tests/test_startup.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("healthy", [True, False])
def test_launcher_exports_only_after_health_and_under_existing_lease(tmp_path, healthy):
    helpers = launcher_helpers()
    worktree = tmp_path / "worktree"
    helpers._copy_launcher_worktree(worktree)
    commands = tmp_path / "commands.log"
    environment = helpers._mock_launcher_environment(
        tmp_path,
        [worktree],
        commands,
        tls_directory=tmp_path / "tls",
        lock_file=tmp_path / "start.lock",
        qa_state_directory=tmp_path / "qa",
    )
    destination = tmp_path / "private/receipt.json"
    environment["SCHEMII_RUNTIME_RECEIPT"] = str(destination)
    executable = tmp_path / "mock-bin/docker"
    executable.write_text("""#!/bin/sh
printf '%s\\n' "$*" >> "$COMMAND_LOG"
case "$*" in
  *'up --detach --remove-orphans --wait'*) exit "${EXPORT_HEALTH_RESULT:-0}" ;;
  *'ps --quiet --filter label=com.docker.compose.project=schemii-test --filter status=running'*) printf 'abc123\\n' ;;
  'inspect --format '*) printf '{"project":"schemii-test","service":"schemii","pid":123}\\n' ;;
esac
""")
    executable.chmod(0o755)
    node = tmp_path / "mock-bin/node"
    node.write_text("""#!/usr/bin/env python3
import json, os, pathlib, sys
assert os.environ["SCHEMII_QA_LEASE_MODE"] == "exclusive"
assert os.readlink("/proc/self/fd/3").endswith("qa-deployment.lock")
records = [json.loads(line) for line in sys.stdin if line.strip()]
assert records == [{"project": "schemii-test", "service": "schemii", "pid": 123}]
assert sys.argv[2] == "export"
path = pathlib.Path(sys.argv[3]); path.parent.mkdir(mode=0o700)
path.write_text(json.dumps({"mock": True})); path.chmod(0o600)
with open(os.environ["COMMAND_LOG"], "a") as output: output.write("runtime-export\\n")
""")
    node.chmod(0o755)
    git = tmp_path / "mock-bin/git"
    original = git.read_text()
    git.write_text(
        original.replace(
            "  *) exit 64 ;;",
            "  *'rev-parse HEAD'*) printf '%s\\n' aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ;;\n  *'status --porcelain'*) exit 0 ;;\n  *) exit 64 ;;",
        )
    )
    environment["EXPORT_HEALTH_RESULT"] = "0" if healthy else "1"
    result = helpers._run_mocked_launcher(worktree, environment)
    recorded = commands.read_text()
    if healthy:
        assert result.returncode == 0, result.stderr
        assert destination.stat().st_mode & 0o777 == 0o600
        assert recorded.index("up --detach") < recorded.index("runtime-export")
        assert ".Config.Env" not in recorded and ".Config.Cmd" not in recorded
    else:
        assert result.returncode != 0
        assert not destination.exists()
        assert "runtime-export" not in recorded
