"""Saved rules, owner identity, schema drift and shared execution API contracts."""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from schemii.main import create_app, create_services
from schemii.common.metadata.models import Principal, get_current_principal
from schemii.schemoo.catalog import ModelCatalogs, initial_positions


CATALOG = {"namespace": "public", "database": "warehouse", "fingerprint": "one",
           "tables": [{"name": "people", "columns": [{"name": "id", "dataType": "uuid"}, {"name": "name", "dataType": "text"}]}],
           "relationships": [], "positions": []}
DEFINITION = {"root": "people", "nodes": [{"id": "people", "table": "people", "label": "People"}], "edges": [], "scopes": []}
EXPLORE = {"root": "people", "fields": [{"table": "people", "column": "name"}]}


@pytest.fixture
def setup():
    services = create_services()
    catalog = deepcopy(CATALOG)
    calls = []
    def get(services, owner, connection_id, namespace, fresh=False):
        profile = services.connections.get(owner, connection_id)
        calls.append(fresh)
        return {**deepcopy(catalog), "connectionId": connection_id, "database": profile.database}
    services = replace(services, model_catalogs=SimpleNamespace(get=get))
    api = TestClient(create_app(services=services), base_url="http://localhost")
    connection = api.post("/api/v1/connections", json={"name": "Warehouse", "host": "localhost", "database": "warehouse", "username": "reader"}).json()
    response = api.post("/api/v1/schemoo/models", json={"name": "People", "connectionId": connection["id"], "namespace": "public", "definition": DEFINITION, "explore": EXPLORE})
    assert response.status_code == 201, response.text
    yield api, response.json(), catalog, calls
    api.close()


def test_explicit_source_and_multiple_models_without_workspace(setup):
    api, model, _, _ = setup
    assert api.get("/schemoo").status_code == 200
    assert api.get("/api/v1/schemoo/catalog").status_code == 422
    assert model["database"] == "warehouse"
    assert api.app.state.services.workspaces.list(model["ownerId"]) == []
    created = api.post("/api/v1/schemoo/models", json={"name": "Second", "connectionId": model["connectionId"], "namespace": "public"})
    assert created.status_code == 201
    assert created.json()["definition"]["sourceContract"] == {"scope": "model", "tables": [], "relationships": []}
    assert len(api.get("/api/v1/schemoo/models").json()["models"]) == 2
    assert api.get("/api/v1/schemoo/prototype/catalog").status_code == 404


def test_save_revision_conflict_and_independent_layout_explore(setup):
    api, model, _, _ = setup
    url = f"/api/v1/schemoo/models/{model['id']}"
    body = {"expectedRevision": 1, "name": "Updated", "definition": DEFINITION}
    assert api.put(url, json=body).json()["revision"] == 2
    assert api.put(url, json=body).status_code == 409
    moved = api.put(url + "/layout", json={"expectedRevision": 1, "layout": {"positions": [{"id": "people", "x": 12, "y": 34}]}}).json()
    assert moved["revision"] == 2 and moved["layoutRevision"] == 2
    assert api.put(url + "/explore", json={"expectedRevision": 1, "explore": EXPLORE}).status_code == 200
    assert api.delete(url, params={"expected_revision": 1}).status_code == 409
    assert api.delete(url, params={"expected_revision": 2}).status_code == 204


def test_duplicate_saved_model_without_catalog_access_and_with_owner_fencing(setup):
    api, model, _, calls = setup
    url = f"/api/v1/schemoo/models/{model['id']}"
    preview = api.post(url + "/previews", json={"name": "Current", "explore": EXPLORE}).json()
    before_calls = len(calls)
    body = {"name": "People copy", "expectedRevision": 1,
        "expectedLayoutRevision": 1, "expectedExploreRevision": 1}
    response = api.post(url + "/duplicate", json=body)
    assert response.status_code == 201, response.text
    copied = response.json()
    assert copied["id"] != model["id"] and copied["name"] == "People copy"
    for key in ("definition", "layout", "explore", "connectionId", "catalogFingerprint"):
        assert copied[key] == model[key]
    previews = api.get(f"/api/v1/schemoo/models/{copied['id']}/previews").json()["previews"]
    assert len(previews) == 1 and previews[0]["id"] != preview["id"]
    assert previews[0]["explore"] == preview["explore"]
    assert len(calls) == before_calls
    assert api.post(url + "/duplicate", json={**body, "expectedLayoutRevision": 2}).status_code == 409
    api.app.dependency_overrides[get_current_principal] = lambda: Principal(user_id="other", authentication_source="local_prototype")
    assert api.post(url + "/duplicate", json=body).status_code == 404
    api.app.dependency_overrides.clear()
    api.app.state.services.models._maximum = 2
    limited = api.post(url + "/duplicate", json=body)
    assert limited.status_code == 409 and limited.json()["error"]["code"] == "model_limit_reached"


def test_saved_previews_crud_owner_scope_and_model_delete_cascade(setup):
    api, model, _, _ = setup
    model_url = f"/api/v1/schemoo/models/{model['id']}"
    url = model_url + "/previews"
    assert api.get(url).json() == {"previews": []}
    created = api.post(url, json={"name": " Current ", "explore": EXPLORE})
    assert created.status_code == 201, created.text
    preview = created.json()
    assert preview["name"] == "Current" and preview["modelId"] == model["id"]
    assert "ownerId" not in preview and "definition" not in preview and "rows" not in preview
    item_url = url + "/" + preview["id"]
    duplicate = api.post(url, json={"name": " CURRENT ", "explore": EXPLORE})
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "preview_name_conflict"
    other = api.post(url, json={"name": "Other", "explore": {}}).json()
    duplicate_update = api.put(item_url, json={"expectedRevision": 1, "name": "other", "explore": {}})
    assert duplicate_update.status_code == 409 and duplicate_update.json()["error"]["code"] == "preview_name_conflict"
    assert api.delete(url + "/" + other["id"], params={"expected_revision": 1}).status_code == 204
    assert api.put(item_url, json={"expectedRevision": 1, "name": "Changed", "explore": {}}).json()["revision"] == 2
    stale = api.put(item_url, json={"expectedRevision": 1, "name": "Stale", "explore": {}})
    assert stale.status_code == 409 and stale.json()["error"]["details"]["currentRevision"] == 2
    assert api.delete(item_url, params={"expected_revision": 1}).status_code == 409
    assert api.get(model_url).json() == model
    api.app.dependency_overrides[get_current_principal] = lambda: Principal(user_id="other", authentication_source="local_prototype")
    assert api.get(url).status_code == 404
    assert api.post(url, json={"name": "No", "explore": {}}).status_code == 404
    assert api.put(item_url, json={"expectedRevision": 2, "name": "No", "explore": {}}).status_code == 404
    assert api.delete(item_url, params={"expected_revision": 2}).status_code == 404
    api.app.dependency_overrides.clear()
    assert api.delete(item_url, params={"expected_revision": 2}).status_code == 204
    assert api.get(url).json() == {"previews": []}
    assert api.post(url, json={"name": "Another", "explore": EXPLORE}).status_code == 201
    assert api.delete(model_url, params={"expected_revision": 1}).status_code == 204
    assert api.get(url).status_code == 404


