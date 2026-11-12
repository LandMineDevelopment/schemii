"""Pure controls for private fixed-pressure recipe; no live HTTP/PG processes."""

from __future__ import annotations

import io
import json
import builtins
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
        "profile": p.PRESSURE_PROFILE.receipt(),
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
    p.operation("start-owned-app")
    assert json.loads(capsys.readouterr().out) == {"operation": "start-owned-app"}
    monkeypatch.setattr(p.sys, "stdin", io.StringIO('{"confirmed":false}\n'))
    with pytest.raises(Refused, match="lifecycle_not_confirmed"):
        p.operation("start-owned-app")
    with pytest.raises(Refused, match="fixed_lifecycle_operation"):
        p.operation("restart-daemon")


def test_noncanonical_public_origin_and_arbitrary_source_sql_are_rejected_before_io(
    monkeypatch,
):
    original_import = builtins.__import__

    def reject_client_import(name, *args, **kwargs):
        assert name not in {"httpx", "httpx2"}, (
            "Invalid origins must fail before client loading"
        )
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_client_import)
    probe = p.Probe.__new__(p.Probe)
    with pytest.raises(Refused, match="canonical_https_only"):
        probe.client("http://localhost:8000")
    with pytest.raises(Refused, match="fixed_source_statement"):
        probe.execute(None, {}, {}, "DROP DATABASE retained")


@pytest.mark.parametrize("origin", [p.LOCAL, p.PREVIEW])
def test_canonical_clients_use_the_declared_dependency_without_connecting(origin):
    import httpx2

    probe = p.Probe.__new__(p.Probe)
    with probe.client(origin) as client:
        assert isinstance(client, httpx2.Client)
        assert str(client.base_url) == origin
        assert client.headers["Origin"] == origin
        assert client.follow_redirects is False


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
    probe.profile = p.PRESSURE_PROFILE
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
                "maximum_connections": 24,
                "shared_buffers": "32MB",
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


def test_stop_confirmation_without_stock_lifespan_evidence_is_rejected(monkeypatch):
    monkeypatch.setattr(p.sys, "stdin", io.StringIO('{"confirmed":true}\n'))
    with pytest.raises(Refused, match="stock_lifespan_evidence_missing"):
        p.operation("stop-owned-app")


def test_fixture_wrapper_preserves_stock_messages_and_observes_real_owner_counts(
    tmp_path, monkeypatch
):
    import asyncio
    from dataclasses import asdict
    import psycopg
    from dev.pressure.app import ObservedApplication
    from schemii.common.errors import (
        MetadataCapacityError,
        MetadataStorageUnavailableError,
    )
    from schemii.common.metadata.config import MetadataConfig
    from schemii.common.metadata.database import (
        MetadataConnectionFactory,
        MetadataReadinessProbe,
    )

    def forbidden_contact(*args, **kwargs):
        raise AssertionError("Real database contact/query forbidden")

    monkeypatch.setattr(psycopg, "connect", forbidden_contact)
    tmp_path.chmod(0o700)
    password = tmp_path / "test-owned-password"
    password.write_text("offline-test-owned-password")
    password.chmod(0o600)
    config = MetadataConfig(
        dsn="host=invalid.test dbname=offline user=offline",
        password_file=password,
        encryption_key_file=tmp_path / "never-open-key",
        maximum_connections=2,
    )
    natives, connect_calls = [], []
    fail_connect = False

    class Native:
        closed = False

        def close(self):
            self.closed = True

        cursor = forbidden_contact

    def offline_connect(*args, **kwargs):
        connect_calls.append(True)
        if fail_connect:
            raise psycopg.OperationalError("offline injected connection failure")
        connection = Native()
        natives.append(connection)
        return connection

    normal = MetadataConnectionFactory(config, connect=offline_connect)
    ready = MetadataConnectionFactory(
        config, connect=offline_connect, maximum_connections=2
    )
    held = [normal(), normal(), ready()]
    with pytest.raises(MetadataCapacityError):
        normal()
    assert len(connect_calls) == 3

    class Application:
        state = SimpleNamespace(
            services=SimpleNamespace(
                metadata=SimpleNamespace(
                    connection_factory=normal,
                    readiness_probe=MetadataReadinessProbe(ready),
                )
            )
        )

        async def __call__(self, scope, receive, send):
            nonlocal fail_connect
            assert await receive() == {"type": "lifespan.startup"}
            await send({"type": "lifespan.startup.complete"})
            assert await receive() == {"type": "lifespan.shutdown"}
            for connection in held:
                connection.close()
            fail_connect = True
            with pytest.raises(MetadataStorageUnavailableError):
                normal()
            normal.close()
            ready.close()
            await send({"type": "lifespan.shutdown.complete"})

    wrapper = ObservedApplication(Application(), "pressure_" + "a" * 32, tmp_path)
    events = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
    sent, running = [], []

    async def receive():
        return next(events)

    async def send(message):
        sent.append(message)
        if message["type"] == "lifespan.startup.complete":
            running.append(wrapper.snapshot())

    asyncio.run(wrapper({"type": "lifespan"}, receive, send))
    result = json.loads((tmp_path / "observation.json").read_text())
    assert sent == [
        {"type": "lifespan.startup.complete"},
        {"type": "lifespan.shutdown.complete"},
    ]
    assert running[0]["normal"]["counts"]["active"] == 2
    assert running[0]["normal"]["counts"]["rejected"] == 1
    assert running[0]["readiness"]["counts"]["active"] == 1
    assert result["stage"] == "shutdown-complete"
    for name, owner in (("normal", normal), ("readiness", ready)):
        counts = asdict(owner.admission_snapshot())
        counts["maximum"] = counts.pop("maximum_connections")
        assert result[name] == {"status": "observed", "counts": counts}
        assert counts["closed"] is True and counts["active"] == 0
    assert result["normal"]["counts"]["admitted"] == 3
    assert result["normal"]["counts"]["connection_failures"] == 1
    assert result["normal"]["counts"]["connection_seconds"] > 0
    assert len(connect_calls) == 4 and all(connection.closed for connection in natives)
    assert result["instrumentation"]["stockCapacityEligible"] is False
    assert wrapper.thread is not None and not wrapper.thread.is_alive()


