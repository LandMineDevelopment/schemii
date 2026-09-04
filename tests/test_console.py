from datetime import datetime, timedelta, timezone
import threading
import time

from fastapi.testclient import TestClient

from schemii.common.connections.service import ConnectionService
from schemii.common.connections.store import InMemoryConnectionRepository
from schemii.common.metadata.factory import MetadataRepositories
from schemii.common.postgres.console.execution import (
    ConsoleQueryResult,
    ConsoleStatementValidationError,
    validate_read_only_statements,
    validate_transaction_statements,
)
from schemii.common.postgres.console.models import (
    ConsoleExecutionCreate,
    ConsoleResultColumn,
)
from schemii.common.postgres.errors import PostgresConsoleCancelledError
from schemii.common.postgres.models import (
    PostgresConnectionTestResult,
    build_postgres_catalog,
)
from schemii.main import ApplicationServices, create_app
from schemii.schemii.designs.store import InMemoryDesignRepository
from schemii.schemii.console.repository import InMemoryConsoleRepository
from schemii.schemii.workspaces.store import InMemoryWorkspaceRepository


class ConsolePostgresGateway:
    def __init__(self) -> None:
        self.executed: list[tuple[str, ...]] = []
        self.transactions: list["FakeConsoleTransaction"] = []

    def test_connection(self, connection):
        return PostgresConnectionTestResult(
            database=connection.database,
            server_version="17.2",
        )

    def namespace_exists(self, connection, namespace):
        return namespace == "public"

    def introspect(self, connection, namespace):
        return build_postgres_catalog(
            database=connection.database,
            namespace=namespace,
            server_version="17.2",
            server_version_num=170002,
            server_timezone="UTC",
            tables=(),
            relationships=(),
            functions=(),
            views=(),
            materialized_views=(),
            captured_at=datetime.now(timezone.utc),
        )

    def execute_console(self, connection, namespace, statements, *, on_started):
        assert namespace == "public"
        assert on_started(4321)
        self.executed.append(tuple(statements))
        return tuple(
            ConsoleQueryResult(
                statement_index=index,
                command="SELECT",
                columns=(ConsoleResultColumn(name="value", data_type="integer"),),
                rows=tuple((row,) for row in range(105)) if index == 0 else ((2,),),
                truncated=False,
            )
            for index, _statement in enumerate(statements)
        )

    def cancel_console(self, connection, backend_pid):
        return True

    def open_console_transaction(self, connection, namespace):
        assert namespace == "public"
        transaction = FakeConsoleTransaction()
        self.transactions.append(transaction)
        return transaction


class FakeConsoleTransaction:
    def __init__(self) -> None:
        self.backend_pid = 9876
        self.executed: list[tuple[str, ...]] = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, statements):
        self.executed.append(tuple(statements))
        return tuple(
            ConsoleQueryResult(
                statement_index=index,
                command="UPDATE" if statement.upper().startswith("UPDATE") else "SELECT",
                columns=(),
                rows=(),
                truncated=False,
            )
            for index, statement in enumerate(statements)
        )

    def commit(self):
        self.committed = True
        self.closed = True

    def rollback(self):
        self.rolled_back = True
        self.closed = True

    def close(self):
        self.closed = True


class BlockingConsolePostgresGateway(ConsolePostgresGateway):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.cancelled = threading.Event()

    def execute_console(self, connection, namespace, statements, *, on_started):
        assert namespace == "public"
        assert on_started(4321)
        self.started.set()
        if not self.cancelled.wait(timeout=2):
            raise AssertionError("cancellation did not reach the active PostgreSQL target")
        raise PostgresConsoleCancelledError()

    def cancel_console(self, connection, backend_pid):
        assert backend_pid == 4321
        self.cancelled.set()
        return True


def console_client(
    postgres: ConsolePostgresGateway | None = None,
) -> tuple[TestClient, ConsolePostgresGateway]:
    connection_repository = InMemoryConnectionRepository()
    designs = InMemoryDesignRepository()
    workspaces = InMemoryWorkspaceRepository(designs=designs)
    postgres = postgres or ConsolePostgresGateway()
    services = ApplicationServices(
        metadata=MetadataRepositories(connections=connection_repository),
        connections=ConnectionService(connection_repository, (workspaces,)),
        postgres=postgres,
        workspaces=workspaces,
        designs=designs,
    )
    return TestClient(create_app(services), base_url="http://localhost"), postgres


def target_workspace(api: TestClient) -> dict:
    connection = api.post(
        "/api/v1/connections",
        json={
            "name": "Console target",
            "host": "localhost",
            "database": "analytics",
            "username": "reader",
            "password": "secret",
            "sslMode": "require",
        },
    ).json()
    response = api.post(
        "/api/v1/schemii/workspaces/postgres",
        json={
            "connectionId": connection["id"],
            "namespace": "public",
        },
    )
    assert response.status_code == 201
    return response.json()["workspace"]


def execution_body(workspace: dict, sql: str) -> dict:
    return {
        "consoleId": "con_" + "1" * 32,
        "expectedWorkspaceRevision": workspace["revision"],
        "expectedSettingsRevision": 1,
        "mode": "managed_read",
        "statements": [sql],
    }


