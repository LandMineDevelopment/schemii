"""Assistant tools retain the semantic API's validation and owner boundaries."""

from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from schemii.common.api.errors import ApiProblem
from schemii.common.connections.store import ConnectionNotFoundError
from schemii.common.postgres.console.models import ConsoleExecution
from schemii.common.query_executions.errors import ConsoleServiceError
from schemii.schemoo import ai_tools, routes
from schemii.schemoo.store import InMemoryModelRepository, ModelNotFoundError


CONNECTION_ID = "pg_" + "a" * 32
EXECUTION_ID = "cex_" + "b" * 32
RESULT_ID = "res_" + "c" * 32
CONSOLE_ID = "con_" + "d" * 32
DEFINITION = {"root": "people", "nodes": [{"id": "people", "table": "people", "label": "People"}]}
EXPLORE = {"root": "people", "fields": [{"table": "people", "column": "name"}]}


@pytest.fixture
def services():
    def catalog(services, owner, connection_id, namespace, fresh=False):
        if owner != "alice" or connection_id != CONNECTION_ID:
            raise ConnectionNotFoundError("Connection not found")
        return {"connectionId": CONNECTION_ID, "namespace": namespace, "database": "warehouse",
                "fingerprint": "catalog-one", "positions": [], "relationships": [],
                "tables": [{"name": "people", "columns": [{"name": "id", "dataType": "uuid"},
                    {"name": "name", "dataType": "text"}]}]}
    return SimpleNamespace(models=InMemoryModelRepository(),
                           model_catalogs=SimpleNamespace(get=catalog),
                           connections=SimpleNamespace(get=lambda owner, connection_id: SimpleNamespace(
                               id=CONNECTION_ID, database="warehouse", owner_id=owner)
                               if owner == "alice" and connection_id == CONNECTION_ID
                               else (_ for _ in ()).throw(ConnectionNotFoundError("Connection not found")),
                               list=lambda owner: [SimpleNamespace(
                               id=CONNECTION_ID, name="Warehouse", database="warehouse", revision=1,
                               password="never share", host="private-host", username="private-user")]
                               if owner == "alice" else []),
                           workspaces=SimpleNamespace(list=lambda owner: []), console=None)


def run(services, operation, args=None, model=None, owner="alice"):
    return ai_tools.execute_action(services, owner, model, {"operation": operation, "args": args or {}})


@pytest.fixture
def model(services):
    return run(services, "create_model", {"connectionId": CONNECTION_ID, "namespace": "public",
        "name": "People", "definition": DEFINITION, "explore": EXPLORE})


def test_registry_covers_every_schemoo_product_route_and_uses_native_contracts():
    actual = {(method, route.path) for route in routes.router.routes for method in route.methods}
    covered = {(item["method"], item["apiPath"]) for item in ai_tools.ACTIONS.values()
               if item["apiPath"].startswith("/api/v1/schemoo/")}
    assert covered == actual
    tool = ai_tools.tool_definitions()[0]
    assert tool["name"] == "schemoo_actions"
    schema = tool["parameters"]
    assert schema["properties"]["actions"]["maxItems"] == 8
    assert "ModelDefinition" in schema["$defs"]
    assert {item["id"] for item in ai_tools.ACTIONS.values()} == set(ai_tools.ACTIONS)
    assert ai_tools.ACTIONS["get_result_page"]["readsRows"]
    assert not ai_tools.ACTIONS["execute_model"]["readsRows"]