def test_owner_observation_failure_stays_unknown_and_never_synthesizes_counts():
    from dev.pressure.app import owner_snapshot

    assert owner_snapshot(None) == {
        "status": "unknown",
        "reason": "owner_snapshot_failed",
    }


def test_peak_requires_actual_owner_sample_inside_pressure_window_same_process():
    from datetime import datetime, timezone, timedelta

    at = datetime.now(timezone.utc)
    event = {
        "at": at.isoformat(),
        "stage": "running",
        "normal": {
            "status": "observed",
            "counts": {"active": 2, "peak_active": 2, "maximum": 2, "closed": False},
        },
    }
    sample = {
        "observation": {
            "process": {"pid": 7},
            "at": (at + timedelta(seconds=1)).isoformat(),
            "ownerHistory": [event],
            "normal": {"counts": {"active": 0}},
        }
    }
    assert (
        p.observed_peak(
            sample, process_pid=7, started_at=(at - timedelta(seconds=1)).isoformat()
        )
        == event
    )
    with pytest.raises(Refused, match="saturated_owner_process_changed"):
        p.observed_peak(sample, process_pid=8, started_at=at.isoformat())
    with pytest.raises(RuntimeError, match="saturated_owner_count_not_observed"):
        p.observed_peak(
            sample, process_pid=7, started_at=(at + timedelta(seconds=1)).isoformat()
        )
    event["normal"]["counts"]["active"] = 0
    with pytest.raises(RuntimeError, match="saturated_owner_count_not_observed"):
        p.observed_peak(sample, process_pid=7, started_at=at.isoformat())


def test_actual_metadata_owners_are_observed_without_connecting_before_and_after_close(
    tmp_path,
):
    from dataclasses import asdict
    from dev.pressure.app import ObservedApplication
    from schemii.common.metadata.config import MetadataConfig
    from schemii.common.metadata.database import (
        MetadataConnectionFactory,
        MetadataReadinessProbe,
    )

    calls = []

    def forbidden_connect(*args, **kwargs):
        calls.append(True)
        raise AssertionError("Database contact forbidden")

    config = MetadataConfig(
        dsn="host=invalid.test dbname=offline user=offline",
        password_file="/never-open/offline-password",
        encryption_key_file="/never-open/offline-key",
        maximum_connections=2,
    )
    normal = MetadataConnectionFactory(config, connect=forbidden_connect)
    readiness = MetadataConnectionFactory(
        config, connect=forbidden_connect, maximum_connections=2
    )
    app = SimpleNamespace(
        state=SimpleNamespace(
            services=SimpleNamespace(
                metadata=SimpleNamespace(
                    connection_factory=normal,
                    readiness_probe=MetadataReadinessProbe(readiness),
                )
            )
        )
    )
    tmp_path.chmod(0o700)
    wrapper = ObservedApplication(app, "pressure_" + "a" * 32, tmp_path)
    wrapper.startup_complete, wrapper.stage = True, "running"
    for closed in (False, True):
        if closed:
            normal.close()
            readiness.close()
            wrapper.stage = "shutdown-complete"
        wrapper.write()
        value = json.loads((tmp_path / "observation.json").read_text())
        for name, owner in (("normal", normal), ("readiness", readiness)):
            expected = asdict(owner.admission_snapshot())
            expected["maximum"] = expected.pop("maximum_connections")
            assert value[name] == {"status": "observed", "counts": expected}
            assert value[name]["counts"]["closed"] is closed
            assert value[name]["counts"]["active"] == 0
        assert not calls


