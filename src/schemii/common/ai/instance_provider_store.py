"""Encrypted instance Zen credential and exact per-user product/source grants.

The public methods used by administrators return metadata only. ``resolve`` is
the sole internal method that decrypts a key, after checking the current grant.
An absent connection pair means a detached Schemii workspace, never a wildcard.
"""

from __future__ import annotations

import secrets
from threading import RLock
from typing import Any, Callable

from schemii.common.metadata.crypto import CredentialCipher, EncryptedCredential


_PROVIDER = "opencode"
_ENCRYPTION_OWNER = "schemii-instance"
_ENCRYPTION_ID = "ai-instance-provider:opencode"
_PRODUCTS = frozenset(("schemii", "schemoo", "schemer"))


def _source(connection_owner_id: str | None, connection_id: str | None) -> tuple[str | None, str | None]:
    if connection_owner_id is None and connection_id is None:
        return None, None
    if (not isinstance(connection_owner_id, str) or not connection_owner_id or "\0" in connection_owner_id
            or not isinstance(connection_id, str) or not connection_id.startswith("pg_")
            or len(connection_id) != 35 or any(c not in "0123456789abcdef" for c in connection_id[3:])):
        raise ValueError("Connection grants require an exact owner and PostgreSQL connection ID")
    return connection_owner_id, connection_id


def _grant(user_id: str, product: str, connection_owner_id: str | None,
           connection_id: str | None) -> tuple[str, str, str | None, str | None]:
    if not isinstance(user_id, str) or not user_id or "\0" in user_id:
        raise ValueError("A valid user ID is required")
    if product not in _PRODUCTS:
        raise ValueError("Unknown AI product")
    owner, connection = _source(connection_owner_id, connection_id)
    if owner is None and product != "schemii":
        raise ValueError("Only detached Schemii workspaces have no database connection")
    return user_id, product, owner, connection


def _public_grant(grant: tuple[str, str, str | None, str | None]) -> dict[str, str | None]:
    user, product, owner, connection = grant
    return {"userId": user, "product": product,
            "connectionOwnerId": owner, "connectionId": connection}


