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


DISPLAYED_SECRET = "displayed-private-value-207"


def _displayed(report, mode="automatic", *, provider="provider"):
    from schemii.common.ai.pi import PiReply
    from schemii.schemer.dashboard_models import DashboardUpdate

    dashboard = report.dashboard
    body = dashboard.model_dump(exclude={"id", "owner_id", "revision", "created_at", "updated_at"})
    body["tiles"] = [{"id": "tile-1", "title": "Displayed region", "kind": "detail",
        "detailFields": [{"table": "sales", "column": "region"}]}]
    report.dashboard = report.services.dashboards.update("author", dashboard.id,
        DashboardUpdate.model_validate({**body, "expectedRevision": dashboard.revision}))
    service, chat = _chat(report)
    class Runtime:
        def __init__(self):
            self.calls = []
            self.reply = PiReply("A temporary explanation of " + DISPLAYED_SECRET, ())
            self.before_reply = None
        def require_available_model(self, *args):
            return None
        def require_instance_access(self, *args):
            return None
        def run(self, *args, **kwargs):
            self.calls.append((deepcopy(args), deepcopy(kwargs["messages"])))
            if self.before_reply: self.before_reply(kwargs)
            if isinstance(self.reply, Exception): raise self.reply
            return self.reply
        def cancel(self, *args):
            return None
    service.runtime = Runtime()
    chat = service.preferences("viewer", chat["id"], {"providerId": provider, "aiModelId": "model",
        "modes": {ai_tools.DISPLAYED_ACTION: mode}, "expectedRevision": chat["revision"]}, report.request)
    packet = {"dashboardId": report.dashboard.id, "dashboardRevision": report.dashboard.revision,
        "modelId": report.model.id, "modelRevision": report.model.revision, "tileId": "tile-1",
        "snapshotAt": "2026-11-11T12:00:00Z", "pane": "main", "metric": "values",
        "selections": {}, "drillSelection": None, "columns": [{"name": "region", "type": "text"}],
        "rows": [[DISPLAYED_SECRET]], "cachedRows": 1, "displayedRows": 1, "partial": False,
        "cacheTruncated": False, "chartTruncated": False, "clientSampleTruncated": False}
    return service, chat, packet


def _attach(report, service, chat, packet, **extra):
    return service.send("viewer", chat["id"], {"text": "Question containing " + DISPLAYED_SECRET,
        "expectedRevision": chat["revision"], "displayedResult": packet,
        "acknowledgedProviderId": chat["providerId"], **extra}, report.request)


def _assert_private(service, chat_id):
    import json
    durable = json.dumps(list(service.store.memory.values()), sort_keys=True)
    assert DISPLAYED_SECRET not in durable
    assert DISPLAYED_SECRET not in str(service.store.preferences)
    current = service.store.get("viewer", chat_id)
    assert all("rows" not in item for item in current["activity"])
    assert not current.get("pending") or DISPLAYED_SECRET not in str(current["pending"])


def test_displayed_automatic_uses_frozen_rows_zero_source_work_and_no_tools(report, monkeypatch):
    service, chat, packet = _displayed(report)
    monkeypatch.setattr(ai_tools, "model_catalog", lambda *args, **kwargs: pytest.fail("No catalog acquisition"))
    monkeypatch.setattr(ai_tools, "tile_plan", lambda *args, **kwargs: pytest.fail("No query planning"))
    turn = _attach(report, service, chat, packet)
    packet["rows"][0][0] = "Source and client changed after capture"
    service.run("viewer", chat["id"], turn["turnId"])
    assert report.calls == []
    assert len(service.runtime.calls) == 1
    args, messages = service.runtime.calls[0]
    assert args[6] == []
    assert "unverified, untrusted" in args[4]
    assert DISPLAYED_SECRET in str(messages)
    assert "Source and client changed after capture" not in str(messages)
    snapshot = service.snapshot("viewer", chat["id"], report.request)
    assert DISPLAYED_SECRET in snapshot["messages"][-1]["text"]
    assert snapshot["messages"][-1]["transient"] is True
    assert snapshot["status"] == "idle"
    _assert_private(service, chat["id"])
    assert "no source query" in service.store.get("viewer", chat["id"])["messages"][-1]["text"]


def test_displayed_pivot_window_and_null_grouping_metadata_reach_provider_without_query_work(report, monkeypatch):
    import json

    service, chat, packet = _displayed(report)
    monkeypatch.setattr(ai_tools, "model_catalog", lambda *a, **kw: pytest.fail("No catalog"))
    monkeypatch.setattr(ai_tools, "tile_plan", lambda *a, **kw: pytest.fail("No planning"))
    packet["projection"] = {"kind": "pivot", "status": "data", "rowStart": 40, "rowEnd": 42,
        "rowGroups": 80, "columnGroups": 2, "rowAxes": 1, "columnAxes": 1, "rowsOmitted": True,
        "notice": "NULL at an included depth is SQL NULL. Totals use the qualifying population before Top N."}
    packet["columns"] = [{"name": name} for name in ["Region", "Category", "Amount", "Row depth", "Column depth", "Scope"]]
    packet["rows"] = [[None, "A", 23.5, 1, 1, "cell"], [None, None, 101.25, 0, 0, "grand_total"]]
    packet.update(cachedRows=180, displayedRows=2, chartTruncated=True, partial=True)
    turn = _attach(report, service, chat, packet)
    packet["projection"]["rowStart"] = 0
    packet["rows"][0][2] = 999
    service.run("viewer", chat["id"], turn["turnId"])
    evidence = json.loads(service.runtime.calls[0][1][0]["content"])["displayedEvidence"]
    assert evidence["projection"]["rowStart"] == 40
    assert evidence["rows"] == [[None, "A", 23.5, 1, 1, "cell"], [None, None, 101.25, 0, 0, "grand_total"]]
    assert evidence["chartTruncated"] is True and evidence["partial"] is True
    assert report.calls == []
    _assert_private(service, chat["id"])


