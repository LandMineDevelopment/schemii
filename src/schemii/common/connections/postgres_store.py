"""Durable PostgreSQL adapter for owner-scoped connection profiles."""

from __future__ import annotations

import secrets
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from cryptography.exceptions import InvalidTag
from pydantic import SecretStr

from schemii.common.metadata.crypto import CredentialCipher, EncryptedCredential

from .models import (
    PostgresConnectionCreate,
    PostgresConnectionMetadata,
    PostgresConnectionProfile,
    PostgresConnectionUpdate,
    ResolvedPostgresConnection,
)
from .store import (
    MAX_CONNECTIONS_PER_OWNER,
    ConnectionConflictError,
    ConnectionCredentialUnreadableError,
    ConnectionLimitError,
    ConnectionNotFoundError,
    ConnectionRepositoryError,
    ConnectionStorageUnavailableError,
)


class PostgresConnectionRepository:
    """Store profiles and authenticated-encrypted passwords in metadata PostgreSQL."""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        cipher: CredentialCipher,
        *,
        max_connections_per_owner: int = MAX_CONNECTIONS_PER_OWNER,
    ) -> None:
        if max_connections_per_owner < 1:
            raise ValueError("max_connections_per_owner must be positive")
        self._connection_factory = connection_factory
        self._cipher = cipher
        self._max_connections_per_owner = max_connections_per_owner

    def list(self, owner_id: str) -> list[PostgresConnectionProfile]:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT connection.*,
                           (credential.connection_id IS NOT NULL) AS credential_stored
                    FROM metadata.postgres_connections AS connection
                    LEFT JOIN metadata.postgres_connection_credentials AS credential
                      ON credential.owner_id = connection.owner_id
                     AND credential.connection_id = connection.id
                    WHERE connection.owner_id = %s
                    ORDER BY lower(connection.name), connection.id
                    """,
                    (owner_id,),
                )
                return [self._profile(row) for row in cursor.fetchall()]

    def get(self, owner_id: str, connection_id: str) -> PostgresConnectionProfile:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                row = self._select_profile(cursor, owner_id, connection_id)
                if row is None:
                    raise ConnectionNotFoundError("PostgreSQL connection was not found")
                return self._profile(row)

    def create(
        self,
        owner_id: str,
        request: PostgresConnectionCreate,
    ) -> PostgresConnectionProfile:
        connection_id = f"pg_{secrets.token_hex(16)}"
        encrypted = self._encrypt(owner_id, connection_id, request.password)
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO metadata.users (id, display_name)
                    VALUES (%s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (owner_id, "Local user"),
                )
                cursor.execute(
                    "SELECT id FROM metadata.users WHERE id = %s FOR UPDATE",
                    (owner_id,),
                )
                cursor.fetchone()
                cursor.execute(
                    """
                    SELECT count(*) AS connection_count
                    FROM metadata.postgres_connections
                    WHERE owner_id = %s
                    """,
                    (owner_id,),
                )
                if int(cursor.fetchone()["connection_count"]) >= self._max_connections_per_owner:
                    raise ConnectionLimitError(self._max_connections_per_owner)
                cursor.execute(
                    """
                    INSERT INTO metadata.postgres_connections (
                        id, owner_id, name, host, port, database_name, username,
                        ssl_mode, connect_timeout
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        connection_id,
                        owner_id,
                        request.name,
                        request.host,
                        request.port,
                        request.database,
                        request.username,
                        request.ssl_mode.value,
                        request.connect_timeout,
                    ),
                )
                row = cursor.fetchone()
                if encrypted is not None:
                    self._store_credential(cursor, owner_id, connection_id, encrypted)
                row["credential_stored"] = encrypted is not None
                return self._profile(row)

    def update(
        self,
        owner_id: str,
        connection_id: str,
        request: PostgresConnectionUpdate,
    ) -> PostgresConnectionProfile:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT *
                    FROM metadata.postgres_connections
                    WHERE owner_id = %s AND id = %s
                    FOR UPDATE
                    """,
                    (owner_id, connection_id),
                )
                current = cursor.fetchone()
                if current is None:
                    raise ConnectionNotFoundError("PostgreSQL connection was not found")
                if current["revision"] != request.expected_revision:
                    raise ConnectionConflictError(current["revision"])
                changes = request.model_dump(
                    exclude_unset=True,
                    exclude={"expected_revision", "password"},
                )
                metadata = PostgresConnectionMetadata.model_validate(
                    {**self._metadata_row(current), **changes}
                )
                cursor.execute(
                    """
                    UPDATE metadata.postgres_connections
                    SET revision = revision + 1,
                        name = %s,
                        host = %s,
                        port = %s,
                        database_name = %s,
                        username = %s,
                        ssl_mode = %s,
                        connect_timeout = %s,
                        updated_at = clock_timestamp()
                    WHERE owner_id = %s AND id = %s
                    RETURNING *
                    """,
                    (
                        metadata.name,
                        metadata.host,
                        metadata.port,
                        metadata.database,
                        metadata.username,
                        metadata.ssl_mode.value,
                        metadata.connect_timeout,
                        owner_id,
                        connection_id,
                    ),
                )
                row = cursor.fetchone()
                if "password" in request.model_fields_set:
                    if request.password is None:
                        cursor.execute(
                            """
                            DELETE FROM metadata.postgres_connection_credentials
                            WHERE owner_id = %s AND connection_id = %s
                            """,
                            (owner_id, connection_id),
                        )
                    else:
                        encrypted = self._encrypt(
                            owner_id,
                            connection_id,
                            request.password,
                        )
                        assert encrypted is not None
                        self._store_credential(
                            cursor,
                            owner_id,
                            connection_id,
                            encrypted,
                        )
                cursor.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM metadata.postgres_connection_credentials
                        WHERE owner_id = %s AND connection_id = %s
                    ) AS credential_stored
                    """,
                    (owner_id, connection_id),
                )
                row["credential_stored"] = cursor.fetchone()["credential_stored"]
                return self._profile(row)

    def delete(self, owner_id: str, connection_id: str, expected_revision: int) -> None:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT revision
                    FROM metadata.postgres_connections
                    WHERE owner_id = %s AND id = %s
                    FOR UPDATE
                    """,
                    (owner_id, connection_id),
                )
                current = cursor.fetchone()
                if current is None:
                    raise ConnectionNotFoundError("PostgreSQL connection was not found")
                if current["revision"] != expected_revision:
                    raise ConnectionConflictError(current["revision"])
                cursor.execute(
                    """
                    DELETE FROM metadata.postgres_connections
                    WHERE owner_id = %s AND id = %s
                    """,
                    (owner_id, connection_id),
                )

    def resolve(self, owner_id: str, connection_id: str) -> ResolvedPostgresConnection:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT connection.*,
                           credential.ciphertext,
                           credential.nonce,
                           credential.key_version
                    FROM metadata.postgres_connections AS connection
                    LEFT JOIN metadata.postgres_connection_credentials AS credential
                      ON credential.owner_id = connection.owner_id
                     AND credential.connection_id = connection.id
                    WHERE connection.owner_id = %s AND connection.id = %s
                    """,
                    (owner_id, connection_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ConnectionNotFoundError("PostgreSQL connection was not found")
                password = None
                if row["ciphertext"] is not None:
                    encrypted = EncryptedCredential(
                        ciphertext=bytes(row["ciphertext"]),
                        nonce=bytes(row["nonce"]),
                        key_version=row["key_version"],
                    )
                    try:
                        password = SecretStr(
                            self._cipher.decrypt(owner_id, connection_id, encrypted)
                        )
                    except (InvalidTag, UnicodeDecodeError, ValueError) as error:
                        raise ConnectionCredentialUnreadableError(
                            "The saved PostgreSQL password must be entered again"
                        ) from error
                return ResolvedPostgresConnection(
                    id=row["id"],
                    revision=row["revision"],
                    **self._metadata_row(row),
                    password=password,
                )

    def _select_profile(
        self,
        cursor: Any,
        owner_id: str,
        connection_id: str,
    ) -> dict[str, Any] | None:
        cursor.execute(
            """
            SELECT connection.*,
                   (credential.connection_id IS NOT NULL) AS credential_stored
            FROM metadata.postgres_connections AS connection
            LEFT JOIN metadata.postgres_connection_credentials AS credential
              ON credential.owner_id = connection.owner_id
             AND credential.connection_id = connection.id
            WHERE connection.owner_id = %s AND connection.id = %s
            """,
            (owner_id, connection_id),
        )
        return cursor.fetchone()

    def _store_credential(
        self,
        cursor: Any,
        owner_id: str,
        connection_id: str,
        encrypted: EncryptedCredential,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO metadata.postgres_connection_credentials (
                owner_id, connection_id, ciphertext, nonce, key_version
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (connection_id) DO UPDATE
            SET ciphertext = EXCLUDED.ciphertext,
                nonce = EXCLUDED.nonce,
                key_version = EXCLUDED.key_version,
                updated_at = clock_timestamp()
            """,
            (
                owner_id,
                connection_id,
                encrypted.ciphertext,
                encrypted.nonce,
                encrypted.key_version,
            ),
        )

    def _encrypt(
        self,
        owner_id: str,
        connection_id: str,
        password: SecretStr | None,
    ) -> EncryptedCredential | None:
        if password is None:
            return None
        return self._cipher.encrypt(
            owner_id,
            connection_id,
            password.get_secret_value(),
        )

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        try:
            connection = self._connection_factory()
        except Exception as error:
            raise ConnectionStorageUnavailableError(
                "Saved PostgreSQL connections are temporarily unavailable"
            ) from error
        try:
            yield connection
            connection.commit()
        except ConnectionRepositoryError:
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            raise ConnectionStorageUnavailableError(
                "Saved PostgreSQL connections are temporarily unavailable"
            ) from error
        finally:
            connection.close()

    @staticmethod
    def _profile(row: dict[str, Any]) -> PostgresConnectionProfile:
        return PostgresConnectionProfile(
            id=row["id"],
            revision=row["revision"],
            **PostgresConnectionRepository._metadata_row(row),
            credential_stored=row["credential_stored"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _metadata_row(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": row["name"],
            "host": row["host"],
            "port": row["port"],
            "database": row["database_name"],
            "username": row["username"],
            "ssl_mode": row["ssl_mode"],
            "connect_timeout": row["connect_timeout"],
        }
