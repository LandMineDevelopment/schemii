from __future__ import annotations

import base64
import os
from pathlib import Path
import uuid

import pytest
from pydantic import SecretStr

from schemii.common.connections.models import PostgresConnectionCreate
from schemii.common.connections.policy import ConnectionTargetForbiddenError
from schemii.common.metadata.database import packaged_migrations
from schemii.common.metadata.factory import create_metadata_repositories


pytestmark = pytest.mark.skipif(
    not os.environ.get("SCHEMII_TEST_METADATA_DSN"),
    reason="SCHEMII_TEST_METADATA_DSN selects the real PostgreSQL integration test",
)


def test_postgres_metadata_migrates_persists_encrypts_and_reports_ready(
    tmp_path: Path,
) -> None:
    dsn = os.environ["SCHEMII_TEST_METADATA_DSN"]
    password = os.environ["SCHEMII_TEST_METADATA_PASSWORD"]
    password_file = tmp_path / "metadata-password"
    key_file = tmp_path / "metadata-key"
    password_file.write_text(f"{password}\n", encoding="utf-8")
    key_file.write_text(
        base64.b64encode(bytes(range(32))).decode("ascii") + "\n",
        encoding="utf-8",
    )
    environment = {
        "SCHEMII_STORAGE_MODE": "postgresql",
        "SCHEMII_METADATA_DSN": dsn,
        "SCHEMII_METADATA_PASSWORD_FILE": str(password_file),
        "SCHEMII_METADATA_ENCRYPTION_KEY_FILE": str(key_file),
        "SCHEMII_METADATA_TARGET_HOST_ALIASES": "metadata-postgres",
    }
    owner_id = f"integration_{uuid.uuid4().hex}"

    repositories = create_metadata_repositories(environment)
    try:
        repositories.check_readiness()
        assert repositories.storage == "postgresql"
        assert repositories.durable is True

        saved = repositories.connections.create(
            owner_id,
            PostgresConnectionCreate(
                name="Persistent integration target",
                host="application-postgres",
                port=5432,
                database="application_data",
                username="application_user",
                password=SecretStr("target-only-secret"),
            ),
        )

        reopened = create_metadata_repositories(environment)
        assert reopened.connections.list(owner_id) == [saved]
        resolved = reopened.connections.resolve(owner_id, saved.id)
        assert resolved.password is not None
        assert resolved.password.get_secret_value() == "target-only-secret"

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
        assert applied_versions == [migration.version for migration in packaged_migrations()]

        forbidden = PostgresConnectionCreate(
            name="Metadata bypass",
            host="127.0.0.1",
            port=5432,
            database="a_different_database_on_the_same_server",
            username="postgres",
        )
        with pytest.raises(ConnectionTargetForbiddenError):
            reopened.target_policy.validate(forbidden)
    finally:
        assert repositories.connection_factory is not None
        with repositories.connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM metadata.users WHERE id = %s", (owner_id,))
            connection.commit()