def test_saved_preview_limit_logs_existing_config_resource_and_rejects_rows(setup):
    api, model, _, _ = setup
    url = f"/api/v1/schemoo/models/{model['id']}/previews"
    assert api.post(url, json={"name": " ", "explore": {}}).status_code == 422
    assert api.post(url, json={"name": "Rows", "explore": {"rows": [["secret"]]}}).status_code == 422
    api.app.state.services.models._maximum_document_bytes = 100
    rejected = api.post(url, json={"name": "Too large", "explore": EXPLORE})
    assert rejected.status_code == 413 and rejected.json()["error"]["code"] == "model_document_limit_reached"
    assert api.get(url).json() == {"previews": []}
    events = api.app.state.services.metadata.limit_events.events()
    assert events[-1].notice.limit_name == "resources.maximum_model_document_bytes"


def test_patch_preserves_unrelated_documents_and_schema_contract(setup):
    api, model, _, _ = setup
    url = f"/api/v1/schemoo/models/{model['id']}"
    patch = {"expectedRevision": 1, "nodes": {"upsert": [{"id": "manager", "table": "people", "label": "Manager"}]}}
    response = api.patch(url, json=patch)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["revision"] == 2
    assert saved["definition"]["nodes"][0] == model["definition"]["nodes"][0]
    assert saved["definition"]["nodes"][1]["id"] == "manager"
    for key in ("layout", "explore", "catalogFingerprint", "layoutRevision", "exploreRevision"):
        assert saved[key] == model[key]
    assert saved["definition"]["sourceContract"] == model["definition"]["sourceContract"]
    assert api.patch(url, json=patch).status_code == 409
    assert api.get(url).json() == saved


def test_patch_validates_binding_before_atomic_save_without_requiring_report_values(setup):
    api, model, _, _ = setup
    url = f"/api/v1/schemoo/models/{model['id']}"
    scope = {"id": "date", "kind": "conditional", "alternatives": [{"id": "default",
        "inputs": [{"id": "input", "type": "text"}], "conditions": [
            {"parameterId": "input", "domain": {"nodeId": "people", "column": "name"}}]}]}
    patch = {"expectedRevision": 1, "name": "Must not save", "scopes": {"upsert": [scope]}}
    failed = api.patch(url, json=patch)
    assert failed.status_code == 422, failed.text
    assert failed.json()["error"]["details"] == {"scopeId": "date", "alternativeId": "default", "conditionIndex": 0}
    assert api.get(url).json() == model
    scope["alternatives"][0]["conditions"][0].update(table="people", column="name")
    success = api.patch(url, json=patch)
    assert success.status_code == 200, success.text
    assert success.json()["revision"] == 2


def test_patch_removals_and_exposure_are_explicit_and_owner_scoped(setup):
    api, model, _, _ = setup
    url = f"/api/v1/schemoo/models/{model['id']}"
    for body in (
        {"nodes": {"remove": ["unknown"]}},
        {"nodes": {"remove": ["people"]}},
        {"nodes": {"remove": ["people"], "upsert": DEFINITION["nodes"]}},
        {"expose": [{"table": "people", "column": "invented"}]},
        {"sourceContract": {}}, {"catalogFingerprint": "invented"}, {},
    ):
        failed = api.patch(url, json={"expectedRevision": 1, **body})
        assert failed.status_code == 422, failed.text
        assert api.get(url).json() == model
    saved = api.patch(url, json={"expectedRevision": 1, "hide": [{"table": "people", "column": "id"}]}).json()
    assert saved["definition"]["exposedFields"] == [{"table": "people", "column": "name", "aggregate": "none"}]
    saved = api.patch(url, json={"expectedRevision": 2, "expose": [{"table": "people", "column": "id"}]}).json()
    assert [field["column"] for field in saved["definition"]["exposedFields"]] == ["name", "id"]
    api.app.dependency_overrides[get_current_principal] = lambda: Principal(user_id="other", authentication_source="local_prototype")
    assert api.patch(url, json={"expectedRevision": 3, "name": "Not mine"}).status_code == 404


def test_owner_isolation_covers_model_and_source(setup):
    api, model, _, _ = setup
    api.app.dependency_overrides[get_current_principal] = lambda: Principal(user_id="other", authentication_source="local_prototype")
    url = f"/api/v1/schemoo/models/{model['id']}"
    assert api.get(url).status_code == 404
    assert api.post(url + "/plan", json={"expectedRevision": 1, "explore": EXPLORE}).status_code == 404
    assert api.get("/api/v1/schemoo/catalog", params={"connection_id": model["connectionId"], "namespace": "public"}).status_code == 404
    assert api.get("/api/v1/schemoo/models").json() == {"models": []}


def test_plans_load_saved_rules_and_cannot_accept_raw_sql(setup):
    api, model, _, _ = setup
    url = f"/api/v1/schemoo/models/{model['id']}/plan"
    body = {"expectedRevision": 1, "explore": EXPLORE}
    plan = api.post(url, json=body)
    assert plan.status_code == 200, plan.text
    assert '"People.name"' in plan.json()["sql"]
    for injected in ({"sql": "DROP TABLE people"}, {"definition": DEFINITION}, {"scopes": []}):
        assert api.post(url, json={**body, **injected}).status_code == 422


