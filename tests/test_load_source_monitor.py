"""Deterministic source witness controls; no PostgreSQL or runtime is started."""

import copy
import importlib.util
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "source_monitor", Path(__file__).parents[1] / "testing/load/source_monitor.py"
)
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)


def config():
    return {
        "version": 2,
        "runId": "load_0123456789abcdef",
        "authority": {
            "kind": "exclusive_source_role",
            "exclusiveToRun": True,
            "database": "disposable_database",
            "role": "disposable_run_role",
            "applicationName": "schemii",
            "provisioningReceipt": "/private/explicit-owner-receipt.json",
            "endpoint": {"host": "127.0.0.1", "port": 5432, "sslmode": "require"},
            "server": {
                "address": "127.0.0.1",
                "port": 5432,
                "postmasterStart": "2026-11-11T09:00:00.000000+00:00",
                "databaseOid": 100,
                "fixtureId": "a" * 64,
            },
        },
        "connection": {
            "host": "127.0.0.1",
            "port": 5432,
            "dbname": "disposable_database",
            "user": "disposable_run_role",
            "password": "private-deterministic-password",
            "sslmode": "require",
        },
    }


def row():
    return {
        "pid": 102,
        "backend_start": datetime(2026, 11, 11, 10, tzinfo=timezone.utc),
        "datname": "disposable_database",
        "usename": "disposable_run_role",
        "application_name": "schemii",
        "state": "idle in transaction",
    }


class Connection:
    def __init__(self, rows, own=None, server=None, checked=None):
        self.rows = rows
        self.checked = checked
        self.server = server or {
            "server_address": "127.0.0.1",
            "server_port": 5432,
            "postmaster_start": datetime(2026, 11, 11, 9, tzinfo=timezone.utc),
            "database_oid": 100,
            "fixture_marker": "schemii-load:" + "a" * 64,
        }
        self.own = own or {
            "database": "disposable_database",
            "role": "disposable_run_role",
            "login": "disposable_run_role",
        }
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def transaction(self):
        return self

    def execute(self, sql, parameters=None):
        self.calls.append((sql, parameters))
        return self

    def fetchone(self):
        if "AS server_address" in self.calls[-1][0]:
            return self.server
        return self.own

    def fetchall(self):
        if "ANY" in self.calls[-1][0] and self.checked is not None:
            return self.checked
        return self.rows


def sample(rows, previous=None, **kwargs):
    connection = Connection(rows, **kwargs)

    def connect(**arguments):
        assert arguments["user"] == "disposable_run_role"
        assert arguments["application_name"] == "schemii_load_monitor"
        assert arguments["passfile"] == "/dev/null"
        assert "default_transaction_read_only=on" in arguments["options"]
        return connection

    value = monitor.sample(config(), previous, connect=connect)
    assert all(
        not sql.startswith(("GRANT", "UPDATE", "DELETE")) for sql, _ in connection.calls
    )
    assert "query" not in connection.calls[-1][0]
    return value


def test_exact_backend_capture_idle_is_live_then_independent_release():
    baseline = sample([])
    assert baseline["ownedBackends"] == 0
    assert not baseline["releaseVerified"]
    active = sample([row()], baseline)
    assert active["ownedBackends"] == 1
    assert active["captured"][0] == {
        "pid": 102,
        "backendStart": "2026-11-11T10:00:00.000000+00:00",
        "database": "disposable_database",
        "role": "disposable_run_role",
        "applicationName": "schemii",
    }
    released = sample([], active)
    assert released["releaseVerified"]
    assert released["captured"] == active["captured"]
    assert released["ownedBackends"] == 0
    assert "password" not in str(released)


def test_no_adoption_safe_pid_reuse_and_changed_authority():
    with pytest.raises(ValueError, match="baseline_not_empty"):
        sample([row()])
    baseline = sample([])
    active = sample([row()], baseline)
    replacement = {
        **row(),
        "backend_start": datetime(2026, 11, 11, 11, tzinfo=timezone.utc),
    }
    reused = sample([replacement], active)
    assert reused["ownedBackends"] == 1
    assert not reused["releaseVerified"]
    assert [value["status"] for value in reused["capturedChecks"]] == [
        "pid_reused",
        "present",
    ]
    assert sample([], reused)["releaseVerified"]
    altered = copy.deepcopy(active)
    altered["authority"]["runId"] = "load_foreign"
    with pytest.raises(ValueError, match="authority_changed"):
        sample([], altered)