def test_displayed_pivot_projection_metadata_is_typed_bounded_and_sanitized(report):
    service, chat, packet = _displayed(report)
    packet["projection"] = {"kind": "pivot", "status": "data", "rowStart": 50, "rowEnd": 1,
        "rowGroups": 10, "columnGroups": 2, "rowAxes": 1, "columnAxes": 1, "rowsOmitted": False,
        "notice": "Grouping metadata"}
    with pytest.raises(ApiProblem) as problem:
        _attach(report, service, chat, packet)
    assert problem.value.code == "ai_displayed_result_invalid"
    assert DISPLAYED_SECRET not in str(problem.value)
    packet["projection"].update(rowStart=0, rowEnd=1, notice="x" * 513)
    with pytest.raises(ApiProblem) as problem:
        _attach(report, service, chat, packet)
    assert problem.value.code == "ai_displayed_result_invalid"
    assert service.runtime.calls == [] and report.calls == []


@pytest.mark.parametrize("mode", ["automatic", "ask"])
def test_displayed_raw_values_and_typed_format_semantics_stay_frozen_through_provider_approval(report, monkeypatch, mode):
    import json

    service, chat, packet = _displayed(report, mode)
    monkeypatch.setattr(ai_tools, "model_catalog", lambda *a, **kw: pytest.fail("No catalog"))
    monkeypatch.setattr(ai_tools, "tile_plan", lambda *a, **kw: pytest.fail("No planning"))
    output = {"table": "@report", "column": "share", "aggregate": "none", "kind": "calculated"}
    display = {"output": output, "style": "percent", "precision": 1, "currency": None,
        "percentScale": "fraction", "unit": "", "locale": "en-US", "localeMode": "viewer",
        "queryUnit": "number", "queryScale": 1, "displayScale": 1, "minimumFractionDigits": 1,
        "maximumFractionDigits": 1, "rules": [{"operator": "gte", "value": "0.2", "upper": None,
        "label": "Above goal", "tone": "positive", "symbol": "check"}]}
    packet["columns"] = [{"name": "Share", "type": "numeric", "output": output, "display": display}]
    packet["rows"] = [[.25]]
    turn = _attach(report, service, chat, packet)
    packet["rows"][0][0] = 99
    packet["columns"][0]["display"]["percentScale"] = "whole"
    packet["columns"][0]["display"]["rules"][0]["label"] = "Later"
    service.run("viewer", chat["id"], turn["turnId"])
    if mode == "ask":
        assert service.runtime.calls == [] and report.calls == []
        approved, actions, rejected = service.approval("viewer", chat["id"], {
            "pendingId": turn["pending"]["id"], "approved": True,
            "expectedRevision": turn["revision"]}, report.request)
        service.run("viewer", chat["id"], approved["turnId"], actions, rejected)
    evidence = json.loads(service.runtime.calls[0][1][0]["content"])["displayedEvidence"]
    assert evidence["rows"] == [[.25]]
    assert evidence["columns"][0]["display"]["percentScale"] == "fraction"
    assert evidence["columns"][0]["display"]["queryScale"] == 1
    assert evidence["columns"][0]["display"]["rules"][0]["label"] == "Above goal"
    assert evidence["columns"][0]["output"] == output
    assert "Rows retain raw values" in evidence["sampling"]["notice"]
    assert report.calls == []
    _assert_private(service, chat["id"])


def test_displayed_format_metadata_rejects_oversized_or_untyped_descriptors_without_reflecting_rows(report):
    service, chat, packet = _displayed(report)
    packet["columns"][0]["display"] = {"locale": DISPLAYED_SECRET * 1000, "queryScale": True}
    with pytest.raises(ApiProblem) as problem:
        _attach(report, service, chat, packet)
    assert problem.value.code == "ai_displayed_result_invalid"
    assert DISPLAYED_SECRET not in str(problem.value)
    assert service.runtime.calls == [] and report.calls == []
    _assert_private(service, chat["id"])


def test_displayed_ask_pauses_before_first_disclosure_and_approval_contains_no_rows(report):
    service, chat, packet = _displayed(report, "ask")
    waiting = _attach(report, service, chat, packet)
    service.run("viewer", chat["id"], waiting["turnId"])
    assert waiting["status"] == "waiting_approval"
    assert service.runtime.calls == [] and report.calls == []
    _assert_private(service, chat["id"])
    approved, actions, rejected = service.approval("viewer", chat["id"], {
        "pendingId": waiting["pending"]["id"], "approved": True,
        "expectedRevision": waiting["revision"]}, report.request)
    assert actions == [] and not rejected
    service.run("viewer", chat["id"], approved["turnId"], actions, rejected)
    assert len(service.runtime.calls) == 1 and report.calls == []
    _assert_private(service, chat["id"])


