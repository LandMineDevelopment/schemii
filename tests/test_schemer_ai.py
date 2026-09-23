"""Schemer chat authority follows the selected dashboard, including shared views."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from schemii.common.admin_config import AiPolicy
from schemii.common.ai.conversation_store import ConversationStore
from schemii.common.ai.conversations import Conversations
from schemii.common.api.errors import ApiProblem
from schemii.common.auth.middleware import permits_api
from schemii.common.connections.models import SCHEMII_CONNECTION_OWNER_ID
from schemii.schemer import ai_tools
from schemii.schemer.dashboard_models import DashboardCreate
from schemii.schemer.dashboard_store import InMemoryDashboardRepository
from schemii.schemoo.models import ModelCreate
from schemii.schemoo.store import InMemoryModelRepository


@pytest.fixture
def report():
    source_id, shared_id = "pg_" + "a" * 32, "pg_" + "b" * 32
    profiles = {
        source_id: NS(id=source_id, owner_id="author", host="postgres", port=5432,
                      database="sales", revision=1, ownership="user"),
        shared_id: NS(id=shared_id, owner_id=SCHEMII_CONNECTION_OWNER_ID, host="postgres", port=5432,
                      database="sales", revision=1, ownership="schemii"),
    }
    class Connections:
        def for_product(self, product):
            assert product == "schemer"
            return self
        def get(self, owner, key):
            assert owner == ("author" if key == source_id else SCHEMII_CONNECTION_OWNER_ID)
            return profiles[key]
        @contextmanager
        def use(self, owner, key):
            yield self.get(owner, key)
    models = InMemoryModelRepository()
    dashboards = InMemoryDashboardRepository(models)
    model = models.create("author", ModelCreate(name="Sales", connection_id=source_id,
        database="sales", namespace="public", definition={"root":"sales", "nodes":[
            {"id":"sales", "table":"sales", "label":"Sales"}], "scopes":[
            {"id":"region", "label":"Region", "kind":"conditional", "requirement":"optional",
             "alternatives":[{"id":"region_one", "label":"One region",
                 "inputs":[{"id":"region_value", "label":"Choose region", "type":"text"}],
                 "conditions":[{"table":"sales", "column":"region", "operator":"eq",
                                "parameterId":"region_value"}]}]},
            {"id":"private", "label":"Private", "kind":"conditional", "requirement":"optional",
             "alternatives":[{"id":"private_one", "label":"Private option",
                 "inputs":[{"id":"secret_input", "label":"Secret", "type":"text"}],
                 "conditions":[{"table":"sales", "column":"secret", "operator":"eq",
                                "parameterId":"secret_input"}]}]}]}))
    dashboard = dashboards.create("author", DashboardCreate(name="Sales",model_id=model.id,
        model_revision=1, optional_filters=["region"]))
    grant = {"role_id":"east", "owner_id":"author", "dashboard_id":dashboard.id,
        "connection_id":shared_id, "connection_owner_id":SCHEMII_CONNECTION_OWNER_ID,
        "can_export":False, "can_drill":False}
    db_grant = {"role_id":"east", "owner_id":SCHEMII_CONNECTION_OWNER_ID,
                "connection_id":shared_id}
    state = {"grant":True, "viewer_author":False, "active":True}
    def capabilities(actor):
        if actor == "author" or state["viewer_author"]:
            return ["schemer:access", "schemer:author"]
        return ["schemer:access"] if actor == "viewer" else []
    auth = NS(enabled=True, capabilities=capabilities, resolve=lambda token:
              {"id":"viewer", "disabled":False} if state["active"] else None,
              user=lambda actor:{"disabled":False}, audit=lambda *args:None,
              dashboard_grants=lambda actor:[deepcopy(grant)] if actor == "viewer" and state["grant"] else [],
              connection_grants=lambda actor:[deepcopy(db_grant)] if actor == "viewer" and state["grant"] else [])
    calls=[]
    class Console:
        def reserve_read_target(self, actor, **kwargs):
            calls.append(("reserve", actor, kwargs))
            return NS(id="cex_"+"c"*32)
        def run(self, actor, execution_id, **kwargs):
            calls.append(("run", actor, kwargs))
        def get_owned(self, actor, execution_id):
            return NS(status="succeeded", results=[NS(id="res_"+"d"*32)], updated_at=datetime.now(timezone.utc))
        def page(self, actor, unused, execution_id, result_id, cursor):
            calls.append(("page", actor, {}))
            return NS(model_dump=lambda **kwargs:{"rows":[[7, "East"]],
                "columns":[{"name":"count"},{"name":"region"}],
                "nextCursor":None, "truncated":False})
        def close_result(self, actor, unused, execution_id, result_id):
            calls.append(("close", actor, {}))
        def cancel(self, actor, unused, execution_id):
            calls.append(("cancel", actor, {}))
    services = NS(models=models, dashboards=dashboards, connections=Connections(),
                  console=Console(), model_catalogs=None,
                  admin_config=NS(ai=AiPolicy(),console=NS(maximum_statements_per_run=20)))
    request = NS(app=NS(state=NS(services=services, auth=auth)),
                 cookies={"schemii_session":"session"})
    return NS(request=request, services=services, state=state, dashboard=dashboard,
              model=model, calls=calls)


def _chat(report, owner="viewer"):
    store = ConversationStore(None, AiPolicy(), "schemer", "dashboardId")
    service = Conversations(store, None, report.services, ai_tools)
    chat = service.create(owner, {"dashboardId": report.dashboard.id,
        "providerId":"provider", "aiModelId":"model"}, report.request)
    return service, chat


def test_viewer_chat_is_dashboard_scoped_and_revocation_hides_history(report):
    service, chat = _chat(report)
    snapshot = service.snapshot("viewer", chat["id"], report.request)
    assert snapshot["permissions"] == {"edit":False, "export":False, "drill":False}
    assert "update_dashboard" not in snapshot["availableActions"]
    assert [item["id"] for item in service.list("viewer", None, report.request)] == [chat["id"]]
    report.state["viewer_author"] = True  # Global author role does not grant this dashboard.
    assert "update_dashboard" not in service.snapshot("viewer", chat["id"], report.request)["availableActions"]
    report.state["grant"] = False
    assert service.list("viewer", None, report.request) == []
    with pytest.raises(ApiProblem):
        service.snapshot("viewer", chat["id"], report.request)
    with pytest.raises(ApiProblem):
        service.list("viewer", report.dashboard.id, report.request)


def test_viewer_get_dashboard_lists_only_exposed_filter_inputs(report):
    scope = ai_tools.authorize(report.services,"viewer",report.dashboard.id,report.request)
    summary = ai_tools.execute_action(scope,"viewer",report.dashboard.id,
        {"operation":"get_dashboard","args":{}})
    assert summary["filterScopes"] == [{"id":"region", "label":"Region",
        "alternatives":[{"id":"region_one", "label":"One region",
            "inputs":[{"id":"region_value", "label":"Choose region", "type":"text"}]}]}]
    assert "modelFields" not in summary
    assert "secret_input" not in str(summary)


def test_viewer_cannot_edit_selected_dashboard_even_with_automatic_mode(report):
    scope = ai_tools.authorize(report.services, "viewer", report.dashboard.id, report.request)
    body = {**report.dashboard.model_dump(mode="json", by_alias=True,
        exclude={"id","owner_id","revision","created_at","updated_at"}),
        "expectedRevision":report.dashboard.revision, "name":"Changed"}
    action = ai_tools.validate_action({"operation":"update_dashboard","args":body})
    with pytest.raises(ApiProblem) as error:
        ai_tools.execute_action(scope,"viewer",report.dashboard.id,action)
    assert error.value.status_code == 403
    assert report.services.dashboards.get("author",report.dashboard.id).name == "Sales"


def test_author_can_edit_owned_dashboard_with_current_revision(report):
    scope = ai_tools.authorize(report.services, "author", report.dashboard.id, report.request)
    assert scope.permissions["edit"]
    assert "update_dashboard" in ai_tools.available_actions(report.services,"author",report.dashboard.id,report.request)
    body = {**report.dashboard.model_dump(mode="json", by_alias=True,
        exclude={"id","owner_id","revision","created_at","updated_at"}),
        "expectedRevision":report.dashboard.revision, "name":"Updated"}
    receipt = ai_tools.execute_action(scope,"author",report.dashboard.id,
        {"operation":"update_dashboard","args":body})
    assert receipt == {"status":"succeeded","dashboardId":report.dashboard.id,
                       "dashboardRevision":2,"name":"Updated"}
    assert report.services.dashboards.get("author",report.dashboard.id).name == "Updated"


def test_viewer_executes_bounded_tile_under_own_identity(report, monkeypatch):
    scope = ai_tools.authorize(report.services,"viewer",report.dashboard.id,report.request)
    monkeypatch.setattr(ai_tools,"tile_plan",lambda services, owner, dashboard, tile_id, **kwargs:
        (NS(connection_id="pg_"+"b"*32,database="sales",namespace="public"),{"sql":"SELECT 7"}))
    result = ai_tools.execute_action(scope,"viewer",report.dashboard.id,
        {"operation":"execute_tile","args":{"tileId":"tile-1"}})
    assert result["sample"]["rows"] == [[7,"East"]]
    assert result["sample"]["sampling"]["returnedRows"] == 1
    assert [actor for _, actor, _ in report.calls] == ["viewer"]*4
    assert report.calls[0][2]["connection_access"] is scope.services.report_access or report.calls[0][2]["connection_access"].actor == "viewer"
    report.state["grant"] = False
    with pytest.raises(ApiProblem):
        ai_tools.execute_action(scope,"viewer",report.dashboard.id,
            {"operation":"execute_tile","args":{"tileId":"tile-1"}})


def test_viewer_value_lookup_requires_exposed_optional_filter(report):
    scope = ai_tools.authorize(report.services,"viewer",report.dashboard.id,report.request)
    with pytest.raises(ApiProblem) as error:
        ai_tools.execute_action(scope,"viewer",report.dashboard.id,
            {"operation":"parameter_values","args":{"scopeId":"private",
                "alternativeId":"one","parameterId":"value"}})
    assert error.value.status_code == 403
    assert ai_tools.ACTIONS["drill_tile"]["requiresDrill"] is True
    assert "drill_tile" not in ai_tools.available_actions(report.services,"viewer",report.dashboard.id,report.request)


def test_viewer_value_lookup_uses_granted_model_and_exposed_input(report, monkeypatch):
    captured = {}
    monkeypatch.setattr(ai_tools,"model_catalog",lambda services, owner, model, **kwargs:
        captured.setdefault("source", (owner, model.connection_id, model.connection_owner_id)) or {})
    def plan(catalog, model, body):
        captured["request"] = body
        return {"sql":"SELECT region"}
    monkeypatch.setattr(ai_tools,"domain_query",plan)
    monkeypatch.setattr(ai_tools,"_sample_plan",lambda bundle, model, plan:
        {"sample":{"rows":[["East"]]}})
    scope = ai_tools.authorize(report.services,"viewer",report.dashboard.id,report.request)
    result = ai_tools.execute_action(scope,"viewer",report.dashboard.id,
        {"operation":"parameter_values","args":{"scopeId":"region",
            "alternativeId":"region_one","parameterId":"region_value"}})
    assert result["sample"]["rows"] == [["East"]]
    assert captured["source"][0] == "viewer"
    assert captured["source"][2] == SCHEMII_CONNECTION_OWNER_ID
    assert captured["request"].scope_id == "region"


def test_viewer_http_access_is_narrow():
    rights = ["schemer:access"]
    assert permits_api("/api/v1/schemer/ai/chats/chat_123/messages","POST",rights)
    assert permits_api("/api/v1/ai/status","GET",rights)
    assert permits_api("/api/v1/ai/credentials/openai","POST",rights)
    assert not permits_api("/api/v1/connections","GET",rights)
    assert not permits_api("/api/v1/schemoo/ai/chats","GET",rights)
    assert not permits_api("/api/v1/schemer/dashboards/dashboard_123","PUT",rights)


def test_temporary_filter_and_drill_values_are_removed_from_receipts():
    action = {"operation":"drill_tile", "args":{"tileId":"chart-1",
        "selections":{"region":{"active":True,"values":{"name":"Private East"}}},
        "selection":{"dimensions":[{"table":"sales","column":"name","value":"Private East"}],
                     "measureIndex":0}}}
    assert ai_tools.receipt_action(action) == {"operation":"drill_tile","args":{"tileId":"chart-1"}}


def test_revocation_during_provider_turn_releases_capacity_without_saving_reply(report):
    service, chat = _chat(report)
    class Runtime:
        def require_available_model(self, *args):
            return None
        def run(self, *args, **kwargs):
            report.state["grant"] = False
            return NS(text="Private answer", tool_calls=[])
    service.runtime = Runtime()
    turn = service.send("viewer", chat["id"], {"text":"Summarize the report",
        "expectedRevision":chat["revision"]}, report.request)
    service.run("viewer", chat["id"], turn["turnId"])
    persisted = service.store.get("viewer", chat["id"])
    assert persisted["status"] == "failed"
    assert "Private answer" not in str(persisted)
    assert service.active == {}


def test_revocation_before_background_turn_starts_releases_capacity(report):
    service, chat = _chat(report)
    class Runtime:
        def require_available_model(self, *args):
            return None
    service.runtime = Runtime()
    turn = service.send("viewer", chat["id"], {"text":"Summarize the report",
        "expectedRevision":chat["revision"]}, report.request)
    report.state["grant"] = False
    service.run("viewer", chat["id"], turn["turnId"])
    assert service.store.get("viewer",chat["id"])["status"] == "failed"
    assert service.active == {}