@pytest.mark.parametrize(
    "key,value",
    [("state", None), ("pid", True), ("backend_start", None), ("usename", "foreign")],
)
def test_incomplete_visibility_or_foreign_identity_never_becomes_zero(key, value):
    with pytest.raises(ValueError, match="identity|visibility"):
        sample([{**row(), key: value}], sample([]))


def test_no_role_fallback_and_explicit_authority_is_required():
    with pytest.raises(ValueError, match="authority_mismatch"):
        sample(
            [],
            own={"database": "disposable_database", "role": "author", "login": "admin"},
        )
    for change in (
        {"exclusiveToRun": False},
        {"kind": "borrowed_author"},
        {"role": "other"},
    ):
        altered = config()
        altered["authority"].update(change)
        with pytest.raises(ValueError, match="authority_required"):
            monitor.authority(altered)
    altered = config()
    altered["connection"]["service"] = "ambient-fallback"
    with pytest.raises(ValueError, match="authority_required"):
        monitor.authority(altered)
    altered = config()
    altered["runId"] = "load_not-an-owned-identity"
    with pytest.raises(ValueError, match="authority_required"):
        monitor.authority(altered)


def test_monitor_failure_cannot_supply_release():
    def denied(**_):
        raise PermissionError("private connection details must not escape")

    with pytest.raises(PermissionError):
        monitor.sample(config(), sample([]), connect=denied)


def test_private_receipts_reject_loose_modes_and_symlinks(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / "receipt.json"
    monitor.private_write(path, {"private": True})
    assert path.stat().st_mode & 0o077 == 0
    assert monitor.private_read(path) == {"private": True}
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="private_source_file"):
        monitor.private_read(link)
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private_source_file"):
        monitor.private_read(path)


def test_observation_limits_are_explicit():
    with pytest.raises(ValueError, match="observation_budget"):
        sample([row()] * 10001, sample([]))


def test_review_endpoint_retarget_cannot_reuse_captured_authority():
    active = sample([row()], sample([]))
    changed = copy.deepcopy(config())
    changed["connection"].update(host="192.0.2.77", port=6543)
    with pytest.raises(ValueError, match="authority|endpoint"):
        monitor.sample(changed, active, connect=lambda **_: Connection([]))


@pytest.mark.parametrize(
    "field,value",
    [
        ("server_address", "192.0.2.77"),
        ("server_port", 6543),
        ("database_oid", 101),
        ("postmaster_start", datetime(2026, 11, 11, 9, 0, 0, 1, tzinfo=timezone.utc)),
        ("fixture_marker", "schemii-load:" + "b" * 64),
        ("fixture_marker", None),
    ],
)
def test_actual_server_fixture_identity_or_restart_never_reuses_authority(field, value):
    active = sample([row()], sample([]))
    connection = Connection([])
    connection.server[field] = value
    with pytest.raises(ValueError, match="server_identity"):
        monitor.sample(config(), active, connect=lambda **_: connection)
    assert not any("pg_stat_activity" in sql for sql, _ in connection.calls)


def test_provisioning_and_previous_ledger_bind_declared_endpoint_and_actual_server():
    original = config()
    scope = monitor.authority(original)
    record = {"version": 2, "status": "ready", **scope}
    for key in ("endpoint", "server"):
        altered = copy.deepcopy(original)
        if key == "endpoint":
            altered["connection"].update(host="192.0.2.77", port=6543)
            altered["authority"]["endpoint"].update(host="192.0.2.77", port=6543)
        else:
            altered["authority"]["server"]["fixtureId"] = "b" * 64
        with pytest.raises(ValueError, match="provisioned_source_authority"):
            monitor.provisioned(monitor.authority(altered), record)
        with pytest.raises(ValueError, match="authority_changed"):
            monitor.sample(altered, sample([]), connect=lambda **_: Connection([]))
    rotated = copy.deepcopy(original)
    rotated["connection"]["password"] = "rotated-private-password"
    assert monitor.authority(rotated) == scope


def test_review_captured_backend_label_drift_cannot_be_disappearance():
    registry = []

    class FilteredConnection(Connection):
        def execute(self, sql, parameters=None):
            self.calls.append((sql, parameters))
            if parameters:
                if "ANY" in sql:
                    self.rows = [
                        value for value in registry if value["pid"] in parameters[0]
                    ]
                else:
                    self.rows = [
                        value
                        for value in registry
                        if (
                            value["datname"],
                            value["usename"],
                            value["application_name"],
                        )
                        == tuple(parameters)
                    ]
            return self

    def connect(**_):
        return FilteredConnection([])

    baseline = monitor.sample(config(), connect=connect)
    registry.append(row())
    active = monitor.sample(config(), baseline, connect=connect)
    registry[0]["application_name"] = "other-visible-application"
    with pytest.raises(ValueError, match="identity|label"):
        monitor.sample(config(), active, connect=connect)