def test_console_runs_multi_statement_script_and_pages_retained_rows() -> None:
    api, postgres = console_client()
    workspace = target_workspace(api)

    created = api.post(
        f"/api/v1/schemii/workspaces/{workspace['id']}/console/executions",
        json=execution_body(workspace, "SELECT 1; SELECT 2;"),
    )
    assert created.status_code == 201
    execution_id = created.json()["id"]

    receipt = api.get(
        f"/api/v1/schemii/workspaces/{workspace['id']}/console/executions/{execution_id}"
    ).json()
    assert receipt["status"] == "succeeded"
    assert receipt["completedStatementIndexes"] == [0, 1]
    assert [result["rowCount"] for result in receipt["results"]] == [105, 1]
    assert postgres.executed == [("SELECT 1", "SELECT 2")]

    first_result = receipt["results"][0]
    first_page = api.get(
        f"/api/v1/schemii/workspaces/{workspace['id']}/console/executions/{execution_id}"
        f"/results/{first_result['id']}"
    ).json()
    assert len(first_page["rows"]) == 100
    assert first_page["columns"] == [{"name": "value", "dataType": "integer"}]
    assert first_page["nextCursor"].startswith("crc_")

    second_page = api.get(
        f"/api/v1/schemii/workspaces/{workspace['id']}/console/executions/{execution_id}"
        f"/results/{first_result['id']}",
        params={"cursor": first_page["nextCursor"]},
    ).json()
    assert second_page["rows"] == [[100], [101], [102], [103], [104]]
    assert second_page["nextCursor"] is None

    reused = api.get(
        f"/api/v1/schemii/workspaces/{workspace['id']}/console/executions/{execution_id}"
        f"/results/{first_result['id']}",
        params={"cursor": first_page["nextCursor"]},
    )
    assert reused.status_code == 410
    assert reused.json()["error"]["code"] == "console_result_gone"


def test_console_streams_complete_csv_without_a_metadata_result_copy() -> None:
    api, _postgres = console_client()
    workspace = target_workspace(api)
    created = api.post(
        f"/api/v1/schemii/workspaces/{workspace['id']}/console/executions",
        json=execution_body(workspace, "SELECT 1"),
    ).json()
    receipt = api.get(
        f"/api/v1/schemii/workspaces/{workspace['id']}/console/executions/{created['id']}"
    ).json()
    result_id = receipt["results"][0]["id"]

    exported = api.get(
        f"/api/v1/schemii/workspaces/{workspace['id']}/console/executions/"
        f"{created['id']}/results/{result_id}/export.csv"
    )

    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/csv")
    lines = exported.text.splitlines()
    assert lines[0] == "value"
    assert lines[1:] == [str(value) for value in range(105)]


