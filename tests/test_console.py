from datetime import datetime, timedelta, timezone
import threading
import time
from types import SimpleNamespace

import pytest

from fastapi.testclient import TestClient

from schemii.common.connections.service import ConnectionService
from schemii.common.connections.store import InMemoryConnectionRepository
from schemii.common.connections.store import ConnectionNotFoundError
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
from schemii.schemii.console.repository import ConsoleTarget, InMemoryConsoleRepository
from schemii.schemii.console.service import ConsoleService, ConsoleServiceError
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


def test_managed_result_requires_the_reviewed_credential_identity() -> None:
    repository = InMemoryConsoleRepository()
    target = ConsoleTarget("pg_" + "1" * 32, 2, "analytics", "public", "role-writer")
    receipt = repository.reserve(
        "friend", None, "con_" + "2" * 32, None, target,
        ("SELECT 1",), 100, datetime.now(timezone.utc),
    )
    service = ConsoleService(
        repository=repository,
        connections=SimpleNamespace(),
        postgres=ConsolePostgresGateway(),
        workspaces=InMemoryWorkspaceRepository(),
    )

    def access(owner_id):
        return SimpleNamespace(get=lambda actor, connection: SimpleNamespace(
            owner_id=owner_id, revision=2, database="analytics",
        ))

    service.authorize_managed_result("friend", receipt.execution.id, access("role-writer"))
    with pytest.raises(ConsoleServiceError) as changed:
        service.authorize_managed_result("friend", receipt.execution.id, access("role-reader"))
    assert changed.value.code == "console_target_changed"
    with pytest.raises(ConsoleServiceError) as revoked:
        service.authorize_managed_result(
            "friend", receipt.execution.id,
            SimpleNamespace(get=lambda actor, connection: (_ for _ in ()).throw(ConnectionNotFoundError())),
        )
    assert revoked.value.code == "console_connection_revoked"


def test_console_preferences_are_durable_bounded_and_not_write_authority() -> None:
    api, _ = console_client()
    path = "/api/v1/schemii/console/settings"
    defaults = api.get(path).json()
    assert defaults["rowPageSize"] == defaults["maximumRowPageSize"] == 100
    saved = api.put(path, json={"expectedRevision": 1, "rowPageSize": 25})
    assert saved.status_code == 200
    assert saved.json()["revision"] == 2
    assert saved.json()["writeIntent"] is False
    assert saved.json()["defaultMode"] == "managed_read"
    assert api.get(path).json() == saved.json()
    assert api.put(path, json={"expectedRevision": 1, "rowPageSize": 50}).status_code == 409
    for page_size in (0, 101, 1001):
        assert api.put(path, json={"expectedRevision": 2, "rowPageSize": page_size}).status_code == 422
    for forbidden in ({"writeIntent": True}, {"defaultMode": "autocommit"}, {"statementLimit": 1000}):
        assert api.put(path, json={"expectedRevision": 2, "rowPageSize": 50, **forbidden}).status_code == 422


def test_console_preferences_apply_to_new_results_and_reject_stale_start() -> None:
    api, _ = console_client()
    workspace = target_workspace(api)
    settings = api.put("/api/v1/schemii/console/settings", json={"expectedRevision": 1, "rowPageSize": 25}).json()
    path = f"/api/v1/schemii/workspaces/{workspace['id']}/console/executions"
    body = execution_body(workspace, "SELECT 1")
    stale = api.post(path, json=body)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "console_settings_changed"
    body["expectedSettingsRevision"] = settings["revision"]
    created = api.post(path, json=body)
    assert created.status_code == 201
    receipt = api.get(f"{path}/{created.json()['id']}").json()
    page = api.get(f"{path}/{receipt['id']}/results/{receipt['results'][0]['id']}").json()
    assert len(page["rows"]) == 25


def test_console_preference_repository_is_owner_scoped() -> None:
    from schemii.schemii.console.repository import ConsoleConflictError
    import pytest

    repository = InMemoryConsoleRepository()
    repository.update_settings("owner-a", 1, 25)
    assert repository.settings("owner-a") == (2, 25)
    assert repository.settings("owner-b") == (1, None)
    with pytest.raises(ConsoleConflictError):
        repository.update_settings("owner-a", 1, 50)


def test_console_preferences_read_postgres_dictionary_rows() -> None:
    from unittest.mock import MagicMock
    from schemii.schemii.console.repository import PostgresConsoleRepository

    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    repository = PostgresConsoleRepository(lambda: connection)
    cursor.fetchone.return_value = {"revision": 3, "row_page_size": 25}
    assert repository.settings("owner-a") == (3, 25)
    assert cursor.execute.call_args.args[1] == ("owner-a",)
    connection.commit.assert_called_once()
    connection.close.assert_called_once()
    cursor.fetchone.return_value = None
    assert repository.settings("owner-b") == (1, None)


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