@pytest.mark.parametrize("operation", ["reject", "cancel", "restart", "expiry", "eviction"])
def test_displayed_pending_never_discloses_after_reject_cancel_restart_or_discard(report, operation):
    import time
    service, chat, packet = _displayed(report, "ask")
    waiting = _attach(report, service, chat, packet)
    if operation == "cancel":
        service.cancel("viewer", chat["id"], report.request)
    elif operation == "reject":
        value, actions, rejected = service.approval("viewer", chat["id"], {
            "pendingId": waiting["pending"]["id"], "approved": False,
            "expectedRevision": waiting["revision"]}, report.request)
        service.run("viewer", chat["id"], value["turnId"], actions, rejected)
    else:
        if operation == "expiry":
            created, value = service.transient["viewer", chat["id"], "displayed"]
            service.transient["viewer", chat["id"], "displayed"] = (time.monotonic() - service.policy.transient_response_ttl_seconds - 1, value)
        elif operation == "eviction":
            service.transient["other", "other", "response"] = (time.monotonic(), "x" * (service.policy.transient_response_memory_bytes + 1))
        else:
            service.transient.clear()
        with pytest.raises(ApiProblem) as error:
            service.approval("viewer", chat["id"], {"pendingId": waiting["pending"]["id"],
                "approved": True, "expectedRevision": waiting["revision"]}, report.request)
        assert error.value.code == "ai_displayed_result_expired"
    assert service.runtime.calls == [] and report.calls == []
    _assert_private(service, chat["id"])


def test_displayed_disabled_rejects_without_persisting_question_or_evidence(report):
    service, chat, packet = _displayed(report, "disabled")
    before = deepcopy(service.store.memory)
    with pytest.raises(ApiProblem) as error:
        _attach(report, service, chat, packet)
    assert error.value.status_code == 403
    assert service.store.memory == before and service.transient == {}
    assert report.calls == [] and service.runtime.calls == []


@pytest.mark.parametrize("change", ["dashboard", "model", "tile", "resource", "session", "grant", "source", "grant_identity", "provider"])
def test_displayed_approval_rechecks_resource_revision_session_and_exact_authority(report, change):
    service, chat, packet = _displayed(report, "ask")
    waiting = _attach(report, service, chat, packet)
    if change == "dashboard":
        current = report.services.dashboards._records["author", report.dashboard.id]
        report.services.dashboards._records["author", report.dashboard.id] = current.model_copy(update={"revision": current.revision + 1})
    elif change == "model":
        current = report.services.models.get("author", report.model.id)
        report.services.models._records["author", report.model.id] = current.model_copy(update={"revision": current.revision + 1})
    elif change == "tile":
        current = report.services.dashboards._records["author", report.dashboard.id]
        report.services.dashboards._records["author", report.dashboard.id] = current.model_copy(update={"tiles": []})
    elif change == "resource":
        payload = service._displayed_payload("viewer", chat["id"])
        payload["packet"]["dashboardId"] = "dashboard_" + "f" * 32
        import json
        created, _ = service.transient["viewer", chat["id"], "displayed"]
        service.transient["viewer", chat["id"], "displayed"] = (created, json.dumps(payload))
    elif change == "provider":
        service.store.update("viewer", chat["id"], lambda value: value.update(providerId="replacement-provider"))
        waiting["revision"] += 1
    elif change == "session": report.state["active"] = False
    elif change == "grant": report.state["grant"] = False
    elif change == "source":
        profile = report.services.connections.get(SCHEMII_CONNECTION_OWNER_ID, "pg_" + "b" * 32)
        profile.revision += 1
    else:
        # Same source identity, new role ownership: the original disclosure grant
        # is not silently transferred to a replacement role.
        auth = report.request.app.state.auth
        previous = auth.dashboard_grants
        auth.dashboard_grants = lambda owner: [{**item, "role_id": "replacement"} for item in previous(owner)]
        previous_connections = auth.connection_grants
        auth.connection_grants = lambda owner: [{**item, "role_id": "replacement"} for item in previous_connections(owner)]
    with pytest.raises(ApiProblem):
        service.approval("viewer", chat["id"], {"pendingId": waiting["pending"]["id"],
            "approved": True, "expectedRevision": waiting["revision"]}, report.request)
    assert service.runtime.calls == [] and report.calls == []
    _assert_private(service, chat["id"])


def test_displayed_acknowledgment_is_bound_to_selected_provider(report):
    service, chat, packet = _displayed(report, provider="opencode")
    with pytest.raises(ApiProblem):
        _attach(report, service, chat, packet, acknowledgeProviderDataPolicy=False)
    with pytest.raises(ApiProblem):
        _attach(report, service, chat, packet, acknowledgeProviderDataPolicy=True, acknowledgedProviderId="different-provider")
    turn = _attach(report, service, chat, packet, acknowledgeProviderDataPolicy=True)
    service.run("viewer", chat["id"], turn["turnId"])
    assert len(service.runtime.calls) == 1 and report.calls == []


@pytest.mark.parametrize("failure", ["provider_error", "unexpected_tool", "revocation", "cancel"])
def test_displayed_provider_failures_and_races_never_persist_derived_text(report, failure):
    from schemii.common.ai.pi import PiReply
    service, chat, packet = _displayed(report)
    if failure == "provider_error": service.runtime.reply = RuntimeError(DISPLAYED_SECRET)
    elif failure == "unexpected_tool": service.runtime.reply = PiReply(DISPLAYED_SECRET, (("schemer_actions", {"actions": [{"operation": "execute_tile", "args": {"tileId": "tile-1"}}]}),))
    elif failure == "revocation": service.runtime.before_reply = lambda kwargs: report.state.update(grant=False)
    else: service.runtime.before_reply = lambda kwargs: service.cancel("viewer", chat["id"], report.request)
    turn = _attach(report, service, chat, packet)
    service.run("viewer", chat["id"], turn["turnId"])
    assert report.calls == []
    _assert_private(service, chat["id"])
    assert service.active == {}