def _key(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > 16384:
        raise ValueError("An OpenCode Zen API key of at most 16384 bytes is required")
    return value


class MemoryInstanceAiProviderStore:
    """Encrypted in-memory implementation for tests and memory deployments."""

    def __init__(self, cipher: CredentialCipher | None = None) -> None:
        self._cipher = cipher or CredentialCipher(secrets.token_bytes(32))
        self._lock = RLock()
        self._encrypted: EncryptedCredential | None = None
        self._generation = 0
        self._grants: set[tuple[str, str, str | None, str | None]] = set()

    def status(self) -> dict[str, bool | int]:
        with self._lock:
            return {"connected": self._encrypted is not None, "generation": self._generation}

    def generation(self) -> int:
        with self._lock:
            return self._generation

    def set_key(self, api_key: str) -> dict[str, bool | int]:
        encrypted = self._cipher.encrypt(_ENCRYPTION_OWNER, _ENCRYPTION_ID, _key(api_key))
        with self._lock:
            self._encrypted = encrypted
            self._generation += 1
            return self.status()

    def clear_key(self) -> dict[str, bool | int]:
        with self._lock:
            self._encrypted = None
            self._generation += 1
            return self.status()

    def list_grants(self) -> list[dict[str, str | None]]:
        with self._lock:
            return [_public_grant(grant) for grant in sorted(
                self._grants, key=lambda row: tuple(value or "" for value in row))]

    def upsert_grant(self, user_id: str, product: str, connection_owner_id: str | None = None,
                     connection_id: str | None = None) -> dict[str, str | None]:
        grant = _grant(user_id, product, connection_owner_id, connection_id)
        with self._lock:
            self._grants.add(grant)
        return _public_grant(grant)

    def delete_grant(self, user_id: str, product: str, connection_owner_id: str | None = None,
                     connection_id: str | None = None) -> bool:
        grant = _grant(user_id, product, connection_owner_id, connection_id)
        with self._lock:
            if grant not in self._grants:
                return False
            self._grants.remove(grant)
            return True

    def has_grant(self, user_id: str, product: str, connection_owner_id: str | None = None,
                  connection_id: str | None = None) -> bool:
        grant = _grant(user_id, product, connection_owner_id, connection_id)
        with self._lock:
            return grant in self._grants

    def resolve(self, user_id: str, product: str, connection_owner_id: str | None = None,
                connection_id: str | None = None) -> dict[str, str | int] | None:
        grant = _grant(user_id, product, connection_owner_id, connection_id)
        with self._lock:
            if grant not in self._grants or self._encrypted is None:
                return None
            return {"credential": self._cipher.decrypt(
                _ENCRYPTION_OWNER, _ENCRYPTION_ID, self._encrypted),
                "generation": self._generation}


class PostgresInstanceAiProviderStore:
    """Durable metadata-backed store using the instance metadata encryption key."""

    def __init__(self, connection_factory: Callable[[], Any], cipher: CredentialCipher) -> None:
        self._connection_factory = connection_factory
        self._cipher = cipher

    def status(self) -> dict[str, bool | int]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT generation, ciphertext IS NOT NULL AS connected "
                           "FROM metadata.ai_instance_provider_credentials WHERE provider_id = %s", (_PROVIDER,))
            row = cursor.fetchone()
        return {"connected": bool(row["connected"]), "generation": row["generation"]} if row else {"connected": False, "generation": 0}

    def generation(self) -> int:
        return int(self.status()["generation"])

    def set_key(self, api_key: str) -> dict[str, bool | int]:
        encrypted = self._cipher.encrypt(_ENCRYPTION_OWNER, _ENCRYPTION_ID, _key(api_key))
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""UPDATE metadata.ai_instance_provider_credentials
                SET ciphertext = %s, nonce = %s, key_version = %s,
                    generation = generation + 1, updated_at = clock_timestamp()
                WHERE provider_id = %s RETURNING generation""",
                (encrypted.ciphertext, encrypted.nonce, encrypted.key_version, _PROVIDER))
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("Instance provider metadata is missing")
        return {"connected": True, "generation": row["generation"]}

    def clear_key(self) -> dict[str, bool | int]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""UPDATE metadata.ai_instance_provider_credentials
                SET ciphertext = NULL, nonce = NULL, key_version = NULL,
                    generation = generation + 1, updated_at = clock_timestamp()
                WHERE provider_id = %s RETURNING generation""", (_PROVIDER,))
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("Instance provider metadata is missing")
        return {"connected": False, "generation": row["generation"]}

    def list_grants(self) -> list[dict[str, str | None]]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT user_id, product, connection_owner_id, connection_id
                FROM metadata.ai_instance_provider_grants
                WHERE provider_id = %s
                ORDER BY user_id, product, connection_owner_id NULLS FIRST, connection_id NULLS FIRST""",
                (_PROVIDER,))
            rows = cursor.fetchall()
        return [_public_grant((row["user_id"], row["product"], row["connection_owner_id"], row["connection_id"])) for row in rows]

    def upsert_grant(self, user_id: str, product: str, connection_owner_id: str | None = None,
                     connection_id: str | None = None) -> dict[str, str | None]:
        grant = _grant(user_id, product, connection_owner_id, connection_id)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""INSERT INTO metadata.ai_instance_provider_grants
                (provider_id, user_id, product, connection_owner_id, connection_id)
                VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""", (_PROVIDER, *grant))
        return _public_grant(grant)

    def delete_grant(self, user_id: str, product: str, connection_owner_id: str | None = None,
                     connection_id: str | None = None) -> bool:
        grant = _grant(user_id, product, connection_owner_id, connection_id)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""DELETE FROM metadata.ai_instance_provider_grants
                WHERE provider_id = %s AND user_id = %s AND product = %s
                  AND connection_owner_id IS NOT DISTINCT FROM %s
                  AND connection_id IS NOT DISTINCT FROM %s""", (_PROVIDER, *grant))
            deleted = cursor.rowcount > 0
        return deleted

    def has_grant(self, user_id: str, product: str, connection_owner_id: str | None = None,
                  connection_id: str | None = None) -> bool:
        grant = _grant(user_id, product, connection_owner_id, connection_id)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT 1 FROM metadata.ai_instance_provider_grants
                WHERE provider_id = %s AND user_id = %s AND product = %s
                  AND connection_owner_id IS NOT DISTINCT FROM %s
                  AND connection_id IS NOT DISTINCT FROM %s""", (_PROVIDER, *grant))
            return cursor.fetchone() is not None

    def resolve(self, user_id: str, product: str, connection_owner_id: str | None = None,
                connection_id: str | None = None) -> dict[str, str | int] | None:
        grant = _grant(user_id, product, connection_owner_id, connection_id)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT credential.generation, credential.ciphertext,
                       credential.nonce, credential.key_version
                FROM metadata.ai_instance_provider_credentials AS credential
                JOIN metadata.ai_instance_provider_grants AS grant
                  ON grant.provider_id = credential.provider_id
                WHERE credential.provider_id = %s AND credential.ciphertext IS NOT NULL
                  AND grant.user_id = %s AND grant.product = %s
                  AND grant.connection_owner_id IS NOT DISTINCT FROM %s
                  AND grant.connection_id IS NOT DISTINCT FROM %s""", (_PROVIDER, *grant))
            row = cursor.fetchone()
        if row is None:
            return None
        credential = self._cipher.decrypt(_ENCRYPTION_OWNER, _ENCRYPTION_ID,
            EncryptedCredential(bytes(row["ciphertext"]), bytes(row["nonce"]), row["key_version"]))
        return {"credential": credential, "generation": row["generation"]}