def test_owned_models_catalog_and_context(services, model):
    assert run(services, "get_model", model=model["id"])["name"] == "People"
    assert run(services, "list_models")["models"][0]["id"] == model["id"]
    assert run(services, "catalog", {"connectionId": CONNECTION_ID, "namespace": "public"})["database"] == "warehouse"
    assert ai_tools.context(services, "alice", model["id"])["model"]["revision"] == 1
    sources = run(services, "list_connections")
    assert sources == {"connections": [{"id": CONNECTION_ID, "name": "Warehouse", "database": "warehouse", "revision": 1}]}
    assert run(services, "list_connections", owner="bob") == {"connections": []}
    assert run(services, "list_models", owner="bob") == {"models": []}
    with pytest.raises(ApiProblem) as error:
        run(services, "get_model", model=model["id"], owner="bob")
    assert error.value.status_code == 404
    with pytest.raises(ApiProblem) as error:
        run(services, "catalog", {"connectionId": CONNECTION_ID, "namespace": "public"}, owner="bob")
    assert error.value.status_code == 404
    with pytest.raises(ModelNotFoundError):
        ai_tools.context(services, "bob", model["id"])


def test_duplicate_tool_uses_native_saved_snapshot_and_compact_receipt(services, model):
    services.connections.get = lambda owner, connection_id: SimpleNamespace(database="warehouse")
    run(services, "create_preview", {"name": "Current", "explore": EXPLORE}, model["id"])
    body = {"name": "People copy", "expectedRevision": 1,
        "expectedLayoutRevision": 1, "expectedExploreRevision": 1}
    copied = run(services, "duplicate_model", body, model["id"])
    assert copied["status"] == "succeeded" and copied["id"] != model["id"]
    assert "definition" not in copied and copied["revision"] == 1
    assert len(run(services, "list_previews", model=copied["id"])["previews"]) == 1
    with pytest.raises(ApiProblem) as forbidden:
        run(services, "duplicate_model", body, model["id"], owner="bob")
    assert forbidden.value.status_code == 404


def test_writes_keep_revision_guards_and_independent_documents(services, model):
    updated = run(services, "update_model", {"name": "Renamed", "definition": DEFINITION,
                  "expectedRevision": 1}, model["id"])
    assert updated["revision"] == 2
    with pytest.raises(ApiProblem) as error:
        run(services, "update_model", {"name": "Stale", "definition": DEFINITION,
            "expectedRevision": 1}, model["id"])
    assert error.value.code == "model_revision_conflict"
    layout = run(services, "update_layout", {"expectedRevision": 1,
        "layout": {"positions": [{"id": "people", "x": 10, "y": 20}]}}, model["id"])
    assert layout["revision"] == 2 and layout["layoutRevision"] == 2
    explore = run(services, "update_explore", {"expectedRevision": 1, "explore": EXPLORE}, model["id"])
    assert explore["exploreRevision"] == 2
    assert run(services, "delete_model", {"expectedRevision": 2}, model["id"]) == {"status": "succeeded"}
    assert run(services, "list_models") == {"models": []}


def test_saved_preview_tools_preserve_model_and_working_exploration(services, model):
    assert run(services, "list_previews", model=model["id"]) == {"previews": []}
    saved = run(services, "create_preview", {"name": "  Staffing test  ", "explore": EXPLORE}, model["id"])
    assert saved == {"status": "succeeded", "previewId": saved["previewId"],
                     "modelId": model["id"], "name": "Staffing test", "revision": 1}
    assert len(json.dumps(saved).encode()) < 300
    assert not {"explore", "definition", "rows", "layout"} & saved.keys()
    listed = run(services, "list_previews", model=model["id"])["previews"]
    assert listed[0]["id"] == saved["previewId"]
    assert listed[0]["explore"]["fields"] == [{**EXPLORE["fields"][0], "aggregate": "none"}]
    assert "ownerId" not in listed[0]
    before = services.models.get("alice", model["id"])
    updated = run(services, "update_preview", {"previewId": saved["previewId"], "expectedRevision": 1,
        "name": "Names only", "explore": EXPLORE}, model["id"])
    assert updated["revision"] == 2 and updated["name"] == "Names only"
    assert "explore" not in updated
    after = services.models.get("alice", model["id"])
    assert after == before
    with pytest.raises(ApiProblem) as stale:
        run(services, "delete_preview", {"previewId": saved["previewId"], "expectedRevision": 1}, model["id"])
    assert stale.value.status_code == 409
    assert run(services, "delete_preview", {"previewId": saved["previewId"], "expectedRevision": 2}, model["id"]) == {"status": "succeeded"}
    assert run(services, "list_previews", model=model["id"]) == {"previews": []}