def test_displayed_rechecks_immediately_before_provider_dispatch(report, monkeypatch):
    service, chat, packet = _displayed(report)
    previous = service._progress
    def progress(*args):
        previous(*args)
        report.state["grant"] = False
    monkeypatch.setattr(service, "_progress", progress)
    turn = _attach(report, service, chat, packet)
    service.run("viewer", chat["id"], turn["turnId"])
    assert service.runtime.calls == [] and report.calls == []
    _assert_private(service, chat["id"])


def test_displayed_bounds_rows_bytes_metadata_and_preserves_independent_flags(report):
    import json
    service, chat, packet = _displayed(report)
    packet.update(rows=[[str(i)] for i in range(200)], cachedRows=10000, displayedRows=200,
        partial=True, cacheTruncated=True, chartTruncated=True, clientSampleTruncated=True)
    _attach(report, service, chat, packet)
    payload = service._displayed_payload("viewer", chat["id"])
    sample = payload["packet"]
    assert len(sample["rows"]) == service.policy.result_context_rows
    assert sample["sampling"]["truncated"] is True
    assert all(sample[key] is True for key in ("partial", "cacheTruncated", "chartTruncated", "clientSampleTruncated"))
    assert len(json.dumps(sample).encode()) <= service.policy.result_context_bytes
    assert report.calls == []


def test_displayed_typed_validation_never_reflects_invalid_row_input():
    from schemii.schemer.ai_routes import Message
    for packet in [{"rows": [[DISPLAYED_SECRET]]}, DISPLAYED_SECRET]:
        with pytest.raises(ApiProblem) as error:
            Message.model_validate({"text": "Explain", "expectedRevision": 1, "displayedResult": packet})
        assert DISPLAYED_SECRET not in str(error.value)


def test_displayed_regular_followup_cannot_implicitly_reuse_private_evidence(report):
    from schemii.common.ai.pi import PiReply
    service, chat, packet = _displayed(report)
    turn = _attach(report, service, chat, packet)
    service.run("viewer", chat["id"], turn["turnId"])
    current = service.store.get("viewer", chat["id"])
    service.runtime.reply = PiReply("Describe a new question or attach the displayed result again.", ())
    followup = service.send("viewer", chat["id"], {"text": "What about another region?",
        "expectedRevision": current["revision"]}, report.request)
    service.run("viewer", chat["id"], followup["turnId"])
    assert DISPLAYED_SECRET not in str(service.runtime.calls[-1][1])
    assert report.calls == []
    _assert_private(service, chat["id"])


def test_displayed_byte_sampling_drops_oversized_values_without_durable_errors(report):
    service, chat, packet = _displayed(report)
    packet["rows"] = [[DISPLAYED_SECRET * 30000]]
    turn = _attach(report, service, chat, packet)
    payload = service._displayed_payload("viewer", chat["id"])
    assert payload["packet"]["rows"] == []
    assert payload["packet"]["sampling"]["truncated"] is True
    service.run("viewer", chat["id"], turn["turnId"])
    _assert_private(service, chat["id"])


def test_displayed_metadata_budget_rejects_without_persisting_question(report):
    service, chat, packet = _displayed(report)
    packet["selections"] = {"region": {"active": True, "alternativeId": "region_one",
        "values": {"region_value": [DISPLAYED_SECRET * 100] * 500}}}
    with pytest.raises(ApiProblem) as error:
        _attach(report, service, chat, packet)
    assert error.value.code == "ai_result_metadata_too_large"
    assert DISPLAYED_SECRET not in str(error.value)
    _assert_private(service, chat["id"])


def test_displayed_old_completion_cannot_remove_a_replacement_attachment_or_unlock_it(report):
    service, chat, packet = _displayed(report)
    replacement = {}
    def replace(kwargs):
        service.cancel("viewer", chat["id"], report.request)
        current = service.store.get("viewer", chat["id"])
        next_packet = deepcopy(packet)
        next_packet["rows"] = [["replacement-displayed-row"]]
        replacement["turn"] = _attach(report, service, current, next_packet)
    service.runtime.before_reply = replace
    first = _attach(report, service, chat, packet)
    service.run("viewer", chat["id"], first["turnId"])
    assert service._displayed_payload("viewer", chat["id"])["packet"]["rows"] == [["replacement-displayed-row"]]
    assert service.store.get("viewer", chat["id"])["status"] == "working"
    assert service.active["viewer", chat["id"]]["turnId"] == replacement["turn"]["turnId"]
    service.runtime.before_reply = None
    service.run("viewer", chat["id"], replacement["turn"]["turnId"])
    assert service.store.get("viewer", chat["id"])["status"] == "idle"
    assert report.calls == []
    _assert_private(service, chat["id"])


def test_displayed_waiting_review_retains_the_existing_approval_deadline(report):
    from datetime import timedelta
    service, chat, packet = _displayed(report, "ask")
    waiting = _attach(report, service, chat, packet)
    def expire(value):
        value["pending"]["createdAt"] = (datetime.now(timezone.utc) - timedelta(seconds=service.policy.proposal_ttl_seconds + 1)).isoformat()
    current = service.store.update("viewer", chat["id"], expire)
    with pytest.raises(ApiProblem) as error:
        service.approval("viewer", chat["id"], {"pendingId": waiting["pending"]["id"],
            "approved": True, "expectedRevision": current["revision"]}, report.request)
    assert error.value.code == "ai_approval_expired"
    assert service.runtime.calls == [] and report.calls == []


