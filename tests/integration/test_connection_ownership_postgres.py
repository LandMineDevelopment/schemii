"""The metadata database enforces the personal/managed credential boundary."""

from __future__ import annotations

import pytest
from psycopg.errors import CheckViolation
from types import SimpleNamespace
from uuid import uuid4

from schemii.common.ai.instance_provider_store import MemoryInstanceAiProviderStore
from schemii.common.ai.routes import _admin_instance_state
from schemii.common.auth.service import AuthService

from schemii.common.connections.models import (
    SCHEMII_CONNECTION_OWNER_ID,
    PostgresConnectionCreate,
)
from schemii.common.connections.store import ConnectionNotFoundError
from schemii.common.connections.service import ConnectionService
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


def test_provider_inventory_batches_real_owner_metadata_and_current_authority(
    postgres_metadata: PostgresMetadataHarness,
    monkeypatch,
) -> None:
    repository = postgres_metadata.repositories.connections
    owner = postgres_metadata.owner_id
    peer = f"peer-{uuid4().hex}"
    role_id = f"role-{uuid4().hex}"
    personal = repository.create(owner, _request("Personal", "personal-secret"))
    excluded = repository.create(peer, _request("Peer", "peer-secret"))
    managed = repository.create(SCHEMII_CONNECTION_OWNER_ID, _request("Managed", "managed-secret"))
    auth = AuthService(postgres_metadata.connection_factory, enabled=True, setup_token="unused")
    instance = MemoryInstanceAiProviderStore()
    connections = ConnectionService(repository, ())
    connections.set_authority(auth)
    with auth.store.transaction(write=True) as state:
        state["users"][owner] = dict(id=owner, username=f"inventory-{uuid4().hex}", display_name="Inventory",
                                    password_hash="unused", is_admin=False, disabled=False)
        state["roles"][role_id] = dict(id=role_id, name=role_id, user_ids=[owner],
                                      capabilities=["schemoo:access"], dashboards=[],
                                      connections=[dict(owner_id=SCHEMII_CONNECTION_OWNER_ID,
                                                        connection_id=managed.id, allow_authoring=True)])
    instance.upsert_role_grant(role_id, "schemoo", SCHEMII_CONNECTION_OWNER_ID, managed.id)
    reads = dict(authority=0, profiles=0)
    auth_factory = auth.store.factory
    profile_factory = repository._connection_factory

    def authority_factory():
        reads["authority"] += 1
        return auth_factory()

    def metadata_factory():
        reads["profiles"] += 1
        return profile_factory()

    monkeypatch.setattr(auth.store, "factory", authority_factory)
    monkeypatch.setattr(repository, "_connection_factory", metadata_factory)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        auth=auth, services=SimpleNamespace(connections=connections,
                                          metadata=SimpleNamespace(ai_instance_providers=instance)))))
    try:
        result = _admin_instance_state(request, "opencode")
        mine = [row for row in result["connections"] if row["userId"] == owner]
        assert {(row["product"], row["connectionOwnerId"], row["connectionId"]) for row in mine} == {
            ("schemoo", owner, personal.id), ("schemoo", SCHEMII_CONNECTION_OWNER_ID, managed.id),
        }
        assert not any(row["connectionId"] == excluded.id for row in result["connections"])
        assert result["roleGrants"] == [{**instance.list_role_grants("opencode")[0], "active": True}]
        assert reads == {"authority": 1, "profiles": 1}
        assert "personal-secret" not in str(result) and "managed-secret" not in str(result)
        with auth.store.transaction(write=True) as state:
            state["roles"][role_id]["connections"].clear()
        reads.update(authority=0, profiles=0)
        revoked = _admin_instance_state(request, "opencode")
        assert not any(row["userId"] == owner and row["connectionId"] == managed.id for row in revoked["connections"])
        assert revoked["roleGrants"][0]["active"] is False
        assert reads == {"authority": 1, "profiles": 1}
    finally:
        with auth.store.transaction(write=True) as state:
            state["roles"].pop(role_id, None)
            state["users"].pop(owner, None)
        for profile in (personal, excluded, managed):
            repository.delete(profile.owner_id, profile.id, profile.revision)
