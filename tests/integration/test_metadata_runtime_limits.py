"""Owned real PostgreSQL blocking/admission/recovery oracles for metadata."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import asdict, replace
import hashlib
import json
import os
from threading import Event, Lock
from time import monotonic, sleep, time
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import schemii.main as application_module
from schemii.common.admin_config import AdminConfig
from schemii.common.api.runtime import RuntimeConfig
from schemii.common.connections.models import PostgresConnectionCreate
from schemii.common.connections.policy import ConnectionTargetForbiddenError
from schemii.common.api.errors import ApiProblem, install_api_error_handlers
from schemii.common.auth.routes import AccountCreate
from schemii.common.auth.service import AuthService, COOKIE
from schemii.common.auth.middleware import AuthenticationMiddleware
from schemii.common.errors import MetadataCapacityError, MetadataStorageUnavailableError
from schemii.common.metadata.config import MetadataConfig
from schemii.common.metadata.database import (
    MetadataConnectionFactory,
    MetadataMigrationError,
    MetadataReadinessProbe,
)
from schemii.common.metadata.factory import create_metadata_repositories
import schemii.common.metadata.factory as factory_module
from schemii.common.metadata.migrations import (
    MIGRATION_PACKAGE as COMMON_MIGRATION_PACKAGE,
)
from schemii.schemii.metadata import MIGRATION_PACKAGE as SCHEMII_MIGRATION_PACKAGE
from schemii.schemoo.metadata.migrations import (
    MIGRATION_PACKAGE as SCHEMOO_MIGRATION_PACKAGE,
)
from schemii.schemer.metadata.migrations import (
    MIGRATION_PACKAGE as SCHEMER_MIGRATION_PACKAGE,
)
from schemii.schemii.ai.repository import PostgresAiRepository
from schemii.schemii.bulk_jobs.repository import JobRepository
from schemii.schemii.workspaces.models import WorkspaceCreateRecord


MIGRATION_PACKAGES = (
    COMMON_MIGRATION_PACKAGE,
    SCHEMII_MIGRATION_PACKAGE,
    SCHEMOO_MIGRATION_PACKAGE,
    SCHEMER_MIGRATION_PACKAGE,
)


def runtime_factory(postgres_metadata, **policy) -> MetadataConnectionFactory:
    config = MetadataConfig.from_env(postgres_metadata.environment)
    assert config is not None
    return MetadataConnectionFactory(
        replace(config, application_name="metadata_limits_" + uuid4().hex, **policy)
    )


def assert_backends_closed(postgres_metadata, application_name: str) -> None:
    deadline = monotonic() + 3
    while True:
        with postgres_metadata.connection_factory() as connection:
            count = connection.execute(
                "SELECT count(*) AS n FROM pg_stat_activity WHERE application_name=%s AND pid <> pg_backend_pid()",
                (application_name,),
            ).fetchone()["n"]
        if count == 0:
            return
        assert monotonic() < deadline, f"{count} owned metadata backends remain"
        sleep(0.02)


def effective_deadlines(connection) -> dict[str, int]:
    rows = connection.execute(
        "SELECT name,setting FROM pg_settings WHERE name IN "
        "('statement_timeout','lock_timeout','idle_in_transaction_session_timeout')"
    ).fetchall()
    return {row["name"]: int(row["setting"]) for row in rows}


@pytest.mark.parametrize("repository_type", ["bulk", "ai"])
def test_real_repository_statement_deadline_is_retryable_and_recovers(
    postgres_metadata, repository_type, record_property
) -> None:
    factory = runtime_factory(
        postgres_metadata, statement_timeout_ms=250, lock_timeout_ms=1000
    )
    repository = (
        JobRepository(factory)
        if repository_type == "bulk"
        else PostgresAiRepository(factory)
    )
    table = "schemii.bulk_jobs" if repository_type == "bulk" else "schemii.ai_chats"
    app = FastAPI()
    install_api_error_handlers(app)

    @app.get("/owned")
    def endpoint():
        return (
            repository.list(postgres_metadata.owner_id, "owned-workspace")
            if repository_type == "bulk"
            else repository.list_chats(postgres_metadata.owner_id)
        )

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            with postgres_metadata.connection_factory() as blocker:
                blocker.execute("LOCK TABLE " + table + " IN ACCESS EXCLUSIVE MODE")
                started = monotonic()
                response = client.get("/owned")
                elapsed = monotonic() - started
                assert 0.15 < elapsed < 2
                assert response.status_code == 503
                assert (
                    response.json()["error"]["code"] == "metadata_storage_unavailable"
                )
                assert response.json()["error"]["retryable"] is True
                assert "set-cookie" not in response.headers
                assert factory.admission_snapshot().active == 0
                assert_backends_closed(postgres_metadata, factory._application_name)
            assert client.get("/owned").json() == []
        record_property("repository_type", repository_type)
        record_property("repository_deadline_seconds", elapsed)
    finally:
        factory.close()
    assert_backends_closed(postgres_metadata, factory._application_name)


@pytest.mark.parametrize("repository_type", ["bulk", "ai"])
def test_repository_native_context_commits_and_rolls_back_owned_writes(
    postgres_metadata, repository_type
):
    factory = runtime_factory(postgres_metadata)
    repository = (
        JobRepository(factory)
        if repository_type == "bulk"
        else PostgresAiRepository(factory)
    )
    transaction = (
        repository._cursor if repository_type == "bulk" else repository._transaction
    )
    owner = postgres_metadata.owner_id
    try:
        with transaction() as executor:
            executor.execute(
                "INSERT INTO metadata.users(id,display_name) VALUES (%s,%s)",
                (owner, "committed"),
            )
        with postgres_metadata.connection_factory() as connection:
            assert (
                connection.execute(
                    "SELECT display_name FROM metadata.users WHERE id=%s", (owner,)
                ).fetchone()["display_name"]
                == "committed"
            )
        failure = ApiProblem(409, "owned_conflict", "Owned transaction conflict")
        with pytest.raises(ApiProblem) as raised:
            with transaction() as executor:
                executor.execute(
                    "UPDATE metadata.users SET display_name=%s WHERE id=%s",
                    ("must roll back", owner),
                )
                raise failure
        assert raised.value is failure
        with postgres_metadata.connection_factory() as connection:
            assert (
                connection.execute(
                    "SELECT display_name FROM metadata.users WHERE id=%s", (owner,)
                ).fetchone()["display_name"]
                == "committed"
            )
        assert factory.admission_snapshot().active == 0
    finally:
        factory.close()
    assert_backends_closed(postgres_metadata, factory._application_name)


def test_real_auth_saturation_keeps_session_and_authoritative_revocation(
    postgres_metadata, record_property
):
    factory = runtime_factory(postgres_metadata, maximum_connections=1)
    service = AuthService(factory, enabled=True, setup_token="integration-unused")
    username = "it_pressure_" + uuid4().hex
    role_id = "role_pressure_" + uuid4().hex
    user = service.create_user(
        AccountCreate(
            username=username,
            display_name="Owned pressure account",
            password="integration-pressure-password",
        )
    )
    token = uuid4().hex
    try:
        with service.store.transaction(write=True) as state:
            state["roles"][role_id] = dict(
                id=role_id,
                name=role_id,
                capabilities=["schemii:access"],
                user_ids=[user["id"]],
                connections=[],
                dashboards=[],
            )
        with postgres_metadata.connection_factory() as connection:
            connection.execute(
                "INSERT INTO metadata.auth_sessions VALUES (%s,%s,%s)",
                (hashlib.sha256(token.encode()).hexdigest(), user["id"], time() + 3600),
            )
        app = FastAPI()
        app.state.auth = service
        install_api_error_handlers(app)
        app.add_middleware(AuthenticationMiddleware)

        @app.get("/api/v1/schemii/owned")
        def read_owned():
            with factory() as connection:
                return connection.execute("SELECT 42 AS value").fetchone()

        @app.get("/api/v1/readiness")
        def readiness():
            postgres_metadata.repositories.check_readiness()
            return {"ready": True}

        with TestClient(app, base_url="https://localhost:8001") as client:
            client.cookies.set(COOKIE, token)
            with factory():
                started = monotonic()
                response = client.get("/api/v1/schemii/owned")
                elapsed = monotonic() - started
                assert elapsed < 1
                assert response.status_code == 503
                assert response.json()["error"]["retryable"] is True
                assert response.json()["error"]["code"] == "metadata_capacity_exceeded"
                assert "set-cookie" not in response.headers
                assert client.cookies.get(COOKIE) == token
                assert client.get("/api/v1/readiness").json() == {"ready": True}
            assert client.get("/api/v1/schemii/owned").json() == {"value": 42}
            with service.store.transaction(write=True) as state:
                state["roles"][role_id]["capabilities"] = []
            assert client.get("/api/v1/schemii/owned").status_code == 403
            service.logout(token)
            assert client.get("/api/v1/schemii/owned").status_code == 401
        record_property("auth_saturated_response_seconds", elapsed)
    finally:
        with postgres_metadata.connection_factory() as connection:
            connection.execute(
                "DELETE FROM metadata.auth_roles WHERE id=%s", (role_id,)
            )
            connection.execute(
                "DELETE FROM metadata.auth_audit WHERE actor_id=%s OR target_id=%s",
                (user["id"], user["id"]),
            )
            connection.execute(
                "DELETE FROM metadata.auth_accounts WHERE user_id=%s", (user["id"],)
            )
            connection.execute("DELETE FROM metadata.users WHERE id=%s", (user["id"],))
        factory.close()
    assert_backends_closed(postgres_metadata, factory._application_name)


@pytest.fixture
def assembled_source_database(postgres_metadata):
    """Own a separate disposable source DB; never relax control-plane policy.

    A second explicitly disposable external server is necessary: the assembled
    target policy forbids every DB on the metadata host/port. Its integration
    login must have CREATEDB. Missing inputs/privilege fail this prerequisite.
    """
    del postgres_metadata  # Keep the opt-in metadata prerequisite ahead of this one.
    dsn = os.environ.get("SCHEMII_TEST_SOURCE_DSN")
    password = os.environ.get("SCHEMII_TEST_SOURCE_PASSWORD")
    assert dsn and password, (
        "assembled recovery requires SCHEMII_TEST_SOURCE_DSN and "
        "SCHEMII_TEST_SOURCE_PASSWORD for a separate disposable server with CREATEDB"
    )
    parameters = conninfo_to_dict(dsn)
    database = "metadata_recovery_" + uuid4().hex
    created = False

    def administration_connection():
        return psycopg.connect(
            dsn,
            password=password,
            autocommit=True,
            connect_timeout=2,
            application_name="assembled_source_admin_" + uuid4().hex,
            options="-c statement_timeout=5000 -c lock_timeout=1000",
        )

    try:
        with administration_connection() as connection:
            connection.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                    sql.Identifier(database)
                )
            )
            created = True
        yield PostgresConnectionCreate(
            name="Owned assembled recovery source",
            host=parameters.get("host") or parameters.get("hostaddr") or "localhost",
            port=int(parameters.get("port") or 5432),
            database=database,
            username=parameters["user"],
            ssl_mode="prefer",
            connect_timeout=2,
            password=password,
        )
    finally:
        if created:
            # No FORCE/termination: retained owned backends must be closed by
            # their resource owner. A leak remains visible as a cleanup failure.
            with administration_connection() as connection:
                connection.execute(
                    sql.SQL("DROP DATABASE {}").format(sql.Identifier(database))
                )


def test_assembled_auth_cancel_and_source_query_recover_after_metadata_saturation(
    postgres_metadata, assembled_source_database, monkeypatch, record_property
):
    """Real request composition, not mounted HTTPS/UI or capacity acceptance."""
    environment = dict(
        postgres_metadata.environment, SCHEMII_METADATA_MAXIMUM_CONNECTIONS="1"
    )
    monkeypatch.setenv("SCHEMII_AUTH_ENABLED", "1")
    monkeypatch.setenv("SCHEMII_SETUP_TOKEN", "unused-integration-setup-token")
    monkeypatch.delenv("SCHEMII_SETUP_TOKEN_FILE", raising=False)
    from schemii.common.ai.prototype import PiClient

    monkeypatch.setattr(PiClient, "from_env", lambda: None)
    runtime = RuntimeConfig.from_env(
        {
            "SCHEMII_DEPLOYMENT_MODE": "authenticated",
            "SCHEMII_AUTH_ENABLED": "1",
            "SCHEMII_TARGET_EGRESS_MODE": "internal-only",
            "SCHEMII_ALLOWED_TARGET_HOSTS": assembled_source_database.host,
        }
    )
    admin = AdminConfig()
    admin = replace(
        admin,
        ai=replace(admin.ai, enabled=False, credential_expiration_enabled=False),
        postgres_timeouts=replace(admin.postgres_timeouts, catalog_statement_seconds=8),
    )
    role_id = "role_assembled_" + uuid4().hex
    usernames = ["it_assembled_" + uuid4().hex for _ in range(2)]

    def clean_accounts():
        # Look up the predeclared usernames even if creation fails before its
        # return. No other accounts, roles, profiles or execution rows are owned.
        with postgres_metadata.connection_factory() as connection:
            owners = [
                row["user_id"]
                for row in connection.execute(
                    "SELECT user_id FROM metadata.auth_accounts WHERE username=ANY(%s)",
                    (usernames,),
                ).fetchall()
            ]
            connection.execute(
                "DELETE FROM metadata.auth_roles WHERE id=%s", (role_id,)
            )
            connection.execute(
                "DELETE FROM metadata.auth_audit WHERE actor_id=ANY(%s) OR target_id=ANY(%s)",
                (owners, owners),
            )
            connection.execute(
                "DELETE FROM schemii.workspaces WHERE owner_id=ANY(%s)", (owners,)
            )
            connection.execute(
                "DELETE FROM metadata.auth_accounts WHERE user_id=ANY(%s)", (owners,)
            )
            connection.execute(
                "DELETE FROM metadata.auth_login_attempts WHERE username=ANY(%s)",
                (usernames,),
            )
            connection.execute("DELETE FROM metadata.users WHERE id=ANY(%s)", (owners,))

    with ExitStack() as cleanup:

        def compose_metadata(**policy):
            metadata = create_metadata_repositories(environment, **policy)
            # Register ownership before the rest of service construction can fail.
            cleanup.callback(metadata.close)
            return metadata

        monkeypatch.setattr(
            application_module, "create_metadata_repositories", compose_metadata
        )
        services = application_module.create_services(runtime, admin)
        cleanup.callback(clean_accounts)
        normal = services.metadata.connection_factory
        assert isinstance(normal, MetadataConnectionFactory)
        normal._application_name = "assembled_metadata_" + uuid4().hex
        console = services.console
        assert console is not None
        cleanup.callback(console.close)
        app = application_module.create_app(services, runtime_config=runtime)
        cleanup.callback(app.state.bulk_jobs.close)
        cleanup.callback(app.state.raw_console.close)
        auth = app.state.auth
        assert auth.store.factory is normal
        assert services.metadata.connections._connection_factory is normal
        assert console._repository._connection_factory is normal
        readiness = services.metadata.readiness_probe._connection_factory
        assert readiness is not normal
        readiness._application_name = "assembled_readiness_" + uuid4().hex
        users = [
            auth.create_user(
                AccountCreate(
                    username=name,
                    display_name="Owned assembled account",
                    password="integration-assembled-password",
                )
            )
            for name in usernames
        ]
        with auth.store.transaction(write=True) as state:
            state["roles"][role_id] = dict(
                id=role_id,
                name=role_id,
                capabilities=["schemii:access"],
                user_ids=[user["id"] for user in users],
                connections=[],
                dashboards=[],
            )
        tokens = [uuid4().hex for _ in users]
        with postgres_metadata.connection_factory() as connection:
            for user, token in zip(users, tokens, strict=True):
                connection.execute(
                    "INSERT INTO metadata.auth_sessions VALUES (%s,%s,%s)",
                    (
                        hashlib.sha256(token.encode()).hexdigest(),
                        user["id"],
                        time() + 3600,
                    ),
                )
        # Do not enter TestClient's lifespan: maintenance/recovery scheduling has
        # separate oracles. Pressure here belongs solely to these requests.
        clients = []
        for token in tokens:
            client = TestClient(
                app,
                base_url="https://localhost:8001",
                headers={"Origin": "https://localhost:8001"},
            )
            cleanup.callback(client.close)
            client.cookies.set(COOKIE, token)
            clients.append(client)
        client, peer = clients
        owner = users[0]["id"]
        assert client.get("/api/v1/auth/me").json()["user"]["id"] == owner
        metadata_parameters = conninfo_to_dict(environment["SCHEMII_METADATA_DSN"])
        with pytest.raises(ConnectionTargetForbiddenError) as forbidden:
            services.metadata.target_policy.validate(
                assembled_source_database.model_copy(
                    update={
                        "host": metadata_parameters.get("host")
                        or metadata_parameters.get("hostaddr")
                        or "localhost",
                        "port": int(metadata_parameters.get("port") or 5432),
                    }
                )
            )
        assert forbidden.value.code == "metadata_control_plane_target_forbidden"
        profile_response = client.post(
            "/api/v1/connections",
            json=assembled_source_database.model_dump(mode="json", exclude={"password"})
            | {"password": assembled_source_database.password.get_secret_value()},
        )
        assert profile_response.status_code == 201
        profile = profile_response.json()
        workspace = services.workspaces.create(
            owner,
            WorkspaceCreateRecord(
                name="Owned recovery workspace",
                connection_id=profile["id"],
                database=profile["database"],
                namespace="public",
            ),
            expected_connection_revision=profile["revision"],
        )
        execution_path = f"/api/v1/schemii/workspaces/{workspace.id}/console/executions"

        def execute(statement):
            response = client.post(
                execution_path,
                json={
                    "consoleId": "con_" + uuid4().hex,
                    "expectedWorkspaceRevision": workspace.revision,
                    "expectedSettingsRevision": console.settings(owner).revision,
                    "mode": "managed_read",
                    "statements": [statement],
                },
            )
            assert response.status_code == 201
            path = "/api/v1/common/query-executions/" + response.json()["id"]
            receipt_response = client.get(path)
            assert receipt_response.status_code == 200
            receipt = receipt_response.json()
            assert receipt["status"] == "succeeded"
            return path, path + "/results/" + receipt["results"][0]["id"]

        path, result_path = execute("SELECT 1::bigint FROM pg_sleep(30)")
        with psycopg.connect(
            host=assembled_source_database.host,
            port=assembled_source_database.port,
            dbname=assembled_source_database.database,
            user=assembled_source_database.username,
            password=assembled_source_database.password.get_secret_value(),
            sslmode=assembled_source_database.ssl_mode.value,
            connect_timeout=2,
            autocommit=True,
            row_factory=dict_row,
            application_name="assembled_source_monitor_" + uuid4().hex,
            options="-c statement_timeout=2000 -c lock_timeout=1000",
        ) as monitor:
            backend = monitor.execute(
                "SELECT pid, backend_start FROM pg_stat_activity WHERE datname=%s AND application_name='schemii'",
                (assembled_source_database.database,),
            ).fetchall()
            assert len(backend) == 1
            identity = backend[0]
            with ThreadPoolExecutor(max_workers=1) as executor:
                page = executor.submit(client.get, result_path)
                try:
                    deadline = monotonic() + 3
                    while True:
                        waiting = monitor.execute(
                            "SELECT wait_event FROM pg_stat_activity WHERE pid=%s AND backend_start=%s",
                            (identity["pid"], identity["backend_start"]),
                        ).fetchone()
                        if waiting and waiting["wait_event"] == "PgSleep":
                            break
                        assert monotonic() < deadline, (
                            "owned source fetch never entered pg_sleep"
                        )
                        sleep(0.01)
                    assert peer.delete(path).status_code == 404
                    assert not page.done(), (
                        "cross-owner cancellation stopped the source"
                    )
                    with normal() as blocker:
                        blocker.execute("SELECT 1")
                        for method, url in (
                            (client.get, "/api/v1/auth/me"),
                            (client.delete, path),
                        ):
                            started = monotonic()
                            saturated = method(url)
                            elapsed = monotonic() - started
                            assert elapsed < 1
                            assert saturated.status_code == 503
                            assert (
                                saturated.json()["error"]["code"]
                                == "metadata_capacity_exceeded"
                            )
                            assert saturated.json()["error"]["retryable"] is True
                            assert "set-cookie" not in saturated.headers
                            assert client.cookies.get(COOKIE) == tokens[0]
                            record_property(
                                "saturated_" + url.rsplit("/", 1)[-1] + "_seconds",
                                elapsed,
                            )
                        ready_started = monotonic()
                        ready = client.get("/api/v1/readiness")
                        assert (
                            ready.status_code == 200 and ready.json()["ready"] is True
                        )
                        assert monotonic() - ready_started < 2
                        assert normal.admission_snapshot().active == 1
                        assert not page.done(), (
                            "retryable metadata rejection cancelled the source"
                        )
                    assert normal.admission_snapshot().active == 0
                    started = monotonic()
                    cancelled = client.delete(path)
                    assert cancelled.status_code == 200
                    stopped = page.result(timeout=3)
                    assert (
                        stopped.json()["error"]["code"] == "postgres_console_cancelled"
                    )
                    assert monotonic() - started < 3
                    record_property("accepted_cancel_seconds", monotonic() - started)
                finally:
                    # Even an assertion failure signals only this retained
                    # execution. The SQL ceiling also bounds an unsuccessful stop.
                    console.cancel(owner, workspace.id, path.rsplit("/", 1)[-1])
                    page.result(timeout=10)
            deadline = monotonic() + 3
            while monitor.execute(
                "SELECT count(*) AS n FROM pg_stat_activity WHERE pid=%s AND backend_start=%s",
                (identity["pid"], identity["backend_start"]),
            ).fetchone()["n"]:
                assert monotonic() < deadline, (
                    "owned source backend remained after cancellation"
                )
                sleep(0.01)
        assert normal.admission_snapshot().active == 0
        assert_backends_closed(postgres_metadata, normal._application_name)
        recovered_path, recovered_result = execute("SELECT 42::bigint AS recovered")
        result = client.get(recovered_result)
        assert result.status_code == 200 and result.json()["rows"] == [[42]]
        assert client.delete(recovered_result).status_code == 204
        with auth.store.transaction(write=True) as state:
            state["roles"][role_id]["capabilities"] = []
        assert client.get("/api/v1/auth/me").json()["capabilities"] == []
        assert client.post(execution_path, json={}).status_code == 403
        assert client.delete(recovered_path).status_code == 403
        auth.logout(tokens[0])
        assert client.get("/api/v1/auth/me").status_code == 401
        assert peer.get("/api/v1/auth/me").status_code == 200
        assert normal.admission_snapshot().active == 0
        assert all(
            lane["permits"] == lane["connections"] == 0
            for lane in services.postgres.observation_snapshot().values()
        )
        record_property("owned_source_database", assembled_source_database.database)
        record_property("owned_source_backend", str(identity))
    assert_backends_closed(postgres_metadata, normal._application_name)
    assert_backends_closed(postgres_metadata, readiness._application_name)


def test_composition_keeps_migration_and_readiness_budgets_separate(
    postgres_metadata, monkeypatch, record_property
) -> None:
    environment = dict(
        postgres_metadata.environment,
        SCHEMII_METADATA_STATEMENT_TIMEOUT_MS="100",
        SCHEMII_METADATA_LOCK_TIMEOUT_MS="20",
        SCHEMII_METADATA_IDLE_TRANSACTION_TIMEOUT_MS="3000",
        SCHEMII_METADATA_MAXIMUM_CONNECTIONS="1",
    )
    original_migrate = factory_module.MetadataMigrator.migrate
    migration_factories = []
    migration_settings = {}

    def observed_migrate(migrator):
        migration_factories.append(migrator._connection_factory)
        with migrator._connection_factory() as connection:
            migration_settings.update(effective_deadlines(connection))
            # This would exceed normal metadata's 100ms statement allowance.
            connection.execute("SELECT pg_sleep(0.2)")
        return original_migrate(migrator)

    monkeypatch.setattr(factory_module.MetadataMigrator, "migrate", observed_migrate)
    repositories = create_metadata_repositories(
        environment, migration_packages=MIGRATION_PACKAGES
    )
    normal = repositories.connection_factory
    readiness = repositories.readiness_probe._connection_factory
    assert normal is not None
    try:
        assert migration_settings == {
            "statement_timeout": 120_000,
            "lock_timeout": 30_000,
            "idle_in_transaction_session_timeout": 120_000,
        }
        assert len(migration_factories) == 1
        assert migration_factories[0].admission_snapshot().closed
        assert migration_factories[0].admission_snapshot().active == 0
        with normal() as occupied:
            assert effective_deadlines(occupied) == {
                "statement_timeout": 100,
                "lock_timeout": 20,
                "idle_in_transaction_session_timeout": 3000,
            }
            with pytest.raises(MetadataCapacityError):
                normal()
            started = monotonic()
            repositories.check_readiness()
            ready_elapsed = monotonic() - started
            assert ready_elapsed < 2
            with readiness() as connection:
                ready_settings = effective_deadlines(connection)
                assert ready_settings == {
                    "statement_timeout": 2_000,
                    "lock_timeout": 1_000,
                    "idle_in_transaction_session_timeout": 2_000,
                }
        with pytest.raises(MetadataStorageUnavailableError):
            with normal() as connection:
                connection.execute("SELECT pg_sleep(0.2)")
        record_property("composed_readiness_seconds", ready_elapsed)
        record_property("migration_deadlines_ms", json.dumps(migration_settings))
        record_property("readiness_deadlines_ms", json.dumps(ready_settings))
    finally:
        repositories.close()
    assert normal.admission_snapshot().closed
    assert readiness.admission_snapshot().closed
    assert_backends_closed(postgres_metadata, normal._application_name)
    assert_backends_closed(postgres_metadata, readiness._application_name)


def test_migration_lock_has_its_own_bounded_allowance(
    postgres_metadata, record_property
) -> None:
    environment = dict(
        postgres_metadata.environment,
        SCHEMII_METADATA_STATEMENT_TIMEOUT_MS="100",
        SCHEMII_METADATA_LOCK_TIMEOUT_MS="20",
        SCHEMII_METADATA_MIGRATION_LOCK_TIMEOUT_MS="300",
    )
    started = monotonic()
    with postgres_metadata.connection_factory() as blocker:
        blocker.execute("SELECT pg_advisory_xact_lock(%s)", (0x534348454D4949,))
        with pytest.raises(MetadataMigrationError) as failure:
            create_metadata_repositories(
                environment, migration_packages=MIGRATION_PACKAGES
            )
    elapsed = monotonic() - started
    assert isinstance(failure.value.__cause__, psycopg.errors.LockNotAvailable)
    assert 0.2 < elapsed < 2
    assert_backends_closed(postgres_metadata, "schemii-metadata-migrations")
    recovered = create_metadata_repositories(
        environment, migration_packages=MIGRATION_PACKAGES
    )
    recovered.check_readiness()
    recovered.close()
    record_property("migration_lock_wait_seconds", elapsed)


def test_idle_transaction_deadline_closes_backend_but_keeps_native_lease(
    postgres_metadata, record_property
) -> None:
    factory = runtime_factory(
        postgres_metadata, maximum_connections=1, idle_transaction_timeout_ms=200
    )
    connection = factory()
    try:
        connection.execute("SELECT 1")
        started = monotonic()
        # PostgreSQL owns this timeout, not a Python timer or an async cancellation.
        assert_backends_closed(postgres_metadata, factory._application_name)
        elapsed = monotonic() - started
        assert 0.1 < elapsed < 2
        assert factory.admission_snapshot().active == 1
        with pytest.raises(MetadataCapacityError):
            factory()
        with pytest.raises(MetadataStorageUnavailableError) as failure:
            with connection:
                connection.execute("SELECT 42 AS recovered")
        assert isinstance(
            failure.value.__cause__, psycopg.errors.IdleInTransactionSessionTimeout
        )
        assert factory.admission_snapshot().active == 0
        with factory() as recovered:
            assert (
                recovered.execute("SELECT 42 AS recovered").fetchone()["recovered"]
                == 42
            )
        record_property("idle_transaction_timeout_seconds", elapsed)
    finally:
        connection.close()
        factory.close()
    assert_backends_closed(postgres_metadata, factory._application_name)


def test_statement_deadline_closes_backend_and_recovers(
    postgres_metadata, record_property
) -> None:
    factory = runtime_factory(postgres_metadata, statement_timeout_ms=250)
    started = monotonic()
    try:
        with pytest.raises(MetadataStorageUnavailableError) as failure:
            with factory() as connection:
                connection.execute("SELECT pg_sleep(10)")
        elapsed = monotonic() - started
        assert isinstance(failure.value.__cause__, psycopg.errors.QueryCanceled)
        assert 0.15 <= elapsed < 2
        assert factory.admission_snapshot().active == 0
        assert_backends_closed(postgres_metadata, factory._application_name)
        with factory() as connection:
            assert (
                connection.execute("SELECT 42 AS recovered").fetchone()["recovered"]
                == 42
            )
        record_property("statement_timeout_seconds", elapsed)
        record_property("admission", json.dumps(asdict(factory.admission_snapshot())))
    finally:
        factory.close()
    assert_backends_closed(postgres_metadata, factory._application_name)


def test_auth_advisory_lock_failure_is_bounded_and_session_recovers(
    postgres_metadata, record_property
) -> None:
    factory = runtime_factory(
        postgres_metadata, statement_timeout_ms=1_500, lock_timeout_ms=150
    )
    service = AuthService(factory, enabled=True, setup_token="integration-unused")
    username = "it_limits_" + uuid4().hex
    user = service.create_user(
        AccountCreate(
            username=username,
            display_name="Owned admission fixture",
            password="integration-limits-password",
        )
    )
    # Own the exact session row; avoid an anonymous global sign-in audit fixture.
    token = uuid4().hex
    with postgres_metadata.connection_factory() as connection:
        connection.execute(
            "INSERT INTO metadata.auth_sessions VALUES (%s,%s,%s)",
            (hashlib.sha256(token.encode()).hexdigest(), user["id"], time() + 3600),
        )
    try:
        with postgres_metadata.connection_factory() as blocker:
            blocker.execute("SELECT pg_advisory_xact_lock(%s)", (0x53434841555448,))
            started = monotonic()
            with pytest.raises(MetadataStorageUnavailableError) as failure:
                service.logout(token)
            elapsed = monotonic() - started
            assert isinstance(failure.value.__cause__, psycopg.errors.LockNotAvailable)
            assert 0.1 <= elapsed < 2
            assert factory.admission_snapshot().active == 0
            assert_backends_closed(postgres_metadata, factory._application_name)
            # Targeted authenticated reads do not acquire the global mutation lock.
            assert service.resolve(token)["id"] == user["id"]
        assert service.resolve(token)["id"] == user["id"]
        assert service.capabilities(user["id"]) == []
        with factory() as connection:
            assert (
                connection.execute("SELECT 42 AS recovered").fetchone()["recovered"]
                == 42
            )
        service.logout(token)
        assert service.resolve(token) is None
        record_property("auth_lock_wait_seconds", elapsed)
        record_property("admission", json.dumps(asdict(factory.admission_snapshot())))
    finally:
        with postgres_metadata.connection_factory() as connection:
            connection.execute(
                "DELETE FROM metadata.auth_audit WHERE actor_id=%s OR target_id=%s",
                (user["id"], user["id"]),
            )
            connection.execute(
                "DELETE FROM metadata.auth_accounts WHERE user_id=%s", (user["id"],)
            )
            connection.execute(
                "DELETE FROM metadata.auth_login_attempts WHERE username=%s",
                (username,),
            )
            connection.execute("DELETE FROM metadata.users WHERE id=%s", (user["id"],))
        factory.close()
    assert_backends_closed(postgres_metadata, factory._application_name)


def test_concurrent_native_calls_preserve_readiness_budget_and_recover(
    postgres_metadata, record_property
) -> None:
    factory = runtime_factory(postgres_metadata, maximum_connections=2)
    readiness_factory = runtime_factory(
        postgres_metadata, maximum_connections=1, statement_timeout_ms=2_000
    )
    release = Event()
    attempts_finished = Event()
    attempts_lock = Lock()
    attempts = 0

    def work():
        nonlocal attempts
        connection = None
        try:
            try:
                connection = factory()
                connection.execute("SELECT 1 AS admitted")
            except MetadataCapacityError:
                return "rejected"
            finally:
                with attempts_lock:
                    attempts += 1
                    if attempts == 10:
                        attempts_finished.set()
            assert release.wait(5)
            return "admitted"
        finally:
            if connection is not None:
                connection.close()

    started = monotonic()
    try:
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(work) for _ in range(10)]
            try:
                assert attempts_finished.wait(5)
                snapshot = factory.admission_snapshot()
                assert (
                    snapshot.active,
                    snapshot.peak_active,
                    snapshot.admitted,
                    snapshot.rejected,
                ) == (2, 2, 2, 8)
                with postgres_metadata.connection_factory() as connection:
                    row = connection.execute(
                        "SELECT count(*) AS n FROM pg_stat_activity WHERE application_name=%s",
                        (factory._application_name,),
                    ).fetchone()
                    assert row["n"] == 2
                ready_started = monotonic()
                MetadataReadinessProbe(readiness_factory)()
                assert monotonic() - ready_started < 2
                with pytest.raises(MetadataCapacityError):
                    factory()
            finally:
                release.set()
            assert [future.result() for future in futures].count("rejected") == 8
        assert factory.admission_snapshot().active == 0
        with factory() as connection:
            assert (
                connection.execute("SELECT 42 AS recovered").fetchone()["recovered"]
                == 42
            )
        record_property("burst_seconds", monotonic() - started)
        record_property("admission", json.dumps(asdict(factory.admission_snapshot())))
    finally:
        release.set()
        factory.close()
        readiness_factory.close()
    assert_backends_closed(postgres_metadata, factory._application_name)
    assert_backends_closed(postgres_metadata, readiness_factory._application_name)


def test_shutdown_drains_owned_native_waiter_without_releasing_early(
    postgres_metadata, record_property
) -> None:
    factory = runtime_factory(
        postgres_metadata, maximum_connections=1, lock_timeout_ms=700
    )
    native_started = Event()
    lock = int(uuid4().hex[:12], 16)

    def blocked_work() -> None:
        with factory() as connection:
            native_started.set()
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (lock,))

    try:
        with postgres_metadata.connection_factory() as blocker:
            blocker.execute("SELECT pg_advisory_xact_lock(%s)", (lock,))
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(blocked_work)
                assert native_started.wait(5)
                assert factory.admission_snapshot().active == 1
                started = monotonic()
                factory.close()
                elapsed = monotonic() - started
                assert 0.4 < elapsed < 3
                with pytest.raises(MetadataStorageUnavailableError):
                    future.result()
                assert factory.admission_snapshot().active == 0
                with pytest.raises(
                    MetadataStorageUnavailableError, match="shutting down"
                ):
                    factory()
        record_property("shutdown_drain_seconds", elapsed)
    finally:
        factory.close()
    assert_backends_closed(postgres_metadata, factory._application_name)


def test_canceled_async_lock_wait_retains_permit_until_native_timeout(
    postgres_metadata, record_property
) -> None:
    factory = runtime_factory(
        postgres_metadata, maximum_connections=1, lock_timeout_ms=700
    )
    native_started = Event()
    native_finished = Event()
    failures: list[Exception] = []
    lock = int(uuid4().hex[:12], 16)

    def blocked_work() -> None:
        try:
            with factory() as connection:
                native_started.set()
                connection.execute("SELECT pg_advisory_xact_lock(%s)", (lock,))
        except Exception as error:
            failures.append(error)
        finally:
            native_finished.set()

    async def cancel_waiter() -> None:
        task = asyncio.create_task(asyncio.to_thread(blocked_work))
        assert await asyncio.to_thread(native_started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not native_finished.is_set()
        assert factory.admission_snapshot().active == 1
        with pytest.raises(MetadataCapacityError):
            factory()
        assert await asyncio.to_thread(native_finished.wait, 3)

    started = monotonic()
    try:
        with postgres_metadata.connection_factory() as blocker:
            blocker.execute("SELECT pg_advisory_xact_lock(%s)", (lock,))
            asyncio.run(cancel_waiter())
        assert len(failures) == 1
        assert isinstance(failures[0], MetadataStorageUnavailableError)
        assert isinstance(failures[0].__cause__, psycopg.errors.LockNotAvailable)
        assert factory.admission_snapshot().active == 0
        assert_backends_closed(postgres_metadata, factory._application_name)
        with factory() as connection:
            assert (
                connection.execute("SELECT 42 AS recovered").fetchone()["recovered"]
                == 42
            )
        record_property("canceled_native_wait_seconds", monotonic() - started)
    finally:
        factory.close()
    assert_backends_closed(postgres_metadata, factory._application_name)
