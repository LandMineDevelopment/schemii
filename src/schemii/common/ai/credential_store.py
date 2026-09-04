"""Owner-scoped encrypted Pi credentials with login/logout generation fencing."""

from __future__ import annotations

from contextlib import contextmanager
import json
import secrets
from threading import RLock
from typing import Any, Callable

from schemii.common.errors import MetadataStorageUnavailableError
from schemii.common.metadata.crypto import CredentialCipher, EncryptedCredential
from schemii.common.metadata.users import ensure_local_metadata_user


def _validate(owner: str, credential_id: str, provider_id: str | None = None) -> None:
    if not isinstance(owner, str) or not owner or "\0" in owner:
        raise ValueError("A valid credential owner is required")
    if not isinstance(credential_id, str) or not 1 <= len(credential_id) <= 128 or "\0" in credential_id:
        raise ValueError("Credential ID must contain 1 to 128 characters")
    if provider_id is not None and provider_id not in {"openai", "openai-codex"}:
        raise ValueError("Unsupported AI credential provider")


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    return {"credential_id": row["credential_id"], "provider_id": row["provider_id"],
            "generation": row["generation"], "connected": row["ciphertext"] is not None}


class _Encryption:
    def _encrypt(self, owner: str, credential_id: str, provider_id: str,
                 credential: dict[str, Any]) -> EncryptedCredential:
        if not isinstance(credential, dict) or not credential:
            raise ValueError("Credential must be a nonempty object")
        plaintext = json.dumps({"provider_id": provider_id, "credential": credential},
                               allow_nan=False, separators=(",", ":"))
        if len(plaintext.encode("utf-8")) > 65536:
            raise ValueError("Credential exceeds the maximum size")
        return self._cipher.encrypt(owner, "ai-credential:" + credential_id, plaintext)

    def _decrypt(self, owner: str, row: dict[str, Any]) -> dict[str, Any]:
        envelope = json.loads(self._cipher.decrypt(
            owner, "ai-credential:" + row["credential_id"],
            EncryptedCredential(bytes(row["ciphertext"]), bytes(row["nonce"]), row["key_version"]),
        ))
        if envelope["provider_id"] != row["provider_id"]:
            raise ValueError("Encrypted credential provider does not match its record")
        return {**_metadata(row), "credential": envelope["credential"]}


class MemoryAiCredentialStore(_Encryption):
    """Process-local test store; even retained in-memory values are encrypted."""

    def __init__(self, *, maximum_records: int = 20, cipher: CredentialCipher | None = None) -> None:
        if maximum_records < 1:
            raise ValueError("maximum_records must be positive")
        self._maximum_records = maximum_records
        self._cipher = cipher or CredentialCipher(secrets.token_bytes(32))
        self._rows: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = RLock()

    def begin_login(self, owner: str, credential_id: str, provider_id: str) -> dict[str, Any]:
        _validate(owner, credential_id, provider_id)
        with self._lock:
            previous = self._rows.get((owner, credential_id))
            if previous is None and sum(key[0] == owner for key in self._rows) >= self._maximum_records:
                raise ValueError("Maximum AI credential records reached")
            row = {"credential_id": credential_id, "provider_id": provider_id,
                   "generation": previous["generation"] + 1 if previous else 1,
                   "ciphertext": None, "nonce": None, "key_version": None}
            if previous and previous["provider_id"] == provider_id:
                row.update({key: previous[key] for key in ("ciphertext", "nonce", "key_version")})
            self._rows[(owner, credential_id)] = row
            return _metadata(row)

    def save(self, owner: str, credential_id: str, provider_id: str,
             credential: dict[str, Any], expected_generation: int) -> bool:
        _validate(owner, credential_id, provider_id)
        encrypted = self._encrypt(owner, credential_id, provider_id, credential)
        with self._lock:
            row = self._rows.get((owner, credential_id))
            if row is None or row["generation"] != expected_generation or row["provider_id"] != provider_id:
                return False
            row.update(ciphertext=encrypted.ciphertext, nonce=encrypted.nonce,
                       key_version=encrypted.key_version)
            return True

    def get(self, owner: str, credential_id: str) -> dict[str, Any] | None:
        _validate(owner, credential_id)
        with self._lock:
            row = self._rows.get((owner, credential_id))
            return self._decrypt(owner, row) if row and row["ciphertext"] is not None else None

    def list(self, owner: str) -> list[dict[str, Any]]:
        with self._lock:
            return [_metadata(row) for key, row in sorted(self._rows.items())
                    if key[0] == owner and row["ciphertext"] is not None]

    def delete(self, owner: str, credential_id: str) -> None:
        _validate(owner, credential_id)
        with self._lock:
            row = self._rows.get((owner, credential_id))
            if row:
                row.update(generation=row["generation"] + 1, ciphertext=None,
                           nonce=None, key_version=None)

    def fence_login(self, owner: str, credential_id: str, expected_generation: int) -> bool:
        """Cancel one login generation without removing a working credential."""
        _validate(owner, credential_id)
        with self._lock:
            row = self._rows.get((owner, credential_id))
            if row is None or row["generation"] != expected_generation:
                return False
            row["generation"] += 1
            return True