def test_drift_preserves_saved_rules_and_unrelated_changes_are_ignored(setup):
    api, model, catalog, _ = setup
    url = f"/api/v1/schemoo/models/{model['id']}"
    catalog["fingerprint"] = "two"
    plan = api.post(url + "/plan", json={"expectedRevision": 1, "explore": EXPLORE})
    assert not any("schema changed" in w for w in plan.json()["warnings"])
    catalog["tables"] = []
    failed = api.post(url + "/plan", json={"expectedRevision": 1, "explore": EXPLORE})
    assert failed.status_code == 422
    assert failed.json()["error"]["details"]["issues"][0]["kind"] == "missing_table"
    assert api.get(url).json()["definition"] == model["definition"]


def test_required_rules_exposed_fields_and_domain_values_are_server_owned(setup):
    api, model, _, _ = setup
    url = f"/api/v1/schemoo/models/{model['id']}"
    definition = {**DEFINITION, "exposedFields": [{"table": "people", "column": "name"}], "scopes": [{
        "id": "person", "kind": "required", "alternatives": [{"id": "pick", "inputs": [{"id": "id", "type": "uuid", "domain": {"table": "people", "column": "id", "labelColumn": "name"}}],
        "conditions": [{"table": "people", "column": "id", "parameterId": "id", "operator": "eq"}]}]}]}
    assert api.put(url, json={"expectedRevision": 1, "name": "Scoped", "definition": definition}).status_code == 200
    missing = api.post(url + "/plan", json={"expectedRevision": 2, "explore": EXPLORE})
    assert missing.status_code == 422 and missing.json()["error"]["details"]["parameterId"] == "id"
    hidden = api.post(url + "/plan", json={"expectedRevision": 2, "explore": {"fields": [{"table": "people", "column": "id"}]}})
    assert hidden.json()["error"]["code"] == "field_not_exposed"
    unknown = api.post(url + "/parameter-values", json={"expectedRevision": 2, "scopeId": "person", "alternativeId": "pick", "parameterId": "other", "consoleId": "con_" + "a"*32})
    assert unknown.status_code == 422


@pytest.mark.parametrize("mode", ["rows", "exists", "not_exists"])
def test_preview_report_filters_cannot_bypass_exposure(setup, mode):
    api, model, _, _ = setup
    url = f"/api/v1/schemoo/models/{model['id']}"
    definition = {**DEFINITION, "exposedFields": [{"table": "people", "column": "name"}]}
    assert api.put(url, json={"expectedRevision": 1, "name": "Restricted", "definition": definition}).status_code == 200
    explore = {**EXPLORE, "reportFilters": [{"id": "hidden", "mode": mode, "conditions": [
        {"table": "people", "column": "id", "operator": "not_null"}]}]}
    response = api.post(url + "/plan", json={"expectedRevision": 2, "explore": explore})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "field_not_exposed"
    assert response.json()["error"]["details"]["fields"] == [{"table": "people", "column": "id"}]


def test_preview_order_and_aggregations_are_independent_of_model_exposure(setup):
    api, model, _, _ = setup
    url = f"/api/v1/schemoo/models/{model['id']}"
    definition = {**DEFINITION, "exposedFields": [{"table": "people", "column": "name"}, {"table": "people", "column": "id"}]}
    saved = api.put(url, json={"expectedRevision": 1, "name": "Exposed", "definition": definition}).json()
    fields = [{"table": "people", "column": "id", "aggregate": "count"}, {"table": "people", "column": "name"}]
    response = api.post(url + "/plan", json={"expectedRevision": 2, "explore": {"fields": fields}})
    assert response.status_code == 200, response.text
    sql = response.json()["sql"]
    assert sql.index('COUNT(t0."id")') < sql.index('t0."name"')
    assert 'GROUP BY' in sql
    assert api.get(url).json()["definition"] == saved["definition"]
    # Repeating the same source as detail and measure is a valid preview choice.
    fields = [{"table": "people", "column": "name"}, {"table": "people", "column": "name", "aggregate": "count"}]
    assert api.post(url + "/plan", json={"expectedRevision": 2, "explore": {"fields": fields}}).status_code == 200


def test_exposure_is_per_alias_not_physical_table(setup):
    api, model, _, _ = setup
    url = f"/api/v1/schemoo/models/{model['id']}"
    definition = {**DEFINITION, "nodes": DEFINITION["nodes"] + [{"id": "manager", "table": "people", "label": "Manager"}],
                  "exposedFields": [{"table": "people", "column": "name"}]}
    assert api.put(url, json={"expectedRevision": 1, "name": "Alias", "definition": definition}).status_code == 200
    response = api.post(url + "/plan", json={"expectedRevision": 2, "explore": {
        "root": "manager", "fields": [{"table": "manager", "column": "name"}]}})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "field_not_exposed"


def test_domain_lookup_uses_saved_alias_and_rejects_deleted_role():
    from schemii.schemoo.models import ModelDefinition
    from schemii.schemoo.service import domain_query
    from schemii.common.api.errors import ApiProblem
    definition = {**DEFINITION, "nodes": DEFINITION["nodes"] + [{"id": "alias_people", "table": "people", "label": "Domain people"}], "scopes": [{
        "id": "scope", "kind": "conditional", "alternatives": [{"id": "option", "inputs": [{"id": "person", "domain": {"nodeId": "alias_people", "table": "not_authoritative", "column": "id", "labelColumn": "name"}}]}]}]}
    model = SimpleNamespace(definition=ModelDefinition.model_validate(definition))
    body = SimpleNamespace(scope_id="scope", alternative_id="option", parameter_id="person", search="Alice")
    plan = domain_query(CATALOG, model, body)
    assert 'FROM "public"."people"' in plan["sql"]
    assert "not_authoritative" not in plan["sql"]
    assert "Alice" in plan["sql"]
    model.definition.nodes.pop()
    with pytest.raises(ApiProblem, match="domain source alias was removed"):
        domain_query(CATALOG, model, body)


