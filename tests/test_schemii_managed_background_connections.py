"""Long-lived Schemii services must resolve managed targets through live grants."""

import json
from datetime import datetime, timezone

import pytest

from schemii.common.ai.pi import PiReply
from schemii.common.auth.service import AuthService
from schemii.common.connections.models import PostgresConnectionCreate, SCHEMII_CONNECTION_OWNER_ID
from schemii.common.connections.service import ConnectionService
from schemii.common.connections.store import ConnectionNotFoundError, InMemoryConnectionRepository
from schemii.common.metadata.factory import MetadataRepositories
from schemii.common.postgres.models import PostgresCatalog, PostgresColumn, PostgresTable
from schemii.main import ApplicationServices, create_app
from schemii.schemii.ai.actions import relation_browser
from schemii.schemii.ai.models import AiCapabilities
from schemii.schemii.ai.repository import InMemoryAiRepository
from schemii.schemii.designs.store import InMemoryDesignRepository
from schemii.schemii.migrations.repository import InMemoryMigrationRepository
from schemii.schemii.migrations.service import MigrationService
from schemii.schemii.workspaces.models import WorkspaceCreateRecord
from schemii.schemii.workspaces.store import (
    InMemoryWorkspaceRepository,
    WorkspaceMutationBlockedError,
)


def _user(identifier):
    return dict(id=identifier, username=identifier, display_name=identifier,
                password_hash="unused", is_admin=False, disabled=False)


class CatalogGateway:
    def __init__(self):
        self.resolved_owners = []
        self.catalog = PostgresCatalog.model_construct(
            database="reports", namespace="qa", server_version="16",
            server_version_num=160000, server_timezone="UTC",
            types=(), relationships=(), functions=(), views=(), materialized_views=(),
            tables=(PostgresTable(namespace="qa", name="items", kind="table",
                                  is_partition=False, columns=(PostgresColumn(
                                      name="id", ordinal=1, data_type="bigint", nullable=False),)),),
            captured_at=datetime.now(timezone.utc), fingerprint="a" * 64,
        )

    def introspect(self, connection, namespace):
        assert namespace == "qa"
        self.resolved_owners.append(connection.owner_id)
        return self.catalog


def _application(*, postgres=None, username="qa_reader"):
    repository = InMemoryConnectionRepository()
    designs = InMemoryDesignRepository()
    workspaces = InMemoryWorkspaceRepository(designs=designs)
    connections = ConnectionService(repository, (workspaces,))
    ai_repository = InMemoryAiRepository()
    postgres = postgres or CatalogGateway()
    # The app must bind even prebuilt services to their owning product scope.
    migrations = MigrationService(
        repository=InMemoryMigrationRepository(designs),
        connection_access=connections.for_product("schemoo"),
        postgres=postgres,
        workspaces=workspaces,
        designs=designs,
    )
    app = create_app(ApplicationServices(
        metadata=MetadataRepositories(connections=repository), connections=connections,
        postgres=postgres, workspaces=workspaces, designs=designs,
        migrations=migrations, ai_repository=ai_repository,
    ))
    auth = AuthService(enabled=True, setup_token="unused")
    app.state.auth = auth
    connections.set_authority(auth)
    profile = connections.create_schemii_owned(PostgresConnectionCreate(
        name="QA reports", host="localhost", database="reports", username=username,
        password="test-only-password"))
    with auth.store.transaction(write=True) as state:
        state["users"]["reader"] = _user("reader")
        state["users"]["stranger"] = _user("stranger")
        state["roles"]["readers"] = dict(
            id="readers", name="Readers", capabilities=["schemii:access"],
            user_ids=["reader"], dashboards=[], connections=[dict(
                owner_id=SCHEMII_CONNECTION_OWNER_ID,
                connection_id=profile.id, allow_authoring=True,
            )],
        )
    workspace = workspaces.create("reader", WorkspaceCreateRecord(
        name="QA reports", connection_id=profile.id,
        connection_owner_id=SCHEMII_CONNECTION_OWNER_ID,
        database="reports", namespace="qa",
    ))
    return app, auth, profile, workspace, postgres