def test_console_rejects_mutation_before_postgresql_execution() -> None:
    api, postgres = console_client()
    workspace = target_workspace(api)

    response = api.post(
        f"/api/v1/schemii/workspaces/{workspace['id']}/console/executions",
        json=execution_body(workspace, "SELECT 1; DELETE FROM customers"),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "console_statement_not_read_only"
    assert response.json()["error"]["details"] == {"statementIndex": 1}
    assert postgres.executed == []


def test_read_only_validation_blocks_write_explain_and_comment_only_scripts() -> None:
    for sql, code in (
        ("EXPLAIN INSERT INTO events VALUES (1)", "console_statement_not_read_only"),
        ("-- no query", "console_statement_empty"),
        ("SELECT 1 INTO temporary_result", "console_select_into_blocked"),
    ):
        try:
            validate_read_only_statements([sql])
        except ConsoleStatementValidationError as error:
            assert error.code == code
        else:
            raise AssertionError(f"{sql!r} should have been rejected")


def test_cancellation_uses_the_active_target_without_waiting_for_its_use_lock() -> None:
    postgres = BlockingConsolePostgresGateway()
    api, _ = console_client(postgres)
    workspace = target_workspace(api)
    service = api.app.state.services.console
    request = ConsoleExecutionCreate.model_validate(
        execution_body(workspace, "SELECT pg_sleep(10)")
    )
    execution = service.reserve("user_local_prototype", workspace["id"], request)
    worker = threading.Thread(
        target=service.run,
        args=("user_local_prototype", execution.id),
    )
    worker.start()
    assert postgres.started.wait(timeout=1)

    before = time.monotonic()
    receipt = service.cancel("user_local_prototype", workspace["id"], execution.id)
    elapsed = time.monotonic() - before
    worker.join(timeout=1)

    assert elapsed < 0.5
    assert not worker.is_alive()
    assert receipt.status in {"running", "cancelled"}
    assert service.get(
        "user_local_prototype", workspace["id"], execution.id
    ).status == "cancelled"


def test_explicit_console_transaction_runs_multiple_statements_then_commits() -> None:
    api, postgres = console_client()
    workspace = target_workspace(api)
    base = f"/api/v1/schemii/workspaces/{workspace['id']}/console/transactions"

    opened = api.post(
        base,
        json={
            "consoleId": "con_" + "2" * 32,
            "expectedWorkspaceRevision": workspace["revision"],
            "expectedSettingsRevision": 1,
        },
    )
    assert opened.status_code == 201
    transaction = opened.json()
    assert transaction["status"] == "open"
    resumed = api.post(
        base,
        json={
            "consoleId": "con_" + "2" * 32,
            "expectedWorkspaceRevision": workspace["revision"],
            "expectedSettingsRevision": 1,
        },
    )
    assert resumed.status_code == 201
    assert resumed.json()["id"] == transaction["id"]
    assert len(postgres.transactions) == 1

    competing = api.post(
        base,
        json={
            "consoleId": "con_" + "3" * 32,
            "expectedWorkspaceRevision": workspace["revision"],
            "expectedSettingsRevision": 1,
        },
    )
    assert competing.status_code == 409
    assert competing.json()["error"]["code"] == "console_transaction_active"

    created = api.post(
        f"{base}/{transaction['id']}/executions",
        json={
            "expectedRevision": transaction["revision"],
            "statements": ["UPDATE books SET title = title", "SELECT 1"],
        },
    )
    assert created.status_code == 201
    execution = api.get(
        f"/api/v1/schemii/workspaces/{workspace['id']}/console/executions/"
        f"{created.json()['id']}"
    ).json()
    assert execution["status"] == "succeeded"
    assert execution["transactionId"] == transaction["id"]
    assert postgres.transactions[0].executed == [
        ("UPDATE books SET title = title", "SELECT 1")
    ]

    active = api.get(f"{base}/{transaction['id']}").json()
    assert active["revision"] == 2
    assert active["executionIds"] == [execution["id"]]
    committed = api.post(
        f"{base}/{transaction['id']}/commit",
        json={"expectedRevision": active["revision"]},
    )
    assert committed.status_code == 200
    assert committed.json()["status"] == "committed"
    assert postgres.transactions[0].committed is True


def test_transaction_parser_keeps_savepoints_but_separates_terminal_commands() -> None:
    script = validate_transaction_statements(
        ["SAVEPOINT before_edit; UPDATE books SET title = title; COMMIT"]
    )
    assert script.statements == (
        "SAVEPOINT before_edit",
        "UPDATE books SET title = title",
    )
    assert script.terminal_action == "commit"

    try:
        validate_transaction_statements(["COMMIT; SELECT 1"])
    except ConsoleStatementValidationError as error:
        assert error.code == "console_transaction_action_not_final"
    else:
        raise AssertionError("A transaction command before another statement should fail")


def test_saved_queries_are_durable_workspace_metadata_with_revision_checks() -> None:
    api, _postgres = console_client()
    workspace = target_workspace(api)
    base = f"/api/v1/schemii/workspaces/{workspace['id']}/console/saved-queries"

    created = api.post(base, json={"name": "  Author totals  ", "sql": "  SELECT 1;  "})
    assert created.status_code == 201
    query = created.json()
    assert query["name"] == "Author totals"
    assert query["sql"] == "SELECT 1;"
    assert query["revision"] == 1
    assert query["starter"] is False

    duplicate = api.post(base, json={"name": "author TOTALS", "sql": "SELECT 2;"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "console_saved_query_name_exists"

    listed = api.get(base)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["queries"]] == [query["id"]]

    updated = api.patch(
        f"{base}/{query['id']}",
        json={"expectedRevision": 1, "name": "Author counts", "sql": "SELECT 2;"},
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert updated.json()["name"] == "Author counts"

    stale_delete = api.delete(
        f"{base}/{query['id']}",
        params={"expectedRevision": 1},
    )
    assert stale_delete.status_code == 409
    assert stale_delete.json()["error"]["code"] == "console_saved_query_changed"

    deleted = api.delete(
        f"{base}/{query['id']}",
        params={"expectedRevision": 2},
    )
    assert deleted.status_code == 204
    assert api.get(base).json() == {"queries": []}


def test_console_history_retains_only_replay_sql_and_run_timestamp() -> None:
    api, _postgres = console_client()
    workspace = target_workspace(api)
    execution = api.post(
        f"/api/v1/schemii/workspaces/{workspace['id']}/console/executions",
        json=execution_body(workspace, "SELECT 42;"),
    )
    assert execution.status_code == 201

    response = api.get(
        f"/api/v1/schemii/workspaces/{workspace['id']}/console/history",
        params={"limit": 10},
    )
    assert response.status_code == 200
    history = response.json()["queries"]
    assert len(history) == 1
    assert history[0]["sql"] == "SELECT 42"
    assert set(history[0]) == {"sql", "ranAt"}


def test_console_history_limit_prunes_oldest_replay_queries_per_workspace() -> None:
    repository = InMemoryConsoleRepository()
    started = datetime(2026, 9, 3, tzinfo=timezone.utc)

    for index in range(3):
        repository.record_history(
            "user-1",
            "workspace-1",
            f"SELECT {index}",
            started + timedelta(seconds=index),
            2,
        )

    assert [
        entry.sql
        for entry in repository.list_history("user-1", "workspace-1", 10)
    ] == ["SELECT 2", "SELECT 1"]
