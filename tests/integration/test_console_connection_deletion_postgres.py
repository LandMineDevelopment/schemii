"""Exercise real profile foreign keys across execution and credential owners."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from schemii.common.connections.models import PostgresConnectionCreate, SCHEMII_CONNECTION_OWNER_ID
from schemii.common.connections.policy import AllowAllConnectionTargetPolicy
from schemii.common.connections.service import ConnectionService
from schemii.common.connections.store import ConnectionInUseError, ConnectionNotFoundError
from schemii.schemii.console.connection_dependencies import PostgresConsoleConnectionDependencies
from schemii.schemii.console.repository import ConsoleNotFoundError, ConsoleTarget, PostgresConsoleRepository


@pytest.mark.parametrize("managed", [False, True])
def test_delete_blocks_live_work_then_prunes_only_its_completed_receipts(postgres_metadata, managed):
    factory = postgres_metadata.connection_factory
    actor = postgres_metadata.owner_id
    credential_owner = SCHEMII_CONNECTION_OWNER_ID if managed else actor
    repositories = postgres_metadata.repositories
    provider = PostgresConsoleConnectionDependencies(factory)
    connections = ConnectionService(repositories.connections, (provider,), target_policy=AllowAllConnectionTargetPolicy())
    console = PostgresConsoleRepository(factory)
    now = datetime.now(timezone.utc)
    profiles = []
    try:
        # Provision the viewer even when both profiles belong to the shared pool.
        with factory() as connection:
            connection.execute("INSERT INTO metadata.users(id,display_name) VALUES(%s,%s) ON CONFLICT DO NOTHING", (actor, "Deletion test viewer"))
        for name in ("Used reporting profile", "Unrelated profile"):
            profiles.append(connections.create(credential_owner, PostgresConnectionCreate(
                name=name, host="application-postgres", database="application_data", username="reader", password="fixture-only-password",
            )))
        def reserve(profile):
            return console.reserve(actor, None, "con_" + uuid4().hex, None,
                ConsoleTarget(profile.id, profile.revision, profile.database, "public", connection_owner_id=credential_owner),
                ("SELECT 1",), 100, now)
        used, unrelated = (reserve(profile) for profile in profiles)
        impact = connections.deletion_impact(credential_owner, profiles[0].id)
        assert [item.resource_id for item in impact.dependencies] == [used.execution.id]
        assert impact.dependencies[0].deletion_blocked
        with pytest.raises(ConnectionInUseError):
            connections.delete(credential_owner, profiles[0].id, profiles[0].revision)
        # Exercise the final transactional guard too, bypassing the advisory
        # service preflight just as a concurrent admission could invalidate it.
        with pytest.raises(ConnectionInUseError):
            repositories.connections.delete(credential_owner, profiles[0].id, profiles[0].revision)
        assert console.get(actor, used.execution.id).execution.status == "reserved"
        with factory() as connection:
            connection.execute("UPDATE schemii.console_executions SET status='succeeded' WHERE id=%s", (used.execution.id,))
        assert not connections.deletion_impact(credential_owner, profiles[0].id).dependencies
        # Admission may hold a receipt lock before acquiring its profile FK
        # lock. Deletion must report a conflict immediately, never wait on it.
        with factory() as locked:
            locked.execute("SELECT id FROM schemii.console_executions WHERE id=%s FOR UPDATE", (used.execution.id,))
            with pytest.raises(ConnectionInUseError):
                repositories.connections.delete(credential_owner, profiles[0].id, profiles[0].revision)
        connections.delete(credential_owner, profiles[0].id, profiles[0].revision)
        with pytest.raises(ConnectionNotFoundError):
            connections.get(credential_owner, profiles[0].id)
        with pytest.raises(ConsoleNotFoundError):
            console.get(actor, used.execution.id)
        assert console.get(actor, unrelated.execution.id).execution.status == "reserved"
        assert connections.get(credential_owner, profiles[1].id).id == profiles[1].id
    finally:
        # The managed pool outlives the fixture owner; remove only these profiles.
        with factory() as connection:
            for profile in profiles:
                connection.execute("DELETE FROM schemii.console_executions WHERE connection_owner_id=%s AND connection_id=%s", (credential_owner, profile.id))
                connection.execute("DELETE FROM metadata.postgres_connections WHERE owner_id=%s AND id=%s", (credential_owner, profile.id))


@pytest.mark.parametrize("status,blocked", [
    ("open", True), ("failed", True), ("uncertain", True),
    ("committed", False), ("rolled_back", False), ("expired", False),
])
def test_managed_transaction_lifecycle_is_preserved_on_profile_deletion(postgres_metadata, status, blocked):
    factory = postgres_metadata.connection_factory
    actor = postgres_metadata.owner_id
    workspace = "ws_" + uuid4().hex
    provider = PostgresConsoleConnectionDependencies(factory)
    connections = ConnectionService(postgres_metadata.repositories.connections, (provider,), target_policy=AllowAllConnectionTargetPolicy())
    console = PostgresConsoleRepository(factory)
    now = datetime.now(timezone.utc)
    profile = connections.create(SCHEMII_CONNECTION_OWNER_ID, PostgresConnectionCreate(
        name="Managed transaction fixture", host="application-postgres", database="application_data", username="reader", password="fixture-only-password",
    ))
    try:
        with factory() as connection:
            connection.execute("INSERT INTO metadata.users(id,display_name) VALUES(%s,%s) ON CONFLICT DO NOTHING", (actor, "Transaction test viewer"))
            connection.execute("INSERT INTO schemii.workspaces(id,owner_id,name) VALUES(%s,%s,%s)", (workspace, actor, "Transaction fixture"))
        record = console.create_transaction(actor, workspace, "con_" + uuid4().hex, 1,
            ConsoleTarget(profile.id, profile.revision, profile.database, "public", connection_owner_id=SCHEMII_CONNECTION_OWNER_ID),
            123, now, now + timedelta(minutes=5), now + timedelta(minutes=10))
        with factory() as connection:
            connection.execute("UPDATE schemii.console_transactions SET status=%s WHERE id=%s", (status, record.transaction.id))
        if blocked:
            with pytest.raises(ConnectionInUseError):
                # Skip service preflight to prove the locked store guard also protects it.
                postgres_metadata.repositories.connections.delete(SCHEMII_CONNECTION_OWNER_ID, profile.id, profile.revision)
            assert console.get_transaction(actor, record.transaction.id).transaction.status == status
        else:
            with factory() as locked:
                locked.execute("SELECT id FROM schemii.console_transactions WHERE id=%s FOR UPDATE", (record.transaction.id,))
                with pytest.raises(ConnectionInUseError):
                    postgres_metadata.repositories.connections.delete(SCHEMII_CONNECTION_OWNER_ID, profile.id, profile.revision)
            connections.delete(SCHEMII_CONNECTION_OWNER_ID, profile.id, profile.revision)
            with pytest.raises(ConsoleNotFoundError):
                console.get_transaction(actor, record.transaction.id)
    finally:
        with factory() as connection:
            connection.execute("DELETE FROM schemii.console_transactions WHERE connection_owner_id=%s AND connection_id=%s", (SCHEMII_CONNECTION_OWNER_ID, profile.id))
            connection.execute("DELETE FROM metadata.postgres_connections WHERE owner_id=%s AND id=%s", (SCHEMII_CONNECTION_OWNER_ID, profile.id))
            connection.execute("DELETE FROM schemii.workspaces WHERE owner_id=%s AND id=%s", (actor, workspace))
