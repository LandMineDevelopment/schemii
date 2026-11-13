"""Android deployment controls, without starting the application or databases."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "dev/android/runtime.py"
SPEC = importlib.util.spec_from_file_location("android_runtime", MODULE_PATH)
assert SPEC and SPEC.loader
android = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(android)


@pytest.fixture
def runtime(tmp_path):
    instance = android.Runtime(tmp_path / "state")
    instance._prepare()
    return instance


@pytest.mark.parametrize(
    "address,hostname",
    [
        ("192.168.1.3", "phone.example.ts.net"),
        ("100.63.255.255", "phone.example.ts.net"),
        ("100.128.0.1", "phone.example.ts.net"),
        ("::1", "phone.example.ts.net"),
        ("100.100.2.3", "phone;listen 0.0.0.0"),
        ("100.100.2.3", "-phone.example"),
        ("100.100.2.3", "localhost"),
        ("100.100.2.3", "phone..example"),
    ],
)
def test_network_configuration_rejects_non_tailnet_and_injection(address, hostname):
    with pytest.raises(android.RuntimeFailure):
        android.validate_config(address, hostname)


def test_configuration_normalizes_dns_and_preserves_ca(runtime):
    (runtime.state / "tls/ca.key").write_text("original CA")
    runtime.configure("100.64.2.3", "Phone.Example.TS.NET.")
    config = runtime.config()
    assert config["hostname"] == "phone.example.ts.net"
    (runtime.state / "tls/server.crt").write_text("old leaf")
    runtime.configure("100.64.2.4", "phone.example.ts.net")
    assert (runtime.state / "tls/ca.key").read_text() == "original CA"
    assert not (runtime.state / "tls/server.crt").exists()
    assert stat.S_IMODE((runtime.state / "config.json").stat().st_mode) == 0o600


def test_existing_parent_permissions_are_not_changed_by_locks_or_exports(tmp_path):
    parent = tmp_path / "user-owned"
    parent.mkdir(mode=0o755)
    before = stat.S_IMODE(parent.stat().st_mode)
    instance = android.Runtime(parent / "state")
    with instance.lock():
        pass
    android.write_private(parent / "export.json", "private")
    assert stat.S_IMODE(parent.stat().st_mode) == before
    assert stat.S_IMODE((parent / "export.json").stat().st_mode) == 0o600


def test_original_secrets_survive_restart_and_missing_key_is_not_replaced(runtime):
    runtime._secrets()
    first = {
        name: (runtime.state / "secrets" / name).read_bytes()
        for name in android.SECRET_NAMES
    }
    assert len(base64.b64decode(first["metadata_encryption_key"].strip())) == 32
    runtime._secrets()
    assert first == {
        name: (runtime.state / "secrets" / name).read_bytes()
        for name in android.SECRET_NAMES
    }
    pg = runtime.state / "postgres/metadata"
    pg.mkdir()
    (pg / "PG_VERSION").write_text("17\n")
    key = runtime.state / "secrets/metadata_encryption_key"
    key.unlink()
    with pytest.raises(android.RuntimeFailure, match="original secret"):
        runtime._secrets()
    assert not key.exists()


def test_invalid_retained_encryption_key_blocks_start(runtime):
    runtime._secrets()
    (runtime.state / "secrets/metadata_encryption_key").write_text("not-base64")
    with pytest.raises(android.RuntimeFailure, match="encryption key is invalid"):
        runtime._secrets()


def test_runtime_environment_keeps_metadata_fence_and_secret_files(
    runtime, monkeypatch
):
    monkeypatch.setenv("SCHEMII_METADATA_DSN", "secret-desktop-dsn")
    monkeypatch.setenv("PGPASSWORD", "ambient-password")
    env = runtime._environment(
        android.validate_config("100.64.2.3", "phone.example.ts.net")
    )
    assert (
        env["SCHEMII_METADATA_DSN"]
        == "host=127.0.0.1 port=5433 dbname=schemii user=schemii_metadata_app"
    )
    assert "localhost" in env["SCHEMII_METADATA_TARGET_HOST_ALIASES"]
    assert env["SCHEMII_METADATA_MAXIMUM_CONNECTIONS"] == "4"
    assert env["SCHEMII_PI_PASSWORD_FILE"] == str(
        runtime.state / "secrets/opencode_password"
    )
    assert env["SCHEMII_PI_HOST"] == "127.0.0.1"
    assert env["SCHEMII_ALLOWED_TARGET_HOSTS"] == "127.0.0.1,localhost"
    assert "PGPASSWORD" not in env
    assert "https://phone.example.ts.net:8001" in env["SCHEMII_TRUSTED_ORIGINS"]
    assert "https://100.64.2.3:8001" in env["SCHEMII_TRUSTED_ORIGINS"]
    assert "omarchy" not in env["SCHEMII_TRUSTED_ORIGINS"]


def test_ingress_preserves_streaming_and_only_explicit_https_interfaces(runtime):
    runtime._write_runtime_config(
        android.validate_config("100.64.2.3", "phone.example.ts.net")
    )
    nginx = (runtime.state / "run/nginx.conf").read_text()
    assert "listen 127.0.0.1:8001 ssl" in nginx
    assert "listen 100.64.2.3:8001 ssl" in nginx
    assert "0.0.0.0" not in nginx and "127.0.0.11" not in nginx
    assert "proxy_buffering off" in nginx and "proxy_request_buffering off" in nginx
    assert "client_max_body_size 20m" in nginx and "client_max_body_size 0" in nginx
    assert "proxy_set_header Host $http_host" in nginx
    assert "@STATE@" not in nginx


def test_cluster_uses_scram_loopback_and_keeps_durability(runtime, monkeypatch):
    calls = []

    def checked(command, **_kwargs):
        calls.append(command)
        directory = Path(command[command.index("-D") + 1])
        directory.mkdir()
        (directory / "PG_VERSION").write_text("17\n")

    monkeypatch.setattr(runtime, "_checked", checked)
    monkeypatch.setattr(
        runtime, "_spawn", lambda name, command: calls.append((name, command))
    )
    monkeypatch.setattr(runtime, "_wait", lambda *_args: None)
    runtime._cluster("metadata", 5433)
    configuration = (runtime.state / "postgres/metadata/postgresql.conf").read_text()
    assert "--auth-local=scram-sha-256" in calls[0]
    assert "--auth-host=scram-sha-256" in calls[0]
    assert "listen_addresses = '127.0.0.1'" in configuration
    assert "port = 5433" in configuration
    assert "fsync = on" in configuration
    assert "synchronous_commit = on" in configuration
    assert "full_page_writes = on" in configuration


def test_major_version_mismatch_never_reinitializes(runtime, monkeypatch):
    directory = runtime.state / "postgres/metadata"
    directory.mkdir()
    (directory / "PG_VERSION").write_text("18\n")
    monkeypatch.setattr(
        runtime, "_checked", lambda *_args: pytest.fail("must not initialize")
    )
    with pytest.raises(android.RuntimeFailure, match="PostgreSQL 17"):
        runtime._cluster("metadata", 5433)
    assert (directory / "PG_VERSION").read_text() == "18\n"


def test_existing_version_without_control_file_is_refused_without_writes(
    runtime, monkeypatch
):
    directory = runtime.state / "postgres/metadata"
    directory.mkdir()
    (directory / "PG_VERSION").write_text("17\n")
    configuration = directory / "postgresql.conf"
    configuration.write_text("retained partial initialization")
    before = {path: path.read_bytes() for path in directory.iterdir()}
    monkeypatch.setattr(
        runtime, "_checked", lambda *_args: pytest.fail("must not reinitialize")
    )
    monkeypatch.setattr(
        runtime,
        "_spawn",
        lambda *_args: pytest.fail("must not start incomplete cluster"),
    )
    with pytest.raises(android.RuntimeFailure, match="missing global/pg_control"):
        runtime._cluster("metadata", 5433)
    assert before == {path: path.read_bytes() for path in directory.iterdir()}


def test_status_does_not_trust_a_reused_pid(runtime, monkeypatch):
    android.write_private(
        runtime.state / "run/status.json",
        json.dumps(
            {
                "state": "ready",
                "ready": True,
                "supervisor": {"pid": 12345, "start_ticks": 10, "pgid": 12345},
                "services": {"app": {"pid": 12346, "start_ticks": 11, "pgid": 12346}},
            }
        ),
    )
    monkeypatch.setattr(
        android,
        "process_identity",
        lambda pid: {"pid": pid, "start_ticks": 99, "pgid": pid},
    )
    assert runtime.status()["ready"] is False
    assert runtime.status()["state"] == "failed"
    called = []
    monkeypatch.setattr(os, "killpg", lambda *_args: called.append(True))
    runtime._stop_children({"app": {"pid": 12346, "start_ticks": 11, "pgid": 12346}})
    assert not called


def test_shutdown_terminates_only_captured_process_group(runtime):
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    try:
        identity = android.process_identity(process.pid)
        assert identity and identity["pgid"] == process.pid
        runtime._stop_children({"pi": identity})
        assert process.wait(timeout=3) < 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_prior_boot_identity_never_authorizes_signaling_a_current_process(monkeypatch):
    current = android.process_identity(os.getpid())
    assert current and android.is_current(current)
    old = {**current, "boot_id": "a-different-boot"}
    assert not android.is_current(old)
    assert not android.owns_group(old)
    called = []
    monkeypatch.setattr(os, "killpg", lambda *_args: called.append(True))
    android.Runtime._stop_children({"app": old})
    assert not called


def test_cold_recovery_refuses_uncleared_postgres_pid(runtime):
    directory = runtime.state / "postgres/target"
    directory.mkdir()
    (directory / "postmaster.pid").write_text("123\n")
    with pytest.raises(android.RuntimeFailure, match="uncleared postmaster.pid"):
        runtime.assert_stopped()


def test_start_admits_only_previously_owned_dead_postgres_lock_without_removing_it(
    runtime, monkeypatch
):
    directory = runtime.state / "postgres/metadata"
    directory.mkdir()
    pidfile = directory / "postmaster.pid"
    contents = f"123456\n{directory}\n"
    pidfile.write_text(contents)
    receipt = {
        "services": {"metadata": {"pid": 123456, "start_ticks": 25, "pgid": 123456}}
    }
    android.write_private(runtime.state / "run/status.json", json.dumps(receipt))
    monkeypatch.setattr(android, "process_identity", lambda _pid: None)
    runtime._assert_startable()
    assert pidfile.read_text() == contents
    monkeypatch.setattr(
        android,
        "process_identity",
        lambda pid: {"pid": pid, "start_ticks": 99, "pgid": pid},
    )
    with pytest.raises(android.RuntimeFailure, match="Unverified PostgreSQL"):
        runtime._assert_startable()
    assert pidfile.read_text() == contents


def test_start_refuses_unknown_lock_and_keeps_prior_ownership_receipt(
    runtime, monkeypatch
):
    runtime.configure("100.64.2.3", "phone.example.ts.net")
    prior = {
        "services": {"metadata": {"pid": 123456, "start_ticks": 25, "pgid": 123456}}
    }
    receipt_path = runtime.state / "run/status.json"
    android.write_private(receipt_path, json.dumps(prior))
    directory = runtime.state / "postgres/target"
    directory.mkdir()
    (directory / "postmaster.pid").write_text(f"987654\n{directory}\n")
    monkeypatch.setattr(android, "process_identity", lambda _pid: None)
    monkeypatch.setattr(android.signal, "signal", lambda *_args: None)
    before = receipt_path.read_bytes()
    with pytest.raises(android.RuntimeFailure, match="Unverified PostgreSQL"):
        runtime.run()
    assert receipt_path.read_bytes() == before


def test_captured_orphan_member_authorizes_only_its_original_group(
    runtime, monkeypatch
):
    leader = {
        "pid": 12345,
        "start_ticks": 10,
        "pgid": 12345,
        "members": [
            {"pid": 12346, "start_ticks": 11, "pgid": 12345, "boot_id": "test-boot"}
        ],
    }
    alive = True

    def identity(pid):
        return leader["members"][0] if pid == 12346 and alive else None

    def signal_group(group, _signal):
        nonlocal alive
        assert group == 12345
        alive = False

    monkeypatch.setattr(android, "process_identity", identity)
    monkeypatch.setattr(os, "killpg", signal_group)
    assert android.owns_group(leader)
    runtime._stop_children({"metadata": leader})
    assert not alive


def test_pending_owned_postgres_lock_survives_interrupted_recovery_attempt(
    runtime, monkeypatch
):
    directory = runtime.state / "postgres/metadata"
    directory.mkdir()
    pidfile = directory / "postmaster.pid"
    pidfile.write_text(f"123456\n{directory}\n")
    runtime.recovery_identities = {
        "metadata": {"pid": 123456, "start_ticks": 25, "pgid": 123456}
    }
    runtime._record("failed", "early interrupted recovery")
    monkeypatch.setattr(android, "process_identity", lambda _pid: None)
    runtime._assert_startable()
    assert pidfile.exists()


def test_failed_commands_have_bounded_private_diagnostics(runtime):
    for _ in range(6):
        with pytest.raises(android.RuntimeFailure, match="private setup log"):
            runtime._checked(
                [
                    sys.executable,
                    "-c",
                    "import sys; sys.stdout.write('x'*(2*1024*1024)); sys.exit(1)",
                ]
            )
    logs = list((runtime.state / "logs").iterdir())
    assert len(logs) == 3
    assert sum(path.stat().st_size for path in logs) <= 3 * 1024 * 1024
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in logs)


def test_ca_export_excludes_private_keys_and_credentials_are_file_only(
    runtime, tmp_path
):
    runtime.configure("100.64.2.3", "phone.example.ts.net")
    runtime._secrets()
    (runtime.state / "tls/ca.crt").write_text("public CA")
    (runtime.state / "tls/ca.key").write_text("private CA")
    public = tmp_path / "ca.crt"
    private = tmp_path / "credentials.json"
    result = runtime.export_ca(public)
    assert public.read_text() == "public CA" and not result["private_key_exported"]
    result = runtime.credentials(private)
    assert "account_setup_token" not in result
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    target = json.loads(private.read_text())["target_connection"]
    assert target["port"] == 5434 and target["username"] == "schemii_target"
    with pytest.raises(android.RuntimeFailure, match="already exists"):
        runtime.credentials(private)


def test_exclusive_export_never_replaces_existing_file_or_symlink(tmp_path):
    existing = tmp_path / "existing"
    existing.write_text("retained user data")
    for destination in (existing, tmp_path / "link"):
        if destination != existing:
            destination.symlink_to(existing)
        with pytest.raises(android.RuntimeFailure, match="already exists"):
            android.write_new_private(destination, "new secrets")
    assert existing.read_text() == "retained user data"


def test_failed_start_cleans_owned_children_and_reports_safe_error(
    runtime, monkeypatch
):
    runtime.configure("100.64.2.3", "phone.example.ts.net")
    monkeypatch.setattr(android, "PYTHON", Path(sys.executable))
    monkeypatch.setattr(android, "NODE", Path(sys.executable))
    monkeypatch.setattr(android, "PG_BIN", Path(sys.executable).parent)
    original_is_file = Path.is_file
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda self: (
            True
            if str(self) == "/usr/sbin/nginx" or self.name == "postgres"
            else original_is_file(self)
        ),
    )
    monkeypatch.setattr(android.signal, "signal", lambda *_args: None)

    class Probe:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def bind(self, _address):
            pass

    monkeypatch.setattr(android.socket, "socket", Probe)
    monkeypatch.setattr(runtime, "_tls", lambda _config: None)
    monkeypatch.setattr(runtime, "_cluster", lambda *_args: None)
    monkeypatch.setattr(runtime, "_bootstrap", lambda *_args: None)
    monkeypatch.setattr(
        runtime,
        "_spawn",
        lambda *_args: (_ for _ in ()).throw(
            RuntimeError("a hypothetical secret must not reach status")
        ),
    )
    stopped = []
    monkeypatch.setattr(
        runtime, "_stop_children", lambda _identities: stopped.append(True)
    )
    with pytest.raises(android.RuntimeFailure, match="Startup failed"):
        runtime.run()
    assert stopped == [True]
    saved = (runtime.state / "run/status.json").read_text()
    assert "hypothetical secret" not in saved
    assert json.loads(saved)["state"] == "failed"


def _control_process(runtime, body):
    code = (
        """
