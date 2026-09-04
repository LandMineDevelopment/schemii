from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.exceptions import InvalidTag

from schemii.common.ai.credential_store import MemoryAiCredentialStore
from schemii.common.metadata.crypto import CredentialCipher


def test_credentials_are_owner_scoped_encrypted_and_listing_has_no_secrets():
    store = MemoryAiCredentialStore()
    attempt = store.begin_login("alice", "primary", "openai")
    secret = {"apiKey": "secret-key"}
    assert store.save("alice", "primary", "openai", secret, attempt["generation"])
    assert store.get("alice", "primary")["credential"] == secret
    assert store.get("bob", "primary") is None
    assert store.list("bob") == []
    assert store.list("alice") == [{"credential_id": "primary", "provider_id": "openai",
                                    "generation": 1, "connected": True}]
    assert "secret-key" not in repr(store._rows)
    secret["apiKey"] = "changed"
    fetched = store.get("alice", "primary")
    fetched["credential"]["apiKey"] = "changed"
    assert store.get("alice", "primary")["credential"]["apiKey"] == "secret-key"


def test_logout_and_new_login_fence_late_completions():
    store = MemoryAiCredentialStore()
    first = store.begin_login("alice", "primary", "openai-codex")
    second = store.begin_login("alice", "primary", "openai-codex")
    assert second["generation"] > first["generation"]
    assert not store.save("alice", "primary", "openai-codex", {"token": "late"}, first["generation"])
    assert store.save("alice", "primary", "openai-codex", {"token": "current"}, second["generation"])
    store.delete("alice", "primary")
    assert not store.save("alice", "primary", "openai-codex", {"token": "late"}, second["generation"])
    assert store.get("alice", "primary") is None
    assert store.list("alice") == []
    third = store.begin_login("alice", "primary", "openai-codex")
    assert third["generation"] > second["generation"]


def test_login_retains_existing_secret_only_for_same_provider():
    store = MemoryAiCredentialStore()
    store.begin_login("alice", "primary", "openai")
    store.save("alice", "primary", "openai", {"key": "old"}, 1)
    assert store.begin_login("alice", "primary", "openai")["connected"]
    assert store.get("alice", "primary")["credential"] == {"key": "old"}
    assert not store.begin_login("alice", "primary", "openai-codex")["connected"]
    assert not store.save("alice", "primary", "openai", {"key": "wrong-provider"}, 3)


def test_cancel_fences_late_completion_preserving_working_credential():
    store = MemoryAiCredentialStore()
    store.begin_login("alice", "primary", "openai")
    store.save("alice", "primary", "openai", {"key": "working"}, 1)
    attempt = store.begin_login("alice", "primary", "openai")
    assert store.fence_login("alice", "primary", attempt["generation"])
    assert not store.save("alice", "primary", "openai", {"key": "late"}, attempt["generation"])
    assert store.get("alice", "primary")["credential"] == {"key": "working"}
    assert not store.fence_login("alice", "primary", attempt["generation"])
    assert not store.fence_login("bob", "primary", attempt["generation"])
    assert not store.fence_login("alice", "missing", attempt["generation"])


def test_cancelling_old_login_cannot_invalidate_newer_login():
    store = MemoryAiCredentialStore()
    old = store.begin_login("alice", "primary", "openai-codex")
    new = store.begin_login("alice", "primary", "openai-codex")
    assert not store.fence_login("alice", "primary", old["generation"])
    assert store.save("alice", "primary", "openai-codex", {"token": "new"}, new["generation"])
    assert store.get("alice", "primary")["credential"] == {"token": "new"}


def test_ciphertext_is_bound_to_owner_id_and_provider():
    store = MemoryAiCredentialStore(cipher=CredentialCipher(b"x" * 32))
    store.begin_login("alice", "primary", "openai")
    store.save("alice", "primary", "openai", {"key": "secret"}, 1)
    original = deepcopy(store._rows[("alice", "primary")])
    store.touch_activity("bob")
    store._rows[("bob", "primary")] = deepcopy(original)
    with pytest.raises(InvalidTag):
        store.get("bob", "primary")
    store._rows[("alice", "other")] = {**original, "credential_id": "other"}
    with pytest.raises(InvalidTag):
        store.get("alice", "other")
    store._rows[("alice", "primary")]["provider_id"] = "openai-codex"
    with pytest.raises(ValueError, match="provider"):
        store.get("alice", "primary")


