from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from schemii.common.api.runtime import RuntimeConfig
from schemii.common.connections.models import PostgresConnectionCreate
from schemii.common.connections.policy import (
    CompositeConnectionTargetPolicy,
    ConnectionTargetForbiddenError,
    InternalOnlyConnectionTargetPolicy,
    MetadataControlPlaneTargetPolicy,
)
from schemii.common.connections.service import ConnectionService
from schemii.common.connections.store import InMemoryConnectionRepository
from schemii.common.errors import MetadataStorageUnavailableError
from schemii.common.metadata.factory import MetadataRepositories
from schemii.main import ApplicationServices, create_app, create_services
from schemii.schemii.designs.store import InMemoryDesignRepository
from schemii.schemii.migrations.repository import InMemoryMigrationRepository
from schemii.schemii.migrations.service import MigrationService
from schemii.schemii.workspaces.store import InMemoryWorkspaceRepository


class UnusedPostgresGateway:
    pass


def application_services(
    *,
    metadata: MetadataRepositories | None = None,
    target_policy=None,
) -> ApplicationServices:
    connections = InMemoryConnectionRepository()
    designs = InMemoryDesignRepository()
    workspaces = InMemoryWorkspaceRepository(designs=designs)
    selected_metadata = metadata or MetadataRepositories(connections=connections)
    connection_service = ConnectionService(
        selected_metadata.connections,
        (workspaces,),
        target_policy=target_policy or selected_metadata.target_policy,
    )
    postgres = UnusedPostgresGateway()
    migrations = MigrationService(
        repository=InMemoryMigrationRepository(designs),
        connections=connection_service,
        postgres=postgres,
        workspaces=workspaces,
        designs=designs,
    )
    return ApplicationServices(
        metadata=selected_metadata,
        connections=connection_service,
        postgres=postgres,
        workspaces=workspaces,
        designs=designs,
        migrations=migrations,
    )


def test_runtime_config_rejects_implicit_or_unauthenticated_external_deployment() -> None:
    with pytest.raises(ValueError, match="SCHEMII_DEPLOYMENT_MODE"):
        RuntimeConfig.from_env({})
    with pytest.raises(ValueError, match="SCHEMII_AUTH_ENABLED"):
        RuntimeConfig.from_env(
            {
                "SCHEMII_DEPLOYMENT_MODE": "authenticated",
                "SCHEMII_TARGET_EGRESS_MODE": "internal-only",
            }
        )
    with pytest.raises(ValueError, match="external target egress"):
        RuntimeConfig.from_env(
            {
                "SCHEMII_DEPLOYMENT_MODE": "local-development",
                "SCHEMII_TARGET_EGRESS_MODE": "external",
            }
        )

    configured = RuntimeConfig.from_env(
        {
            "SCHEMII_DEPLOYMENT_MODE": "local-development",
            "SCHEMII_TARGET_EGRESS_MODE": "internal-only",
            "SCHEMII_ALLOWED_TARGET_HOSTS": " demo-postgres,postgres ",
            "SCHEMII_DEVELOPER_INSPECTION": "1",
        }
    )
    assert configured.developer_inspection is True
    assert configured.allowed_target_hosts == ("demo-postgres", "postgres")

    with pytest.raises(ValueError, match="SCHEMII_ALLOWED_TARGET_HOSTS"):
        RuntimeConfig.from_env(
            {
                "SCHEMII_DEPLOYMENT_MODE": "local-development",
                "SCHEMII_TARGET_EGRESS_MODE": "internal-only",
            }
        )


def test_authenticated_external_targets_still_require_explicit_approval(monkeypatch) -> None:
    settings = {
        "SCHEMII_DEPLOYMENT_MODE": "authenticated",
        "SCHEMII_AUTH_ENABLED": "1",
        "SCHEMII_TARGET_EGRESS_MODE": "external",
        "SCHEMII_ALLOWED_TARGET_HOSTS": "reports.example.test,metadata.internal",
    }
    configured = RuntimeConfig.from_env(settings)
    metadata = MetadataRepositories(
        connections=InMemoryConnectionRepository(),
        target_policy=MetadataControlPlaneTargetPolicy.from_dsn(
            "host=metadata.internal port=5432 dbname=control user=runtime"
        ),
    )
    monkeypatch.setattr("schemii.main.create_metadata_repositories", lambda **kwargs: metadata)
    services = create_services(configured)
    profile = services.connections.create("owner", PostgresConnectionCreate(
        name="Approved report source", host="reports.example.test",
        database="reports", username="reporter",
    ))
    assert profile.host == "reports.example.test"
    for host, code in (
        ("arbitrary.example.test", "connection_target_not_allowed"),
        ("metadata.internal", "metadata_control_plane_target_forbidden"),
    ):
        with pytest.raises(ConnectionTargetForbiddenError) as rejected:
            services.connections.create("owner", PostgresConnectionCreate(
                name="Rejected target", host=host, database="reports", username="reporter",
            ))
        assert rejected.value.code == code
    with pytest.raises(ValueError, match="SCHEMII_ALLOWED_TARGET_HOSTS"):
        RuntimeConfig.from_env({**settings, "SCHEMII_ALLOWED_TARGET_HOSTS": " , "})
    with pytest.raises(ValueError, match="SCHEMII_AUTH_ENABLED"):
        RuntimeConfig.from_env({**settings, "SCHEMII_AUTH_ENABLED": "0"})