def test_author_domain_lookup_validates_draft_source_without_saving_it(setup):
    api, model, _, _ = setup
    reservations = []
    receipt = SimpleNamespace(id="exec_domain", model_dump=lambda **kwargs: {"id": "exec_domain"})
    console = SimpleNamespace(reserve_read_target=lambda owner, **kwargs: (reservations.append(kwargs) or receipt), run=lambda *args, **kwargs: None)
    api.app.state.services = replace(api.app.state.services, console=console)
    url = f"/api/v1/schemoo/models/{model['id']}"
    definition = {**DEFINITION, "nodes": DEFINITION["nodes"] + [{"id": "alias", "table": "people", "label": "Role"}]}
    body = {"expectedRevision": 1, "definition": definition, "domain": {"nodeId": "alias", "table": "ignored", "column": "id", "labelColumn": "name"}, "search": "Alice", "consoleId": "con_" + "a"*32}
    response = api.post(url + "/domain-values", json=body)
    assert response.status_code == 201, response.text
    assert 'FROM "public"."people"' in reservations[0]["statements"][0]
    assert "Alice" in reservations[0]["statements"][0]
    assert api.get(url).json()["definition"] == model["definition"]
    assert api.post(url + "/domain-values", json={**body, "expectedRevision": 2}).status_code == 409
    assert api.post(url + "/domain-values", json={**body, "domain": {**body["domain"], "nodeId": "missing"}}).status_code == 422
    assert api.post(url + "/domain-values", json={**body, "domain": {**body["domain"], "column": "missing"}}).status_code == 422
    assert api.post(url + "/domain-values", json={**body, "sql": "DROP TABLE people"}).status_code == 422
    assert len(reservations) == 1


def test_execution_recompiles_fresh_and_uses_direct_shared_read(setup):
    api, model, _, calls = setup
    reservations = []
    receipt = SimpleNamespace(id="exec_test", model_dump=lambda **kwargs: {"id": "exec_test"})
    service = SimpleNamespace(reserve_read_target=lambda owner, **kwargs: (reservations.append((owner, kwargs)) or receipt), run=lambda *args, **kwargs: None)
    api.app.state.services = replace(api.app.state.services, console=service)
    response = api.post(f"/api/v1/schemoo/models/{model['id']}/executions", json={"expectedRevision": 1, "explore": EXPLORE, "consoleId": "con_" + "a"*32})
    assert response.status_code == 201, response.text
    assert response.json()["executionUrl"] == "/api/v1/common/query-executions/exec_test"
    assert calls[-1] is True
    assert reservations[0][1]["statements"] == [response.json()["plan"]["sql"]]
    assert "workspace_id" not in reservations[0][1]


def test_catalog_cache_is_identity_bound_and_refreshes_execution():
    counts = []
    profile = SimpleNamespace(revision=1, database="warehouse")
    @contextmanager
    def use(owner, connection):
        yield profile
    def introspect(connection, namespace):
        counts.append(namespace)
        return SimpleNamespace(
            tables=[],
            views=[SimpleNamespace(name="personnel_summary", columns=[SimpleNamespace(name="id", model_dump=lambda **kwargs:{"name":"id"})], query_definition="SELECT 1")],
            materialized_views=[SimpleNamespace(name="personnel_summary_reporting_mv", columns=[SimpleNamespace(name="id", model_dump=lambda **kwargs:{"name":"id"})], query_definition="SELECT 1", populated=True)],
            relationships=[],
            fingerprint="one",
        )
    services = SimpleNamespace(connections=SimpleNamespace(get=lambda *args: profile, use=use), postgres=SimpleNamespace(introspect=introspect, readable_columns=lambda *args:{("personnel_summary","id"),("personnel_summary_reporting_mv","id")}))
    cache = ModelCatalogs(maximum_entries=2)
    cache.get(services, "one", "connection", "public")
    cache.get(services, "one", "connection", "public")
    cache.get(services, "two", "connection", "public")
    cache.get(services, "one", "connection", "public", fresh=True)
    profile.revision = 2
    catalog = cache.get(services, "one", "connection", "public")
    assert len(counts) == 4
    assert len(cache._cache) == 2
    assert [table["name"] for table in catalog["tables"]] == [
        "personnel_summary",
        "personnel_summary_reporting_mv",
    ]


def test_document_limits_are_actionable_logged_and_do_not_save(setup):
    from schemii.schemoo.store import InMemoryModelRepository
    api, model, _, _ = setup
    api.app.state.services = replace(api.app.state.services, models=InMemoryModelRepository(maximum_document_bytes=50))
    response = api.post("/api/v1/schemoo/models", json={"name": "Too large", "connectionId": model["connectionId"], "namespace": "public", "definition": DEFINITION})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "model_document_limit_reached"
    assert api.get("/api/v1/schemoo/models").json() == {"models": []}
    event = api.app.state.services.metadata.limit_events.events()[-1]
    assert event.notice.limit_name == "resources.maximum_model_document_bytes"
    assert event.notice.configured_limit == 50


def test_catalog_layout_is_only_an_initial_seed():
    from datetime import datetime, timezone
    workspace = SimpleNamespace(id="ws_one", connection_id="pg_one", database="warehouse", namespace="public", created_at=datetime.now(timezone.utc))
    services = SimpleNamespace(workspaces=SimpleNamespace(list=lambda owner: [workspace]), designs=SimpleNamespace(
        get=lambda *args: SimpleNamespace(content=SimpleNamespace(tables=[SimpleNamespace(id="t1", name="people"), SimpleNamespace(id="t2", name="unapplied")])),
        get_layout=lambda *args: SimpleNamespace(content=SimpleNamespace(objects=[SimpleNamespace(object_id="t1", layer="tables", x=10, y=20), SimpleNamespace(object_id="t2", layer="tables", x=0, y=0)]))))
    result = initial_positions(services, "owner", {**deepcopy(CATALOG), "connectionId": "pg_one"})
    assert result["positions"] == [{"name": "people", "x": 10, "y": 20}]


@pytest.mark.parametrize("analyze", [False, True])
def test_physical_explain_uses_owned_saved_model_and_read_target(setup, analyze):
    api, model, _, calls = setup
    reservations = []
    receipt = SimpleNamespace(id="exec_test", model_dump=lambda **kwargs: {"id": "exec_test"})
    api.app.state.services = replace(api.app.state.services, console=SimpleNamespace(
        reserve_read_target=lambda owner, **kwargs: (reservations.append((owner, kwargs)) or receipt), run=lambda *args, **kwargs: None))
    body = {"expectedRevision": 1, "explore": EXPLORE, "consoleId": "con_" + "a"*32, "analyze": analyze}
    response = api.post(f"/api/v1/schemoo/models/{model['id']}/explain", json=body)
    assert response.status_code == 201, response.text
    assert calls[-1] is True
    sql = reservations[0][1]["statements"][0]
    assert sql.startswith("EXPLAIN (ANALYZE " + ("TRUE" if analyze else "FALSE"))
    assert "FORMAT JSON" in sql
    assert response.json()["plan"]["sql"].startswith("SELECT")
    assert api.post(f"/api/v1/schemoo/models/{model['id']}/explain", json={**body, "analyze": "true"}).status_code == 422
    assert api.post(f"/api/v1/schemoo/models/{model['id']}/explain", json={**body, "expectedRevision": 999}).status_code == 409


