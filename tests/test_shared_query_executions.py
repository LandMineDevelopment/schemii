"""Product-neutral reads must share the Console's safety and result lifecycle."""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from schemii.common.metadata.models import LOCAL_PROTOTYPE_USER_ID, Principal, get_current_principal
from schemii.common.query_executions.routes import router
from schemii.schemii.console.service import ConsoleServiceError
from schemii.schemii.console.repository import PostgresConsoleRepository
from test_console import console_client


def setup_target():
    api, postgres = console_client()
    # Isolate router tests from application wiring, which is tested by integration.
    api.app.include_router(router, prefix="/shared-test")
    connection = api.post("/api/v1/connections", json={
        "name": "Semantic reads", "host": "localhost", "database": "analytics",
        "username": "reader", "password": "secret", "sslMode": "require",
    }).json()
    service = api.app.state.services.console
    args = dict(
        connection_id=connection["id"], database="analytics", namespace="public",
        console_id="con_" + "a" * 32, statements=["SELECT 1"],
    )
    return api, postgres, service, args


def test_shared_read_runs_without_workspace_and_pages_closes_results():
    api, postgres, service, args = setup_target()
    receipt = service.reserve_read_target(LOCAL_PROTOTYPE_USER_ID, **args)
    assert receipt.workspace_id is None
    assert api.app.state.services.workspaces.list(LOCAL_PROTOTYPE_USER_ID) == []
    service.run(LOCAL_PROTOTYPE_USER_ID, receipt.id)
    path = f"/shared-test/query-executions/{receipt.id}"
    response = api.get(path)
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert response.json()["workspaceId"] is None
    assert postgres.executed == [("SELECT 1",)]
    result_id = response.json()["results"][0]["id"]
    result_path = f"{path}/results/{result_id}"
    first = api.get(result_path).json()
    assert len(first["rows"]) == 100
    second = api.get(result_path, params={"cursor": first["nextCursor"]}).json()
    assert len(second["rows"]) == 5
    assert service._repository.list_history(LOCAL_PROTOTYPE_USER_ID, None, 100) == []
    assert api.delete(result_path).status_code == 204
    assert api.get(result_path).status_code == 410


def test_shared_read_enforces_owner_on_receipts_pages_and_cancellation():
    api, _, service, args = setup_target()
    receipt = service.reserve_read_target(LOCAL_PROTOTYPE_USER_ID, **args)
    service.run(LOCAL_PROTOTYPE_USER_ID, receipt.id)
    result_id = service.get_owned(LOCAL_PROTOTYPE_USER_ID, receipt.id).results[0].id
    api.app.dependency_overrides[get_current_principal] = lambda: Principal(
        user_id="other-owner", authentication_source="local_prototype"
    )
    path = f"/shared-test/query-executions/{receipt.id}"
    assert api.get(path).status_code == 404
    assert api.delete(path).status_code == 404
    assert api.get(f"{path}/results/{result_id}").status_code == 404
    assert api.delete(f"{path}/results/{result_id}").status_code == 404
    assert api.get(f"{path}/results/{result_id}/export.csv").status_code == 404


def test_shared_read_rejects_writes_and_connection_mismatch():
    _, postgres, service, args = setup_target()
    with pytest.raises(ConsoleServiceError) as error:
        service.reserve_read_target(LOCAL_PROTOTYPE_USER_ID, **{**args, "statements": ["DELETE FROM people"]})
    assert error.value.status == 422
    with pytest.raises(ConsoleServiceError) as error:
        service.reserve_read_target(LOCAL_PROTOTYPE_USER_ID, **{**args, "database": "other"})
    assert error.value.code == "console_database_changed"
    with pytest.raises(ConsoleServiceError) as error:
        service.reserve_read_target("other-owner", **args)
    assert error.value.status == 404
    assert postgres.executed == []


def test_shared_read_reservations_are_per_console_and_can_be_cancelled():
    api, _, service, args = setup_target()
    first = service.reserve_read_target(LOCAL_PROTOTYPE_USER_ID, **args)
    with pytest.raises(ConsoleServiceError) as error:
        service.reserve_read_target(LOCAL_PROTOTYPE_USER_ID, **args)
    assert error.value.code == "console_execution_active"
    second = service.reserve_read_target(LOCAL_PROTOTYPE_USER_ID, **{
        **args, "console_id": "con_" + "b" * 32,
    })
    assert second.id != first.id
    cancelled = api.delete(f"/shared-test/query-executions/{first.id}")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_postgres_receipt_round_trips_absent_workspace_and_closes_by_null_safe_ownership():
    now = datetime.now(timezone.utc)
    row = {
        "id": "cex_" + "a" * 32, "owner_id": LOCAL_PROTOTYPE_USER_ID,
        "revision": 1, "workspace_id": None, "workspace_revision": None,
        "console_id": "con_" + "a" * 32, "status": "succeeded",
        "completed_statement_indexes": [0], "error_code": None,
        "error_message": None, "error_statement_index": None,
        "created_at": now, "updated_at": now,
        "connection_id": "pgc_" + "a" * 32, "connection_revision": 1,
        "database_name": "analytics", "namespace": "public",
        "statements": ["SELECT 1"], "page_size": 100,
        "backend_pid": None, "cancel_requested": False,
    }
    cursor = MagicMock()
    cursor.fetchone.return_value = row
    repository = PostgresConsoleRepository(lambda: None)
    record = repository._owned_execution(cursor, LOCAL_PROTOTYPE_USER_ID, None, row["id"])
    assert record.execution.workspace_id is None
    assert record.workspace_revision is None
    sql, args = cursor.execute.call_args.args
    assert "workspace_id IS NOT DISTINCT FROM %s" in sql
    assert args == (LOCAL_PROTOTYPE_USER_ID, None, row["id"])