def _replace_displayed_authority(report, service, chat, change):
    """Keep the account able to read where possible; change the exact data grant."""
    request = NS(app=report.request.app, cookies=dict(report.request.cookies))
    auth = report.request.app.state.auth
    if change == "grant_identity":
        dashboards, connections = auth.dashboard_grants, auth.connection_grants
        auth.dashboard_grants = lambda owner: [{**item, "role_id": "replacement"} for item in dashboards(owner)]
        auth.connection_grants = lambda owner: [{**item, "role_id": "replacement"} for item in connections(owner)]
    elif change == "source":
        report.services.connections.get(SCHEMII_CONNECTION_OWNER_ID, "pg_" + "b" * 32).revision += 1
    elif change == "model":
        current = report.services.models.get("author", report.model.id)
        report.services.models._records["author", report.model.id] = current.model_copy(update={"revision": current.revision + 1})
    elif change in {"dashboard", "tile", "tile_filters"}:
        current = report.services.dashboards._records["author", report.dashboard.id]
        if change == "tile_filters":
            tile = current.tiles[0].model_copy(update={"selections": {}})
            # A source adapter replacing tile content cannot preserve old answer
            # authority merely by retaining its parent revision number.
            tile = tile.model_copy(update={"title": "replacement tile metadata"})
            report.services.dashboards._records["author", report.dashboard.id] = current.model_copy(update={"tiles": [tile]})
            return request
        report.services.dashboards._records["author", report.dashboard.id] = current.model_copy(
            update={"revision": current.revision + 1} if change == "dashboard" else {"tiles": []})
    elif change == "session":
        request.cookies["schemii_session"] = "replacement-session"
    elif change == "product":
        auth.capabilities = lambda actor: []
    elif change == "permissions":
        previous = auth.dashboard_grants
        auth.dashboard_grants = lambda owner: [{**item, "can_export": True} for item in previous(owner)]
    elif change == "provider":
        service.store.update("viewer", chat["id"], lambda value: value.update(providerId="replacement-provider"))
    else:
        service.store.update("viewer", chat["id"], lambda value: value["modes"].update({ai_tools.DISPLAYED_ACTION: "ask"}))
    return request


@pytest.mark.parametrize("stage", ["active", "completed"])
@pytest.mark.parametrize("change", ["grant_identity", "source", "dashboard", "model", "tile", "tile_filters", "session", "product", "permissions", "provider", "mode"])
def test_displayed_snapshot_revalidates_each_stream_and_answer_with_current_request_metadata(report, monkeypatch, stage, change):
    import json
    service, chat, packet = _displayed(report)
    monkeypatch.setattr(ai_tools, "model_catalog", lambda *a, **kw: pytest.fail("Snapshot must not acquire catalog"))
    monkeypatch.setattr(ai_tools, "tile_plan", lambda *a, **kw: pytest.fail("Snapshot must not plan or run"))
    stream_marker = "private-buffered-stream-232"
    observed = []
    def inspect():
        request = _replace_displayed_authority(report, service, chat, change)
        # Fetch uses the fresh request. A replacement session must not silently
        # borrow the still-valid request retained by the provider worker.
        try:
            snapshot = service.snapshot("viewer", chat["id"], request)
        except ApiProblem:
            snapshot = {}
        assert DISPLAYED_SECRET not in json.dumps(snapshot)
        assert stream_marker not in json.dumps(snapshot)
    if stage == "active":
        def during(kwargs):
            kwargs["on_text"](stream_marker)
            assert service.snapshot("viewer", chat["id"], report.request)["stream"] == stream_marker
            assert service.active["viewer", chat["id"]]["stream"] == ""
            inspect()
            assert not kwargs["is_authorized"]()
            observed.append(True)
        service.runtime.before_reply = during
    turn = _attach(report, service, chat, packet)
    service.run("viewer", chat["id"], turn["turnId"])
    if stage == "completed": inspect()
    else: assert observed == [True]  # Provider exceptions are deliberately sanitized.
    assert report.calls == []
    _assert_private(service, chat["id"])


def test_displayed_denied_snapshot_purges_old_answer_before_same_grant_returns(report):
    service, chat, packet = _displayed(report)
    turn = _attach(report, service, chat, packet)
    service.run("viewer", chat["id"], turn["turnId"])
    report.state["grant"] = False
    with pytest.raises(ApiProblem): service.snapshot("viewer", chat["id"], report.request)
    report.state["grant"] = True
    snapshot = service.snapshot("viewer", chat["id"], report.request)
    assert DISPLAYED_SECRET not in str(snapshot)
    assert "Attach the displayed result again" in snapshot["messages"][-1]["text"]


