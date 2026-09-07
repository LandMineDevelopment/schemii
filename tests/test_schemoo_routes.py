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
        return SimpleNamespace(tables=[], relationships=[], fingerprint="one")
    services = SimpleNamespace(connections=SimpleNamespace(get=lambda *args: profile, use=use), postgres=SimpleNamespace(introspect=introspect))
    cache = ModelCatalogs(maximum_entries=2)
    cache.get(services, "one", "connection", "public")
    cache.get(services, "one", "connection", "public")
    cache.get(services, "two", "connection", "public")
    cache.get(services, "one", "connection", "public", fresh=True)
    profile.revision = 2
    cache.get(services, "one", "connection", "public")
    assert len(counts) == 4
    assert len(cache._cache) == 2


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
