"""Encrypted installation AI credentials and exact per-user product/source grants.

Administrator status and grant methods return metadata only. ``resolve``
decrypts after checking an exact grant; ``credential`` is internal to the
administrator's provider test. An absent connection pair means a detached
Schemii workspace, never a wildcard.
"""

from __future__ import annotations

import json
import secrets
from threading import RLock
from typing import Any, Callable

from schemii.common.metadata.crypto import CredentialCipher, EncryptedCredential


_PROVIDER = "opencode"
_CODEX = "openai-codex"
_PROVIDERS = frozenset((_PROVIDER, _CODEX))
_ENCRYPTION_OWNER = "schemii-instance"
_PRODUCTS = frozenset(("schemii", "schemoo", "schemer"))


def _provider(provider_id: str) -> str:
    if provider_id not in _PROVIDERS:
        raise ValueError("Unsupported instance AI provider")
    return provider_id


def _encryption_id(provider_id: str) -> str:
    return "ai-instance-provider:" + _provider(provider_id)


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


def _public_grant(grant: tuple[str, str, str | None, str | None], *, provider_id: str = _PROVIDER,
                  model_id: str | None = None, reasoning_effort: str | None = None,
                  revision: int | None = None) -> dict[str, Any]:
    user, product, owner, connection = grant
    result: dict[str, Any] = {"userId": user, "product": product,
                              "connectionOwnerId": owner, "connectionId": connection}
    if provider_id == _CODEX:
        result.update(modelId=model_id, reasoningEffort=reasoning_effort, revision=revision)
    return result


def _policy(provider_id: str, model_id: str, reasoning_effort: str) -> tuple[str | None, str | None]:
    _provider(provider_id)
    if provider_id == _PROVIDER:
        return None, None
    if (not isinstance(model_id, str) or not 1 <= len(model_id) <= 128
            or "\0" in model_id or not isinstance(reasoning_effort, str)
            or not 1 <= len(reasoning_effort) <= 32 or "\0" in reasoning_effort):
        raise ValueError("A bounded Codex model and reasoning effort are required")
    return model_id, reasoning_effort