class PostgresAiCredentialStore(_Encryption):
    def __init__(self, connection_factory: Callable[[], Any], cipher: CredentialCipher,
                 *, maximum_records: int = 20) -> None:
        if maximum_records < 1:
            raise ValueError("maximum_records must be positive")
        self._connection_factory = connection_factory
        self._cipher = cipher
        self._maximum_records = maximum_records

    @contextmanager
    def _transaction(self):
        connection = None
        try:
            connection = self._connection_factory()
            with connection.cursor() as cursor:
                yield cursor
            connection.commit()
        except ValueError:
            if connection is not None:
                connection.rollback()
            raise
        except Exception as error:
            if connection is not None:
                connection.rollback()
            raise MetadataStorageUnavailableError("AI credential storage is temporarily unavailable") from error
        finally:
            if connection is not None:
                connection.close()

    def begin_login(self, owner: str, credential_id: str, provider_id: str) -> dict[str, Any]:
        _validate(owner, credential_id, provider_id)
        with self._transaction() as cursor:
            ensure_local_metadata_user(cursor, owner)
            cursor.execute("SELECT credential_id FROM metadata.ai_credentials WHERE owner_id = %s AND credential_id = %s",
                           (owner, credential_id))
            if cursor.fetchone() is None:
                cursor.execute("SELECT count(*) AS count FROM metadata.ai_credentials WHERE owner_id = %s", (owner,))
                if cursor.fetchone()["count"] >= self._maximum_records:
                    raise ValueError("Maximum AI credential records reached")
            cursor.execute("""
                INSERT INTO metadata.ai_credentials (owner_id, credential_id, provider_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (owner_id, credential_id) DO UPDATE SET
                    provider_id = EXCLUDED.provider_id,
                    generation = metadata.ai_credentials.generation + 1,
                    ciphertext = CASE WHEN metadata.ai_credentials.provider_id = EXCLUDED.provider_id
                        THEN metadata.ai_credentials.ciphertext END,
                    nonce = CASE WHEN metadata.ai_credentials.provider_id = EXCLUDED.provider_id
                        THEN metadata.ai_credentials.nonce END,
                    key_version = CASE WHEN metadata.ai_credentials.provider_id = EXCLUDED.provider_id
                        THEN metadata.ai_credentials.key_version END,
                    updated_at = clock_timestamp()
                RETURNING credential_id, provider_id, generation, ciphertext
                """, (owner, credential_id, provider_id))
            return _metadata(cursor.fetchone())

    def save(self, owner: str, credential_id: str, provider_id: str,
             credential: dict[str, Any], expected_generation: int) -> bool:
        _validate(owner, credential_id, provider_id)
        encrypted = self._encrypt(owner, credential_id, provider_id, credential)
        with self._transaction() as cursor:
            cursor.execute("""
                UPDATE metadata.ai_credentials SET ciphertext = %s, nonce = %s,
                    key_version = %s, updated_at = clock_timestamp()
                WHERE owner_id = %s AND credential_id = %s AND provider_id = %s
                    AND generation = %s
                RETURNING credential_id
                """, (encrypted.ciphertext, encrypted.nonce, encrypted.key_version,
                      owner, credential_id, provider_id, expected_generation))
            return cursor.fetchone() is not None

    def get(self, owner: str, credential_id: str) -> dict[str, Any] | None:
        _validate(owner, credential_id)
        with self._transaction() as cursor:
            cursor.execute("""SELECT credential_id, provider_id, generation, ciphertext, nonce, key_version
                FROM metadata.ai_credentials WHERE owner_id = %s AND credential_id = %s
                AND ciphertext IS NOT NULL""", (owner, credential_id))
            row = cursor.fetchone()
            return self._decrypt(owner, row) if row else None

    def list(self, owner: str) -> list[dict[str, Any]]:
        with self._transaction() as cursor:
            cursor.execute("""SELECT credential_id, provider_id, generation, TRUE AS connected
                FROM metadata.ai_credentials WHERE owner_id = %s AND ciphertext IS NOT NULL
                ORDER BY credential_id""", (owner,))
            return [dict(row) for row in cursor.fetchall()]

    def delete(self, owner: str, credential_id: str) -> None:
        _validate(owner, credential_id)
        with self._transaction() as cursor:
            # Serialize with first-time login creation so an uncommitted insert
            # cannot escape a logout that arrived after that login started.
            ensure_local_metadata_user(cursor, owner)
            cursor.execute("""UPDATE metadata.ai_credentials SET generation = generation + 1,
                ciphertext = NULL, nonce = NULL, key_version = NULL, updated_at = clock_timestamp()
                WHERE owner_id = %s AND credential_id = %s""", (owner, credential_id))

    def fence_login(self, owner: str, credential_id: str, expected_generation: int) -> bool:
        """Cancel one login generation without removing a working credential."""
        _validate(owner, credential_id)
        with self._transaction() as cursor:
            ensure_local_metadata_user(cursor, owner)
            cursor.execute("""UPDATE metadata.ai_credentials SET generation = generation + 1,
                updated_at = clock_timestamp()
                WHERE owner_id = %s AND credential_id = %s AND generation = %s
                RETURNING credential_id""", (owner, credential_id, expected_generation))
            return cursor.fetchone() is not None