def test_application_registers_bulk_work_guard_for_workspace_lifecycle():
    app, _, _, workspace, _ = _application()
    workspaces = app.state.services.workspaces
    job_repository = app.state.bulk_jobs.repository
    job_repository.list = lambda owner, workspace_id: [
        {"status": "queued"}
    ] if workspace_id == workspace.id else []

    with pytest.raises(WorkspaceMutationBlockedError):
        workspaces.delete("reader", workspace.id, workspace.revision)


def test_migration_worker_uses_managed_grant_and_rechecks_revocation():
    app, auth, profile, _, _ = _application()
    raw = app.state.services.connections
    coordinator = app.state.services.migrations.execution_coordinator
    scoped = coordinator.connection_access

    with pytest.raises(ConnectionNotFoundError):
        with raw.use("reader", profile.id):
            pass
    with scoped.use("reader", profile.id) as selected:
        assert selected.owner_id == SCHEMII_CONNECTION_OWNER_ID
    with pytest.raises(ConnectionNotFoundError):
        with scoped.use("stranger", profile.id):
            pass

    with auth.store.transaction(write=True) as state:
        state["roles"]["readers"]["connections"] = []
    with pytest.raises(ConnectionNotFoundError):
        with scoped.use("reader", profile.id):
            pass


def test_ai_live_context_refresh_and_relation_tools_use_same_revocable_grant():
    app, auth, profile, workspace, postgres = _application()
    service = app.state.ai_service
    repository = service.repository
    chat = repository.create_chat("reader", workspace.id, "Read reports", "provider", "model",
                                  AiCapabilities(live_catalog=True, structured_data_read=True))
    turn, _ = repository.create_turn("reader", chat.id, "Describe items", None, 4, 2, 100)

    class Runtime:
        def status(self, owner):
            return {"healthy": True, "providers": [{"id": "provider", "available": True,
                    "models": [{"id": "model", "status": "active"}]}]}

        def run(self, owner, turn_id, provider, model, system, prompt, tools, **kwargs):
            context = json.loads(system.split("\nCONTEXT ", 1)[1])
            assert context["liveCatalog"]["tables"][0]["name"] == "items"
            return PiReply("One items table.", ())

    service.runtime = Runtime()
    service.run_turn("reader", chat.id, turn.id)
    assert repository.get_turn("reader", chat.id, turn.id).status == "succeeded"
    assert postgres.resolved_owners == [SCHEMII_CONNECTION_OWNER_ID]
    assert service._zen_scope("reader", workspace.id) == (
        "schemii", SCHEMII_CONNECTION_OWNER_ID, profile.id)

    refreshed = service._refresh_tool_context("reader", chat,
        'Instructions\nCONTEXT {"workspace": {}, "design": {}}')
    assert json.loads(refreshed.split("\nCONTEXT ", 1)[1])["liveCatalog"]["tables"][0]["name"] == "items"
    listed = relation_browser(
        service.services, connection_access=service.connection_access
    ).list(
        "reader", workspace.id, cursor=None, page_size=20, search=None)
    assert [relation.name for relation in listed.relations] == ["items"]
    assert postgres.resolved_owners == [SCHEMII_CONNECTION_OWNER_ID] * 3

    with auth.store.transaction(write=True) as state:
        state["roles"]["readers"]["connections"] = []
    with pytest.raises(ConnectionNotFoundError):
        service._refresh_tool_context("reader", chat,
            'Instructions\nCONTEXT {"workspace": {}, "design": {}}')
    with pytest.raises(ConnectionNotFoundError):
        relation_browser(
            service.services, connection_access=service.connection_access
        ).list(
            "reader", workspace.id, cursor=None, page_size=20, search=None)
    with pytest.raises(ConnectionNotFoundError):
        service._zen_scope("reader", workspace.id)