def _key(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > 16384:
        raise ValueError("An OpenCode Zen API key of at most 16384 bytes is required")
    return value


class _Encryption:
    def _encrypt(self, provider_id: str, credential: dict[str, Any]) -> EncryptedCredential:
        _provider(provider_id)
        if not isinstance(credential, dict) or not credential:
            raise ValueError("Credential must be a nonempty object")
        if provider_id == _PROVIDER:
            if set(credential) != {"key"}:
                raise ValueError("Zen credential requires only a key")
            plaintext = _key(credential["key"])
        else:
            plaintext = json.dumps({"provider_id": provider_id, "credential": credential},
                                   allow_nan=False, separators=(",", ":"))
            if len(plaintext.encode("utf-8")) > 65536:
                raise ValueError("Credential exceeds the maximum size")
        return self._cipher.encrypt(_ENCRYPTION_OWNER, _encryption_id(provider_id), plaintext)

    def _decrypt(self, provider_id: str, encrypted: EncryptedCredential) -> str | dict[str, Any]:
        plaintext = self._cipher.decrypt(_ENCRYPTION_OWNER, _encryption_id(provider_id), encrypted)
        if provider_id == _PROVIDER:
            return plaintext
        envelope = json.loads(plaintext)
        if envelope["provider_id"] != provider_id or not isinstance(envelope["credential"], dict):
            raise ValueError("Encrypted credential provider does not match its record")
        return envelope["credential"]


class MemoryInstanceAiProviderStore(_Encryption):
    """Encrypted in-memory implementation for tests and memory deployments."""

    def __init__(self, cipher: CredentialCipher | None = None) -> None:
        self._cipher = cipher or CredentialCipher(secrets.token_bytes(32))
        self._lock = RLock()
        self._rows: dict[str, dict[str, Any]] = {
            provider: {"encrypted": None, "generation": 0} for provider in _PROVIDERS}
        self._grants: dict[tuple[str, str, str, str | None, str | None], dict[str, Any]] = {}
        self._grant_revision = 0

    @property
    def _encrypted(self) -> EncryptedCredential | None:
        return self._rows[_PROVIDER]["encrypted"]

    def status(self, provider_id: str = _PROVIDER) -> dict[str, bool | int]:
        with self._lock:
            row = self._rows[_provider(provider_id)]
            return {"connected": row["encrypted"] is not None, "generation": row["generation"]}

    def generation(self, provider_id: str = _PROVIDER) -> int:
        with self._lock:
            return self._rows[_provider(provider_id)]["generation"]

    def set_credential(self, provider_id: str, credential: dict[str, Any]) -> dict[str, bool | int]:
        encrypted = self._encrypt(provider_id, credential)
        with self._lock:
            row = self._rows[provider_id]
            row["encrypted"] = encrypted
            row["generation"] += 1
            return self.status(provider_id)

    def save_credential(self, provider_id: str, credential: dict[str, Any],
                        expected_generation: int) -> bool:
        encrypted = self._encrypt(provider_id, credential)
        if isinstance(expected_generation, bool) or not isinstance(expected_generation, int):
            raise ValueError("Expected credential generation must be an integer")
        with self._lock:
            row = self._rows[provider_id]
            if row["encrypted"] is None or row["generation"] != expected_generation:
                return False
            row["encrypted"] = encrypted
            return True

    def clear_credential(self, provider_id: str) -> dict[str, bool | int]:
        with self._lock:
            row = self._rows[_provider(provider_id)]
            row["encrypted"] = None
            row["generation"] += 1
            return self.status(provider_id)

    def set_key(self, api_key: str) -> dict[str, bool | int]:
        return self.set_credential(_PROVIDER, {"key": api_key})

    def clear_key(self) -> dict[str, bool | int]:
        return self.clear_credential(_PROVIDER)

    def credential(self, provider_id: str = _CODEX) -> dict[str, Any] | None:
        with self._lock:
            row = self._rows[_provider(provider_id)]
            if row["encrypted"] is None:
                return None
            return {"credential": self._decrypt(provider_id, row["encrypted"]),
                    "generation": row["generation"]}

    def list_grants(self, provider_id: str = _PROVIDER) -> list[dict[str, Any]]:
        provider_id = _provider(provider_id)
        with self._lock:
            return [self._grant_public(key, value) for key, value in sorted(
                self._grants.items(), key=lambda item: tuple(value or "" for value in item[0]))
                if key[0] == provider_id]

    @staticmethod
    def _grant_public(key: tuple[str, str, str, str | None, str | None],
                      value: dict[str, Any]) -> dict[str, Any]:
        return _public_grant(key[1:], provider_id=key[0],
                             model_id=value["modelId"], reasoning_effort=value["reasoningEffort"],
                             revision=value["revision"])

    def upsert_grant(self, user_id: str, product: str, connection_owner_id: str | None = None,
                     connection_id: str | None = None, *, provider_id: str = _PROVIDER,
                     model_id: str = "gpt-6-luna", reasoning_effort: str = "default") -> dict[str, Any]:
        grant = (_provider(provider_id), *_grant(user_id, product, connection_owner_id, connection_id))
        model_id, reasoning_effort = _policy(provider_id, model_id, reasoning_effort)
        with self._lock:
            self._grant_revision += 1
            value = {"modelId": model_id, "reasoningEffort": reasoning_effort,
                     "revision": self._grant_revision}
            self._grants[grant] = value
            return self._grant_public(grant, value)

    def delete_grant(self, user_id: str, product: str, connection_owner_id: str | None = None,
                     connection_id: str | None = None, *, provider_id: str = _PROVIDER) -> bool:
        grant = (_provider(provider_id), *_grant(user_id, product, connection_owner_id, connection_id))
        with self._lock:
            if grant not in self._grants:
                return False
            del self._grants[grant]
            return True

    def has_grant(self, user_id: str, product: str, connection_owner_id: str | None = None,
                  connection_id: str | None = None, *, provider_id: str = _PROVIDER) -> bool:
        grant = (_provider(provider_id), *_grant(user_id, product, connection_owner_id, connection_id))
        with self._lock:
            return grant in self._grants

    def get_grant(self, user_id: str, product: str, connection_owner_id: str | None = None,
                  connection_id: str | None = None, *, provider_id: str = _PROVIDER) -> dict[str, Any] | None:
        grant = (_provider(provider_id), *_grant(user_id, product, connection_owner_id, connection_id))
        with self._lock:
            value = self._grants.get(grant)
            return self._grant_public(grant, value) if value else None

    def resolve(self, user_id: str, product: str, connection_owner_id: str | None = None,
                connection_id: str | None = None, *, provider_id: str = _PROVIDER) -> dict[str, Any] | None:
        grant = (_provider(provider_id), *_grant(user_id, product, connection_owner_id, connection_id))
        with self._lock:
            row = self._rows[provider_id]
            value = self._grants.get(grant)
            if value is None or row["encrypted"] is None:
                return None
            result = {"credential": self._decrypt(provider_id, row["encrypted"]),
                      "generation": row["generation"]}
            if provider_id == _CODEX:
                result.update(value)
            return result


class PostgresInstanceAiProviderStore(_Encryption):
    """Durable metadata-backed store using the instance metadata encryption key."""

    def __init__(self, connection_factory: Callable[[], Any], cipher: CredentialCipher) -> None:
        self._connection_factory = connection_factory
        self._cipher = cipher

    def status(self, provider_id: str = _PROVIDER) -> dict[str, bool | int]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT generation, ciphertext IS NOT NULL AS connected "
                           "FROM metadata.ai_instance_provider_credentials WHERE provider_id = %s", (_provider(provider_id),))
            row = cursor.fetchone()
        return {"connected": bool(row["connected"]), "generation": row["generation"]} if row else {"connected": False, "generation": 0}

    def generation(self, provider_id: str = _PROVIDER) -> int:
        return int(self.status(provider_id)["generation"])

    def set_credential(self, provider_id: str, credential: dict[str, Any]) -> dict[str, bool | int]:
        encrypted = self._encrypt(provider_id, credential)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""UPDATE metadata.ai_instance_provider_credentials
                SET ciphertext = %s, nonce = %s, key_version = %s,
                    generation = generation + 1, updated_at = clock_timestamp()
                WHERE provider_id = %s RETURNING generation""",
                (encrypted.ciphertext, encrypted.nonce, encrypted.key_version, provider_id))
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("Instance provider metadata is missing")
        return {"connected": True, "generation": row["generation"]}

    def save_credential(self, provider_id: str, credential: dict[str, Any],
                        expected_generation: int) -> bool:
        encrypted = self._encrypt(provider_id, credential)
        if isinstance(expected_generation, bool) or not isinstance(expected_generation, int):
            raise ValueError("Expected credential generation must be an integer")
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""UPDATE metadata.ai_instance_provider_credentials
                SET ciphertext = %s, nonce = %s, key_version = %s, updated_at = clock_timestamp()
                WHERE provider_id = %s AND generation = %s AND ciphertext IS NOT NULL""",
                (encrypted.ciphertext, encrypted.nonce, encrypted.key_version,
                 provider_id, expected_generation))
            return cursor.rowcount > 0

    def clear_credential(self, provider_id: str) -> dict[str, bool | int]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""UPDATE metadata.ai_instance_provider_credentials
                SET ciphertext = NULL, nonce = NULL, key_version = NULL,
                    generation = generation + 1, updated_at = clock_timestamp()
                WHERE provider_id = %s RETURNING generation""", (_provider(provider_id),))
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("Instance provider metadata is missing")
        return {"connected": False, "generation": row["generation"]}

    def set_key(self, api_key: str) -> dict[str, bool | int]:
        return self.set_credential(_PROVIDER, {"key": api_key})

    def clear_key(self) -> dict[str, bool | int]:
        return self.clear_credential(_PROVIDER)

    def credential(self, provider_id: str = _CODEX) -> dict[str, Any] | None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT generation, ciphertext, nonce, key_version
                FROM metadata.ai_instance_provider_credentials
                WHERE provider_id = %s AND ciphertext IS NOT NULL""", (_provider(provider_id),))
            row = cursor.fetchone()
        if row is None:
            return None
        return {"credential": self._decrypt(provider_id, EncryptedCredential(
                    bytes(row["ciphertext"]), bytes(row["nonce"]), row["key_version"])),
                "generation": row["generation"]}

    def list_grants(self, provider_id: str = _PROVIDER) -> list[dict[str, Any]]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT user_id, product, connection_owner_id, connection_id,
                       model_id, reasoning_effort, revision
                FROM metadata.ai_instance_provider_grants
                WHERE provider_id = %s
                ORDER BY user_id, product, connection_owner_id NULLS FIRST, connection_id NULLS FIRST""",
                (_provider(provider_id),))
            rows = cursor.fetchall()
        return [self._grant_public(row, provider_id) for row in rows]

    @staticmethod
    def _grant_public(row: Any, provider_id: str) -> dict[str, Any]:
        return _public_grant((row["user_id"], row["product"], row["connection_owner_id"],
                              row["connection_id"]), provider_id=provider_id,
                             model_id=row["model_id"], reasoning_effort=row["reasoning_effort"],
                             revision=row["revision"])

    def upsert_grant(self, user_id: str, product: str, connection_owner_id: str | None = None,
                     connection_id: str | None = None, *, provider_id: str = _PROVIDER,
                     model_id: str = "gpt-6-luna", reasoning_effort: str = "default") -> dict[str, Any]:
        grant = _grant(user_id, product, connection_owner_id, connection_id)
        model_id, reasoning_effort = _policy(provider_id, model_id, reasoning_effort)
        conflict = ("(provider_id, user_id, product) WHERE connection_id IS NULL"
                    if connection_id is None else
                    "(provider_id, user_id, product, connection_owner_id, connection_id) "
                    "WHERE connection_id IS NOT NULL")
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(f"""INSERT INTO metadata.ai_instance_provider_grants
                (provider_id, user_id, product, connection_owner_id, connection_id,
                 model_id, reasoning_effort)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT {conflict}
                DO UPDATE SET model_id = EXCLUDED.model_id,
                    reasoning_effort = EXCLUDED.reasoning_effort,
                    revision = nextval('metadata.ai_instance_provider_grant_revision_seq')
                RETURNING user_id, product, connection_owner_id, connection_id,
                          model_id, reasoning_effort, revision""",
                (_provider(provider_id), *grant, model_id, reasoning_effort))
            row = cursor.fetchone()
        return self._grant_public(row, provider_id)

    def delete_grant(self, user_id: str, product: str, connection_owner_id: str | None = None,
                     connection_id: str | None = None, *, provider_id: str = _PROVIDER) -> bool:
        grant = _grant(user_id, product, connection_owner_id, connection_id)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""DELETE FROM metadata.ai_instance_provider_grants
                WHERE provider_id = %s AND user_id = %s AND product = %s
                  AND connection_owner_id IS NOT DISTINCT FROM %s
                  AND connection_id IS NOT DISTINCT FROM %s""", (_provider(provider_id), *grant))
            deleted = cursor.rowcount > 0
        return deleted

    def has_grant(self, user_id: str, product: str, connection_owner_id: str | None = None,
                  connection_id: str | None = None, *, provider_id: str = _PROVIDER) -> bool:
        grant = _grant(user_id, product, connection_owner_id, connection_id)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT 1 FROM metadata.ai_instance_provider_grants
                WHERE provider_id = %s AND user_id = %s AND product = %s
                  AND connection_owner_id IS NOT DISTINCT FROM %s
                  AND connection_id IS NOT DISTINCT FROM %s""", (_provider(provider_id), *grant))
            return cursor.fetchone() is not None

    def get_grant(self, user_id: str, product: str, connection_owner_id: str | None = None,
                  connection_id: str | None = None, *, provider_id: str = _PROVIDER) -> dict[str, Any] | None:
        grant = _grant(user_id, product, connection_owner_id, connection_id)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT user_id, product, connection_owner_id, connection_id,
                       model_id, reasoning_effort, revision
                FROM metadata.ai_instance_provider_grants
                WHERE provider_id = %s AND user_id = %s AND product = %s
                  AND connection_owner_id IS NOT DISTINCT FROM %s
                  AND connection_id IS NOT DISTINCT FROM %s""", (_provider(provider_id), *grant))
            row = cursor.fetchone()
        return self._grant_public(row, provider_id) if row else None

    def resolve(self, user_id: str, product: str, connection_owner_id: str | None = None,
                connection_id: str | None = None, *, provider_id: str = _PROVIDER) -> dict[str, Any] | None:
        grant = _grant(user_id, product, connection_owner_id, connection_id)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT credential.generation, credential.ciphertext,
                       credential.nonce, credential.key_version, access_grant.model_id,
                       access_grant.reasoning_effort, access_grant.revision
                FROM metadata.ai_instance_provider_credentials AS credential
                JOIN metadata.ai_instance_provider_grants AS access_grant
                  ON access_grant.provider_id = credential.provider_id
                WHERE credential.provider_id = %s AND credential.ciphertext IS NOT NULL
                  AND access_grant.user_id = %s AND access_grant.product = %s
                  AND access_grant.connection_owner_id IS NOT DISTINCT FROM %s
                  AND access_grant.connection_id IS NOT DISTINCT FROM %s""", (_provider(provider_id), *grant))
            row = cursor.fetchone()
        if row is None:
            return None
        credential = self._decrypt(provider_id, EncryptedCredential(
            bytes(row["ciphertext"]), bytes(row["nonce"]), row["key_version"]))
        result = {"credential": credential, "generation": row["generation"]}
        if provider_id == _CODEX:
            result.update(modelId=row["model_id"], reasoningEffort=row["reasoning_effort"],
                          revision=row["revision"])
        return result
