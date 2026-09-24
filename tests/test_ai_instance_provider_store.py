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


def test_instance_codex_credential_is_owned_and_provider_grants_are_separate() -> None:
    store = MemoryInstanceAiProviderStore(cipher=CredentialCipher(bytes(range(32))))
    first = {"type": "oauth", "refresh": "refresh-secret", "access": "access-secret"}
    assert store.status("openai-codex") == {"connected": False, "generation": 0}
    assert store.set_credential("openai-codex", first) == {"connected": True, "generation": 1}
    assert b"refresh-secret" not in store._rows["openai-codex"]["encrypted"].ciphertext
    assert "refresh-secret" not in repr(store.status("openai-codex"))
    assert store.credential() == {"credential": first, "generation": 1}
    assert store.resolve(ALICE, "schemii", provider_id="openai-codex") is None

    policy = store.upsert_grant(ALICE, "schemii", provider_id="openai-codex")
    assert policy == {"userId": ALICE, "product": "schemii",
                      "connectionOwnerId": None, "connectionId": None,
                      "modelId": "gpt-6-luna", "reasoningEffort": "default", "revision": 1}
    assert store.get_grant(ALICE, "schemii", provider_id="openai-codex") == policy
    assert store.list_grants("openai-codex") == [policy]
    assert store.list_grants() == []
    assert store.resolve(ALICE, "schemii", provider_id="openai-codex") == {
        "credential": first, "generation": 1,
        "modelId": "gpt-6-luna", "reasoningEffort": "default", "revision": 1}
    assert store.resolve(ALICE, "schemii") is None
    assert store.resolve(ALICE, "schemoo", SOURCE_OWNER, SOURCE,
                         provider_id="openai-codex") is None


def test_codex_refresh_and_policy_revision_fence_stale_turns() -> None:
    store = MemoryInstanceAiProviderStore()
    store.set_credential("openai-codex", {"refresh": "first"})
    first = store.upsert_grant(ALICE, "schemer", SOURCE_OWNER, SOURCE,
                               provider_id="openai-codex")
    assert store.save_credential("openai-codex", {"refresh": "second"}, 1)
    assert store.generation("openai-codex") == 1
    changed = store.upsert_grant(ALICE, "schemer", SOURCE_OWNER, SOURCE,
                                 provider_id="openai-codex", model_id="gpt-6-sol",
                                 reasoning_effort="high")
    assert changed["revision"] > first["revision"]
    assert changed["modelId"] == "gpt-6-sol"
    assert changed["reasoningEffort"] == "high"
    assert store.delete_grant(ALICE, "schemer", SOURCE_OWNER, SOURCE,
                              provider_id="openai-codex")
    assert store.get_grant(ALICE, "schemer", SOURCE_OWNER, SOURCE,
                           provider_id="openai-codex") is None
    recreated = store.upsert_grant(ALICE, "schemer", SOURCE_OWNER, SOURCE,
                                   provider_id="openai-codex")
    assert recreated["revision"] > changed["revision"]

    assert store.clear_credential("openai-codex") == {"connected": False, "generation": 2}
    assert store.credential() is None
    assert not store.save_credential("openai-codex", {"refresh": "stale"}, 1)
    assert store.set_credential("openai-codex", {"refresh": "new"}) == {
        "connected": True, "generation": 3}
    assert not store.save_credential("openai-codex", {"refresh": "stale"}, 1)
    assert store.credential()["credential"] == {"refresh": "new"}


def test_codex_credentials_and_policy_reject_invalid_values() -> None:
    store = MemoryInstanceAiProviderStore()
    with pytest.raises(ValueError, match="Unsupported instance AI provider"):
        store.status("other")
    with pytest.raises(ValueError, match="maximum size"):
        store.set_credential("openai-codex", {"refresh": "x" * 65536})
    with pytest.raises(ValueError, match="bounded Codex model"):
        store.upsert_grant(ALICE, "schemii", provider_id="openai-codex", model_id="")
    with pytest.raises(ValueError, match="bounded Codex model"):
        store.upsert_grant(ALICE, "schemii", provider_id="openai-codex", reasoning_effort="")


def test_multiple_direct_and_role_codex_policies_are_exact_and_independent() -> None:
    store = MemoryInstanceAiProviderStore()
    first = store.upsert_grant(ALICE, "schemii", SOURCE_OWNER, SOURCE,
                               provider_id="openai-codex", model_id="gpt-6-luna",
                               reasoning_effort="default")
    second = store.add_grant(ALICE, "schemii", SOURCE_OWNER, SOURCE,
                             model_id="gpt-6-sol", reasoning_effort="high")
    role = store.upsert_role_grant("role_readers", "schemii", SOURCE_OWNER, SOURCE,
                                   provider_id="openai-codex", model_id="gpt-6-sol",
                                   reasoning_effort="minimal")
    assert {grant["modelId"] for grant in store.list_grants("openai-codex")} == {
        "gpt-6-luna", "gpt-6-sol"}
    assert store.list_role_grants("openai-codex") == [role]
    assert store.delete_model_grant(ALICE, "schemii", SOURCE_OWNER, SOURCE,
                                    model_id="gpt-6-sol", reasoning_effort="high")
    assert store.list_grants("openai-codex") == [first]
    assert store.list_role_grants("openai-codex") == [role]
    assert store.delete_role_grant("role_readers", "schemii", SOURCE_OWNER, SOURCE,
                                   provider_id="openai-codex", model_id="gpt-6-sol",
                                   reasoning_effort="minimal")
    assert store.list_role_grants("openai-codex") == []
    assert second["revision"] > first["revision"]


def test_legacy_codex_upsert_replaces_scope_but_preserves_role_policies() -> None:
    store = MemoryInstanceAiProviderStore()
    store.add_grant(ALICE, "schemii", model_id="gpt-6-luna", reasoning_effort="low")
    store.add_grant(ALICE, "schemii", model_id="gpt-6-sol", reasoning_effort="high")
    store.upsert_role_grant("role_readers", "schemii", provider_id="openai-codex",
                            model_id="gpt-6-sol", reasoning_effort="minimal")
    replaced = store.upsert_grant(ALICE, "schemii", provider_id="openai-codex")
    assert store.list_grants("openai-codex") == [replaced]
    assert len(store.list_role_grants("openai-codex")) == 1
