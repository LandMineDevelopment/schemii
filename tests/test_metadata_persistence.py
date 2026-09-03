from __future__ import annotations

import base64

import pytest
from cryptography.exceptions import InvalidTag

from schemii.common.metadata.config import MetadataConfig
from schemii.common.metadata.crypto import CredentialCipher
from schemii.common.metadata.database import (
    MetadataReadinessProbe,
    MetadataMigrationError,
    MetadataMigrator,
    packaged_migrations,
)
from schemii.common.metadata.factory import create_metadata_repositories
from schemii.common.metadata.migrations import (
    MIGRATION_PACKAGE as COMMON_MIGRATION_PACKAGE,
)
from schemii.common.metadata.secrets import read_encryption_key, read_secret_file
from schemii.schemii.metadata import (
    MIGRATION_PACKAGE as SCHEMII_MIGRATION_PACKAGE,
)


def test_metadata_storage_mode_is_explicit_and_memory_is_test_selectable() -> None:
    with pytest.raises(ValueError, match="SCHEMII_STORAGE_MODE must be explicitly set"):
        create_metadata_repositories({})

    repositories = create_metadata_repositories({"SCHEMII_STORAGE_MODE": "memory"})

    assert repositories.storage == "memory"
    assert repositories.durable is False
    repositories.check_readiness()

    with pytest.raises(ValueError, match="must not be set"):
        create_metadata_repositories(
            {
                "SCHEMII_STORAGE_MODE": "memory",
                "SCHEMII_METADATA_DSN": "host=metadata dbname=schemii",
            }
        )

    with pytest.raises(ValueError, match="SCHEMII_METADATA_DSN is required"):
        create_metadata_repositories({"SCHEMII_STORAGE_MODE": "postgresql"})


def test_metadata_configuration_requires_absolute_secret_files() -> None:
    with pytest.raises(ValueError, match="password file must be an absolute path"):
        MetadataConfig(
            dsn="host=metadata dbname=schemii",
            password_file="password",
            encryption_key_file="/run/secrets/key",
        )

    with pytest.raises(ValueError, match="encryption key file must be an absolute path"):
        MetadataConfig(
            dsn="host=metadata dbname=schemii",
            password_file="/run/secrets/password",
            encryption_key_file="key",
        )