def test_displayed_answers_keep_independent_authority_and_original_ttl_across_new_turns(report, monkeypatch):
    import schemii.common.ai.conversations as conversations
    from schemii.common.ai.pi import PiReply
    clock = [1000.0]
    monkeypatch.setattr(conversations.time, "monotonic", lambda: clock[0])
    service, chat, packet = _displayed(report)
    first = _attach(report, service, chat, packet)
    service.run("viewer", chat["id"], first["turnId"])
    first_id = service.store.get("viewer", chat["id"])["messages"][-1]["id"]
    clock[0] += service.policy.transient_response_ttl_seconds - 1
    current = service.store.get("viewer", chat["id"])
    service.runtime.reply = PiReply("second answer marker", ())
    second = _attach(report, service, current, packet)
    service.run("viewer", chat["id"], second["turnId"])
    before = service.snapshot("viewer", chat["id"], report.request)
    assert DISPLAYED_SECRET in next(m for m in before["messages"] if m["id"] == first_id)["text"]
    assert before["messages"][-1]["text"] == "second answer marker"
    clock[0] += 2
    after = service.snapshot("viewer", chat["id"], report.request)
    assert DISPLAYED_SECRET not in str(after)
    assert after["messages"][-1]["text"] == "second answer marker"
    assert report.calls == []
    _assert_private(service, chat["id"])


def test_displayed_old_grant_answer_is_withheld_without_hiding_new_grant_answer(report):
    from schemii.common.ai.pi import PiReply
    service, chat, packet = _displayed(report)
    first = _attach(report, service, chat, packet)
    service.run("viewer", chat["id"], first["turnId"])
    _replace_displayed_authority(report, service, chat, "grant_identity")
    current = service.store.get("viewer", chat["id"])
    service.runtime.reply = PiReply("replacement-grant-answer", ())
    second = _attach(report, service, current, packet)
    service.run("viewer", chat["id"], second["turnId"])
    snapshot = service.snapshot("viewer", chat["id"], report.request)
    assert DISPLAYED_SECRET not in str(snapshot)
    assert snapshot["messages"][-1]["text"] == "replacement-grant-answer"
    assert report.calls == []


@pytest.mark.parametrize("stage", ["active", "completed"])
@pytest.mark.parametrize("discard", ["expiry", "eviction"])
def test_displayed_snapshot_fails_closed_when_text_and_provenance_expire_or_are_evicted(report, stage, discard):
    import time
    service, chat, packet = _displayed(report)
    observed = []
    def inspect():
        if discard == "expiry":
            for key, (_, data) in list(service.transient.items()):
                service.transient[key] = (time.monotonic() - service.policy.transient_response_ttl_seconds - 1, data)
        else:
            service.transient["other", "other", "response"] = (time.monotonic(), "x" * (service.policy.transient_response_memory_bytes + 1))
        snapshot = service.snapshot("viewer", chat["id"], report.request)
        assert DISPLAYED_SECRET not in str(snapshot) and snapshot["stream"] == ""
        assert sum(len(value[1].encode()) for value in service.transient.values()) <= service.policy.transient_response_memory_bytes
    if stage == "active":
        def during(kwargs):
            kwargs["on_text"](DISPLAYED_SECRET)
            inspect()
            assert not kwargs["is_authorized"]()
            observed.append(True)
        service.runtime.before_reply = during
    turn = _attach(report, service, chat, packet)
    service.run("viewer", chat["id"], turn["turnId"])
    if stage == "completed": inspect()
    else: assert observed == [True]
    assert report.calls == []
    _assert_private(service, chat["id"])


def test_displayed_stream_text_and_binding_share_the_existing_memory_budget(report):
    from dataclasses import replace
    import time
    service, chat, packet = _displayed(report)
    turn = _attach(report, service, chat, packet)
    service.policy = replace(service.policy, transient_response_memory_bytes=1024 * 1024)
    used = sum(len(value[1].encode()) for value in service.transient.values())
    service.transient["other", "other", "response"] = (time.monotonic(), "x" * (service.policy.transient_response_memory_bytes - used - 1024))
    observed = []
    def during(kwargs):
        kwargs["on_text"](DISPLAYED_SECRET * 100)
        assert not kwargs["is_authorized"]()
        assert service.snapshot("viewer", chat["id"], report.request)["stream"] == ""
        assert sum(len(value[1].encode()) for value in service.transient.values()) <= service.policy.transient_response_memory_bytes
        observed.append(True)
    service.runtime.before_reply = during
    service.run("viewer", chat["id"], turn["turnId"])
    assert observed == [True]
    _assert_private(service, chat["id"])


@pytest.mark.parametrize("pane", ["main", "drill"])
def test_displayed_effective_population_preserves_tile_overrides_predicates_and_comparison_columns(report, monkeypatch, pane):
    import json
    from schemii.schemoo.models import ScopeSelection, ReportFilter
    service, chat, packet = _displayed(report)
    current = report.services.dashboards._records["author", report.dashboard.id]
    tile = current.tiles[0].model_copy(update={
        "selections": {"region": ScopeSelection(alternativeId="region_one", values={"region_value": "tile region"}, active=True)},
        "report_filters": [ReportFilter(conditions=[{"table": "sales", "column": "region", "operator": "eq", "compareColumn": "other_region"}])]})
    report.services.dashboards._records["author", report.dashboard.id] = current.model_copy(update={"tiles": [tile]})
    packet["selections"] = {"region": {"alternativeId": "region_one", "values": {"region_value": "dashboard region"}, "active": True}}
    if pane == "drill":
        previous = report.request.app.state.auth.dashboard_grants
        report.request.app.state.auth.dashboard_grants = lambda owner: [{**item, "can_drill": True} for item in previous(owner)]
        packet.update(pane="drill", drillSelection={"dimensions": [{"table": "sales", "column": "region", "value": "chosen"}], "measureIndex": 0})
    monkeypatch.setattr(ai_tools, "model_catalog", lambda *a, **kw: pytest.fail("No catalog"))
    monkeypatch.setattr(ai_tools, "tile_plan", lambda *a, **kw: pytest.fail("No plan/query"))
    turn = _attach(report, service, chat, packet)
    packet["selections"]["region"]["values"]["region_value"] = "later UI mutation"
    service.run("viewer", chat["id"], turn["turnId"])
    evidence = json.loads(service.runtime.calls[0][1][0]["content"])["displayedEvidence"]
    assert evidence["dashboardSelections"]["region"]["values"]["region_value"] == "dashboard region"
    assert evidence["tileSelections"]["region"]["values"]["region_value"] == "tile region"
    assert evidence["selections"]["region"]["values"]["region_value"] == "tile region"
    assert evidence["reportFilters"][0]["conditions"][0]["compareColumn"] == "other_region"
    assert evidence["pane"] == pane and report.calls == []
    _assert_private(service, chat["id"])


