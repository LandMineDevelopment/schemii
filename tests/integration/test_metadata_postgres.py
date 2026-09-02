from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import SecretStr

from schemii.common.connections.models import PostgresConnectionCreate
from schemii.common.connections.policy import ConnectionTargetForbiddenError
from schemii.common.connections.service import ConnectionService
from schemii.common.connections.store import ConnectionInUseError
from schemii.common.metadata.database import packaged_migrations
from schemii.common.metadata.factory import create_metadata_repositories
from schemii.common.metadata.migrations import (
    MIGRATION_PACKAGE as COMMON_MIGRATION_PACKAGE,
)
from schemii.schemii.metadata import (
    MIGRATION_PACKAGE as SCHEMII_MIGRATION_PACKAGE,
)
from schemii.common.postgres.models import (
    PostgresColumn,
    PostgresTable,
    build_postgres_catalog,
)
from schemii.schemii.designs.importer import import_postgres_catalog
from schemii.schemii.designs.postgres_store import PostgresDesignRepository
from schemii.schemii.migrations.repository import PostgresMigrationRepository
from schemii.schemii.workspaces.models import SchemiiWorkspaceCreate
from schemii.schemii.workspaces.postgres_store import PostgresWorkspaceRepository
from schemii.schemii.workspaces.store import (
    WorkspaceDesignBootstrap,
    WorkspaceImportBaseline,
    WorkspaceStorageUnavailableError,
)
from tests.integration.postgres_fixture import PostgresMetadataHarness


def _target(harness: PostgresMetadataHarness):
    return harness.repositories.connections.create(
        harness.owner_id,
        PostgresConnectionCreate(
            name="Persistent integration target",
            host="application-postgres",
            port=5432,
            database="application_data",
            username="application_user",
            password=SecretStr("target-only-secret"),
        ),
    )


def _import_documents(connection_revision: int):
    catalog = build_postgres_catalog(
        database="application_data",
        namespace="public",
        server_version="17.2",
        server_version_num=170002,
        server_timezone="UTC",
        tables=(
            PostgresTable(
                namespace="public",
                name="widgets",
                kind="table",
                is_partition=False,
                columns=(
                    PostgresColumn(
                        name="id",
                        ordinal=1,
                        data_type="bigint",
                        nullable=False,
                        identity="by_default",
                    ),
                ),
            ),
        ),
        relationships=(),
        functions=(),
        views=(),
        materialized_views=(),
        captured_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    imported = import_postgres_catalog(catalog)
    return (
        imported,
        WorkspaceDesignBootstrap(
            content=imported.content,
            layout=imported.layout,
            import_summary=imported.summary,
        ),
        WorkspaceImportBaseline(
            connection_revision=connection_revision,
            content=imported.content,
            catalog=catalog,
            complete=imported.summary.complete,
            issues=[issue.model_dump(mode="json") for issue in imported.summary.issues],
        ),
    )


def test_postgres_metadata_migrates_persists_encrypts_and_reports_ready(
    postgres_metadata: PostgresMetadataHarness,
) -> None:
    repositories = postgres_metadata.repositories
    owner_id = postgres_metadata.owner_id
    repositories.check_readiness()
    assert repositories.storage == "postgresql"
    assert repositories.durable is True

    saved = _target(postgres_metadata)

    reopened = create_metadata_repositories(
        postgres_metadata.environment,
        migration_packages=(COMMON_MIGRATION_PACKAGE, SCHEMII_MIGRATION_PACKAGE),
    )
    assert reopened.connections.list(owner_id) == [saved]
    resolved = reopened.connections.resolve(owner_id, saved.id)
    assert resolved.password is not None
    assert resolved.password.get_secret_value() == "target-only-secret"

    assert reopened.connection_factory is not None
    with reopened.connection_factory() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT ciphertext
                FROM metadata.postgres_connection_credentials
                WHERE owner_id = %s AND connection_id = %s
                """,
                (owner_id, saved.id),
            )
            ciphertext = bytes(cursor.fetchone()["ciphertext"])
            cursor.execute(
                "SELECT version FROM metadata.schema_migrations ORDER BY version"
            )
            applied_versions = [row["version"] for row in cursor.fetchall()]
    assert b"target-only-secret" not in ciphertext
    assert applied_versions == [
        migration.version
        for migration in packaged_migrations(
            (COMMON_MIGRATION_PACKAGE, SCHEMII_MIGRATION_PACKAGE)
        )
    ]

    forbidden = PostgresConnectionCreate(
        name="Metadata bypass",
        host="127.0.0.1",
        port=5432,
        database="a_different_database_on_the_same_server",
        username="postgres",
    )
    with pytest.raises(ConnectionTargetForbiddenError):
        reopened.target_policy.validate(forbidden)


def test_workspace_import_commits_every_record_in_one_postgres_transaction(
    postgres_metadata: PostgresMetadataHarness,
) -> None:
    factory = postgres_metadata.connection_factory
    target = _target(postgres_metadata)
    imported, bootstrap, baseline = _import_documents(target.revision)
    workspaces = PostgresWorkspaceRepository(factory)
    migrations = PostgresMigrationRepository(factory)

    workspace = workspaces.create_import(
        postgres_metadata.owner_id,
        SchemiiWorkspaceCreate(
            name="Atomic import",
            connection_id=target.id,
            database=target.database,
            namespace="public",
        ),
        bootstrap=bootstrap,
        baseline=baseline,
        baselines=migrations,
    )

    assert workspace.import_summary == imported.summary
    assert PostgresDesignRepository(factory).get(
        postgres_metadata.owner_id, workspace.id
    ).content == imported.content
    saved_baseline = migrations.current_baseline(
        postgres_metadata.owner_id, workspace.id
    )
    assert saved_baseline is not None
    assert saved_baseline.source == "import"
    assert saved_baseline.connection_revision == target.revision
    assert saved_baseline.content == imported.content

    with factory() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    (SELECT count(*) FROM schemii.workspaces
                     WHERE owner_id = %s) AS workspaces,
                    (SELECT count(*) FROM schemii.workspace_targets
                     WHERE owner_id = %s) AS targets,
                    (SELECT count(*) FROM schemii.workspace_designs
                     WHERE owner_id = %s) AS designs,
                    (SELECT count(*) FROM schemii.workspace_design_layouts
                     WHERE owner_id = %s) AS layouts,
                    (SELECT count(*) FROM schemii.workspace_design_imports
                     WHERE owner_id = %s) AS imports,
                    (SELECT count(*) FROM schemii.workspace_schema_baselines
                     WHERE owner_id = %s) AS baselines,
                    (SELECT count(*) FROM schemii.workspace_schema_baseline_heads
                     WHERE owner_id = %s) AS baseline_heads
                """,
                (postgres_metadata.owner_id,) * 7,
            )
            counts = cursor.fetchone()
    assert counts == {
        "workspaces": 1,
        "targets": 1,
        "designs": 1,
        "layouts": 1,
        "imports": 1,
        "baselines": 1,
        "baseline_heads": 1,
    }