def test_metadata_target_policy_normalizes_host_and_blocks_create_and_use() -> None:
    policy = MetadataControlPlaneTargetPolicy.from_dsn(
        "host=Metadata.Internal. port=5433 dbname=control user=runtime",
        host_aliases=("metadata-postgres",),
    )
    forbidden = PostgresConnectionCreate(
        name="Control plane",
        host="metadata.internal",
        port=5433,
        database="control",
        username="runtime",
    )
    with pytest.raises(ConnectionTargetForbiddenError):
        policy.validate(forbidden)

    services = application_services(target_policy=policy)
    api = TestClient(create_app(services), base_url="http://localhost")
    response = api.post(
        "/api/v1/connections",
        json={
            "name": "Control plane",
            "host": "metadata-postgres",
            "port": 5433,
            "database": "control",
            "username": "runtime",
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "metadata_control_plane_target_forbidden"
    assert services.connections.list("user_local_prototype") == []

    allowed = PostgresConnectionCreate(
        name="Target",
        host="demo-postgres",
        port=5433,
        database="application_data",
        username="runtime",
    )
    profile = services.connections.create("owner", allowed)
    with services.connections.use("owner", profile.id) as resolved:
        assert resolved.database == "application_data"

    bypassed_repository = InMemoryConnectionRepository()
    bypassed = bypassed_repository.create(
        "owner",
        forbidden.model_copy(update={"database": "postgres"}),
    )
    guarded_service = ConnectionService(
        bypassed_repository,
        (),
        target_policy=policy,
    )
    with pytest.raises(ConnectionTargetForbiddenError):
        with guarded_service.use("owner", bypassed.id):
            pytest.fail("a legacy metadata-server target must never be resolved")


def test_internal_allowlist_blocks_unlisted_aliases_on_create_update_and_use() -> None:
    policy = CompositeConnectionTargetPolicy(
        (
            MetadataControlPlaneTargetPolicy.from_dsn(
                "host=metadata-postgres port=5432 dbname=control user=runtime",
            ),
            InternalOnlyConnectionTargetPolicy.from_hosts(("DEMO-POSTGRES.",)),
        )
    )
    services = application_services(target_policy=policy)
    api = TestClient(create_app(services), base_url="http://localhost")

    allowed = api.post(
        "/api/v1/connections",
        json={
            "name": "Application data",
            "host": "demo-postgres",
            "database": "application_data",
            "username": "runtime",
        },
    )
    assert allowed.status_code == 201

    for host in ("metadata-alias", "10.20.30.40"):
        rejected = api.post(
            "/api/v1/connections",
            json={
                "name": "Unlisted destination",
                "host": host,
                "database": "control",
                "username": "runtime",
            },
        )
        assert rejected.status_code == 403
        assert rejected.json()["error"]["code"] == "connection_target_not_allowed"

    update = api.patch(
        f"/api/v1/connections/{allowed.json()['id']}",
        json={
            "expectedRevision": allowed.json()["revision"],
            "host": "metadata-alias",
        },
    )
    assert update.status_code == 403
    assert update.json()["error"]["code"] == "connection_target_not_allowed"
    assert services.connections.get(
        "user_local_prototype", allowed.json()["id"]
    ).host == "demo-postgres"

    bypassed_repository = InMemoryConnectionRepository()
    bypassed = bypassed_repository.create(
        "owner",
        PostgresConnectionCreate(
            name="Legacy unlisted target",
            host="10.20.30.40",
            database="control",
            username="runtime",
        ),
    )
    guarded_service = ConnectionService(
        bypassed_repository,
        (),
        target_policy=policy,
    )
    with pytest.raises(
        ConnectionTargetForbiddenError,
        match="not allowed",
    ) as forbidden:
        with guarded_service.use("owner", bypassed.id):
            pytest.fail("an unlisted stored target must never be resolved")
    assert forbidden.value.code == "connection_target_not_allowed"


def test_forbidden_target_errors_are_safe_when_raised_outside_connection_routes() -> None:
    application = create_app(application_services())

    @application.get("/test-only-forbidden-target")
    def forbidden_target():
        raise ConnectionTargetForbiddenError(
            "This PostgreSQL host is not allowed by the deployment's internal target policy",
            code="connection_target_not_allowed",
        )

    response = TestClient(
        application,
        base_url="http://localhost",
        raise_server_exceptions=False,
    ).get("/test-only-forbidden-target")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "connection_target_not_allowed"


def test_readiness_returns_503_when_metadata_probe_fails() -> None:
    connections = InMemoryConnectionRepository()

    def unavailable() -> None:
        raise MetadataStorageUnavailableError("private database detail")

    metadata = MetadataRepositories(
        connections=connections,
        storage="postgresql",
        durable=True,
        readiness_probe=unavailable,
    )
    api = TestClient(
        create_app(application_services(metadata=metadata)),
        base_url="http://localhost",
    )

    response = api.get("/api/v1/readiness")

    assert response.status_code == 503
    assert response.json() == {
        "ready": False,
        "metadata": "postgresql",
        "persistence": "durable",
    }


def test_structured_logs_correlate_request_ids_without_exception_messages(caplog) -> None:
    application = create_app(application_services())

    @application.get("/test-only-secret-failure")
    def fail_with_secret():
        raise RuntimeError("password=do-not-log")

    api = TestClient(
        application,
        base_url="http://localhost",
        raise_server_exceptions=False,
    )
    caplog.set_level(logging.INFO, logger="schemii.http")

    response = api.get(
        "/test-only-secret-failure",
        headers={"X-Request-ID": "edge-request-123"},
    )

    assert response.status_code == 500
    assert response.headers["x-request-id"] == "edge-request-123"
    documents = [json.loads(record.message) for record in caplog.records]
    error = next(document for document in documents if document["event"] == "request_error")
    completed = next(
        document for document in documents if document["event"] == "request_complete"
    )
    assert error["request_id"] == completed["request_id"] == "edge-request-123"
    assert error["exception_type"] == "RuntimeError"
    assert "do-not-log" not in caplog.text