@pytest.mark.parametrize(
    "changes",
    [
        {"maximum_connections": 0},
        {"maximum_connections": True},
        {"active": -1},
        {"active": 1},
        {"peak_active": 3},
        {"admitted": True},
        {"rejected": -1},
        {"connection_failures": 0.0},
        {"connection_seconds": float("nan")},
        {"connection_seconds": float("inf")},
        {"connection_seconds": -0.1},
        {"connection_seconds": True},
        {"closed": 1},
    ],
)
def test_invalid_actual_owner_snapshot_never_becomes_observed(changes):
    from dataclasses import replace
    from dev.pressure.app import owner_snapshot
    from schemii.common.metadata.database import MetadataAdmissionSnapshot

    snapshot = MetadataAdmissionSnapshot(2, 0, 0, 0, 0, 0, 0.0, False)
    owner = SimpleNamespace(admission_snapshot=lambda: replace(snapshot, **changes))
    assert owner_snapshot(owner) == {
        "status": "unknown",
        "reason": "owner_snapshot_failed",
    }


@pytest.mark.parametrize("shape", ["missing", "unexpected", "old-dto"])
def test_incomplete_or_new_owner_contract_stays_unknown(shape):
    from dataclasses import asdict, make_dataclass
    from dev.pressure.app import owner_snapshot
    from schemii.common.metadata.database import MetadataAdmissionSnapshot

    values = asdict(MetadataAdmissionSnapshot(2, 0, 0, 0, 0, 0, 0.0, False))
    if shape == "missing":
        values.pop("connection_seconds")
    elif shape == "unexpected":
        values["new_owner_counter"] = 0
    else:
        values["maximum"] = values.pop("maximum_connections")
    ChangedSnapshot = make_dataclass(
        "UnsupportedContract", [(name, object) for name in values]
    )
    owner = SimpleNamespace(admission_snapshot=lambda: ChangedSnapshot(**values))
    assert owner_snapshot(owner) == {
        "status": "unknown",
        "reason": "owner_snapshot_failed",
    }


@pytest.mark.parametrize("profile_name", ["pressure-v1", "representative-load-v1"])
def test_actual_repository_composition_observer_matches_stock_readiness_budget(
    tmp_path, monkeypatch, profile_name
):
    import base64
    import psycopg
    from dev.pressure import controller as c
    from dev.pressure.app import ObservedApplication
    import schemii.common.metadata.factory as factory
    from schemii.common.metadata.database import MetadataConnectionFactory

    calls, migrations = [], []

    def forbidden_connect(*args, **kwargs):
        calls.append(True)
        raise AssertionError("Database contact forbidden")

    def offline_migration(migrator):
        migrations.append(migrator._connection_factory)
        assert (
            migrator._connection_factory.admission_snapshot().maximum_connections == 1
        )
        return 0

    monkeypatch.setattr(psycopg, "connect", forbidden_connect)
    monkeypatch.setattr(factory.MetadataMigrator, "migrate", offline_migration)
    tmp_path.chmod(0o700)
    key = tmp_path / "test-owned-key"
    key.write_text(base64.b64encode(bytes(32)).decode() + "\n")
    key.chmod(0o600)
    profile = c.selected_profile(profile_name)
    environment = {
        "SCHEMII_STORAGE_MODE": "postgresql",
        "SCHEMII_METADATA_DSN": "host=metadata dbname=offline user=offline",
        "SCHEMII_METADATA_PASSWORD_FILE": "/never-open/password",
        "SCHEMII_METADATA_ENCRYPTION_KEY_FILE": str(key),
    }
    if profile is c.PRESSURE_PROFILE:
        environment.update(
            {
                "SCHEMII_METADATA_MAXIMUM_CONNECTIONS": "2",
                "SCHEMII_METADATA_STATEMENT_TIMEOUT_MS": str(profile.statement_ms),
                "SCHEMII_METADATA_LOCK_TIMEOUT_MS": str(profile.lock_ms),
                "SCHEMII_METADATA_IDLE_TRANSACTION_TIMEOUT_MS": str(profile.idle_ms),
                "SCHEMII_METADATA_SHUTDOWN_TIMEOUT_SECONDS": str(
                    profile.shutdown_seconds
                ),
            }
        )
    repositories = factory.create_metadata_repositories(environment)
    assert isinstance(repositories.connection_factory, MetadataConnectionFactory)
    normal = repositories.connection_factory
    assert normal.admission_snapshot().maximum_connections == profile.normal_maximum
    assert normal._statement_timeout_ms == profile.statement_ms
    assert normal._lock_timeout_ms == profile.lock_ms
    assert normal._idle_transaction_timeout_ms == profile.idle_ms
    assert normal._config.shutdown_timeout_seconds == profile.shutdown_seconds
    readiness = repositories.readiness_probe._connection_factory
    assert isinstance(readiness, MetadataConnectionFactory)
    assert readiness.admission_snapshot().maximum_connections == 2
    assert len(migrations) == 1 and migrations[0].admission_snapshot().closed is True
    application = SimpleNamespace(
        state=SimpleNamespace(services=SimpleNamespace(metadata=repositories))
    )
    wrapper = ObservedApplication(application, "pressure_" + "a" * 32, tmp_path)
    wrapper.stage, wrapper.startup_complete = "running", True
    value = wrapper.snapshot()
    # The owner data is the actual composition. Only host UID differs from the
    # launcher's fixed container UID in this no-runtime policy control.
    value["process"]["uid"] = 10001
    c.validate_observation(
        value,
        wrapper.run,
        {"startedAt": "2026-01-01T00:00:00Z"},
        fresh=True,
        profile=profile,
    )
    repositories.close()
    closed = wrapper.snapshot()
    assert closed["normal"]["counts"]["closed"] is True
    assert closed["readiness"]["counts"]["closed"] is True
    assert (
        closed["normal"]["counts"]["active"]
        == closed["readiness"]["counts"]["active"]
        == 0
    )
    assert calls == []