def test_limit_includes_tombstones_and_is_per_owner():
    store = MemoryAiCredentialStore(maximum_records=1)
    store.begin_login("alice", "one", "openai")
    store.delete("alice", "one")
    with pytest.raises(ValueError, match="Maximum"):
        store.begin_login("alice", "two", "openai")
    store.begin_login("alice", "one", "openai")
    store.begin_login("bob", "two", "openai")


@pytest.mark.parametrize("credential_id", ["", "a" * 129, "a\0b"])
def test_invalid_ids_are_rejected(credential_id):
    with pytest.raises(ValueError, match="Credential ID"):
        MemoryAiCredentialStore().begin_login("alice", credential_id, "openai")


def test_oversized_credentials_and_unknown_providers_are_rejected():
    store = MemoryAiCredentialStore()
    with pytest.raises(ValueError, match="Unsupported"):
        store.begin_login("alice", "one", "unknown")
    store.begin_login("alice", "one", "openai")
    with pytest.raises(ValueError, match="maximum size"):
        store.save("alice", "one", "openai", {"key": "x" * 65536}, 1)


def test_inactivity_expires_at_boundary_and_fences_late_refresh():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = MemoryAiCredentialStore(clock=lambda: now, inactivity_days=30)
    first = store.begin_login("alice", "one", "openai")
    assert store.save("alice", "one", "openai", {"key": "old"}, first["generation"])
    now += timedelta(days=29)
    assert store.save("alice", "one", "openai", {"key": "refresh"}, first["generation"])
    assert store.list("alice")
    now += timedelta(days=1)
    assert store.expire_inactive() == 1
    assert store.expire_inactive() == 0
    assert store.get("alice", "one") is None
    store.touch_activity("alice")
    assert not store.save("alice", "one", "openai", {"key": "late"}, first["generation"])
    assert all(store._rows[("alice", "one")][key] is None
               for key in ("ciphertext", "nonce", "key_version"))


def test_touch_expires_before_extending_activity_and_is_owner_scoped():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = MemoryAiCredentialStore(clock=lambda: now, inactivity_days=1)
    for owner in ("alice", "bob"):
        store.begin_login(owner, "one", "openai")
        store.save(owner, "one", "openai", {"key": owner}, 1)
    now += timedelta(hours=23)
    store.touch_activity("bob")
    now += timedelta(hours=1)
    store.touch_activity("alice")
    assert store.get("alice", "one") is None
    assert store.get("bob", "one") is not None


@pytest.mark.parametrize("access", ["get", "list", "save"])
def test_access_enforces_expiry_without_cleanup_job(access):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = MemoryAiCredentialStore(clock=lambda: now, inactivity_days=1)
    store.begin_login("alice", "one", "openai")
    store.save("alice", "one", "openai", {"key": "old"}, 1)
    now += timedelta(days=1)
    if access == "save":
        assert not store.save("alice", "one", "openai", {"key": "late"}, 1)
    elif access == "get":
        assert store.get("alice", "one") is None
    else:
        assert store.list("alice") == []
    assert store._rows[("alice", "one")]["ciphertext"] is None


def test_disabled_expiry_preserves_credentials_but_tracks_real_activity():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = MemoryAiCredentialStore(clock=lambda: now, expiration_enabled=False)
    store.begin_login("alice", "one", "openai")
    store.save("alice", "one", "openai", {"key": "old"}, 1)
    now += timedelta(days=500)
    assert store.expire_inactive() == 0
    assert store.get("alice", "one")
    assert store.save("alice", "one", "openai", {"key": "refresh"}, 1)
    store._expiration_enabled = True
    assert store.get("alice", "one") is None


def test_pending_login_is_fenced_on_inactivity_even_without_saved_credentials():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = MemoryAiCredentialStore(clock=lambda: now, inactivity_days=1)
    attempt = store.begin_login("alice", "one", "openai")
    now += timedelta(days=1)
    store.expire_inactive()
    store.touch_activity("alice")
    assert not store.save("alice", "one", "openai", {"key": "late"}, attempt["generation"])


def test_login_completion_advances_generation_to_reject_replayed_login_results():
    store = MemoryAiCredentialStore()
    attempt = store.begin_login("alice", "one", "openai-codex")
    assert store.save("alice", "one", "openai-codex", {"refresh": "initial"},
                      attempt["generation"], advance_generation=True)
    current = store.get("alice", "one")
    assert current["generation"] == attempt["generation"] + 1
    assert store.save("alice", "one", "openai-codex", {"refresh": "rotated"}, current["generation"])
    assert not store.save("alice", "one", "openai-codex", {"refresh": "initial"},
                          attempt["generation"], advance_generation=True)
    assert store.get("alice", "one")["credential"]["refresh"] == "rotated"
