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
from schemii.schemoo.metadata.migrations import (
    MIGRATION_PACKAGE as SCHEMOO_MIGRATION_PACKAGE,
)


def test_metadata_storage_mode_is_explicit_and_memory_is_test_selectable() -> None:
    with pytest.raises(ValueError, match="SCHEMII_STORAGE_MODE must be explicitly set"):
        create_metadata_repositories({})

    repositories = create_metadata_repositories({"SCHEMII_STORAGE_MODE": "memory"})

    assert repositories.storage == "memory"
    assert repositories.durable is False
    from schemii.common.ai.credential_store import MemoryAiCredentialStore

    assert isinstance(repositories.ai_credentials, MemoryAiCredentialStore)
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


def test_postgres_factory_composes_encrypted_ai_credentials(monkeypatch, tmp_path) -> None:
    import schemii.common.metadata.factory as factory_module
    from schemii.common.ai.credential_store import PostgresAiCredentialStore

    key_file = tmp_path / "key"
    key_file.write_text(base64.b64encode(bytes(range(32))).decode("ascii") + "\n")
    monkeypatch.setattr(factory_module.MetadataMigrator, "migrate", lambda self: 22)
    repositories = create_metadata_repositories(
        {
            "SCHEMII_STORAGE_MODE": "postgresql",
            "SCHEMII_METADATA_DSN": "host=metadata dbname=schemii",
            "SCHEMII_METADATA_PASSWORD_FILE": str(tmp_path / "password"),
            "SCHEMII_METADATA_ENCRYPTION_KEY_FILE": str(key_file),
        },
        migration_packages=(
            COMMON_MIGRATION_PACKAGE,
            SCHEMII_MIGRATION_PACKAGE,
            SCHEMOO_MIGRATION_PACKAGE,
        ),
    )

    store = repositories.ai_credentials
    assert isinstance(store, PostgresAiCredentialStore)
    assert store._connection_factory is repositories.connection_factory
    encrypted = store._encrypt("owner", "primary", "openai", {"apiKey": "secret"})
    assert CredentialCipher(bytes(range(32))).decrypt(
        "owner", "ai-credential:primary", encrypted
    ) == '{"provider_id":"openai","credential":{"apiKey":"secret"}}'


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
        (
            COMMON_MIGRATION_PACKAGE,
            SCHEMII_MIGRATION_PACKAGE,
            SCHEMOO_MIGRATION_PACKAGE,
        )
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
        14,
        15,
        16,
        17,
        18,
        19,
        20,
        21,
        22,
        23,
        24,
        25,
        26,
        27,
        28,
        29,
        30,
        31,
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
        "0014_console_transactions.sql": "5ebae3eb1abe811be761ee02458827d83095af5f8a147df60afc5beb95e402cf",
        "0015_console_saved_queries.sql": "472a6c067bc7e10c96ade52915d9e93023f6de69ac9ce7d2fc431e9fe209d431",
        "0016_console_query_starters.sql": "03725dd0b2e4a95b0ecb0fb452af9dd705a11d3b0bab9fe84a86ee2e3439e76b",
        "0017_console_result_privacy_and_history.sql": "2c4416a083f9058fc33bced8b03a8392ee96fcc5b06e3980233041f68b9eb8fd",
            "0018_limit_events.sql": "61d9a1522051aac24226a3ceeb57d69ba9859ecf53806fa57068399739d73412",
            "0019_ai_assistant.sql": "0f4a8baf1a85aee4e7e4f9d65227134a9347add2776f982553ff28367789fece",
            "0020_ai_lifecycle_retention.sql": "e341b3a48847351054e03835454fda22d6f1c7bbc16c06017b375cc082c74b94",
            "0021_ai_credentials.sql": "c5378cf82fb32ede10d44481e940474ddc84ae4eb1e5fed8af87dce141e07bbf",
            "0022_ai_credential_activity.sql": "eb6bfa354d91d2307ed26349be47c03e1611af0abf709270c9b7710d6f809758",
        "0023_console_preferences.sql": "b7ecb231f3ca4c6dcd017e537ca6b9882db32bf737723bc72617941be5ac37a4",
        "0024_ai_read_continuations.sql": "8cf84f7fd08300e0ecd1ad778ca4fa4d0f57a81d449d5cb8fe6d725396e50621",
        "0025_ai_action_policies.sql": "0f9e6a0df3a76a4ecc1c64fc846ebc3328d4ba336683d6680246dd0c45e2492f",
        "0026_ai_migration_recovery.sql": "a4b6db530ce706ceb9f4b3015951487bb96eed582a9f4e0bafc799894b2f3e39",
        "0027_schemoo_models.sql": "580b4e31d851132934a2c785db81d0a888b27dbdda9790ed2cc8f945222a124b",
        "0028_explicit_model_exposure.sql": "cd5d61feae9fcb9f58593369d1e16a7ea2a022296256c30220c4365deb6a8c9f",
        "0029_unknown_ai_read_counts.sql": "25242c625e8bb12f8f4eca8d1aefebf6af94b0afd4cfca1e80377f40b97fd779",
        "0030_product_ai_conversations.sql": "59583b8179e9162855fc4128147334de9520d71949b2aa615697cfe623d6007f",
        "0031_saved_model_previews.sql": "54efa0f765ee57d70eea7ae20f51a9f61cccafb942e283e9392d59315ae5fad5",
    }
    assert migrations[1].name == "0002_schemii_workspaces.sql"
    assert "CREATE TABLE schemii.workspaces" in migrations[1].sql
    assert "CREATE TABLE schemii.workspace_targets" in migrations[1].sql
    assert "CREATE TABLE schemii.workspace_table_positions" in migrations[1].sql
    assert "ON DELETE RESTRICT" in migrations[1].sql
    privacy_migration = next(
        migration.sql
        for migration in migrations
        if migration.name == "0017_console_result_privacy_and_history.sql"
    )
    assert "DROP COLUMN IF EXISTS rows_document" in privacy_migration
    assert "CREATE TABLE schemii.console_query_history" in privacy_migration
    assert "error_message" not in privacy_migration
    limit_events_migration = next(
        migration.sql
        for migration in migrations
        if migration.name == "0018_limit_events.sql"
    )
    assert "CREATE TABLE metadata.limit_events" in limit_events_migration
    assert "query_text" not in limit_events_migration
    assert "row_data" not in limit_events_migration
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
    assert "CREATE TABLE schemii.console_saved_queries" in migrations[14].sql
    assert "ADD COLUMN starter" in migrations[15].sql
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
