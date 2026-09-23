"""The metadata database enforces the personal/managed credential boundary."""

from __future__ import annotations

import pytest
from psycopg.errors import CheckViolation

from schemii.common.connections.models import (
    SCHEMII_CONNECTION_OWNER_ID,
    PostgresConnectionCreate,
)
from schemii.common.connections.store import ConnectionNotFoundError
from tests.integration.postgres_fixture import PostgresMetadataHarness


def _request(name: str, password: str) -> PostgresConnectionCreate:
    return PostgresConnectionCreate(
        name=name,
        host="application-postgres",
        database="application_data",
        username="report_reader",
        password=password,
    )


def test_managed_and_personal_connections_have_distinct_persisted_owners(
    postgres_metadata: PostgresMetadataHarness,
) -> None:
    repository = postgres_metadata.repositories.connections
    personal = repository.create(
        postgres_metadata.owner_id, _request("Personal", "personal-secret")
    )
    managed = repository.create(
        SCHEMII_CONNECTION_OWNER_ID, _request("Managed", "managed-secret")
    )
    try:
        assert personal.ownership == "user"
        assert managed.ownership == "schemii"
        assert repository.get(SCHEMII_CONNECTION_OWNER_ID, managed.id) == managed
        with pytest.raises(ConnectionNotFoundError):
            repository.get(postgres_metadata.owner_id, managed.id)
        with pytest.raises(ConnectionNotFoundError):
            repository.get(SCHEMII_CONNECTION_OWNER_ID, personal.id)

        resolved = repository.resolve(SCHEMII_CONNECTION_OWNER_ID, managed.id)
        assert resolved.ownership == "schemii"
        assert resolved.password.get_secret_value() == "managed-secret"

        with postgres_metadata.connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT ownership FROM metadata.postgres_connections WHERE id = %s",
                    (personal.id,),
                )
                assert cursor.fetchone()["ownership"] == "user"
                cursor.execute(
                    "SELECT ownership FROM metadata.postgres_connections WHERE id = %s",
                    (managed.id,),
                )
                assert cursor.fetchone()["ownership"] == "schemii"
                cursor.execute(
                    "SELECT ciphertext FROM metadata.postgres_connection_credentials WHERE connection_id = %s",
                    (managed.id,),
                )
                assert b"managed-secret" not in bytes(cursor.fetchone()["ciphertext"])

                for owner_id, ownership, connection_id in (
                    (postgres_metadata.owner_id, "schemii", "pg_" + "a" * 32),
                    (SCHEMII_CONNECTION_OWNER_ID, "user", "pg_" + "b" * 32),
                ):
                    with pytest.raises(CheckViolation):
                        with connection.transaction():
                            cursor.execute(
                                """
                                INSERT INTO metadata.postgres_connections (
                                    id, owner_id, ownership, name, host, port,
                                    database_name, username, ssl_mode, connect_timeout
                                ) VALUES (%s, %s, %s, 'Invalid', 'localhost', 5432,
                                          'application_data', 'reader', 'require', 10)
                                """,
                                (connection_id, owner_id, ownership),
                            )
    finally:
        repository.delete(SCHEMII_CONNECTION_OWNER_ID, managed.id, managed.revision)
        repository.delete(postgres_metadata.owner_id, personal.id, personal.revision)
