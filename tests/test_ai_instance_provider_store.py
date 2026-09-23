from __future__ import annotations

import pytest

from schemii.common.ai.instance_provider_store import MemoryInstanceAiProviderStore
from schemii.common.metadata.crypto import CredentialCipher


ALICE = "user_alice"
SOURCE_OWNER = "user_schemii_connection_pool"
SOURCE = "pg_" + "a" * 32
OTHER_SOURCE = "pg_" + "b" * 32


def test_instance_key_is_encrypted_and_default_denied() -> None:
    store = MemoryInstanceAiProviderStore(cipher=CredentialCipher(bytes(range(32))))
    assert store.status() == {"connected": False, "generation": 0}
    assert store.resolve(ALICE, "schemoo", SOURCE_OWNER, SOURCE) is None

    assert store.set_key("zen-secret-key") == {"connected": True, "generation": 1}
    assert b"zen-secret-key" not in store._encrypted.ciphertext
    assert "zen-secret-key" not in repr(store.status())
    assert store.resolve(ALICE, "schemoo", SOURCE_OWNER, SOURCE) is None

    saved = store.upsert_grant(ALICE, "schemoo", SOURCE_OWNER, SOURCE)
    assert saved == {"userId": ALICE, "product": "schemoo",
                     "connectionOwnerId": SOURCE_OWNER, "connectionId": SOURCE}
    assert store.list_grants() == [saved]
    assert store.resolve(ALICE, "schemoo", SOURCE_OWNER, SOURCE) == {
        "credential": "zen-secret-key", "generation": 1}
    assert store.resolve(ALICE, "schemer", SOURCE_OWNER, SOURCE) is None
    assert store.resolve(ALICE, "schemoo", SOURCE_OWNER, OTHER_SOURCE) is None
    assert store.resolve("user_other", "schemoo", SOURCE_OWNER, SOURCE) is None
    assert store.resolve(ALICE, "schemoo", ALICE, SOURCE) is None


def test_grant_revocation_and_key_rotation_fence_old_turns() -> None:
    store = MemoryInstanceAiProviderStore()
    store.upsert_grant(ALICE, "schemer", SOURCE_OWNER, SOURCE)
    assert store.resolve(ALICE, "schemer", SOURCE_OWNER, SOURCE) is None
    assert store.set_key("first") == {"connected": True, "generation": 1}
    old = store.resolve(ALICE, "schemer", SOURCE_OWNER, SOURCE)
    assert old == {"credential": "first", "generation": 1}

    assert store.set_key("second") == {"connected": True, "generation": 2}
    assert store.generation() != old["generation"]
    assert store.resolve(ALICE, "schemer", SOURCE_OWNER, SOURCE) == {
        "credential": "second", "generation": 2}
    assert store.delete_grant(ALICE, "schemer", SOURCE_OWNER, SOURCE)
    assert not store.has_grant(ALICE, "schemer", SOURCE_OWNER, SOURCE)
    assert store.resolve(ALICE, "schemer", SOURCE_OWNER, SOURCE) is None
    assert not store.delete_grant(ALICE, "schemer", SOURCE_OWNER, SOURCE)

    assert store.clear_key() == {"connected": False, "generation": 3}
    assert store.clear_key() == {"connected": False, "generation": 4}


def test_detached_schemii_scope_is_explicit_and_not_a_wildcard() -> None:
    store = MemoryInstanceAiProviderStore()
    store.set_key("zen-secret-key")
    store.upsert_grant(ALICE, "schemii")
    assert store.has_grant(ALICE, "schemii")
    assert store.resolve(ALICE, "schemii") == {"credential": "zen-secret-key", "generation": 1}
    assert store.resolve(ALICE, "schemii", SOURCE_OWNER, SOURCE) is None

    with pytest.raises(ValueError, match="Only detached Schemii"):
        store.upsert_grant(ALICE, "schemoo")
    with pytest.raises(ValueError, match="exact owner"):
        store.upsert_grant(ALICE, "schemii", SOURCE_OWNER)
    with pytest.raises(ValueError, match="exact owner"):
        store.upsert_grant(ALICE, "schemii", SOURCE_OWNER, "not-a-connection")
    with pytest.raises(ValueError, match="Unknown AI product"):
        store.upsert_grant(ALICE, "other", SOURCE_OWNER, SOURCE)
    with pytest.raises(ValueError, match="OpenCode Zen API key"):
        store.set_key(" ")
