"""Structured assistant tools preserve shared validation and ownership boundaries."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from schemii.schemii.ai import actions
from schemii.schemii.catalog.service import RelationBrowserError
from schemii.schemii.migrations.errors import MigrationServiceError


REF = "rel_" + "a" * 32
PLAN = "mpl_" + "b" * 32
DIGEST = "c" * 64


@pytest.fixture
def browser(monkeypatch):
    service = Mock()
    service.detail.return_value = SimpleNamespace(relation=SimpleNamespace(
        namespace='odd"schema', name="projects",
        columns=[SimpleNamespace(name=name) for name in ["id", "title", "active"]],
    ))
    monkeypatch.setattr(actions, "relation_browser", lambda services: service)
    return service


def test_structured_read_quotes_identifiers_and_values(browser):
    query = actions.relation_read_query(None, "owner", "workspace", actions.RelationRead(
        relation_ref=REF, columns=["title"],
        filters=[{"column": "title", "operator": "eq", "value": "x'; DROP TABLE projects; --"}],
        order_by=[{"column": "id", "direction": "desc"}], limit=7,
    ))
    assert 'FROM "odd""schema"."projects"' in query["sql"]
    assert "'x''; DROP TABLE projects; --'" in query["sql"]
    assert 'ORDER BY "id" DESC LIMIT 7' in query["sql"]
    browser.detail.assert_called_once_with("owner", "workspace", REF)


def test_structured_count_and_null_in_filters(browser):
    query = actions.relation_read_query(None, "o", "w", actions.RelationRead(
        relation_ref=REF, count_only=True,
        filters=[{"column": "id", "operator": "in", "value": [1, 2]}, {"column": "title", "operator": "is_not_null"}],
    ))
    assert 'COUNT(*) AS "row_count"' in query["sql"]
    assert '"id" IN (1, 2) AND "title" IS NOT NULL' in query["sql"]


@pytest.mark.parametrize("options", [
    {"columns": ["missing"]}, {"columns": ["id", "id"]},
    {"filters": [{"column": "missing", "value": 1}]},
    {"order_by": [{"column": "missing"}]},
])
def test_read_rejects_unknown_or_duplicate_columns(browser, options):
    with pytest.raises(RelationBrowserError):
        actions.relation_read_query(None, "o", "w", actions.RelationRead(relation_ref=REF, **options))


@pytest.mark.parametrize("value", [
    {"operator": "eq", "value": None}, {"operator": "in", "value": []},
    {"operator": "in", "value": "not a list"}, {"operator": "is_null", "value": 1},
])
def test_filter_operand_contract(value):
    with pytest.raises(ValidationError):
        actions.RelationFilter(column="id", **value)


def test_discovery_uses_owner_bound_browser(browser):
    actions.list_relations(None, "owner", "workspace", actions.RelationListAction(search="projects"))
    browser.list.assert_called_once_with("owner", "workspace", search="projects", cursor=None, page_size=100)


@pytest.mark.parametrize("direction", ["undo", "redo"])
def test_history_uses_shared_service_revision_guard(direction):
    services = SimpleNamespace(migrations=Mock())
    actions.execute_history(services, "owner", "workspace", 12, actions.DesignHistoryAction(direction=direction))
    method = getattr(services.migrations, f"{direction}_design")
    args = method.call_args.args
    assert args[:2] == ("owner", "workspace")
    assert args[2].expected_design_revision == 12


def test_reset_cannot_skip_review_contract():
    with pytest.raises(ValidationError):
        actions.BaselineResetAction(expected_design_revision=12)
    services = SimpleNamespace(migrations=Mock())
    request = actions.BaselineResetAction(expected_design_revision=12, baseline_id="baseline", baseline_revision=3, review_digest=DIGEST)
    actions.execute_baseline_reset(services, "owner", "workspace", request)
    services.migrations.reset_design_to_baseline.assert_called_once_with("owner", "workspace", request)


def test_plan_reuses_server_options_and_validators():
    services = SimpleNamespace(migrations=Mock())
    options = actions.MigrationPlanAction(allow_destructive=False, rebuild_table_ids=[])
    actions.create_migration_plan(services, "owner", "workspace", 2, 12, options)
    request = services.migrations.create_plan.call_args.args[2]
    assert request.expected_workspace_revision == 2
    assert request.expected_design_revision == 12
    assert request.rebuild_table_ids == []
    assert request.allow_destructive is False
    with pytest.raises(ValidationError):
        actions.MigrationPlanAction(expected_design_revision=999)


def test_migration_does_not_infer_risk_confirmation_or_claim_completion():
    services = SimpleNamespace(migrations=Mock())
    services.migrations.get_plan.return_value = SimpleNamespace(workspace_id="workspace", destructive=True)
    services.migrations.create_execution.return_value = SimpleNamespace(status="queued")
    result = actions.execute_migration(services, "owner", "workspace", actions.MigrationApplyAction(plan_id=PLAN, review_digest=DIGEST))
    assert result.status == "queued"
    owner, plan, request = services.migrations.create_execution.call_args.args
    assert (owner, plan, request.review_digest) == ("owner", PLAN, DIGEST)
    assert request.confirm_destructive is False
    assert request.confirm_external_changes is False


def test_migration_workspace_guard_prevents_reservation():
    services = SimpleNamespace(migrations=Mock())
    services.migrations.get_plan.return_value = SimpleNamespace(workspace_id="other")
    with pytest.raises(MigrationServiceError, match="different workspace"):
        actions.execute_migration(services, "owner", "workspace", actions.MigrationApplyAction(plan_id=PLAN, review_digest=DIGEST))
    services.migrations.create_execution.assert_not_called()


def test_status_never_leaks_other_workspace():
    services = SimpleNamespace(migrations=Mock())
    services.migrations.get_execution.return_value = SimpleNamespace(workspace_id="other")
    with pytest.raises(MigrationServiceError, match="different workspace"):
        actions.migration_status(services, "owner", "workspace", actions.MigrationStatusAction(execution_id="mex_" + "d" * 32))


def test_all_advertised_tool_schema_references_resolve_from_parameter_root():
    from schemii.schemii.ai.models import AiCapabilities
    from schemii.schemii.ai.tools import tool_definitions

    capabilities = AiCapabilities(**{name: True for name in AiCapabilities.model_fields if name != "action_modes"})
    tools = tool_definitions(capabilities)
    assert "schemii_browse_rows" in {tool["name"] for tool in tools}

    def inspect(value, root, tool_name):
        if isinstance(value, dict):
            reference = value.get("$ref")
            if reference:
                assert reference.startswith("#/"), (tool_name, reference)
                target = root
                for part in reference[2:].split("/"):
                    key = part.replace("~1", "/").replace("~0", "~")
                    assert key in target, (tool_name, reference, key)
                    target = target[key]
                assert isinstance(target, dict), (tool_name, reference)
            for child in value.values():
                inspect(child, root, tool_name)
        elif isinstance(value, list):
            for child in value:
                inspect(child, root, tool_name)

    for tool in tools:
        inspect(tool["parameters"], tool["parameters"], tool["name"])