@pytest.mark.parametrize("field", ["tileSelections", "reportFilters"])
def test_displayed_rejects_client_saved_tile_metadata_that_disagrees_with_exact_revision(report, field):
    service, chat, packet = _displayed(report)
    packet[field] = ({"region": {"active": True}} if field == "tileSelections" else [{"mode": "rows", "conditions": [{"table": "sales", "column": "region", "operator": "eq", "value": "invented predicate"}]}])
    with pytest.raises(ApiProblem) as error: _attach(report, service, chat, packet)
    assert error.value.code == "ai_displayed_result_stale"
    assert service.runtime.calls == [] and report.calls == []


def test_displayed_canonical_tile_filter_metadata_is_bounded_before_provider_disclosure(report):
    from schemii.schemoo.models import ScopeSelection
    service, chat, packet = _displayed(report)
    current = report.services.dashboards._records["author", report.dashboard.id]
    tile = current.tiles[0].model_copy(update={"selections": {"region": ScopeSelection(
        alternativeId="region_one", values={"region_value": [DISPLAYED_SECRET * 100] * 500}, active=True)}})
    report.services.dashboards._records["author", report.dashboard.id] = current.model_copy(update={"tiles": [tile]})
    with pytest.raises(ApiProblem) as error: _attach(report, service, chat, packet)
    assert error.value.code == "ai_result_metadata_too_large"
    assert DISPLAYED_SECRET not in str(error.value)
    assert service.runtime.calls == [] and report.calls == []
    _assert_private(service, chat["id"])


def test_displayed_answer_snapshot_checks_only_metadata_and_delete_releases_its_provenance(report, monkeypatch):
    service, chat, packet = _displayed(report)
    turn = _attach(report, service, chat, packet)
    service.run("viewer", chat["id"], turn["turnId"])
    monkeypatch.setattr(service.runtime, "require_available_model", lambda *args: pytest.fail("Snapshot must not contact provider catalog"))
    monkeypatch.setattr(report.services.connections, "use", lambda *args: pytest.fail("Snapshot must not connect to source"))
    assert DISPLAYED_SECRET in service.snapshot("viewer", chat["id"], report.request)["messages"][-1]["text"]
    service.delete("viewer", chat["id"], report.request)
    assert not any(key[:2] == ("viewer", chat["id"]) for key in service.transient)
    assert report.calls == []


def test_displayed_replacement_during_worker_metadata_read_keeps_the_new_attachment(report, monkeypatch):
    service, chat, packet = _displayed(report)
    first = _attach(report, service, chat, packet)
    original = service._get
    replacement = {}
    def replace(*args, **kwargs):
        result = original(*args, **kwargs)
        if not replacement:
            replacement["started"] = True
            service.cancel("viewer", chat["id"], report.request)
            current = service.store.get("viewer", chat["id"])
            replacement["turn"] = _attach(report, service, current, packet)
            replacement["id"] = service._displayed_payload("viewer", chat["id"])["id"]
        return result
    monkeypatch.setattr(service, "_get", replace)
    service.run("viewer", chat["id"], first["turnId"])
    assert service.runtime.calls == []
    assert service._displayed_payload("viewer", chat["id"])["id"] == replacement["id"]
    assert service.active["viewer", chat["id"]]["turnId"] == replacement["turn"]["turnId"]
    monkeypatch.setattr(service, "_get", original)
    service.run("viewer", chat["id"], replacement["turn"]["turnId"])
    assert len(service.runtime.calls) == 1 and report.calls == []
    _assert_private(service, chat["id"])


@pytest.mark.parametrize('kind,rd,cd', [('cell', 1, 1), ('row', 1, 0), ('column', 0, 1), ('subtotal', 1, 1), ('grand_total', 0, 0)])
def test_assistant_typed_pivot_scope_is_forwarded_without_sql_or_membership_overrides(report, monkeypatch, kind, rd, cd):
    captured = []
    selected = {'dimensions': [], 'measureIndex': 0, 'pivot': {'kind': kind, 'rowDepth': rd, 'columnDepth': cd}}
    action = ai_tools.validate_action({'operation': 'drill_tile', 'args': {'tileId': 'pivot', 'selection': selected}})
    bundle = ai_tools.authorize(report.services, 'author', report.dashboard.id, report.request)
    monkeypatch.setattr(ai_tools, '_run_tile', lambda bundle, tile, selections, selection: captured.append(selection) or {'status': 'bounded'})
    assert ai_tools.execute_action(bundle, 'author', report.dashboard.id, action) == {'status': 'bounded'}
    assert captured == [selected]
    from pydantic import ValidationError
    for extra in [{'sql': 'SELECT 1'}, {'pivot': {**selected['pivot'], 'predicate': 'TRUE'}},
                  {'pivot': {**selected['pivot'], 'rowDepth': True}}]:
        with pytest.raises(ValidationError):
            ai_tools.validate_action({'operation': 'drill_tile', 'args': {'tileId': 'pivot', 'selection': {**selected, **extra}}})


