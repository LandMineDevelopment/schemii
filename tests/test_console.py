from datetime import datetime, timezone
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
from schemii.schemii.workspaces.store import InMemoryWorkspaceRepository


class ConsolePostgresGateway:
    def __init__(self) -> None:
        self.executed: list[tuple[str, ...]] = []

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

    def execute_console(self, connection, statements, *, on_started):
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


class BlockingConsolePostgresGateway(ConsolePostgresGateway):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.cancelled = threading.Event()

    def execute_console(self, connection, statements, *, on_started):
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