def test_saved_preview_tools_have_independent_permissions_and_owner_boundaries(services, model):
    saved = run(services, "create_preview", {"name": "Private", "explore": EXPLORE}, model["id"])
    actions = {"list_previews": {}, "create_preview": {"name": "Other", "explore": EXPLORE},
        "update_preview": {"previewId": saved["previewId"], "name": "Other", "explore": EXPLORE, "expectedRevision": 1},
        "delete_preview": {"previewId": saved["previewId"], "expectedRevision": 1}}
    for operation, args in actions.items():
        assert ai_tools.permission_id({"operation": operation, "args": args}) == operation
        assert ai_tools.ACTIONS[operation]["modes"] == ["disabled", "ask", "automatic"]
        assert ai_tools.ACTIONS[operation]["mutates"] == (operation != "list_previews")
        assert not ai_tools.ACTIONS[operation]["readsRows"]
        with pytest.raises(ApiProblem) as forbidden:
            run(services, operation, args, model["id"], owner="bob")
        assert forbidden.value.status_code == 404
    assert ai_tools.ACTIONS["delete_preview"]["destructive"]
    # Ordinary model context must not repeat every saved test configuration.
    context = ai_tools.context(services, "alice", model["id"])
    assert "previews" not in context and "previews" not in context["model"]
    assert saved["previewId"] not in json.dumps(context)


@pytest.mark.parametrize("extra", [{"rows": [["private result"]]}, {"sql": "SELECT 1"}, {"ownerId": "bob"}])
def test_saved_preview_payloads_reject_results_sql_and_ownership(extra):
    for operation, args in [("create_preview", {"name": "Test", "explore": EXPLORE}),
                            ("update_preview", {"name": "Test", "explore": EXPLORE,
                                "previewId": "preview_" + "a" * 32, "expectedRevision": 1})]:
        with pytest.raises(ValidationError):
            ai_tools.validate_action({"operation": operation, "args": {**args, **extra}})


def test_model_write_receipts_are_compact_and_targeted_edit_has_own_permission(services, model):
    assert model["status"] == "succeeded"
    assert not {"definition", "layout", "explore", "sourceContract"} & model.keys()
    saved = run(services, "patch_model", {"expectedRevision": 1, "nodes": {"upsert": [
        {"id": "manager", "table": "people", "label": "Manager"}]}}, model["id"])
    assert saved["revision"] == 2 and saved["status"] == "succeeded"
    assert len(json.dumps(saved).encode()) < 400
    assert ai_tools.permission_id({"operation": "patch_model", "args": {"expectedRevision": 2, "name": "Updated"}}) == "patch_model"
    assert ai_tools.ACTIONS["patch_model"]["mutates"]
    read = run(services, "get_model", model=model["id"])
    assert [node["id"] for node in read["definition"]["nodes"]] == ["people", "manager"]
    assert "sourceContract" not in read["definition"]
    assert "sourceContract" not in ai_tools.context(services, "alice", model["id"])["model"]["definition"]
    assert services.models.get("alice", model["id"]).definition.sourceContract is not None


def test_full_assistant_replacement_cannot_save_unbound_conditions(services, model):
    definition = {**DEFINITION, "scopes": [{"id": "date", "kind": "conditional", "alternatives": [
        {"id": "default", "conditions": [{"domain": {"nodeId": "people", "column": "name"}, "operator": "not_null"}]}]}]}
    with pytest.raises(ApiProblem) as error:
        run(services, "update_model", {"expectedRevision": 1, "name": "Broken", "definition": definition}, model["id"])
    assert error.value.code == "invalid_model_definition"
    assert error.value.details["conditionIndex"] == 0
    unchanged = run(services, "get_model", model=model["id"])
    assert unchanged["revision"] == 1 and unchanged["name"] == "People"


