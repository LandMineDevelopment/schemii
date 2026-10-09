"""The metadata database enforces the personal/managed credential boundary."""

from __future__ import annotations

from contextlib import ExitStack
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
from schemii.common.connections.store import InMemoryConnectionRepository
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

    def remove_peer_identity():
        with postgres_metadata.connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM metadata.users WHERE id = %s", (peer,))
            connection.commit()

    with ExitStack() as cleanup:
        personal = repository.create(owner, _request("Personal", "personal-secret"))
        cleanup.callback(repository.delete, personal.owner_id, personal.id, personal.revision)
        # Register before creation, including a failure after its user insert.
        # LIFO removes the exact peer profile before its generated user identity.
        cleanup.callback(remove_peer_identity)
        excluded = repository.create(peer, _request("Peer", "peer-secret"))
        cleanup.callback(repository.delete, excluded.owner_id, excluded.id, excluded.revision)
        managed = repository.create(SCHEMII_CONNECTION_OWNER_ID, _request("Managed", "managed-secret"))
        cleanup.callback(repository.delete, managed.owner_id, managed.id, managed.revision)
        auth = AuthService(postgres_metadata.connection_factory, enabled=True, setup_token="unused")

        def remove_authority():
            with auth.store.transaction(write=True) as state:
                state["roles"].pop(role_id, None)
                state["users"].pop(owner, None)

        cleanup.callback(remove_authority)
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


@pytest.mark.parametrize("failure", ["peer-user-insert", "managed-create", "role-grant", "managed-cleanup"])
def test_provider_inventory_fixture_unwinds_partial_setup_and_preserves_live_peers(monkeypatch, failure):
    """Run the real fixture body with injected setup/cleanup failures, without PostgreSQL."""
    from tests.integration import test_connection_ownership_postgres as module

    owner = "owned-fixture-owner"
    users = {owner, SCHEMII_CONNECTION_OWNER_ID, "live-peer"}
    deleted_profiles = []
    removed_users = []
    authorities = []

    class Repository(InMemoryConnectionRepository):
        def create(self, user, request):
            users.add(user)
            if user.startswith("peer-") and failure == "peer-user-insert":
                raise RuntimeError("controlled setup failure")
            if user == SCHEMII_CONNECTION_OWNER_ID and failure == "managed-create":
                raise RuntimeError("controlled setup failure")
            return super().create(user, request)

        def delete(self, user, identifier, revision):
            if user == SCHEMII_CONNECTION_OWNER_ID and failure == "managed-cleanup":
                raise RuntimeError("controlled cleanup failure")
            deleted_profiles.append((user, identifier))
            super().delete(user, identifier, revision)

    repository = Repository()
    live_profile = repository.create("live-peer", _request("Live peer", "peer-secret"))
    protected_profile = InMemoryConnectionRepository.create(repository, SCHEMII_CONNECTION_OWNER_ID,
                                                           _request("Live managed", "managed-secret"))

    class CleanupConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def cursor(self):
            return self

        def execute(self, sql, parameters):
            assert sql == "DELETE FROM metadata.users WHERE id = %s"
            identifier, = parameters
            assert identifier.startswith("peer-") and not repository.list(identifier)
            removed_users.append(identifier)
            users.discard(identifier)

        def commit(self):
            return None

    class Authority(AuthService):
        def __init__(self, *_args, **_kwargs):
            super().__init__(enabled=True, setup_token="unused")
            self.store.state["users"]["live-peer"] = dict(id="live-peer", username="peer", display_name="Peer",
                                                         password_hash="unused", is_admin=False, disabled=False)
            self.store.state["roles"]["live-role"] = dict(id="live-role", name="Live role", user_ids=["live-peer"],
                                                         capabilities=[], connections=[], dashboards=[])
            authorities.append(self)

    class Instance(MemoryInstanceAiProviderStore):
        def upsert_role_grant(self, *_args, **_kwargs):
            raise RuntimeError("controlled setup failure")

    monkeypatch.setattr(module, "AuthService", Authority)
    monkeypatch.setattr(module, "MemoryInstanceAiProviderStore", Instance)
    harness = SimpleNamespace(owner_id=owner, repositories=SimpleNamespace(connections=repository),
                              connection_factory=lambda: CleanupConnection())
    error = "controlled cleanup failure" if failure == "managed-cleanup" else "controlled setup failure"
    with pytest.raises(RuntimeError, match=error) as caught:
        module.test_provider_inventory_batches_real_owner_metadata_and_current_authority(harness, monkeypatch)
    assert users == {owner, SCHEMII_CONNECTION_OWNER_ID, "live-peer"}
    assert len(removed_users) == 1
    assert repository.list(owner) == []
    assert repository.get("live-peer", live_profile.id) == live_profile
    assert ("live-peer", live_profile.id) not in deleted_profiles
    if protected_profile:
        assert repository.get(SCHEMII_CONNECTION_OWNER_ID, protected_profile.id) == protected_profile
        assert (SCHEMII_CONNECTION_OWNER_ID, protected_profile.id) not in deleted_profiles
    for authority in authorities:
        assert set(authority.store.state["users"]) == {"live-peer"}
        assert set(authority.store.state["roles"]) == {"live-role"}
    if failure == "managed-cleanup":
        assert caught.value.__context__ is not None