def test_read_only_validation_blocks_write_analysis_and_comment_only_scripts() -> None:
    for sql, code in (
        ("EXPLAIN ANALYZE INSERT INTO events VALUES (1)", "console_statement_not_read_only"),
        ("EXPLAIN (ANALYZE TRUE) DELETE FROM events", "console_statement_not_read_only"),
        ("SET transaction_read_only = off", "console_statement_not_read_only"),
        ("COMMIT; SELECT 1", "console_statement_not_read_only"),
        ("-- no query", "console_statement_empty"),
        ("SELECT 1 INTO temporary_result", "console_select_into_blocked"),
    ):
        try:
            validate_read_only_statements([sql])
        except ConsoleStatementValidationError as error:
            assert error.code == code
        else:
            raise AssertionError(f"{sql!r} should have been rejected")



def test_read_only_validation_preserves_source_spelling_and_unicode_offsets() -> None:
    first = "/* planner hint stays */ select 'é;中' AS \"MixedCase\""
    second = "-- preserve this comment\nselect $$literal;value$$ as value"
    assert validate_read_only_statements([f"{first}; {second};"]) == (first, second)


def test_read_only_validation_allows_planning_writes_without_execution() -> None:
    for sql in (
        "EXPLAIN INSERT INTO events VALUES (1)",
        "EXPLAIN (ANALYZE FALSE) DELETE FROM events",
        "EXPLAIN (ANALYZE OFF, FORMAT JSON) UPDATE events SET value = 2",
        "EXPLAIN SELECT 1 INTO temporary_result",
    ):
        assert validate_read_only_statements([sql]) == (sql,)

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


class BlockingReadSession:
    backend_pid = 4321

    def __init__(self):
        self.started = threading.Event()
        self.cancelled = threading.Event()
        self.closed = False
        self.results = (ConsoleQueryResult(
            statement_index=0, command="SELECT",
            columns=(ConsoleResultColumn(name="value", data_type="integer"),),
            rows=(), truncated=False, row_count=None, replayable=True,
        ),)

    def page(self, *args):
        self.started.set()
        assert self.cancelled.wait(2), "fetch was not cancelled"
        raise PostgresConsoleCancelledError()

    export_page = page

    def cancel(self):
        self.cancelled.set()

    def close(self):
        self.closed = True


class RetainedReadGateway(ConsolePostgresGateway):
    def __init__(self):
        super().__init__()
        self.sessions = []

    def open_console_read_session(self, connection, namespace, statements, *, on_started, page_memory_bytes):
        assert on_started(4321)
        session = BlockingReadSession()
        self.sessions.append(session)
        return session

    def console_activity(self, target, backend_pid, **kwargs):
        return {"monitoringAvailable": True, "databaseState": "active", "waitEvent": "PgSleep"}


def test_live_fetch_does_not_hold_global_lock_and_can_be_cancelled_after_success():
    from concurrent.futures import ThreadPoolExecutor
    from schemii.common.query_executions.errors import ConsoleServiceError
    import pytest

    gateway = RetainedReadGateway()
    api, _ = console_client(gateway)
    workspace = target_workspace(api)
    service = api.app.state.services.console
    owner = "user_local_prototype"
    receipt = service.reserve(owner, workspace["id"], ConsoleExecutionCreate.model_validate(execution_body(workspace, "SELECT pg_sleep(10)")))
    service.run(owner, receipt.id)
    receipt = service.get_owned(owner, receipt.id)
    assert receipt.status == "succeeded"
    result_id = receipt.results[0].id
    with ThreadPoolExecutor(max_workers=1) as pool:
        fetching = pool.submit(service.page, owner, workspace["id"], receipt.id, result_id, None)
        assert gateway.sessions[0].started.wait(1)
        assert service._transient_results_lock.acquire(timeout=0.1)
        service._transient_results_lock.release()
        activity = api.get(f"/api/v1/common/query-executions/{receipt.id}/activity")
        assert activity.status_code == 200
        assert activity.json()["phase"] == "fetching"
        assert activity.json()["completedStatementIndexes"] == []
        assert activity.json()["cancellable"] is True
        assert activity.json()["waitEvent"] == "PgSleep"
        service.close_result(owner, workspace["id"], receipt.id, result_id)
        assert not gateway.sessions[0].closed, "close must defer until fetch releases its lease"
        service.cancel(owner, workspace["id"], receipt.id)
        with pytest.raises(ConsoleServiceError) as error:
            fetching.result(timeout=1)
        assert error.value.code == "postgres_console_cancelled"
    assert gateway.sessions[0].closed
    assert service.activity(owner, receipt.id)["phase"] == "cancelled"
    with pytest.raises(ConsoleServiceError) as error:
        service.activity("another-owner", receipt.id)
    assert error.value.status == 404


