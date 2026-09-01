from __future__ import annotations

import base64

import pytest
from cryptography.exceptions import InvalidTag

from schemii.common.metadata.config import MetadataConfig
from schemii.common.metadata.crypto import CredentialCipher
from schemii.common.metadata.database import (
    MetadataMigrationError,
    MetadataMigrator,
    packaged_migrations,
)
from schemii.common.metadata.factory import create_metadata_repositories
from schemii.common.metadata.secrets import read_encryption_key, read_secret_file


def test_unconfigured_metadata_keeps_isolated_tests_in_memory() -> None:
    repositories = create_metadata_repositories({})

    assert repositories.storage == "memory"
    assert repositories.durable is False


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


def test_packaged_metadata_migrations_are_contiguous_and_checksum_guarded() -> None:
    migrations = packaged_migrations()

    assert [migration.version for migration in migrations] == [1, 2]
    assert migrations[0].name == "0001_connections.sql"
    assert migrations[1].name == "0002_schemii_workspaces.sql"
    assert "CREATE TABLE schemii.workspaces" in migrations[1].sql
    assert "CREATE TABLE schemii.workspace_targets" in migrations[1].sql
    assert "CREATE TABLE schemii.workspace_table_positions" in migrations[1].sql
    assert "ON DELETE RESTRICT" in migrations[1].sql
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