def test_logical_connection_types_checked_before_save_and_on_execute(setup):
    api, model, catalog, calls = setup
    url = f"/api/v1/schemoo/models/{model['id']}"
    definition = deepcopy(DEFINITION)
    definition['nodes'].append({'id': 'other', 'table': 'people', 'label': 'Other'})
    edge = {'id': 'drawn', 'kind': 'logical', 'source': 'people', 'target': 'other',
            'sourceColumn': 'id', 'targetColumn': 'name'}
    definition['edges'] = [edge]
    body = {'expectedRevision': 1, 'name': model['name'], 'definition': definition}
    rejected = api.put(url, json=body)
    assert rejected.status_code == 422, rejected.text
    assert 'comparable types' in rejected.text
    assert api.get(url).json()['revision'] == 1
    create = api.post('/api/v1/schemoo/models', json={'name': 'Invalid', 'connectionId': model['connectionId'],
                     'namespace': 'public', 'definition': definition})
    assert create.status_code == 422, create.text
    edge['targetColumn'] = 'id'
    saved = api.put(url, json=body)
    assert saved.status_code == 200, saved.text
    planned = api.post(url + '/plan', json={'expectedRevision': 2, 'explore': {
        'root': 'other', 'fields': [{'table': 'people', 'column': 'name'}]}})
    assert planned.status_code == 200, planned.text
    assert 'LEFT JOIN' in planned.json()['sql']
    catalog['tables'][0]['columns'][0]['dataType'] = 'json'
    rejected = api.post(url + '/plan', json={'expectedRevision': 2, 'explore': EXPLORE})
    assert rejected.status_code == 422, rejected.text


def test_model_deletion_reports_dashboards_and_preserves_source_connection(setup):
    api, model, _, calls = setup
    url = f"/api/v1/schemoo/models/{model['id']}"
    assert api.get(url + "/dependencies").json() == {"dashboards": []}
    created = api.post("/api/v1/schemer/dashboards", json={
        "name": "People overview", "modelId": model["id"], "modelRevision": 1})
    assert created.status_code == 201, created.text
    dashboard = created.json()
    expected = [{"id": dashboard["id"], "name": dashboard["name"]}]
    assert api.get(url + "/dependencies").json() == {"dashboards": expected}
    catalog_calls = len(calls)
    blocked = api.delete(url, params={"expected_revision": 1})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "model_in_use"
    assert blocked.json()["error"]["details"] == {"dashboards": expected}
    assert api.get(url).status_code == 200
    assert api.delete(f"/api/v1/schemer/dashboards/{dashboard['id']}",
                      params={"expectedRevision": 1}).status_code == 204
    assert api.delete(url, params={"expected_revision": 1}).status_code == 204
    assert api.get(url + "/dependencies").status_code == 404
    assert api.get(f"/api/v1/connections/{model['connectionId']}").status_code == 200
    assert len(calls) == catalog_calls  # Dependency checks/deletion never contact the source database.


def test_large_catalog_subset_create_save_patch_and_unselected_drift(setup):
    api, initial, catalog, _ = setup
    catalog["tables"].extend({"name": f"unused_{i}", "primaryKey": ["id"], "columns": [{"name": "id", "dataType": "integer"}]} for i in range(101))
    catalog["tables"][0]["columns"].extend({"name": f"value_{i}", "dataType": "text"} for i in range(1001))
    selected = {**DEFINITION, "exposedFields": [{"table": "people", "column": "name"}],
                "sourceContract": {"scope": "catalog", "tables": [], "relationships": []}}
    response = api.post("/api/v1/schemoo/models", json={"name": "Bounded", "connectionId": initial["connectionId"],
        "namespace": "public", "definition": selected, "explore": EXPLORE, "catalogFingerprint": "one"})
    assert response.status_code == 201, response.text
    model = response.json(); url = f"/api/v1/schemoo/models/{model['id']}"
    contract = model["definition"]["sourceContract"]
    assert contract["scope"] == "model" and len(contract["tables"]) == 1
    assert [column["name"] for column in contract["tables"][0]["columns"]] == ["name"]
    catalog["tables"][1]["columns"][0]["dataType"] = "uuid"
    catalog["tables"][0]["columns"][2]["dataType"] = "boolean"
    catalog["fingerprint"] = "unrelated"
    plan = api.post(url + "/plan", json={"expectedRevision": 1, "explore": EXPLORE})
    assert plan.status_code == 200, plan.text
    assert plan.json()["sourceIssues"] == [] and not any("schema changed" in warning for warning in plan.json()["warnings"])
    forged = deepcopy(model["definition"])
    forged["sourceContract"] = {"scope": "catalog", "tables": [{"name": "invented", "columns": []}], "relationships": []}
    saved = api.put(url, json={"expectedRevision": 1, "name": "Bounded", "definition": forged}).json()
    assert saved["definition"]["sourceContract"] == contract
    patched = api.patch(url, json={"expectedRevision": 2, "expose": [{"table": "people", "column": "value_1000"}]}).json()
    assert [column["name"] for column in patched["definition"]["sourceContract"]["tables"][0]["columns"]] == ["name", "value_1000"]
    catalog["tables"][0]["columns"][-1]["dataType"] = "integer"
    blocked = api.post(url + "/plan", json={"expectedRevision": 3, "explore": EXPLORE})
    assert blocked.status_code == 422 and blocked.json()["error"]["code"] == "model_source_drift"