@pytest.mark.parametrize(
    "field,value",
    [
        ("application_name", "renamed"),
        ("usename", "foreign"),
        ("datname", "foreign"),
        ("state", None),
        ("backend_start", None),
    ],
)
def test_captured_rechecks_ignore_discovery_filters_and_fail_unknown_visibility(
    field, value
):
    active = sample([row()], sample([]))
    connection = Connection([], checked=[{**row(), field: value}])
    with pytest.raises(ValueError, match="identity|visibility"):
        monitor.sample(config(), active, connect=lambda **_: connection)
    sql, parameters = connection.calls[-1]
    assert "pid = ANY(%s)" in sql
    assert (
        "datname =" not in sql
        and "usename =" not in sql
        and "application_name =" not in sql
    )
    assert parameters == ([row()["pid"]],)


def test_review_private_ancestor_and_fifo_are_promptly_rejected(tmp_path):
    tmp_path.chmod(0o700)
    directory = tmp_path / "real"
    directory.mkdir(mode=0o700)
    path = directory / "private.json"
    monitor.private_write(path, {"private": True})
    alias = tmp_path / "alias"
    alias.symlink_to(directory, target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        monitor.private_read(alias / path.name)


def test_review_fifo_is_rejected_before_blocking_open(tmp_path):
    tmp_path.chmod(0o700)
    directory = tmp_path
    fifo = directory / "private.fifo"
    os.mkfifo(fifo, 0o600)
    script = "import importlib.util,sys; s=importlib.util.spec_from_file_location('monitor',sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)\ntry: m.private_read(sys.argv[2])\nexcept (OSError,ValueError): sys.exit(0)\nsys.exit(1)"
    result = subprocess.run(
        [sys.executable, "-c", script, str(SPEC.origin), str(fifo)],
        timeout=1,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0


def test_unsafe_cli_input_creates_no_output_or_lock(tmp_path, monkeypatch, capsys):
    tmp_path.chmod(0o700)
    actual = tmp_path / "actual"
    actual.mkdir(mode=0o700)
    config_path = actual / "config.json"
    monitor.private_write(config_path, config())
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    fifo = tmp_path / "config.fifo"
    os.mkfifo(fifo, 0o600)
    leaf = tmp_path / "config-link.json"
    leaf.symlink_to(config_path)
    for index, unsafe in enumerate((alias / config_path.name, leaf, fifo)):
        output = tmp_path / f"rejected-{index}.json"
        monkeypatch.setattr(
            sys,
            "argv",
            ["source_monitor.py", "--config", str(unsafe), "--output", str(output)],
        )
        assert monitor.main() == 1
        assert not output.exists()
        assert not Path(str(output) + ".lock").exists()
        assert "password" not in capsys.readouterr().out


def test_private_input_open_and_read_races_are_rejected(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    path = tmp_path / "private.json"
    monitor.private_write(path, {"private": True})
    original_open = monitor.os.open
    replaced = False

    def replace_before_open(name, flags, *args, **kwargs):
        nonlocal replaced
        if name == path.name and not replaced:
            replaced = True
            monitor.private_write(path, {"replacement": True})
        return original_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(monitor.os, "open", replace_before_open)
    with pytest.raises(ValueError, match="file_changed"):
        monitor.private_read(path)
    monkeypatch.setattr(monitor.os, "open", original_open)
    original_read = monitor.os.read
    modified = False

    def modify_during_read(*args):
        nonlocal modified
        result = original_read(*args)
        if not modified:
            modified = True
            path.write_text('{"modified": true}')
        return result

    monkeypatch.setattr(monitor.os, "read", modify_during_read)
    with pytest.raises(ValueError, match="file_changed"):
        monitor.private_read(path)


def test_private_parent_alias_race_and_unknown_owner_cannot_read(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    parent, replacement = tmp_path / "real", tmp_path / "replacement"
    parent.mkdir(mode=0o700)
    replacement.mkdir(mode=0o700)
    path = parent / "private.json"
    monitor.private_write(path, {"private": True})
    monitor.private_write(replacement / path.name, {"foreign": True})
    original_read = monitor.os.read
    moved = False

    def move_parent_during_read(*args):
        nonlocal moved
        result = original_read(*args)
        if not moved:
            moved = True
            parent.rename(tmp_path / "retained")
            parent.symlink_to(replacement, target_is_directory=True)
        return result

    monkeypatch.setattr(monitor.os, "read", move_parent_during_read)
    with pytest.raises((ValueError, OSError)):
        monitor.private_read(path)
    monkeypatch.setattr(monitor.os, "read", original_read)
    assert monitor.private_read(replacement / path.name) == {"foreign": True}
    monkeypatch.setattr(monitor.os, "geteuid", lambda: 999999)
    with pytest.raises(ValueError, match="ancestor|directory"):
        monitor.private_read(replacement / path.name)


def test_cli_failure_is_private_and_foreign_receipt_is_preserved(
    tmp_path, monkeypatch, capsys
):
    tmp_path.chmod(0o700)
    value = config()
    provisioning = tmp_path / "provisioning-private.json"
    value["authority"]["provisioningReceipt"] = str(provisioning)
    scope = monitor.authority(value)
    monitor.private_write(provisioning, {"version": 2, "status": "ready", **scope})
    config_path = tmp_path / "config-private.json"
    output = tmp_path / "source-private.json"
    monitor.private_write(config_path, value)
    monkeypatch.setattr(
        sys,
        "argv",
        ["source_monitor.py", "--config", str(config_path), "--output", str(output)],
    )

    def denied(*_):
        raise PermissionError("private-deterministic-password")

    monkeypatch.setattr(monitor, "sample", denied)
    assert monitor.main() == 1
    failed = monitor.private_read(output)
    assert failed["status"] == "unavailable"
    assert "password" not in str(failed)
    assert "password" not in capsys.readouterr().out
    # A failed run cannot be retried into a passing release observation.
    monkeypatch.setattr(monitor, "sample", lambda *_: {"status": "available"})
    assert monitor.main() == 1
    assert monitor.private_read(output) == failed
    output.unlink()
    foreign = {"authority": {"runId": "foreign-run"}, "status": "available"}
    monitor.private_write(output, foreign)
    assert monitor.main() == 1
    assert monitor.private_read(output) == foreign


def test_monitor_lock_and_provisioning_authority_are_bounded(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / "source.json"
    with monitor.monitor_lock(path):
        assert Path(str(path) + ".lock").stat().st_mode & 0o077 == 0
    scope = monitor.authority(config())
    for record in (
        {},
        {"version": 2, "status": "ready", **scope, "exclusiveToRun": False},
    ):
        with pytest.raises(ValueError, match="provisioned_source_authority"):
            monitor.provisioned(scope, record)


@pytest.mark.skipif(
    __import__("os").environ.get("SCHEMII_LOAD_SOURCE_LIVE") != "1",
    reason="opt-in exact run-owned source monitor oracle; coordinator provisions authority",
)
def test_opt_in_actual_source_acquire_release():
    import os

    value = monitor.private_read(os.environ["SCHEMII_LOAD_SOURCE_CONFIG"])
    scope = monitor.authority(value)
    monitor.provisioned(scope, monitor.private_read(scope["provisioningReceipt"]))
    # This test creates only its own read-only connection, with no source writes.
    baseline = monitor.sample(value)
    with monitor.psycopg.connect(
        **value["connection"], application_name="schemii", passfile="/dev/null"
    ) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        captured = monitor.sample(value, baseline)
        assert captured["ownedBackends"] == 1
        assert captured["captured"][0]["pid"] == connection.info.backend_pid
    released = monitor.sample(value, captured)
    assert released["releaseVerified"]


@pytest.mark.skipif(
    os.environ.get("SCHEMII_LOAD_SOURCE_LIVE") != "1",
    reason="opt-in renamed-backend fixture proof; coordinator provisions authority",
)
def test_opt_in_renamed_live_backend_cannot_be_released():
    value = monitor.private_read(os.environ["SCHEMII_LOAD_SOURCE_CONFIG"])
    scope = monitor.authority(value)
    monitor.provisioned(scope, monitor.private_read(scope["provisioningReceipt"]))
    baseline = monitor.sample(value)
    with monitor.psycopg.connect(
        **value["connection"], application_name="schemii", passfile="/dev/null"
    ) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        active = monitor.sample(value, baseline)
        connection.execute(
            "SELECT pg_catalog.set_config('application_name', 'schemii_load_renamed_control', false)"
        )
        with pytest.raises(ValueError, match="label_changed"):
            monitor.sample(value, active)