import runpy,sys,signal,threading
from pathlib import Path
module=runpy.run_path(sys.argv[1])
runtime=module['Runtime'](Path(sys.argv[2]))
"""
        + body
    )
    return subprocess.Popen(
        [sys.executable, "-c", code, str(MODULE_PATH), str(runtime.state)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )


def _finish_control(process):
    if process.poll() is None:
        process.stdin.write("exit\n")
        process.stdin.flush()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


@pytest.mark.parametrize("lock_name", ["recovery_lock", "lock"])
def test_configure_refuses_real_contended_flock_without_changing_state(
    runtime, lock_name
):
    runtime.configure("100.64.2.3", "phone.example.ts.net")
    before = (runtime.state / "config.json").read_bytes()
    process = _control_process(
        runtime,
        f"""
with runtime.{lock_name}():
    runtime.startup_stage='metadata init'
    runtime._record('starting')
    print('held',flush=True)
    sys.stdin.readline()
""",
    )
    try:
        assert process.stdout.readline().strip() == "held"
        started = time.monotonic()
        with pytest.raises(android.RuntimeFailure, match="busy.*metadata init"):
            runtime.configure("100.64.2.4", "different.example.ts.net")
        assert time.monotonic() - started < 1
        assert (runtime.state / "config.json").read_bytes() == before
    finally:
        _finish_control(process)


def test_stop_signals_owned_startup_before_its_real_lifecycle_lock(runtime):
    runtime.configure("100.64.2.3", "phone.example.ts.net")
    runtime._secrets()
    key = (runtime.state / "secrets/metadata_encryption_key").read_bytes()
    process = _control_process(
        runtime,
        """
