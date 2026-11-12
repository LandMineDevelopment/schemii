"""Deterministic source witness controls; no PostgreSQL or runtime is started."""

import copy
import importlib.util
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
        "version": 1,
        "runId": "load_0123456789abcdef",
        "authority": {
            "kind": "exclusive_source_role",
            "exclusiveToRun": True,
            "database": "disposable_database",
            "role": "disposable_run_role",
            "applicationName": "schemii",
            "provisioningReceipt": "/private/explicit-owner-receipt.json",
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
    def __init__(self, rows, own=None):
        self.rows = rows
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
        return self.own

    def fetchall(self):
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


def test_no_adoption_replacement_or_changed_authority():
    with pytest.raises(ValueError, match="baseline_not_empty"):
        sample([row()])
    baseline = sample([])
    active = sample([row()], baseline)
    replacement = {
        **row(),
        "backend_start": datetime(2026, 11, 11, 11, tzinfo=timezone.utc),
    }
    with pytest.raises(ValueError, match="backend_replaced"):
        sample([replacement], active)
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


def test_cli_failure_is_private_and_foreign_receipt_is_preserved(
    tmp_path, monkeypatch, capsys
):
    tmp_path.chmod(0o700)
    value = config()
    provisioning = tmp_path / "provisioning-private.json"
    value["authority"]["provisioningReceipt"] = str(provisioning)
    scope = monitor.authority(value)
    monitor.private_write(provisioning, {"version": 1, "status": "ready", **scope})
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
        {"version": 1, "status": "ready", **scope, "exclusiveToRun": False},
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
