from __future__ import annotations

import schemii.main as application_module
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