def test_secret_files_are_strict_and_encryption_keys_are_exact(tmp_path) -> None:
    secret = tmp_path / "secret"
    secret.write_text("database-password\n", encoding="utf-8")
    assert read_secret_file(str(secret), "TEST_SECRET") == "database-password"

    secret.write_text(" database-password\n", encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one non-empty line"):
        read_secret_file(str(secret), "TEST_SECRET")

    key_file = tmp_path / "key"
    key = bytes(range(32))
    key_file.write_text(base64.b64encode(key).decode("ascii") + "\n", encoding="utf-8")
    assert read_encryption_key(str(key_file)) == key

    key_file.write_text(base64.b64encode(key[:-1]).decode("ascii") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="256-bit"):
        read_encryption_key(str(key_file))


def test_credentials_are_authenticated_to_owner_and_connection() -> None:
    cipher = CredentialCipher(bytes(range(32)))
    encrypted = cipher.encrypt("owner-a", "pg_" + "a" * 32, "private password")

    assert cipher.decrypt("owner-a", "pg_" + "a" * 32, encrypted) == "private password"
    assert b"private password" not in encrypted.ciphertext
    with pytest.raises(InvalidTag):
        cipher.decrypt("owner-b", "pg_" + "a" * 32, encrypted)
    with pytest.raises(InvalidTag):
        cipher.decrypt("owner-a", "pg_" + "b" * 32, encrypted)


def test_metadata_readiness_probe_executes_and_closes_or_wraps_failure() -> None:
    class Cursor:
        def __init__(self, row):
            self.row = row
            self.executed = []

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def execute(self, query):
            self.executed.append(query)

        def fetchone(self):
            return self.row

    class Connection:
        def __init__(self, row):
            self.cursor_instance = Cursor(row)
            self.closed = False

        def cursor(self):
            return self.cursor_instance

        def close(self):
            self.closed = True

    connection = Connection({"ready": 1})
    MetadataReadinessProbe(lambda: connection)()

    assert connection.cursor_instance.executed == ["SELECT 1 AS ready"]
    assert connection.closed is True

    broken = Connection(None)
    with pytest.raises(RuntimeError, match="Durable metadata is temporarily unavailable"):
        MetadataReadinessProbe(lambda: broken)()
    assert broken.closed is True


def test_common_metadata_migrations_are_product_independent() -> None:
    migrations = packaged_migrations((COMMON_MIGRATION_PACKAGE,))

    assert [migration.version for migration in migrations] == [1]
    assert migrations[0].name == "0001_connections.sql"
    assert "CREATE TABLE metadata.postgres_connections" in migrations[0].sql
    assert "CREATE SCHEMA IF NOT EXISTS schemii" not in migrations[0].sql


def test_composed_metadata_history_preserves_deployed_names_and_checksums() -> None:
    migrations = packaged_migrations(
        (COMMON_MIGRATION_PACKAGE, SCHEMII_MIGRATION_PACKAGE)
    )

    assert [migration.version for migration in migrations] == [
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        12,
        13,
    ]
    assert {migration.name: migration.checksum for migration in migrations} == {
        "0001_connections.sql": "c00ad440b1237618dab9515c9113bcde5ef63721d642f0764e6eb9ae1bdadc65",
        "0002_schemii_workspaces.sql": "06e20fb4ff4624a7246616307dc1d81db53ca2bffe8f9a261b53b097d1725c91",
        "0003_schemii_designs.sql": "2a92c8fceaf2c1edd7793f4205b5fc65f7c283bdae6ec778af1f9845dc20650e",
        "0004_workspace_column_display_orders.sql": "b796f89804fb1144d9f3d1ff71040117dd15aa6be2d6e77db6c169758985063c",
        "0005_workspace_design_imports.sql": "2914f5484fcac5d1a0bca4aa33b61cadb130149882704d5aea05d6738c00c28b",
        "0006_schemii_migrations.sql": "d41dab9b6db1bf1b7e859e241c89200825594d8c92022157375f5faf34dc5d91",
        "0007_design_history.sql": "f5bd7323f69fe20a17eedbe988079845dab36c77533b5424f4dbad3ea1200635",
        "0008_migration_execution_leases.sql": "3e88e8793cce822a50a563df389b0d97416dc8b8ec4ebf8062f3309f2fcebf9c",
        "0009_async_migration_execution.sql": "d752bbef98a6d639f90d9a25c6423c41aea28801d3e4a1c12c86fcff9f706adc",
        "0010_metadata_retention_indexes.sql": "2f5a939e2a0f2f199db11f81a330908551d07c926019983a686b7defea76af52",
        "0011_console_executions.sql": "6f98a2f29b676f3bfb1e69a6d3c3d5e507bdf30a2f885a3ae769c982ca5c939c",
        "0012_remove_workspace_modes.sql": "e8c7428bfa7e3143bbfe49df044520c06ddaba4926b9df2a51c9be4879876bcf",
        "0013_unique_workspace_targets.sql": "06c8c323f5f25c3a7f34edcdd636c5d08b975d006238065b60a698d2b2323a4e",
    }
    assert migrations[1].name == "0002_schemii_workspaces.sql"
    assert "CREATE TABLE schemii.workspaces" in migrations[1].sql
    assert "CREATE TABLE schemii.workspace_targets" in migrations[1].sql
    assert "CREATE TABLE schemii.workspace_table_positions" in migrations[1].sql
    assert "ON DELETE RESTRICT" in migrations[1].sql
    assert migrations[2].name == "0003_schemii_designs.sql"
    assert "CREATE TABLE schemii.workspace_designs" in migrations[2].sql
    assert "CREATE TABLE schemii.workspace_design_layouts" in migrations[2].sql
    assert "camera and inspector state remain browser-owned" in migrations[2].sql
    assert migrations[3].name == "0004_workspace_column_display_orders.sql"
    assert "CREATE TABLE schemii.workspace_table_column_orders" in migrations[3].sql
    assert "PostgreSQL attnum remains authoritative" in migrations[3].sql
    assert "CREATE TABLE schemii.workspace_design_history_entries" in migrations[6].sql
    assert "CREATE TABLE schemii.workspace_design_position_memory" in migrations[6].sql
    assert migrations[7].name == "0008_migration_execution_leases.sql"
    assert "lease_expires_at" in migrations[7].sql
    assert migrations[8].name == "0009_async_migration_execution.sql"
    assert "durable queued work" in migrations[8].sql
    assert migrations[9].name == "0010_metadata_retention_indexes.sql"
    assert "migration_plans_retention_idx" in migrations[9].sql
    assert "workspace_design_history_transitions_retention_idx" in migrations[9].sql
    assert migrations[10].name == "0011_console_executions.sql"
    assert "UNIQUE (owner_id, connection_id, database_name, namespace)" in migrations[12].sql
    assert "CREATE TABLE schemii.console_executions" in migrations[10].sql
    migrator = MetadataMigrator(lambda: None, migrations)
    assert migrator._validate_applied(
        [
            {
                "version": 1,
                "name": migrations[0].name,
                "checksum": migrations[0].checksum,
            }
        ]
    ) == {1}

    with pytest.raises(MetadataMigrationError, match="does not match"):
        migrator._validate_applied(
            [
                {
                    "version": 1,
                    "name": migrations[0].name,
                    "checksum": "0" * 64,
                }
            ]
        )


def test_migration_composition_rejects_duplicate_and_gapped_versions() -> None:
    common = packaged_migrations((COMMON_MIGRATION_PACKAGE,))

    with pytest.raises(MetadataMigrationError, match="must not be empty"):
        packaged_migrations(())
    with pytest.raises(MetadataMigrationError, match="registered twice"):
        packaged_migrations((COMMON_MIGRATION_PACKAGE, COMMON_MIGRATION_PACKAGE))
    with pytest.raises(MetadataMigrationError, match="contiguous history"):
        packaged_migrations((SCHEMII_MIGRATION_PACKAGE,))
    with pytest.raises(MetadataMigrationError, match="versions must be unique"):
        MetadataMigrator(lambda: None, (common[0], common[0]))
