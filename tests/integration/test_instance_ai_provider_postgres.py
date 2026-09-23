"""Real PostgreSQL checks for the installation-owned Zen credential and grants."""

from uuid import uuid4

import pytest
from pydantic import SecretStr

from schemii.common.auth.routes import AccountCreate
from schemii.common.auth.service import AuthService
from schemii.common.connections.models import PostgresConnectionCreate


def test_instance_zen_key_is_encrypted_and_grants_are_exact(postgres_metadata):
    store = postgres_metadata.repositories.ai_instance_providers
    if store.status()["connected"]:
        pytest.skip("Refusing to replace an existing installation Zen key")
    factory = postgres_metadata.connection_factory
    auth = AuthService(factory, enabled=True, setup_token="integration-only-not-used")
    username = "it_zen_" + uuid4().hex
    user = auth.create_user(AccountCreate(
        username=username, display_name="Zen integration user", password="integration-password-123"))
    user_id = user["id"]
    profile = None
    try:
        profile = postgres_metadata.repositories.connections.create(user_id, PostgresConnectionCreate(
            name="Zen integration database", host="integration-postgres", port=5432,
            database="integration_data", username="integration_reader",
            password=SecretStr("fixture-only-password"),
        ))
        assert store.resolve(user_id, "schemii") is None
        assert store.set_key("fake-zen-integration")["connected"]
        store.upsert_grant(user_id, "schemii", user_id, profile.id)
        assert store.resolve(user_id, "schemii") is None
        assert store.resolve(user_id, "schemoo", user_id, profile.id) is None
        assert store.resolve(user_id, "schemii", user_id, profile.id)["credential"] == "fake-zen-integration"
        with factory() as connection:
            row = connection.execute("SELECT ciphertext FROM metadata.ai_instance_provider_credentials "
                                     "WHERE provider_id='opencode'").fetchone()
            assert b"fake-zen-integration" not in bytes(row["ciphertext"])
        old_generation = store.generation()
        store.set_key("replacement-zen-integration")
        assert store.generation() > old_generation
        assert store.resolve(user_id, "schemii", user_id, profile.id)["credential"] == "replacement-zen-integration"
        assert store.delete_grant(user_id, "schemii", user_id, profile.id)
        assert store.resolve(user_id, "schemii", user_id, profile.id) is None
    finally:
        store.clear_key()
        with factory() as connection:
            if profile is not None:
                connection.execute("DELETE FROM metadata.postgres_connections WHERE owner_id=%s AND id=%s",
                                   (user_id, profile.id))
            connection.execute("DELETE FROM metadata.auth_accounts WHERE user_id=%s", (user_id,))
            connection.execute("DELETE FROM metadata.users WHERE id=%s", (user_id,))