def test_explicit_model_selection_and_no_implicit_cross_owner_fallback(services, model):
    selected = run(services, "get_model", {"modelId": model["id"]}, "model_" + "f" * 32)
    assert selected["id"] == model["id"]
    with pytest.raises(ApiProblem) as error:
        run(services, "get_model")
    assert error.value.code == "ai_model_required"


@pytest.mark.parametrize("extra", [{"sql": "DROP TABLE people"}, {"owner": "bob"}, {"definition": DEFINITION}])
def test_plan_rejects_sql_owner_injection_and_replacement_rules(services, model, extra):
    with pytest.raises(ValidationError):
        run(services, "plan_model", {"expectedRevision": 1, "explore": EXPLORE, **extra}, model["id"])
    plan = run(services, "plan_model", {"expectedRevision": 1, "explore": EXPLORE}, model["id"])
    assert '"People.name"' in plan["sql"]


@pytest.mark.parametrize("operation", ["validate_model", "plan_model"])
@pytest.mark.parametrize("explore", [None, {"root": "accounts"},
    {"root": "accounts", "fields": []}])
def test_ai_query_actions_require_explicit_output_fields(operation, explore):
    args = {"expectedRevision": 1}
    if explore is not None:
        args["explore"] = explore
    with pytest.raises(ValidationError):
        ai_tools.validate_action({"operation": operation, "args": args})
    assert ai_tools.AiQueryExplore.model_json_schema()["properties"]["fields"]["minItems"] == 1


@pytest.mark.parametrize("operation", ["validate_model", "plan_model"])
def test_ai_query_actions_keep_requested_root_fields_aggregates_and_limit(operation):
    explore = {"root": "accounts", "fields": [
        {"table": "accounts", "column": "name"},
        {"table": "accounts", "column": "region"},
        {"table": "orders", "column": "id", "aggregate": "count"},
        {"table": "orders", "column": "total", "aggregate": "sum"}], "limit": 10}
    normalized = ai_tools.validate_action({"operation": operation, "args": {
        "expectedRevision": 1, "explore": explore}})
    actual = normalized["args"]["explore"]
    assert actual["root"] == "accounts" and actual["limit"] == 10
    assert actual["fields"] == [{**field, "aggregate": field.get("aggregate", "none")}
                                for field in explore["fields"]]


def test_unknown_operations_and_batch_limits_rejected():
    with pytest.raises(ValidationError):
        ai_tools.validate_action({"operation": "execute_sql", "args": {"sql": "SELECT 1"}})
    with pytest.raises(ValidationError):
        ai_tools.ActionBatch.model_validate({"actions": [{"operation": "list_models", "args": {}}] * 9})
    assert ai_tools.permission_id({"operation": "delete_model", "args": {"expectedRevision": 1}}) == "delete_model"


def test_execution_runs_reserved_job_and_returns_actual_receipt_without_rows(services, model):
    now = datetime.now(timezone.utc)
    receipt = ConsoleExecution(id=EXECUTION_ID, revision=1, console_id=CONSOLE_ID, status="reserved",
        completed_statement_indexes=[], results=[], created_at=now, updated_at=now)
    jobs = []
    def reserve(owner, **kwargs):
        assert owner == "alice" and kwargs["connection_id"] == CONNECTION_ID
        assert kwargs["database"] == "warehouse"
        return receipt
    def execute(owner, execution_id, **kwargs):
        jobs.append((owner, execution_id))
    def owned(owner, execution_id):
        assert jobs == [(owner, execution_id)]
        return receipt.model_copy(update={"status": "succeeded", "revision": 2})
    services.console = SimpleNamespace(reserve_read_target=reserve, run=execute, get_owned=owned)
    result = run(services, "execute_model", {"expectedRevision": 1, "explore": EXPLORE,
                 "consoleId": CONSOLE_ID}, model["id"])
    assert result["execution"]["status"] == "succeeded"
    assert "rows" not in result


