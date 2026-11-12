"""Pure controls for private fixed-pressure recipe; no live HTTP/PG processes."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest

from dev.pressure import probe as p
from dev.pressure.controller import Refused, private_write


def test_pressure_errors_preserve_cookie_and_do_not_accept_safe_looking_200():
    client = SimpleNamespace(cookies={"cookie": "private-test-token"})
    response = SimpleNamespace(
        status_code=503,
        headers={},
        json=lambda: {
            "error": {"code": "metadata_capacity_exceeded", "retryable": True}
        },
    )
    assert (
        p.assert_retryable(response, dict(client.cookies), client)
        == "metadata_capacity_exceeded"
    )
    response.headers["set-cookie"] = "must-not-clear"
    with pytest.raises(Refused, match="safe_pressure_response"):
        p.assert_retryable(response, dict(client.cookies), client)
    response.headers.clear()
    response.status_code = 200
    with pytest.raises(Refused, match="safe_pressure_response"):
        p.assert_retryable(response, dict(client.cookies), client)


def test_private_fixture_requires_independent_nonloopback_database_endpoints(tmp_path):
    manifest = {
        "bootstrapConsent": "explicit-disposable-fixture-only",
        "run": "pressure_" + "a" * 32,
        "endpoints": {"metadata": "172.30.0.2", "source": "172.30.0.3"},
    }
    private_write(tmp_path / "journal.json", manifest)
    assert p.Probe(tmp_path).manifest == manifest
    for invalid in ["127.0.0.1", "8.8.8.8", "172.30.0.2"]:
        manifest["endpoints"]["source"] = invalid
        private_write(tmp_path / "journal.json", manifest)
        with pytest.raises(Refused):
            p.Probe(tmp_path)


def test_lifespan_protocol_accepts_only_fixed_controller_confirmation(
    monkeypatch, capsys
):
    monkeypatch.setattr(p.sys, "stdin", io.StringIO('{"confirmed":true}\n'))
    p.operation("stop-owned-app")
    assert json.loads(capsys.readouterr().out) == {"operation": "stop-owned-app"}
    monkeypatch.setattr(p.sys, "stdin", io.StringIO('{"confirmed":false}\n'))
    with pytest.raises(Refused, match="lifecycle_not_confirmed"):
        p.operation("start-owned-app")
    with pytest.raises(Refused, match="fixed_lifecycle_operation"):
        p.operation("restart-daemon")


def test_noncanonical_public_origin_and_arbitrary_source_sql_are_rejected_before_io():
    probe = p.Probe.__new__(p.Probe)
    with pytest.raises(Refused, match="canonical_https_only"):
        probe.client("http://localhost:8000")
    with pytest.raises(Refused, match="fixed_source_statement"):
        probe.execute(None, {}, {}, "DROP DATABASE retained")


def test_failed_external_source_sample_never_reuses_old_success(tmp_path, monkeypatch):
    probe = p.Probe.__new__(p.Probe)
    probe.directory = tmp_path
    ledger = {
        "authority": {"database": "pressure_source", "role": "pressure_source"},
        "releaseVerified": True,
        "ownedBackends": 0,
    }
    private_write(tmp_path / "source-monitor-ledger.json", ledger)
    monkeypatch.setattr(
        p, "command", lambda *args, **kwargs: '{"status":"unavailable"}'
    )
    with pytest.raises(Refused, match="source_monitor_unavailable"):
        probe.source_sample()
    assert p.private_read(tmp_path / "source-monitor-ledger.json") == ledger


def test_metadata_witness_has_exact_private_database_role_application_scope():
    probe = p.Probe.__new__(p.Probe)

    class Monitor:
        def execute(self, sql, parameters):
            assert parameters == (
                "pressure_metadata",
                "pressure_app",
                "schemii-metadata",
            )
            return self

        def fetchall(self):
            return [{"unexpected": True}] * 3

    with pytest.raises(Refused, match="normal_backend_bound"):
        probe.metadata_sample(Monitor())


def test_probe_without_launcher_handshake_does_not_open_fixture_or_database(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        p.sys, "argv", ["probe.py", "--directory", str(tmp_path), "--phase", "prepare"]
    )
    monkeypatch.setattr(p.sys, "stdin", io.StringIO('{"authorized":false}\n'))
    calls = []
    monkeypatch.setattr(p, "Probe", lambda directory: calls.append(directory))
    assert p.main() == 1
    assert calls == []
    assert json.loads(capsys.readouterr().out)["result"]["acceptance"] is False


def test_captured_metadata_close_proof_cannot_lose_renamed_live_backend():
    from datetime import datetime, timezone

    probe = p.Probe.__new__(p.Probe)
    started = datetime.now(timezone.utc)
    captured = [
        {
            "pid": 4321,
            "backend_start": started.isoformat(),
            "application_name": "schemii-metadata",
        }
    ]

    class Monitor:
        def execute(self, sql, parameters):
            assert parameters == ([4321],)
            return self

        def fetchall(self):
            return [
                {"pid": 4321, "backend_start": started, "application_name": "renamed"}
            ]

    assert probe.metadata_closed(Monitor(), captured) is False


def test_source_provision_receipt_binds_endpoint_server_and_owned_database_marker(
    tmp_path, monkeypatch
):
    from datetime import datetime, timezone
    import hashlib

    probe = p.Probe.__new__(p.Probe)
    probe.directory = tmp_path
    probe.run = "pressure_" + "a" * 32
    probe.manifest = {"endpoints": {"source": "172.30.0.3"}}
    probe.identities = []
    for name in ("app", "monitor", "blocker", "source"):
        path = tmp_path / (name + "_password")
        path.write_text("a" * 40)
        path.chmod(0o600)
    witness = tmp_path / "source_monitor.py"
    witness.touch()
    monkeypatch.setattr(p, "SOURCE_MODULE", witness)
    fixture_id = hashlib.sha256((probe.run + ":source-fixture").encode()).hexdigest()
    started = datetime(2026, 11, 12, 1, 2, 3, 123456, tzinfo=timezone.utc)
    commands = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def execute(self, statement):
            commands.append(
                statement if isinstance(statement, str) else statement.as_string()
            )
            return self

        def fetchone(self):
            return {
                "address": "172.30.0.3",
                "port": 5432,
                "started": started,
                "database_oid": 16384,
                "marker": "schemii-load:" + fixture_id,
            }

    monkeypatch.setattr(probe, "connect", lambda *_: Connection())

    def sample():
        config = p.private_read(tmp_path / "source-monitor-config.json")
        provision = p.private_read(tmp_path / "source-provisioning.json")
        assert config["version"] == provision["version"] == 2
        scope = config["authority"]
        assert scope["endpoint"] == {
            "host": "172.30.0.3",
            "port": 5432,
            "sslmode": "require",
        }
        assert scope["server"] == {
            "address": "172.30.0.3",
            "port": 5432,
            "postmasterStart": started.isoformat(timespec="microseconds"),
            "databaseOid": 16384,
            "fixtureId": fixture_id,
        }
        assert scope["server"] == provision["server"]
        assert scope["endpoint"] == provision["endpoint"]
        return {"version": 2, "ownedBackends": 0, "releaseVerified": False}

    monkeypatch.setattr(probe, "source_sample", sample)
    assert probe.prepare()["phase"] == "prepared"
    assert any(
        "COMMENT ON DATABASE pressure_source" in sql and fixture_id in sql
        for sql in commands
    )


def test_available_source_reply_cannot_adopt_another_server_ledger(
    tmp_path, monkeypatch
):
    probe = p.Probe.__new__(p.Probe)
    probe.directory = tmp_path
    authority = {
        "kind": "exclusive_source_role",
        "exclusiveToRun": True,
        "database": "pressure_source",
        "role": "pressure_source",
        "applicationName": "schemii",
        "endpoint": {"host": "172.30.0.3", "port": 5432, "sslmode": "require"},
        "server": {"fixtureId": "a" * 64},
    }
    config = {"version": 2, "runId": "load_" + "a" * 16, "authority": authority}
    private_write(tmp_path / "source-monitor-config.json", config)
    private_write(
        tmp_path / "source-monitor-ledger.json",
        {
            "version": 2,
            "authority": {**authority, "runId": config["runId"]},
            "serverIdentity": {"fixtureId": "b" * 64},
            "ownedBackends": 0,
            "releaseVerified": True,
        },
    )
    monkeypatch.setattr(p, "command", lambda *args, **kwargs: '{"status":"available"}')
    with pytest.raises(Refused, match="source_monitor_server_binding"):
        probe.source_sample()
