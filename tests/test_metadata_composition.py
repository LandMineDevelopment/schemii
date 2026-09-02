from __future__ import annotations

import pytest

import schemii.main as application_module
from schemii.common.api.runtime import RuntimeConfig
from schemii.common.connections.models import PostgresConnectionCreate
from schemii.common.connections.policy import ConnectionTargetForbiddenError
from schemii.common.connections.store import InMemoryConnectionRepository
from schemii.common.metadata.factory import MetadataRepositories
from schemii.common.metadata.migrations import (
    MIGRATION_PACKAGE as COMMON_MIGRATION_PACKAGE,
)
from schemii.schemii.metadata import (
    MIGRATION_PACKAGE as SCHEMII_MIGRATION_PACKAGE,
)


def test_application_root_explicitly_composes_common_and_schemii_metadata(
    monkeypatch,
) -> None:
    captured_packages: list[tuple[str, ...]] = []

    def create_metadata(*, migration_packages: tuple[str, ...]):
        captured_packages.append(migration_packages)
        return MetadataRepositories(connections=InMemoryConnectionRepository())

    monkeypatch.setattr(
        application_module,
        "create_metadata_repositories",
        create_metadata,
    )

    services = application_module.create_services()

    assert services.metadata.storage == "memory"
    assert captured_packages == [
        (COMMON_MIGRATION_PACKAGE, SCHEMII_MIGRATION_PACKAGE)
    ]


def test_application_root_consumes_internal_target_egress_configuration() -> None:
    services = application_module.create_services(
        RuntimeConfig.from_env(
            {
                "SCHEMII_DEPLOYMENT_MODE": "local-development",
                "SCHEMII_TARGET_EGRESS_MODE": "internal-only",
                "SCHEMII_ALLOWED_TARGET_HOSTS": "application-postgres",
                "SCHEMII_STORAGE_MODE": "memory",
            }
        )
    )
    request = PostgresConnectionCreate(
        name="Application data",
        host="application-postgres",
        database="application_data",
        username="runtime",
    )

    created = services.connections.create("owner", request)
    assert created.host == "application-postgres"

    with pytest.raises(ConnectionTargetForbiddenError) as forbidden:
        services.connections.create(
            "owner",
            request.model_copy(update={"host": "unlisted-postgres"}),
        )
    assert forbidden.value.code == "connection_target_not_allowed"
