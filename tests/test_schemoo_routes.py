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


def test_drift_preserves_saved_rules_and_unrelated_changes_warn(setup):
    api, model, catalog, _ = setup
    url = f"/api/v1/schemoo/models/{model['id']}"
    catalog["fingerprint"] = "two"
    plan = api.post(url + "/plan", json={"expectedRevision": 1, "explore": EXPLORE})
    assert any("schema changed" in w for w in plan.json()["warnings"])
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
    console = SimpleNamespace(reserve_read_target=lambda owner, **kwargs: (reservations.append(kwargs) or receipt), run=lambda *args: None)
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
    service = SimpleNamespace(reserve_read_target=lambda owner, **kwargs: (reservations.append((owner, kwargs)) or receipt), run=lambda *args: None)
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
            views=[SimpleNamespace(name="personnel_summary", columns=[], query_definition="SELECT 1")],
            materialized_views=[SimpleNamespace(name="personnel_summary_reporting_mv", columns=[], query_definition="SELECT 1", populated=True)],
            relationships=[],
            fingerprint="one",
        )
    services = SimpleNamespace(connections=SimpleNamespace(get=lambda *args: profile, use=use), postgres=SimpleNamespace(introspect=introspect))
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
        reserve_read_target=lambda owner, **kwargs: (reservations.append((owner, kwargs)) or receipt), run=lambda *args: None))
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