def test_result_owner_check_precedes_page_or_cancel(services):
    calls = []
    def owned(owner, execution_id):
        calls.append((owner, execution_id))
        raise ConsoleServiceError(404, "console_execution_not_found", "Execution not found")
    services.console = SimpleNamespace(is_shared_report_execution=lambda key:False, get_owned=owned,
        page=lambda *args: pytest.fail("must not page another owner's results"),
        cancel=lambda *args: pytest.fail("must not cancel another owner's execution"))
    for operation, args in [("get_result_page", {"executionId": EXECUTION_ID, "resultId": RESULT_ID}),
                             ("cancel_execution", {"executionId": EXECUTION_ID}),
                             ("export_result", {"executionId": EXECUTION_ID, "resultId": RESULT_ID})]:
        with pytest.raises(ApiProblem) as error:
            run(services, operation, args, owner="bob")
        assert error.value.status_code == 404
    assert calls == [("bob", EXECUTION_ID)] * 3


def test_result_sampling_enforces_rows_bytes_and_discloses_skipped_page_rows(services):
    services.admin_config = SimpleNamespace(ai=SimpleNamespace(result_context_rows=2, result_context_bytes=800))
    receipt = SimpleNamespace(workspace_id=None)
    page = {"rows": [["first"], ["a" * 2000], ["third"]], "columns": [{"name": "label"}], "nextCursor": "next"}
    services.console = SimpleNamespace(is_shared_report_execution=lambda key:False, get_owned=lambda owner, execution_id: receipt,
        page=lambda *args: page)
    result = run(services, "get_result_page", {"executionId": EXECUTION_ID, "resultId": RESULT_ID})
    assert result["rows"] == [["first"]]
    assert result["sampling"]["pageRows"] == 3
    assert result["sampling"]["returnedRows"] == 1 and result["sampling"]["truncated"]
    assert result["nextCursor"] == "next"
    assert len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()) <= 800


def test_execution_can_allocate_console_identity_on_server():
    normalized = ai_tools.validate_action({"operation": "execute_model", "args": {
        "expectedRevision": 1, "explore": EXPLORE}})
    assert normalized["args"]["consoleId"].startswith("con_")
    assert len(normalized["args"]["consoleId"]) == 36
def test_provider_schema_uses_portable_discriminated_unions():
    import json
    from schemii.schemoo.ai_tools import tool_definitions
    schema = json.dumps(tool_definitions())
    assert '"oneOf"' not in schema
    assert '"discriminator"' not in schema
    assert '"anyOf"' in schema


def test_new_diagnostics_default_disabled_and_analysis_cannot_be_smuggled():
    from schemii.schemoo.conversations import Conversations
    service = object.__new__(Conversations)
    service.adapter = ai_tools
    modes = service.modes({"execute_model": "automatic"})
    for action in ("explain_model", "analyze_model", "get_activity"):
        assert modes[action] == "disabled"
        assert service.modes({action: "automatic"})[action] == "automatic"
    assert modes["execute_model"] == "automatic"
    with pytest.raises(ValidationError):
        ai_tools._ARGUMENTS["explain_model"].model_validate({
            "expectedRevision": 1, "explore": EXPLORE, "analyze": True,
        })


def test_download_handoffs_are_owned_disabled_and_do_not_disclose_rows(services, model):
    result = run(services, "export_model", model=model["id"])
    assert result == {"effect": "browser_download", "url": f'/api/v1/schemoo/models/{model["id"]}',
                      "filename": "semantic-model.json"}
    with pytest.raises(ApiProblem):
        run(services, "export_model", model=model["id"], owner="bob")
    calls = []
    def owned(owner, execution_id):
        calls.append((owner, execution_id))
        return SimpleNamespace(results=[SimpleNamespace(id=RESULT_ID, rows=[["private"]])])
    services.console = SimpleNamespace(get_owned=owned)
    result = run(services, "export_result", {"executionId": EXECUTION_ID, "resultId": RESULT_ID})
    assert calls == [("alice", EXECUTION_ID)]
    assert result["url"].endswith(f"/{RESULT_ID}/export.csv")
    assert "private" not in str(result)
    with pytest.raises(ApiProblem):
        run(services, "export_result", {"executionId": EXECUTION_ID, "resultId": "res_" + "e" * 32})
    for name in ("export_model", "export_result"):
        assert ai_tools.ACTIONS[name]["defaultMode"] == "disabled"


