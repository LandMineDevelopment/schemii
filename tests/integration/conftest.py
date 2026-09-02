"""Isolated owner fixture for tests against an explicitly selected PostgreSQL."""

import base64
import os
from pathlib import Path
from typing import Iterator
import uuid

import pytest

from schemii.common.metadata.factory import create_metadata_repositories
from schemii.common.metadata.migrations import (
    MIGRATION_PACKAGE as COMMON_MIGRATION_PACKAGE,
)
from schemii.schemii.metadata import (
    MIGRATION_PACKAGE as SCHEMII_MIGRATION_PACKAGE,
)
from tests.integration.postgres_fixture import PostgresMetadataHarness


_REQUIRED_ENVIRONMENT = (
    "SCHEMII_TEST_METADATA_DSN",
    "SCHEMII_TEST_METADATA_PASSWORD",
)


@pytest.fixture
def postgres_metadata(tmp_path: Path) -> Iterator[PostgresMetadataHarness]:
    missing = [name for name in _REQUIRED_ENVIRONMENT if not os.environ.get(name)]
    if missing:
        pytest.skip(
            "real PostgreSQL integration requires: " + ", ".join(missing)
        )

    password_file = tmp_path / "metadata-password"
    key_file = tmp_path / "metadata-key"
    password_file.write_text(
        f"{os.environ['SCHEMII_TEST_METADATA_PASSWORD']}\n",
        encoding="utf-8",
    )
    key_file.write_text(
        base64.b64encode(bytes(range(32))).decode("ascii") + "\n",
        encoding="utf-8",
    )
    environment = {
        "SCHEMII_STORAGE_MODE": "postgresql",
        "SCHEMII_METADATA_DSN": os.environ["SCHEMII_TEST_METADATA_DSN"],
        "SCHEMII_METADATA_PASSWORD_FILE": str(password_file),
        "SCHEMII_METADATA_ENCRYPTION_KEY_FILE": str(key_file),
        "SCHEMII_METADATA_TARGET_HOST_ALIASES": "metadata-postgres",
    }
    owner_id = f"integration_{uuid.uuid4().hex}"
    repositories = create_metadata_repositories(
        environment,
        migration_packages=(COMMON_MIGRATION_PACKAGE, SCHEMII_MIGRATION_PACKAGE),
    )
    harness = PostgresMetadataHarness(
        owner_id=owner_id,
        environment=environment,
        repositories=repositories,
    )
    try:
        yield harness
    finally:
        with harness.connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM schemii.workspaces WHERE owner_id = %s",
                    (owner_id,),
                )
                cursor.execute("DELETE FROM metadata.users WHERE id = %s", (owner_id,))
            connection.commit()
