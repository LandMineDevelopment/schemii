"""Authenticated encryption for credentials retained in metadata PostgreSQL."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@dataclass(frozen=True)
class EncryptedCredential:
    ciphertext: bytes
    nonce: bytes
    key_version: int


class CredentialCipher:
    """Bind encrypted passwords to their immutable owner and connection IDs."""

    KEY_VERSION = 1

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("credential encryption key must contain 32 bytes")
        self._cipher = AESGCM(key)

    def encrypt(
        self,
        owner_id: str,
        connection_id: str,
        plaintext: str,
    ) -> EncryptedCredential:
        nonce = secrets.token_bytes(12)
        ciphertext = self._cipher.encrypt(
            nonce,
            plaintext.encode("utf-8"),
            self._associated_data(owner_id, connection_id, self.KEY_VERSION),
        )
        return EncryptedCredential(
            ciphertext=ciphertext,
            nonce=nonce,
            key_version=self.KEY_VERSION,
        )

    def decrypt(
        self,
        owner_id: str,
        connection_id: str,
        encrypted: EncryptedCredential,
    ) -> str:
        if encrypted.key_version != self.KEY_VERSION:
            raise ValueError("stored credential uses an unsupported key version")
        plaintext = self._cipher.decrypt(
            encrypted.nonce,
            encrypted.ciphertext,
            self._associated_data(owner_id, connection_id, encrypted.key_version),
        )
        return plaintext.decode("utf-8")

    @staticmethod
    def _associated_data(owner_id: str, connection_id: str, key_version: int) -> bytes:
        return (
            f"schemii\0postgres-password\0{key_version}\0{owner_id}\0{connection_id}"
        ).encode("utf-8")