def test_logical_connections_use_existing_model_write_permissions(services, model):
    action = {"operation": "patch_model", "args": {"expectedRevision": 1,
        "nodes": {"upsert": [{"id": "manager", "table": "people", "label": "Manager"}]},
        "edges": {"upsert": [{"id": "logical_manager", "kind": "logical", "source": "people",
            "target": "manager", "sourceColumn": "id", "targetColumn": "id"}]}}}
    assert ai_tools.permission_id(action) == "patch_model"
    saved = run(services, "patch_model", action["args"], model["id"])
    assert saved["revision"] == 2
    edge = run(services, "get_model", model=model["id"])["definition"]["edges"][0]
    assert edge["kind"] == "logical" and edge["sourceColumn"] == "id"
    with pytest.raises(ApiProblem) as forbidden:
        run(services, "patch_model", {**action["args"], "expectedRevision": 2}, model["id"], owner="bob")
    assert forbidden.value.status_code == 404


def test_ai_logical_connection_rejects_types_and_preserves_starting_table(services, model):
    args = {"expectedRevision": 1,
        "nodes": {"upsert": [{"id": "manager", "table": "people", "label": "Manager"}]},
        "edges": {"upsert": [{"id": "drawn", "kind": "logical", "source": "manager",
            "target": "people", "sourceColumn": "id", "targetColumn": "name"}]}}
    with pytest.raises(ApiProblem) as rejected:
        run(services, "patch_model", args, model["id"])
    assert rejected.value.status_code == 422
    assert "comparable types" in str(rejected.value)
    assert services.models.get("alice", model["id"]).revision == 1
    args["edges"]["upsert"][0]["targetColumn"] = "id"
    run(services, "patch_model", args, model["id"])
    saved = run(services, "get_model", model=model["id"])
    assert saved["definition"]["root"] == "people"
    planned = run(services, "plan_model", {"expectedRevision": 2, "explore": {
        "fields": [{"table": "people", "column": "name"}, {"table": "manager", "column": "name"}]}}, model["id"])
    assert 'FROM "public"."people" AS t0' in planned["sql"]
    assert 't0."name" AS "People.name"' in planned["sql"]


def test_model_dependencies_use_native_owner_scoped_contract_and_block_assistant_delete(services, model):
    from schemii.schemer.dashboard_models import DashboardCreate
    from schemii.schemer.dashboard_store import InMemoryDashboardRepository

    dashboards = InMemoryDashboardRepository(models=services.models)
    assert run(services, "model_dependencies", model=model["id"]) == {"dashboards": []}
    saved = dashboards.create("alice", DashboardCreate(name="People overview",
        model_id=model["id"], model_revision=1))
    expected = {"dashboards": [{"id": saved.id, "name": saved.name}]}
    assert run(services, "model_dependencies", model=model["id"]) == expected
    assert run(services, "model_dependencies", {"modelId": model["id"]}) == expected
    descriptor = ai_tools.ACTIONS["model_dependencies"]
    assert not descriptor["mutates"] and not descriptor["readsRows"] and not descriptor["destructive"]
    assert ai_tools.permission_id({"operation": "model_dependencies", "args": {}}) == "model_dependencies"
    with pytest.raises(ApiProblem) as unauthorized:
        run(services, "model_dependencies", model=model["id"], owner="bob")
    assert unauthorized.value.status_code == 404
    with pytest.raises(ApiProblem) as blocked:
        run(services, "delete_model", {"expectedRevision": 1}, model=model["id"])
    assert blocked.value.status_code == 409
    assert blocked.value.code == "model_in_use"
    assert blocked.value.details == expected