def test_assistant_pivot_result_keeps_bounded_scope_metadata_and_semantic_warning(report, monkeypatch):
    bundle = ai_tools.authorize(report.services, 'author', report.dashboard.id, report.request)
    metadata = {'metadataOffset': 3, 'totalsPopulation': 'complete qualifying groups before Top N'}
    warning = 'This is an average of per-owner semantic values, not raw-child rows.'
    monkeypatch.setattr(ai_tools, 'tile_plan', lambda *args, **kwargs: (NS(), {'pivot': metadata, 'warnings': [warning]}))
    monkeypatch.setattr(ai_tools, '_sample_plan', lambda *args: {'sample': {'rows': [[None, None, 20, 0, 0, 'data']]}})
    result = ai_tools._run_tile(bundle, 'pivot')
    assert result['pivot'] == metadata and result['warnings'] == [warning]
    assert result['sample']['rows'][0][3:5] == [0, 0]
    assert 'SQL' not in result


@pytest.mark.parametrize('kind,values', [
    ('cell', [('region', None), ('category', 'A')]), ('row', [('region', None)]),
    ('column', [('category', None)]), ('subtotal', [('region', None)]),
    ('grand_total', []), ('legacy', [('region', None)]),
    ('cell', [('region', 'East'), ('category', 'A')]), ('legacy', [('region', 'East')]),
])
def test_required_null_drill_value_survives_full_action_validation_revalidation_execution(report, monkeypatch, kind, values):
    from schemii.schemer import tile_queries
    from schemii.schemer.dashboard_models import DashboardUpdate
    f = lambda name, aggregate='none': {'table': 'sales', 'column': name, 'aggregate': aggregate}
    tile = {'id': 'pivot', 'title': 'Sales', 'kind': 'aggregate' if kind == 'legacy' else 'pivot',
        'dimensions': [f('region')] if kind == 'legacy' else [],
        'rowDimensions': [] if kind == 'legacy' else [f('region'), *([f('team')] if kind == 'subtotal' else [])],
        'columnDimensions': [] if kind == 'legacy' else [f('category')],
        'measures': [f('id', 'count')], 'detailFields': [f('id'), f('region')], 'limit': 1}
    current = report.dashboard.model_dump(exclude={'id', 'owner_id', 'revision', 'created_at', 'updated_at'})
    report.services.dashboards.update('author', report.dashboard.id, DashboardUpdate(**{**current, 'tiles': [tile]}, expected_revision=1))
    catalog = {'namespace': 'public', 'fingerprint': '', 'tables': [{'name': 'sales', 'columns': [
        {'name': name, 'dataType': 'integer' if name == 'id' else 'text'} for name in ['id', 'region', 'category', 'team', 'secret']]}], 'relationships': []}
    monkeypatch.setattr(tile_queries, 'model_catalog', lambda *args, **kwargs: catalog)
    captured = []
    monkeypatch.setattr(ai_tools, '_sample_plan', lambda bundle, model, plan: captured.append(plan) or {'sample': {'rows': []}})
    selected = {'dimensions': [{'table': 'sales', 'column': name, 'value': value} for name, value in values], 'measureIndex': 0}
    if kind != 'legacy':
        rd, cd = {'cell': (1, 1), 'row': (1, 0), 'column': (0, 1), 'subtotal': (1, 0), 'grand_total': (0, 0)}[kind]
        selected['pivot'] = {'kind': kind, 'rowDepth': rd, 'columnDepth': cd}
    action = {'operation': 'drill_tile', 'args': {'tileId': 'pivot', 'selection': selected}}
    normalized = ai_tools.validate_action(action)
    assert normalized['args']['selection'] == selected
    assert ai_tools.validate_action(normalized) == normalized
    bundle = ai_tools.authorize(report.services, 'author', report.dashboard.id, report.request)
    result = ai_tools.execute_action(bundle, 'author', report.dashboard.id, normalized)
    assert captured[0]['drill'] is True and result['sample'] == {'rows': []}
    if any(value is None for _, value in values):
        assert 'IS NULL' in captured[0]['sql']
    if kind != 'legacy':
        assert result['pivotScope']['kind'] == kind
    else:
        assert 'pivot' not in normalized['args']['selection']


def test_null_drill_round_trip_retains_current_viewer_drill_grant_requirement(report, monkeypatch):
    selected = {'dimensions': [{'table': 'sales', 'column': 'region', 'value': None}], 'measureIndex': 0}
    action = ai_tools.validate_action({'operation': 'drill_tile', 'args': {'tileId': 'tile', 'selection': selected}})
    bundle = ai_tools.authorize(report.services, 'viewer', report.dashboard.id, report.request)
    calls = []
    monkeypatch.setattr(ai_tools, '_run_tile', lambda *args: calls.append('executed'))
    with pytest.raises(ApiProblem) as denied:
        ai_tools.execute_action(bundle, 'viewer', report.dashboard.id, ai_tools.validate_action(action))
    assert denied.value.status_code == 403 and calls == []