def test_streaming_export_is_visible_and_cancellable_after_receipt_success():
    from concurrent.futures import ThreadPoolExecutor
    import pytest

    gateway = RetainedReadGateway()
    api, _ = console_client(gateway)
    workspace = target_workspace(api)
    service = api.app.state.services.console
    owner = "user_local_prototype"
    receipt = service.reserve(owner, workspace["id"], ConsoleExecutionCreate.model_validate(execution_body(workspace, "SELECT 1")))
    service.run(owner, receipt.id)
    receipt = service.get_owned(owner, receipt.id)
    stream = service.export_csv(owner, workspace["id"], receipt.id, receipt.results[0].id)
    assert next(stream) == b"value\r\n"
    with ThreadPoolExecutor(max_workers=1) as pool:
        exporting = pool.submit(next, stream)
        assert gateway.sessions[0].started.wait(1)
        assert service.activity(owner, receipt.id)["phase"] == "exporting"
        service.cancel(owner, workspace["id"], receipt.id)
        with pytest.raises(PostgresConsoleCancelledError):
            exporting.result(timeout=1)
    assert gateway.sessions[0].closed


def test_lazy_read_progress_completes_only_after_last_page():
    gateway = RetainedReadGateway()
    api, _ = console_client(gateway)
    workspace = target_workspace(api)
    service = api.app.state.services.console
    owner = "user_local_prototype"
    receipt = service.reserve(owner, workspace["id"], ConsoleExecutionCreate.model_validate(execution_body(workspace, "SELECT 1")))
    service.run(owner, receipt.id)
    receipt = service.get_owned(owner, receipt.id)
    assert receipt.completed_statement_indexes == [0]
    assert service.activity(owner, receipt.id)["completedStatementIndexes"] == []
    gateway.sessions[0].page = lambda index, offset, size: tuple((row,) for row in range(offset, min(offset + size, 101)))
    first = service.page(owner, workspace["id"], receipt.id, receipt.results[0].id, None)
    assert first.next_cursor
    assert service.activity(owner, receipt.id)["completedStatementIndexes"] == []
    last = service.page(owner, workspace["id"], receipt.id, receipt.results[0].id, first.next_cursor)
    assert last.next_cursor is None
    assert service.activity(owner, receipt.id)["completedStatementIndexes"] == [0]


@pytest.mark.parametrize("fail_first_fetch", [False, True])
def test_forward_only_first_page_is_claimed_before_fetch(fail_first_fetch):
    from concurrent.futures import ThreadPoolExecutor

    gateway = RetainedReadGateway()
    api, _ = console_client(gateway)
    workspace = target_workspace(api)
    service = api.app.state.services.console
    owner = "user_local_prototype"
    receipt = service.reserve(owner, workspace["id"],
        ConsoleExecutionCreate.model_validate(execution_body(workspace, "SELECT 1")))
    service.run(owner, receipt.id)
    receipt = service.get_owned(owner, receipt.id)
    result_id = receipt.results[0].id
    started, release = threading.Event(), threading.Event()
    calls = []

    def fetch(index, offset, size):
        calls.append((index, offset, size))
        started.set()
        assert release.wait(2)
        if fail_first_fetch:
            raise RuntimeError("fetch failed after it may have advanced")
        return tuple((row,) for row in range(size))

    gateway.sessions[0].page = fetch
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(service.page, owner, workspace["id"], receipt.id, result_id, None)
        assert started.wait(1)
        with pytest.raises(ConsoleServiceError) as repeated:
            service.page(owner, workspace["id"], receipt.id, result_id, None)
        assert repeated.value.status == 410
        assert repeated.value.code == "console_result_gone"
        release.set()
        if fail_first_fetch:
            with pytest.raises(RuntimeError, match="fetch failed"):
                first.result(timeout=2)
        else:
            assert first.result(timeout=2).next_cursor
    with pytest.raises(ConsoleServiceError) as repeated:
        service.page(owner, workspace["id"], receipt.id, result_id, None)
    assert repeated.value.code == "console_result_gone"
    assert len(calls) == 1