def test_fixed_source_admission_profile_matches_actual_stock_config_and_owner_without_io(
    monkeypatch,
):
    from pathlib import Path
    import psycopg
    from dev.pressure import controller as c
    from schemii.common.admin_config import AdminConfig
    from schemii.common.postgres.gateway import PsycopgPostgresGateway

    def forbidden(*args, **kwargs):
        raise AssertionError("Database/query contact forbidden")

    monkeypatch.setattr(psycopg, "connect", forbidden)
    config = AdminConfig.from_env(
        {
            "SCHEMII_CONFIG_FILE": str(
                Path(__file__).resolve().parents[1] / "dev/schemii.toml"
            )
        }
    )
    gateway = PsycopgPostgresGateway(
        connect_factory=forbidden,
        maximum_connections=config.postgres_connections.maximum_total,
        maximum_connections_per_identity=config.postgres_connections.maximum_per_identity,
        connection_acquire_timeout=config.postgres_connections.acquire_timeout_seconds,
    )
    snapshot = gateway.observation_snapshot()["ordinary"]
    assert snapshot["maximum"] == 20 and snapshot["maximumPerProfileRevision"] == 4
    declared = c.LOAD_PROFILE.receipt()["sourceAdmission"]
    assert (
        declared
        == c.PRESSURE_PROFILE.receipt()["sourceAdmission"]
        == {
            "ordinaryTotal": 20,
            "ordinaryPerIdentity": 4,
            "retainedTotal": 19,
            "retainedPerIdentity": 3,
        }
    )
    owner = gateway._connection_capacity
    identities = [f"owned-{index // 3}" for index in range(19)]
    for identity in identities:
        assert owner.acquire(identity, 0, retained=True) is None
    assert owner.acquire("owned-new", 0, retained=True)[1:] == (19, 19)
    assert owner.acquire("owned-0", 0, retained=True)[1:] == (3, 3)
    for identity in identities:
        owner.release(identity, retained=True)
    assert gateway.observation_snapshot()["ordinary"]["permits"] == 0


@pytest.mark.parametrize(
    "profile_name,maximum,buffers",
    [("pressure-v1", 100, "128MB"), ("representative-load-v1", 24, "32MB")],
)
def test_actual_database_settings_must_match_fixed_profile_before_any_provisioning(
    profile_name, maximum, buffers, tmp_path, monkeypatch
):
    from dev.pressure import controller as c

    probe = p.Probe.__new__(p.Probe)
    probe.profile = c.selected_profile(profile_name)
    probe.run, probe.directory = "pressure_" + "a" * 32, tmp_path
    commands = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def execute(self, statement):
            commands.append(statement)
            return self

        def fetchone(self):
            return {"maximum_connections": maximum, "shared_buffers": buffers}

    monkeypatch.setattr(probe, "connect", lambda *args: Connection())
    with pytest.raises(Refused, match="fixed_database_profile_mismatch"):
        probe.prepare()
    assert len(commands) == 1 and "current_setting('max_connections')" in commands[0]
    assert list(tmp_path.iterdir()) == []