def test_source_selection_empty_draft_compatibility_inaccessible_stale_and_overlimit_errors(setup):
    api, model, catalog, _ = setup
    base = {"name": "Selection", "connectionId": model["connectionId"], "namespace": "public"}
    empty = api.post("/api/v1/schemoo/models", json=base)
    assert empty.status_code == 201 and empty.json()["definition"]["sourceContract"]["tables"] == []
    stale = api.post("/api/v1/schemoo/models", json={**base, "definition": DEFINITION, "catalogFingerprint": "old"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "model_source_changed"
    inaccessible = {**DEFINITION, "exposedFields": [{"table": "people", "column": "hidden"}]}
    rejected = api.post("/api/v1/schemoo/models", json={**base, "definition": inaccessible})
    assert rejected.status_code == 422 and "unavailable" in rejected.json()["error"]["message"]
    catalog["tables"][0]["columns"].extend({"name": f"value_{i}", "dataType": "text"} for i in range(1001))
    rejected = api.post("/api/v1/schemoo/models", json={**base, "definition": DEFINITION})
    assert rejected.status_code == 422 and rejected.json()["error"]["code"] == "model_source_limit"
    catalog["tables"][0]["columns"] = []
    rejected = api.post("/api/v1/schemoo/models", json={**base, "definition": DEFINITION})
    assert rejected.status_code == 422 and "readable" in rejected.json()["error"]["message"]


def test_saved_source_baseline_cannot_be_forged_or_omitted_and_requires_live_acknowledgement(setup):
    api, model, catalog, _ = setup
    url = f"/api/v1/schemoo/models/{model['id']}"
    catalog["tables"][0]["columns"][1]["nullable"] = False
    forged = deepcopy(model["definition"])
    forged["sourceContract"]["tables"][0]["columns"] = []
    response = api.put(url, json={"expectedRevision": 1, "name": "Still drifting", "definition": forged})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["definition"]["sourceContract"]["tables"][0]["columns"][1]["nullable"] is True
    assert api.post(url + "/plan", json={"expectedRevision": 2, "explore": EXPLORE}).status_code == 422
    acknowledged = deepcopy(saved["definition"])
    acknowledged["sourceContract"]["tables"][0]["columns"][1]["nullable"] = False
    saved = api.put(url, json={"expectedRevision": 2, "name": "Reviewed", "definition": acknowledged})
    assert saved.status_code == 200, saved.text
    assert api.post(url + "/plan", json={"expectedRevision": 3, "explore": EXPLORE}).status_code == 200


def test_legacy_contract_without_scope_is_readable_and_migrates_on_save(setup):
    api, model, _, _ = setup
    from schemii.schemoo.models import ModelDefinition, ModelUpdate
    services = api.app.state.services
    legacy = deepcopy(model["definition"]); legacy["sourceContract"].pop("scope")
    definition = ModelDefinition.model_validate(legacy)
    assert definition.sourceContract.scope == "catalog"
    services.models.update(model["ownerId"], model["id"], ModelUpdate(expected_revision=1, name="Legacy", definition=definition))
    url = f"/api/v1/schemoo/models/{model['id']}"
    read = api.get(url).json()
    assert read["definition"]["sourceContract"]["scope"] == "catalog"
    migrated = api.put(url, json={"expectedRevision": 2, "name": "Selected", "definition": legacy})
    assert migrated.status_code == 200, migrated.text
    assert migrated.json()["definition"]["sourceContract"]["scope"] == "model"


def _incomplete_domain_scope(domain):
    return {"id": "source_picker", "kind": "conditional", "alternatives": [{"id": "choice", "inputs": [
        {"id": "picked", "type": "source", "domain": domain}], "conditions": [
        {"table": "people", "column": "id", "parameterId": "picked", "domain": domain}]}]}


@pytest.mark.parametrize("domain", [
    {}, {"table": "", "column": "", "labelColumn": ""},
    {"table": "people", "column": ""}, {"nodeId": "people", "column": "", "labelColumn": "name"},
])
def test_name_only_save_preserves_persisted_incomplete_domain_draft(setup, domain):
    from schemii.schemoo.models import ModelDefinition, ModelUpdate
    api, model, _, _ = setup
    # Seed a typed saved draft through the repository, representing documents
    # accepted before source contracts became server-derived. PUT only renames it.
    definition = ModelDefinition.model_validate({**model["definition"], "exposedFields": [
        {"table": "people", "column": "id"}], "scopes": [_incomplete_domain_scope(domain)]})
    saved = api.app.state.services.models.update(model["ownerId"], model["id"], ModelUpdate(
        expected_revision=model["revision"], name=model["name"], definition=definition))
    url = f"/api/v1/schemoo/models/{model['id']}"
    before = api.get(url).json()
    response = api.put(url, json={"expectedRevision": saved.revision, "name": "Renamed draft", "definition": before["definition"]})
    assert response.status_code == 200, response.text
    renamed = response.json()
    assert renamed["revision"] == saved.revision + 1 and renamed["name"] == "Renamed draft"
    assert renamed["definition"]["scopes"] == before["definition"]["scopes"]
    assert renamed["definition"]["sourceContract"]["scope"] == "model"
    assert [table["name"] for table in renamed["definition"]["sourceContract"]["tables"]] == ["people"]
    assert api.get(url).json() == renamed


@pytest.mark.parametrize("method", ["PUT", "PATCH"])
def test_incomplete_domain_can_bind_through_put_or_patch_and_adds_server_dependencies(setup, method):
    api, model, catalog, _ = setup
    catalog["tables"].append({"name": "lookup", "columns": [
        {"name": "id", "dataType": "uuid"}, {"name": "label", "dataType": "text"}, {"name": "unused", "dataType": "text"}]})
    definition = {**model["definition"], "exposedFields": [{"table": "people", "column": "id"}],
                  "scopes": [_incomplete_domain_scope({})]}
    url = f"/api/v1/schemoo/models/{model['id']}"
    incomplete = api.put(url, json={"expectedRevision": model["revision"], "name": "In progress", "definition": definition})
    assert incomplete.status_code == 200, incomplete.text
    saved = incomplete.json()
    assert [table["name"] for table in saved["definition"]["sourceContract"]["tables"]] == ["people"]
    scope = _incomplete_domain_scope({"table": "lookup", "column": "id", "labelColumn": "label"})
    if method == "PUT":
        bound_definition = {**saved["definition"], "scopes": [scope]}
        # A forged empty contract cannot omit the newly bound physical lookup.
        bound_definition["sourceContract"] = {"scope": "model", "tables": [], "relationships": []}
        response = api.put(url, json={"expectedRevision": saved["revision"], "name": "Bound", "definition": bound_definition})
    else:
        response = api.patch(url, json={"expectedRevision": saved["revision"], "scopes": {"upsert": [scope]}})
    assert response.status_code == 200, response.text
    bound = response.json()
    tables = {table["name"]: table for table in bound["definition"]["sourceContract"]["tables"]}
    assert set(tables) == {"people", "lookup"}
    assert [column["name"] for column in tables["lookup"]["columns"]] == ["id", "label"]
    assert bound["definition"]["exposedFields"] == saved["definition"]["exposedFields"]
    # Domain references are dependencies even when absent from nodes/outputs.
    catalog["tables"][1]["columns"][1]["dataType"] = "uuid"
    drift = api.post(url + "/plan", json={"expectedRevision": bound["revision"], "explore": {"root": "people", "fields": [{"table": "people", "column": "id"}]}})
    assert drift.status_code == 422 and drift.json()["error"]["code"] == "model_source_drift"
    assert any(issue["table"] == "lookup" and issue.get("column") == "label" for issue in drift.json()["error"]["details"]["issues"])


@pytest.mark.parametrize("domain", [
    {"table": "missing", "column": ""}, {"nodeId": "missing", "column": ""},
    {"table": "people", "column": "missing"}, {"table": "people", "column": "", "labelColumn": "missing"},
])
def test_incomplete_domain_saves_still_reject_nonempty_unknown_references(setup, domain):
    api, model, _, _ = setup
    url = f"/api/v1/schemoo/models/{model['id']}"
    definition = {**model["definition"], "scopes": [_incomplete_domain_scope(domain)]}
    rejected = api.put(url, json={"expectedRevision": model["revision"], "name": "Must not persist", "definition": definition})
    assert rejected.status_code == 422, rejected.text
    assert api.get(url).json() == model


def test_catalog_selected_namespaces_preserve_composite_order_and_filter_revoked_second_pair():
    from schemii.schemoo.domain import qualified_relationship_id, legacy_relationship_id
    profile = SimpleNamespace(revision=1, database="warehouse", owner_id="manager")
    @contextmanager
    def use(*args):
        yield profile
    column = lambda name: SimpleNamespace(name=name, model_dump=lambda **kwargs:{"name":name,"dataType":"integer","nullable":False})
    relation = SimpleNamespace(name="same_fk", source_namespace="one", source_table="same", source_columns=("tenant","id"),
                               target_namespace="two", target_table="same", target_columns=("tenant","id"))
    seen = []
    def introspect(connection, namespace):
        seen.append(namespace)
        return SimpleNamespace(tables=[SimpleNamespace(name="same", columns=[column("tenant"),column("id")])],
                               relationships=[relation] if namespace=="one" else [], fingerprint=namespace)
    grants = {name:{("same","tenant"),("same","id")} for name in ["one","two"]}
    services = SimpleNamespace(connections=SimpleNamespace(get=lambda *args:profile,use=use),
        postgres=SimpleNamespace(introspect=introspect,readable_columns=lambda connection,namespace:grants[namespace]))
    adapter = ModelCatalogs()
    catalog = adapter.get(services,"viewer","connection","one",namespaces=["one","two"])
    assert [(table["namespace"],table["name"]) for table in catalog["tables"]] == [("one","same"),("two","same")]
    relationship = catalog["relationships"][0]
    assert relationship["columns"] == [{"source":"tenant","target":"tenant"},{"source":"id","target":"id"}]
    assert relationship["id"] == qualified_relationship_id("one","same","same_fk")
    assert relationship["id"] != legacy_relationship_id(relationship)
    assert "sourceColumn" not in relationship
    assert adapter.get(services,"viewer","connection","one")["relationships"] == []
    grants["two"].remove(("same","id"))
    assert adapter.get(services,"viewer","connection","one",namespaces=["two"])["relationships"] == []
    assert seen == ["one","two","one","one","two"]


def test_qualified_model_create_save_patch_and_scalar_domain_ambiguity_are_identity_safe(setup):
    from schemii.schemoo.service import domain_query
    from schemii.common.api.errors import ApiProblem
    api, original, source, calls = setup
    source["namespace"] = "public"
    source["tables"].append({"namespace":"other", "name":"people", "columns":[
        {"name":"id","dataType":"uuid"},{"name":"name","dataType":"integer"}]})
    source["relationships"] = [{"id":"cross", "name":"cross", "sourceTable":"people", "sourceNamespace":"public",
        "targetTable":"people", "targetNamespace":"other", "columns":[{"source":"id","target":"id"}]}]
    get = api.app.state.services.model_catalogs.get
    requested=[]
    def get_selected(*args, namespaces=None, **kwargs):
        requested.append(namespaces)
        return get(*args,**kwargs)
    api.app.state.services = replace(api.app.state.services,model_catalogs=SimpleNamespace(get=get_selected))
    definition = {"root":"stable", "nodes":[{"id":"stable","table":"people","label":"Original"},
        {"id":"role","namespace":"other","table":"people","label":"Other"}],
        "edges":[{"id":"old_edge","relationshipId":"cross","source":"stable","target":"role"}], "scopes":[],
        "exposedFields":[{"table":"stable","column":"name"},{"table":"role","column":"name"}]}
    response=api.post('/api/v1/schemoo/models',json={"name":"Qualified","connectionId":original["connectionId"],"namespace":"public","definition":definition})
    assert response.status_code==201,response.text
    model=response.json(); url=f"/api/v1/schemoo/models/{model['id']}"
    assert requested[-1]==['other','public']
    assert model['definition']['nodes'][0]['id']=='stable'
    assert model['definition']['nodes'][0].get('namespace') is None
    assert len(model['definition']['sourceContract']['tables'])==2
    saved=api.put(url,json={'expectedRevision':1,'name':'Renamed','definition':model['definition']})
    assert saved.status_code==200,saved.text
    assert api.get(url).json()['definition']==model['definition']
    hidden=api.patch(url,json={'expectedRevision':2,'hide':[{'table':'role','column':'name'}]})
    assert hidden.status_code==200,hidden.text
    assert hidden.json()['definition']['sourceContract']['tables'][1]['columns']==[{'name':'id','dataType':'uuid','nullable':True}]
    lookup_definition=deepcopy(definition)
    lookup_definition['scopes']=[{'id':'filter','kind':'conditional','alternatives':[{'id':'choose','inputs':[{'id':'value','type':'text'}],
        'conditions':[{'table':'stable','column':'id','parameterId':'value'}]}]}]
    from schemii.schemoo.models import ModelDefinition
    scoped=SimpleNamespace(definition=ModelDefinition.model_validate(lookup_definition))
    body=SimpleNamespace(scope_id='filter',alternative_id='choose',parameter_id='value',search='')
    assert '"other"."people"' in domain_query(source,scoped,body)['sql']
    source['relationships'][0]['columns'].append({'source':'name','target':'name'})
    with pytest.raises(ApiProblem,match='explicit domain'):
        domain_query(source,scoped,body)


@pytest.fixture
def namespace_catalog(setup):
    """Production cohort adapter over immutable, authorized metadata doubles."""
    from schemii.common.postgres.models import PostgresColumn
    api, model, _, _ = setup
    revisions = {"public": "one", "other": "one"}
    grants = {name: {("people", "id"), ("people", "name")} for name in revisions}
    observations = []

    def introspect(profile, namespace):
        observations.append((profile.id, namespace))
        return SimpleNamespace(tables=[SimpleNamespace(name="people", columns=[
            PostgresColumn(name="id", ordinal=1, data_type="uuid", nullable=True),
            PostgresColumn(name="name", ordinal=2, data_type="text", nullable=True)])],
            relationships=[], fingerprint=f"{namespace}:{revisions[namespace]}")

    services = replace(api.app.state.services, model_catalogs=ModelCatalogs(),
        postgres=SimpleNamespace(introspect=introspect, readable_columns=lambda profile, namespace: grants[namespace]))
    api.app.state.services = services
    return SimpleNamespace(api=api, model=model, services=services, revisions=revisions,
                           grants=grants, observations=observations)


def test_creation_compares_the_reviewed_namespace_cohort_but_saves_only_selected_dependencies(namespace_catalog):
    f = namespace_catalog
    connection = f.model["connectionId"]
    reviewed = f.api.get("/api/v1/schemoo/catalog", params={
        "connection_id": connection, "namespace": "public", "namespaces": ["other", "public"]}).json()
    payload = {"name": "Public selection", "connectionId": connection, "namespace": "public",
        "definition": DEFINITION, "catalogFingerprint": reviewed["fingerprint"], "catalogNamespaces": reviewed["namespaces"]}
    f.observations.clear()
    response = f.api.post("/api/v1/schemoo/models", json=payload)
    assert response.status_code == 201, response.text
    created = response.json()
    assert [namespace for _, namespace in f.observations] == ["other", "public"]
    assert {table["namespace"] for table in created["definition"]["sourceContract"]["tables"]} == {"public"}
    assert "catalogNamespaces" not in created
    assert "catalogNamespaces" not in f.api.get(f"/api/v1/schemoo/models/{created['id']}").json()
    f.revisions["other"] = "changed"
    plan = f.api.post(f"/api/v1/schemoo/models/{created['id']}/plan", json={"expectedRevision": 1, "explore": EXPLORE})
    assert plan.status_code == 200 and plan.json()["sourceIssues"] == []
    rejected = f.api.post("/api/v1/schemoo/models", json=payload)
    assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "model_source_changed"


def test_creation_cohort_preserves_legacy_scope_caps_and_fresh_grant_checks(namespace_catalog):
    f = namespace_catalog
    connection = f.model["connectionId"]
    single = f.api.get("/api/v1/schemoo/catalog", params={"connection_id": connection, "namespace": "public"}).json()
    base = {"name": "Legacy", "connectionId": connection, "namespace": "public", "definition": DEFINITION,
            "catalogFingerprint": single["fingerprint"]}
    assert f.api.post("/api/v1/schemoo/models", json=base).status_code == 201
    for namespaces in [[], [""] , ["x" * 64], [f"schema_{index}" for index in range(17)],
                       [f"schema_{index}" for index in range(16)]]:
        rejected = f.api.post("/api/v1/schemoo/models", json={**base, "catalogNamespaces": namespaces})
        assert rejected.status_code == 422, rejected.text
    draft = deepcopy(DEFINITION)
    draft["nodes"][0]["namespace"] = "other"
    outside = f.api.post("/api/v1/schemoo/models", json={**base, "definition": draft, "catalogNamespaces": ["public"]})
    assert outside.status_code == 409 and outside.json()["error"]["code"] == "model_source_changed"
    reviewed = f.api.get("/api/v1/schemoo/catalog", params={"connection_id": connection, "namespace": "public", "namespaces": ["other"]}).json()
    f.grants["other"] = set()
    rejected = f.api.post("/api/v1/schemoo/models", json={**base, "definition": draft,
        "catalogNamespaces": reviewed["namespaces"], "catalogFingerprint": reviewed["fingerprint"]})
    assert rejected.status_code == 422 and rejected.json()["error"]["code"] == "invalid_model_source"


def namespace_draft(model):
    draft = deepcopy(model["definition"])
    draft["nodes"].append({"id": "new_role", "namespace": "other", "table": "people", "label": "Other people"})
    draft["edges"].append({"id": "cross", "kind": "logical", "source": "people", "target": "new_role",
                           "columns": [{"source": "id", "target": "id"}]})
    draft["exposedFields"] = [{"table": node, "column": "name"} for node in ["people", "new_role"]]
    return draft


def test_validation_reads_proposed_namespace_dependencies_without_saving(namespace_catalog):
    f = namespace_catalog
    draft = namespace_draft(f.model)
    body = {"expectedRevision": 1, "definition": draft,
            "explore": {"root": "people", "fields": [{"table": "new_role", "column": "name"}]}}
    url = f"/api/v1/schemoo/models/{f.model['id']}"
    f.observations.clear()
    response = f.api.post(url + "/validate", json=body)
    assert response.status_code == 200, response.text
    assert '"other"."people"' in response.json()["sql"]
    assert [namespace for _, namespace in f.observations] == ["other", "public"]
    assert f.api.get(url).json() == f.model
    f.grants["other"] = set()
    rejected = f.api.post(url + "/validate", json=body)
    assert rejected.status_code == 422 and rejected.json()["error"]["code"] == "model_source_drift"
    assert f.api.get(url).json() == f.model
