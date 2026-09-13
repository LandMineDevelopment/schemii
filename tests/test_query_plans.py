import pytest

from schemii.common.postgres.console.execution import (
    ConsoleQueryResult, ConsoleStatementValidationError,
)
from schemii.common.postgres.console.models import ConsoleResultColumn
from schemii.common.postgres.query_plans import build_explain_sql
from tests.test_console import ConsolePostgresGateway, console_client, target_workspace


@pytest.mark.parametrize("sql", [
    "SELECT 1; SELECT 2", "EXPLAIN SELECT 1", "SHOW work_mem", "DELETE FROM t",
    "WITH gone AS (DELETE FROM t RETURNING *) SELECT * FROM gone",
    "SELECT * INTO new_table FROM t", "SELECT * FROM t FOR UPDATE",
    "WITH x AS (SELECT * FROM t FOR SHARE) SELECT * FROM x", "-- comment only",
])
def test_explain_rejects_scripts_and_side_effecting_statements(sql):
    with pytest.raises(ConsoleStatementValidationError):
        build_explain_sql(sql, analyze=True)


def test_explain_preserves_filters_limit_and_parameters_without_executing_by_default():
    sql = build_explain_sql("/* review */ SELECT 'a;b' AS label FROM t WHERE id = 2 LIMIT 50;")
    assert "ANALYZE FALSE" in sql
    assert "FORMAT JSON" in sql
    assert "'a;b'" in sql and "id = 2" in sql and "LIMIT 50" in sql
    assert "ANALYZE TRUE, BUFFERS TRUE" in build_explain_sql("VALUES (1)", True)
    with pytest.raises(ConsoleStatementValidationError):
        build_explain_sql("SELECT '" + "x" * (256 * 1024) + "'")


class PlanGateway(ConsolePostgresGateway):
    def execute_console(self, connection, namespace, statements, *, on_started):
        assert on_started(4321)
        self.executed.append(tuple(statements))
        return (ConsoleQueryResult(
            statement_index=0, command="EXPLAIN",
            columns=(ConsoleResultColumn(name="QUERY PLAN", data_type="json"),),
            rows=(([{"Plan": {"Node Type": "Result", "Plan Rows": 1, "Total Cost": 0.01}}],),),
            truncated=False,
        ),)


def test_explain_route_uses_owned_revision_bound_execution_and_shared_result():
    api, gateway = console_client(PlanGateway())
    workspace = target_workspace(api)
    path = f"/api/v1/schemii/workspaces/{workspace['id']}/console/explain"
    body = {
        "consoleId": "con_" + "c" * 32,
        "expectedWorkspaceRevision": workspace["revision"],
        "expectedSettingsRevision": 1, "sql": "SELECT 1",
    }
    response = api.post(path, json=body)
    assert response.status_code == 201, response.text
    base = "/api/v1/common/query-executions/" + response.json()["id"]
    receipt = api.get(base).json()
    assert receipt["status"] == "succeeded"
    page = api.get(base + "/results/" + receipt["results"][0]["id"]).json()
    assert page["rows"][0][0][0]["Plan"]["Node Type"] == "Result"
    assert "ANALYZE FALSE" in gateway.executed[0][0].upper()
    assert api.post(path, json={**body, "expectedSettingsRevision": 99}).status_code == 409
    assert api.post(path, json={**body, "expectedWorkspaceRevision": 99}).status_code == 409
    assert api.post(path, json={**body, "analyze": "true"}).status_code == 422
    assert api.post(path, json={**body, "sql": "DELETE FROM t", "analyze": True}).status_code == 422
    missing = path.replace(workspace["id"], "ws_" + "f" * 32)
    assert api.post(missing, json=body).status_code == 404
    assert len(gateway.executed) == 1