signal.signal(signal.SIGTERM,lambda *_:runtime.shutdown.set())
with runtime.recovery_lock(),runtime.lock():
    runtime.startup_stage='metadata init'
    runtime._record('starting')
    print('held',flush=True)
    runtime.shutdown.wait(60)
""",
    )
    try:
        assert process.stdout.readline().strip() == "held"
        started = time.monotonic()
        assert runtime.stop(timeout=2)["state"] == "stopped"
        assert time.monotonic() - started < 2
        assert process.wait(timeout=2) == 0
        assert (runtime.state / "secrets/metadata_encryption_key").read_bytes() == key
    finally:
        _finish_control(process)


def test_legacy_unresponsive_startup_stop_is_bounded_and_preserves_receipt(runtime):
    process = _control_process(
        runtime,
        """
signal.signal(signal.SIGTERM,lambda *_:None)
with runtime.recovery_lock(),runtime.lock():
    runtime._record('starting')
    print('held',flush=True)
    sys.stdin.readline()
""",
    )
    try:
        assert process.stdout.readline().strip() == "held"
        receipt = (runtime.state / "run/status.json").read_bytes()
        started = time.monotonic()
        with pytest.raises(android.RuntimeFailure, match="older preview blocked"):
            runtime.stop(timeout=0.15)
        assert time.monotonic() - started < 1
        assert process.poll() is None
        assert (runtime.state / "run/status.json").read_bytes() == receipt
    finally:
        _finish_control(process)


def test_cold_snapshot_lock_fences_new_start_with_real_flock(runtime, monkeypatch):
    runtime.configure("100.64.2.3", "phone.example.ts.net")
    runtime._secrets()
    retained = {
        path: path.read_bytes() for path in (runtime.state / "secrets").iterdir()
    }
    process = _control_process(
        runtime,
        """