def test_workspace_import_rolls_back_all_postgres_records_when_baseline_fails(
    postgres_metadata: PostgresMetadataHarness,
) -> None:
    factory = postgres_metadata.connection_factory
    target = _target(postgres_metadata)
    _, bootstrap, baseline = _import_documents(target.revision)
    migrations = PostgresMigrationRepository(factory)

    class FailAfterWritingBaseline:
        def create_import_baseline(self, **values):
            migrations.create_import_baseline(**values)
            raise RuntimeError("failure after baseline insert")

    with pytest.raises(WorkspaceStorageUnavailableError):
        PostgresWorkspaceRepository(factory).create_import(
            postgres_metadata.owner_id,
            SchemiiWorkspaceCreate(
                name="Must roll back",
                connection_id=target.id,
                database=target.database,
                namespace="public",
            ),
            bootstrap=bootstrap,
            baseline=baseline,
            baselines=FailAfterWritingBaseline(),
        )

    with factory() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    (SELECT count(*) FROM metadata.postgres_connections
                     WHERE owner_id = %s) AS connections,
                    (SELECT count(*) FROM schemii.workspaces
                     WHERE owner_id = %s) AS workspaces,
                    (SELECT count(*) FROM schemii.workspace_designs
                     WHERE owner_id = %s) AS designs,
                    (SELECT count(*) FROM schemii.workspace_design_imports
                     WHERE owner_id = %s) AS imports,
                    (SELECT count(*) FROM schemii.workspace_schema_baselines
                     WHERE owner_id = %s) AS baselines,
                    (SELECT count(*) FROM schemii.workspace_schema_baseline_heads
                     WHERE owner_id = %s) AS baseline_heads
                """,
                (postgres_metadata.owner_id,) * 6,
            )
            counts = cursor.fetchone()
    assert counts == {
        "connections": 1,
        "workspaces": 0,
        "designs": 0,
        "imports": 0,
        "baselines": 0,
        "baseline_heads": 0,
    }


def test_postgres_connection_delete_guard_preserves_referenced_target(
    postgres_metadata: PostgresMetadataHarness,
) -> None:
    target = _target(postgres_metadata)
    workspaces = PostgresWorkspaceRepository(postgres_metadata.connection_factory)
    workspace = workspaces.create(
        postgres_metadata.owner_id,
        SchemiiWorkspaceCreate(
            name="Connection guard",
            connection_id=target.id,
            database=target.database,
            namespace="public",
        ),
    )
    repository = postgres_metadata.repositories.connections
    ConnectionService(repository, (workspaces,))

    with pytest.raises(ConnectionInUseError) as blocked:
        repository.delete(
            postgres_metadata.owner_id,
            target.id,
            expected_revision=target.revision,
        )

    assert blocked.value.dependencies == {"schemiiWorkspaces": 1}
    assert repository.get(postgres_metadata.owner_id, target.id) == target

    workspaces.delete(
        postgres_metadata.owner_id,
        workspace.id,
        expected_revision=workspace.revision,
    )
    repository.delete(
        postgres_metadata.owner_id,
        target.id,
        expected_revision=target.revision,
    )
    assert repository.list(postgres_metadata.owner_id) == []
