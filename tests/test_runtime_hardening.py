from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from schemii.common.api.runtime import RuntimeConfig
from schemii.common.connections.models import PostgresConnectionCreate
from schemii.common.connections.policy import (
    ConnectionTargetForbiddenError,
    MetadataControlPlaneTargetPolicy,
)
from schemii.common.connections.service import ConnectionService
from schemii.common.connections.store import InMemoryConnectionRepository
from schemii.common.errors import MetadataStorageUnavailableError
from schemii.common.metadata.factory import MetadataRepositories
from schemii.main import ApplicationServices, create_app
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
    with pytest.raises(ValueError, match="identity adapter"):
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
            "SCHEMII_DEVELOPER_INSPECTION": "1",
        }
    )
    assert configured.developer_inspection is True


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