with runtime.recovery_lock():
    runtime.stop()
    print('snapshot window',flush=True)
    sys.stdin.readline()
""",
    )
    try:
        assert process.stdout.readline().strip() == "snapshot window"
        monkeypatch.setattr(android.signal, "signal", lambda *_: None)
        with pytest.raises(android.RuntimeFailure, match="recovery operation is busy"):
            runtime.run()
        assert retained == {path: path.read_bytes() for path in retained}
    finally:
        _finish_control(process)


def test_restore_lock_fences_start_without_creating_fresh_state(tmp_path, monkeypatch):
    fresh = android.Runtime(tmp_path / "fresh-state")
    process = _control_process(
        fresh,
        """
with runtime.recovery_lock():
    print('restore window',flush=True)
    sys.stdin.readline()
""",
    )
    try:
        assert process.stdout.readline().strip() == "restore window"
        monkeypatch.setattr(android.signal, "signal", lambda *_: None)
        with pytest.raises(android.RuntimeFailure, match="recovery operation is busy"):
            fresh.run()
        assert not fresh.state.exists()
    finally:
        _finish_control(process)


def test_checked_helper_timeout_is_bounded_and_raw_output_stays_private(runtime):
    started = time.monotonic()
    with pytest.raises(android.RuntimeFailure, match="timed out") as caught:
        runtime._checked(
            [
                sys.executable,
                "-c",
                "import time; print('private-sentinel',flush=True); time.sleep(60)",
            ],
            timeout=0.15,
        )
    assert time.monotonic() - started < 4
    assert "private-sentinel" not in str(caught.value)
    assert "private-sentinel" in (runtime.state / "logs/setup.log").read_text()
    assert runtime.helper_identity is None


def test_initial_helper_receipt_failure_cleans_live_helper_and_preserves_error(
    runtime, monkeypatch
):
    runtime._supervising = True
    started = []
    original_popen = subprocess.Popen
    original_error = OSError("synthetic initial receipt failure")

    def spawn(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        started.append(process)
        return process

    def fail_receipt(*_args):
        raise original_error

    monkeypatch.setattr(subprocess, "Popen", spawn)
    monkeypatch.setattr(runtime, "_record", fail_receipt)
    with pytest.raises(OSError) as caught:
        runtime._checked([sys.executable, "-c", "import time; time.sleep(60)"])
    assert caught.value is original_error
    assert len(started) == 1 and started[0].poll() is not None
    assert runtime.helper_identity is None


def test_missing_helper_birth_identity_stops_only_owned_unreaped_direct_child(
    runtime, monkeypatch
):
    started = []
    original_popen = subprocess.Popen

    def spawn(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", spawn)
    monkeypatch.setattr(android, "process_identity", lambda _pid: None)
    monkeypatch.setattr(
        os,
        "killpg",
        lambda *_args: pytest.fail("No guessed group signal is authorized"),
    )
    with pytest.raises(
        android.RuntimeFailure, match="Cannot establish startup helper ownership"
    ):
        runtime._checked([sys.executable, "-c", "import time; time.sleep(60)"])
    assert len(started) == 1 and started[0].poll() is not None
    assert runtime.helper_identity is None


def test_polling_helper_receipt_failure_cleans_parent_and_captured_child(
    runtime, monkeypatch
):
    runtime._supervising = True
    original_record = runtime._record
    original_error = OSError("synthetic polling receipt failure")
    captured = []

    def fail_after_child_capture(*args):
        helper = runtime.helper_identity
        if helper and helper.get("members"):
            captured.extend([dict(helper), *helper["members"]])
            raise original_error
        original_record(*args)

    monkeypatch.setattr(runtime, "_record", fail_after_child_capture)
    code = "import subprocess,sys; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); child.wait()"
    with pytest.raises(OSError) as caught:
        runtime._checked([sys.executable, "-c", code])
    assert caught.value is original_error and len(captured) >= 2
    assert not any(android.is_current(identity) for identity in captured)
    assert runtime.helper_identity is None


def test_helper_cleanup_failure_retains_identity_and_original_receipt_error(
    runtime, monkeypatch
):
    runtime._supervising = True
    original_stop = runtime._stop_helper
    original_error = OSError("synthetic original receipt failure")

    def fail_receipt(*_args):
        raise original_error

    def fail_cleanup(_identity):
        raise android.RuntimeFailure("synthetic cleanup failure")

    monkeypatch.setattr(runtime, "_record", fail_receipt)
    monkeypatch.setattr(runtime, "_stop_helper", fail_cleanup)
    try:
        with pytest.raises(OSError) as caught:
            runtime._checked([sys.executable, "-c", "import time; time.sleep(60)"])
        assert caught.value is original_error
        assert android.is_current(runtime.helper_identity)
    finally:
        original_stop(runtime.helper_identity)


def test_stop_cancels_owned_startup_helper_under_real_locks(runtime):
    process = _control_process(
        runtime,
        """