def test_managed_writer_console_and_ai_use_exact_profile_and_do_not_fall_back():
    from tests.test_console import FakeConsoleTransaction
    from schemii.common.postgres.console.models import (
        ConsoleExecutionCreate, ConsoleTransactionCommand, ConsoleTransactionCreate,
        ConsoleTransactionExecutionCreate,
    )
    from schemii.schemii.ai.write_actions import execute_write
    from schemii.schemii.console.service import ConsoleServiceError

    class WriterGateway(CatalogGateway):
        def __init__(self):
            super().__init__()
            self.opened = []
            self.transactions = []

        def open_console_transaction(self, connection, namespace):
            assert namespace == "qa"
            self.opened.append((connection.owner_id, connection.username))
            transaction = FakeConsoleTransaction()
            self.transactions.append(transaction)
            return transaction

    gateway = WriterGateway()
    app, auth, profile, workspace, _ = _application(
        postgres=gateway, username="scoped_writer")
    services = app.state.services
    # A stronger personal identity exists, but is never selected for this workspace.
    personal = services.connections.create("reader", PostgresConnectionCreate(
        name="Private owner", host="localhost", database="reports",
        username="database_owner", password="private-owner-password"))
    console = services.console
    transaction = console.create_transaction("reader", workspace.id, ConsoleTransactionCreate(
        console_id="con_" + "a" * 32, expected_workspace_revision=workspace.revision,
        expected_settings_revision=console.settings("reader").revision))
    execution = console.reserve_transaction_execution("reader", workspace.id, transaction.id,
        ConsoleTransactionExecutionCreate(expected_revision=transaction.revision,
            statements=["UPDATE qa.items SET id=2 WHERE id=1"]))
    console.run_transaction("reader", execution.id)
    assert console.get("reader", workspace.id, execution.id).status == "succeeded"
    current = console.get_transaction("reader", workspace.id, transaction.id)
    assert console.commit_transaction("reader", workspace.id, transaction.id,
        ConsoleTransactionCommand(expected_revision=current.revision)).status == "committed"
    outcome = execute_write(services, "reader", workspace.id, workspace.revision,
        {"statements": ["UPDATE qa.items SET id=3 WHERE id=2"]})
    assert outcome["commitOutcome"] == "committed"
    assert gateway.opened == [(SCHEMII_CONNECTION_OWNER_ID, "scoped_writer")] * 2
    assert all(transaction.committed for transaction in gateway.transactions)
    # A writer profile does not weaken the safe-read execution contract.
    with pytest.raises(ConsoleServiceError):
        console.reserve("reader", workspace.id, ConsoleExecutionCreate(
            console_id="con_" + "c" * 32, expected_workspace_revision=workspace.revision,
            expected_settings_revision=console.settings("reader").revision,
            mode="managed_read", statements=["UPDATE qa.items SET id=4"]))
    assert gateway.opened == [(SCHEMII_CONNECTION_OWNER_ID, "scoped_writer")] * 2

    with auth.store.transaction(write=True) as state:
        state["roles"]["readers"]["connections"] = []
    for action in (
        lambda: console.create_transaction("reader", workspace.id, ConsoleTransactionCreate(
            console_id="con_" + "b" * 32, expected_workspace_revision=workspace.revision,
            expected_settings_revision=console.settings("reader").revision)),
        lambda: execute_write(services, "reader", workspace.id, workspace.revision,
            {"statements": ["UPDATE qa.items SET id=4"]}),
    ):
        with pytest.raises(ConsoleServiceError) as revoked:
            action()
        assert revoked.value.code == "console_connection_missing"
    assert gateway.opened == [(SCHEMII_CONNECTION_OWNER_ID, "scoped_writer")] * 2
    assert services.connections.get("reader", personal.id).username == "database_owner"
    assert profile.owner_id == SCHEMII_CONNECTION_OWNER_ID