signal.signal(signal.SIGTERM,lambda *_:runtime.shutdown.set())
with runtime.recovery_lock(),runtime.lock():
    runtime._supervising=True
    runtime._stage('metadata init')
    try:
        runtime._checked([sys.executable,'-c','import time; time.sleep(60)'])
    except module['RuntimeFailure']:
        pass
""",
    )
    try:
        assert process.stdout.readline().strip() == "Android startup: metadata init"
        deadline = time.monotonic() + 3
        helper = None
        while time.monotonic() < deadline:
            helper = android.read_json(runtime.state / "run/status.json").get("helper")
            if helper:
                break
            time.sleep(0.02)
        assert helper and android.is_current(helper)
        assert runtime.stop(timeout=3)["state"] == "stopped"
        assert not android.is_current(helper)
        assert process.wait(timeout=2) == 0
    finally:
        _finish_control(process)


def test_stop_cleans_recorded_owned_helper_after_supervisor_dies(runtime):
    process = _control_process(
        runtime,
        """
with runtime.recovery_lock(),runtime.lock():
    runtime._supervising=True
    runtime._stage('metadata init')
    runtime._checked([sys.executable,'-c','import time; time.sleep(60)'])
""",
    )
    helper = None
    try:
        assert process.stdout.readline().strip() == "Android startup: metadata init"
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            helper = android.read_json(runtime.state / "run/status.json").get("helper")
            if helper:
                break
            time.sleep(0.02)
        assert helper and android.is_current(helper)
        process.kill()
        process.wait(timeout=3)
        assert runtime.stop(timeout=2)["state"] == "stopped"
        assert not android.is_current(helper)
    finally:
        _finish_control(process)
        if helper and android.owns_group(helper):
            os.killpg(helper["pid"], android.signal.SIGKILL)


def test_database_bootstrap_limits_connection_and_statement_waits(runtime, monkeypatch):
    import psycopg

    runtime._secrets()
    settings = {}

    def connect(**kwargs):
        settings.update(kwargs)
        raise RuntimeError("no database contacted")

    monkeypatch.setattr(psycopg, "connect", connect)
    with pytest.raises(RuntimeError, match="no database contacted"):
        runtime._bootstrap_database(
            "metadata", 5433, "schemii_metadata_app", "metadata_app_password"
        )
    assert settings["connect_timeout"] == 5
    assert "statement_timeout=30000" in settings["options"]
    assert "lock_timeout=5000" in settings["options"]


def _legacy_initdb_fixture(runtime, tmp_path, *, directory=None, check=True):
    """Real supervisor -> fake PG17 initdb -> /bin/sh -> fake PG17 --check."""
    binaries = tmp_path / "pg17"
    binaries.mkdir()
    for name in ("initdb", "postgres"):
        (binaries / name).symlink_to(sys.executable)
    directory = directory or runtime.state / "postgres/metadata"
    directory.mkdir(parents=True)
    # initdb can write PG_VERSION before completing cluster initialization.
    (directory / "PG_VERSION").write_text("17\n")
    probe_ready = tmp_path / "probe-ready"
    cleanup = tmp_path / "initdb-cleanup"
    probe_code = f"import os,time; from pathlib import Path; Path({str(probe_ready)!r}).write_text(str(os.getpid())); time.sleep(60)"
    import shlex

    probe_command = (
        shlex.join(
            [
                str(binaries / "postgres"),
                "-c",
                probe_code,
                "--check" if check else "--server",
            ]
        )
        + " & wait"
    )
    initdb_code = (
        "import signal,subprocess; from pathlib import Path; "
        "signal.signal(signal.SIGTERM,lambda *_:None); "
        f"subprocess.run(['/bin/sh','-c',{probe_command!r}]); "
        f"Path({str(cleanup)!r}).write_text('initdb-owned cleanup')"
    )
    body = f"""
import subprocess
signal.signal(signal.SIGTERM,lambda *_:runtime.shutdown.set())
with runtime.recovery_lock(),runtime.lock():
    runtime._record('starting')
    child=subprocess.Popen([{str(binaries / "initdb")!r},'-c',{initdb_code!r},'-D',{str(directory)!r},'--username','schemii_bootstrap','--pwfile',str(runtime.state/'secrets/metadata_bootstrap_password')])
    print('held',flush=True)
    child.wait()
"""
    process = _control_process(runtime, body)
    assert process.stdout.readline().strip() == "held"
    deadline = time.monotonic() + 3
    while not probe_ready.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert probe_ready.exists()
    return process, binaries, probe_ready, cleanup


def _finish_process_fixture(process):
    if process.poll() is None:
        # This process group contains only this test's synthetic Python fixtures.
        os.killpg(process.pid, android.signal.SIGKILL)
    process.wait(timeout=3)


def test_stop_rescues_only_verified_unfinished_legacy_initdb_probe(
    runtime, tmp_path, monkeypatch
):
    process, binaries, probe_ready, cleanup = _legacy_initdb_fixture(runtime, tmp_path)
    monkeypatch.setattr(android, "PG_BIN", binaries)
    probe = android.process_identity(int(probe_ready.read_text()))
    try:
        assert runtime.stop(timeout=3)["state"] == "stopped"
        assert process.wait(timeout=2) == 0
        assert not android.is_current(probe)
        assert cleanup.read_text() == "initdb-owned cleanup"
        assert (runtime.state / "postgres/metadata/PG_VERSION").read_text() == "17\n"
    finally:
        _finish_process_fixture(process)


@pytest.mark.parametrize(
    "mismatch", ["data-path", "control-file", "not-check", "birth", "parent"]
)
def test_legacy_initdb_rescue_refuses_unproven_or_initialized_processes(
    runtime, tmp_path, monkeypatch, mismatch
):
    directory = tmp_path / "other-database" if mismatch == "data-path" else None
    process, binaries, probe_ready, cleanup = _legacy_initdb_fixture(
        runtime, tmp_path, directory=directory, check=mismatch != "not-check"
    )
    monkeypatch.setattr(android, "PG_BIN", binaries)
    probe = android.process_identity(int(probe_ready.read_text()))
    original_identity = android.process_identity
    if mismatch == "control-file":
        control = runtime.state / "postgres/metadata/global/pg_control"
        control.parent.mkdir()
        control.write_bytes(b"retained initialized control file")
    if mismatch in ("birth", "parent"):
        original = android.process_identity
        probe_reads = 0

        def identity(pid):
            nonlocal probe_reads
            value = original(pid)
            if value and pid == probe["pid"]:
                probe_reads += 1
                if mismatch == "parent" or probe_reads > 1:
                    value["start_ticks" if mismatch == "birth" else "ppid"] += 1
            return value

        monkeypatch.setattr(android, "process_identity", identity)
    try:
        receipt = android.read_json(runtime.state / "run/status.json")
        assert not runtime._cancel_legacy_initdb_check(receipt, receipt["supervisor"])
        assert process.poll() is None and not cleanup.exists()
        assert original_identity(probe["pid"])
    finally:
        _finish_process_fixture(process)
