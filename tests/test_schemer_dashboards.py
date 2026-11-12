from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from schemii.schemer.dashboard_models import DashboardCreate, DashboardUpdate, DashboardTile
from schemii.schemer.dashboard_store import (InMemoryDashboardRepository, DashboardNotFoundError,
                                             DashboardConflictError)

MODEL = "model_" + "a" * 32
FIELD = {"table": "people", "column": "id"}


def create(**changes):
    return DashboardCreate.model_validate({"name": "People", "modelId": MODEL,
                                           "modelRevision": 1, **changes})


def test_owner_isolation_revisions_and_copy_boundaries():
    repo = InMemoryDashboardRepository()
    saved = repo.create("alice", create())
    assert repo.list("bob") == []
    with pytest.raises(DashboardNotFoundError):
        repo.get("bob", saved.id)
    saved.name = "unsaved mutation"
    assert repo.get("alice", saved.id).name == "People"
    request = DashboardUpdate(**create(name="Updated").model_dump(), expected_revision=1)
    assert repo.update("alice", saved.id, request).revision == 2
    with pytest.raises(DashboardConflictError):
        repo.update("alice", saved.id, request)
    with pytest.raises(DashboardConflictError):
        repo.delete("alice", saved.id, 1)
    repo.delete("alice", saved.id, 2)
    assert repo.list("alice") == []


def test_dashboard_is_bound_to_one_model():
    repo = InMemoryDashboardRepository()
    saved = repo.create("alice", create())
    with pytest.raises(ValueError, match="cannot switch"):
        repo.update("alice", saved.id, DashboardUpdate(**create(modelId="model_" + "b"*32).model_dump(),
                                                       expected_revision=1))


@pytest.mark.parametrize("changes", [
    {"kind": "detail"},
    {"kind": "bar", "measures": [{**FIELD, "aggregate": "count"}]},
    {"kind": "kpi", "dimensions": [FIELD], "measures": [{**FIELD, "aggregate": "count"}]},
    {"kind": "aggregate", "measures": []},
    {"kind": "detail", "detailFields": [{**FIELD, "aggregate": "count"}]},
    {"kind": "detail", "detailFields": [FIELD], "limit": 101},
    {"kind": "detail", "detailFields": [FIELD], "rows": [[1]]},
])
def test_incomplete_and_invalid_tiles_rejected(changes):
    with pytest.raises(ValidationError):
        DashboardTile.model_validate({"id": "tile", "title": "People", **changes})


def test_configuration_without_slicer_values_and_unique_tile_ids():
    tile = {"id": "tile", "title": "People", "kind": "detail", "detailFields": [FIELD]}
    assert create(tiles=[tile]).selections == {}
    assert create(tiles=[tile]).optional_filters == []
    with pytest.raises(ValidationError, match="unique"):
        create(tiles=[tile, tile])


def test_dashboard_serialization_uses_public_names():
    saved = InMemoryDashboardRepository().create("alice", create(tiles=[{
        "id": "tile", "title": "People", "kind": "detail", "detailFields": [FIELD]}]))
    data = saved.model_dump(mode="json", by_alias=True)
    assert data["modelId"] == MODEL
    assert data["tiles"][0]["detailFields"] == [{**FIELD, "aggregate": "none"}]
    assert "rows" not in data["tiles"][0]


def test_save_checks_exposure_types_and_optional_scope_ownership(monkeypatch):
    from schemii.schemer import dashboard_routes as routes
    from schemii.schemoo.models import ModelDefinition
    from schemii.common.api.errors import ApiProblem
    services = SimpleNamespace(admin_config=SimpleNamespace(console=SimpleNamespace(maximum_statements_per_run=20)))
    model = SimpleNamespace(definition=ModelDefinition.model_validate({
        "root": "people", "nodes": [{"id": "people", "table": "people", "label": "People"}],
        "exposedFields": [FIELD], "scopes": [{"id": "required", "label": "Organization", "kind": "required"},
            {"id": "optional", "label": "Region", "kind": "conditional", "requirement": "optional"}]}),
        catalog_fingerprint="")
    monkeypatch.setattr(routes, "load_model", lambda *args: model)
    monkeypatch.setattr(routes, "model_catalog", lambda *args: {
        "fingerprint": "", "namespace": "public", "tables": [{"name": "people", "columns": [
            {"name": "id", "dataType": "uuid"}, {"name": "secret", "dataType": "text"}]}], "relationships": []})
    tile = {"id": "tile", "title": "People", "kind": "detail", "detailFields": [FIELD]}
    routes.validate_dashboard(services, "alice", create(tiles=[tile]))
    # Report control validation uses output contracts without asking for runtime
    # values of required model slicers.
    aggregate = {"id": "count", "title": "Count", "kind": "aggregate", "measures": [{**FIELD, "aggregate": "count"}],
                 "sort": [{**FIELD, "aggregate": "count", "direction": "desc"}], "topN": 5,
                 "aggregateFilters": [{**FIELD, "aggregate": "count", "operator": "gte", "value": 2}]}
    routes.validate_dashboard(services, "alice", create(tiles=[aggregate]))
    with pytest.raises(ApiProblem, match="sort output"):
        routes.validate_dashboard(services, "alice", create(tiles=[{**aggregate, "sort": [{**FIELD, "column": "secret"}]}]))
    routes.validate_dashboard(services, "alice", create(tiles=[tile], optionalFilters=["optional"],
        selections={"optional": {"active": True}}))
    # Saved historical dashboards remain readable, but a new save must explicitly
    # repair stale references, including inactive filter choices.
    with pytest.raises(ValueError, match="always-evaluated"):
        routes.validate_dashboard(services, "alice", create(tiles=[tile], selections={"removed": {}}))
    with pytest.raises(ValueError, match="marked optional"):
        routes.validate_dashboard(services, "alice", create(tiles=[tile], optionalFilters=["required"]))
    with pytest.raises(ValueError, match="exposed optional"):
        routes.validate_dashboard(services, "alice", create(tiles=[tile], selections={"optional": {"active": True}}))
    with pytest.raises(ValueError, match="conditional"):
        routes.validate_dashboard(services, "alice", create(tiles=[{**tile, "selections": {"required": {}}}]))
    with pytest.raises(ApiProblem):
        routes.validate_dashboard(services, "alice", create(tiles=[{**tile, "detailFields": [{**FIELD, "column": "secret"}]}]))
    with pytest.raises(ValueError, match="numeric"):
        routes.validate_dashboard(services, "alice", create(tiles=[{
            "id": "tile", "title": "People", "kind": "kpi", "measures": [{**FIELD, "aggregate": "sum"}]}]))


    with pytest.raises(ValueError, match="numeric"):
        routes.validate_dashboard(services, "alice", create(tiles=[{
            "id": "tile", "title": "People", "kind": "bar", "dimensions": [FIELD],
            "measures": [{**FIELD, "aggregate": "min"}]}]))


def test_dashboard_api_round_trip_and_owner_isolation():
    from fastapi.testclient import TestClient
    from schemii.main import create_app, create_services
    from schemii.common.metadata.models import Principal, get_current_principal
    services = create_services()
    app = create_app(services=services)
    with TestClient(app, base_url="http://localhost") as client:
        connection = client.post("/api/v1/connections", json={"name": "Warehouse", "host": "localhost",
            "database": "warehouse", "username": "reader"}).json()
        # Empty authored models need no catalog access to create a dashboard.
        from schemii.schemoo.models import ModelCreate
        model = services.models.create("user_local_prototype", ModelCreate(name="People",
            connection_id=connection["id"], database="warehouse", namespace="public"))
        body = {"name": "Dashboard", "modelId": model.id, "modelRevision": 1}
        response = client.post("/api/v1/schemer/dashboards", json=body)
        assert response.status_code == 201, response.text
        saved = response.json()
        path = "/api/v1/schemer/dashboards/" + saved["id"]
        assert client.get(path).json()["name"] == "Dashboard"
        update = {**body, "name": "Renamed", "expectedRevision": 1}
        assert client.put(path, json=update).json()["revision"] == 2
        assert client.put(path, json=update).status_code == 409
        app.dependency_overrides[get_current_principal] = lambda: Principal(
            user_id="someone_else", authentication_source="local_prototype")
        assert client.get(path).status_code == 404
        assert client.get("/api/v1/schemer/dashboards").json() == {"dashboards": []}
        assert client.post("/api/v1/schemer/dashboards", json=body).status_code == 404
        app.dependency_overrides.clear()
        assert client.delete(path + "?expectedRevision=1").status_code == 409
        assert client.delete(path + "?expectedRevision=2").status_code == 204
        assert client.get(path).status_code == 404


def test_context_loads_current_model_without_updating_pinned_dashboard(monkeypatch):
    from fastapi.testclient import TestClient
    from schemii.main import create_app, create_services
    from schemii.common.metadata.models import Principal, get_current_principal
    from schemii.schemoo import service as model_service
    from schemii.schemoo.models import ModelCreate, ModelUpdate

    services = create_services()
    app = create_app(services=services)
    owner = "user_local_prototype"
    with TestClient(app, base_url="http://localhost") as client:
        connection = client.post("/api/v1/connections", json={
            "name": "Warehouse", "host": "localhost", "database": "warehouse", "username": "reader",
        }).json()
        model = services.models.create(owner, ModelCreate(
            name="People", connection_id=connection["id"], database="warehouse", namespace="public",
        ))
        for _ in range(3):
            model = services.models.update(owner, model.id, ModelUpdate(
                expected_revision=model.revision, name=model.name, definition=model.definition,
            ))
        assert model.revision == 4
        body = {"name": "Dashboard", "modelId": model.id, "modelRevision": 4}
        created = client.post("/api/v1/schemer/dashboards", json=body)
        assert created.status_code == 201, created.text
        dashboard = created.json()
        path = "/api/v1/schemer/dashboards/" + dashboard["id"]

        model = services.models.update(owner, model.id, ModelUpdate(
            expected_revision=4, name="People v5", definition=model.definition,
        ))
        catalog_requests = []

        def catalog(_services, _owner, current, *, fresh=False):
            catalog_requests.append((current.revision, fresh))
            return {"database": "warehouse", "namespace": "public", "tables": [], "relationships": []}

        monkeypatch.setattr(model_service, "model_catalog", catalog)
        context = client.get(path + "/context")
        assert context.status_code == 200, context.text
        assert context.json()["model"]["revision"] == 5
        assert context.json()["model"]["name"] == "People v5"
        assert context.json()["permissions"]["edit"] is True
        assert catalog_requests == [(5, True)]
        assert client.get(path).json()["modelRevision"] == 4

        stale_update = client.put(path, json={**body, "expectedRevision": dashboard["revision"]})
        assert stale_update.status_code == 409
        assert stale_update.json()["error"]["code"] == "model_revision_conflict"
        stale_lookup = client.post(path + "/parameter-values", json={
            "expectedRevision": 4, "scopeId": "unused", "alternativeId": "unused",
            "parameterId": "unused", "consoleId": "con_" + "a" * 32,
        })
        assert stale_lookup.status_code == 409
        assert stale_lookup.json()["error"]["code"] == "model_revision_conflict"
        updated = client.put(path, json={**body, "modelRevision": 5,
                                         "expectedRevision": dashboard["revision"]})
        assert updated.status_code == 200, updated.text
        assert updated.json()["modelRevision"] == 5
        assert updated.json()["revision"] == dashboard["revision"] + 1

        app.dependency_overrides[get_current_principal] = lambda: Principal(
            user_id="someone_else", authentication_source="local_prototype")
        assert client.get(path + "/context").status_code == 404
        app.dependency_overrides.clear()


class RecordingCursor:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, statement, values):
        self.calls.append((statement, values))

    def fetchone(self):
        return next(self.rows)


class RecordingConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.exited = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.exited = True

    def cursor(self):
        return self._cursor


def test_postgres_update_locks_owner_row_and_persists_only_configuration():
    from schemii.schemer.dashboard_store import PostgresDashboardRepository
    saved = InMemoryDashboardRepository().create("alice", create())
    updated = saved.model_copy(update={"name": "Renamed", "revision": 2})
    cursor = RecordingCursor([saved.model_dump(), updated.model_dump()])
    connection = RecordingConnection(cursor)
    store = PostgresDashboardRepository(lambda: connection)
    result = store.update("alice", saved.id, DashboardUpdate(**create(name="Renamed").model_dump(), expected_revision=1))
    assert result.revision == 2
    assert cursor.calls[0][0].endswith("FOR UPDATE")
    assert cursor.calls[0][1] == ("alice", saved.id)
    assert cursor.calls[1][1][-2:] == ("alice", saved.id)
    assert cursor.calls[1][1][2].obj == []
    assert cursor.calls[1][1][3].obj == {}
    assert cursor.calls[1][1][4].obj == []
    assert connection.exited


def test_postgres_conflict_never_writes_and_missing_owner_is_not_found():
    from schemii.schemer.dashboard_store import PostgresDashboardRepository
    saved = InMemoryDashboardRepository().create("alice", create())
    cursor = RecordingCursor([saved.model_dump()])
    store = PostgresDashboardRepository(lambda: RecordingConnection(cursor))
    with pytest.raises(DashboardConflictError):
        store.delete("alice", saved.id, 2)
    assert len(cursor.calls) == 1
    missing = RecordingCursor([None])
    with pytest.raises(DashboardNotFoundError):
        PostgresDashboardRepository(lambda: RecordingConnection(missing)).get("bob", saved.id)
    assert missing.calls[0][1] == ("bob", saved.id)


def test_postgres_storage_errors_do_not_leak_driver_details():
    from schemii.schemer.dashboard_store import PostgresDashboardRepository, DashboardStorageUnavailableError
    def unavailable():
        raise RuntimeError("private driver connection details")
    with pytest.raises(DashboardStorageUnavailableError, match="temporarily unavailable") as caught:
        PostgresDashboardRepository(unavailable).get("alice", "dashboard")
    assert "private" not in str(caught.value)


@pytest.mark.parametrize("target", ["dashboard", "tile"])
@pytest.mark.parametrize("selection,message", [
    ({"alternativeId": "removed", "active": False}, "unavailable model alternative"),
    ({"alternativeId": "choose", "values": {"removed": "old"}}, "unavailable model input"),
    ({"values": {"removed": "old"}}, "unavailable model input"),
])
def test_save_rejects_stale_inactive_selection_references(monkeypatch, target, selection, message):
    from schemii.schemer import dashboard_routes as routes
    from schemii.schemoo.models import ModelDefinition

    scopes = [{"id": key, "kind": kind, "alternatives": [{
        "id": "choose", "inputs": [{"id": "value", "type": "text"}], "conditions": [],
    }]} for key, kind in [("required", "required"), ("conditional", "conditional")]]
    model = SimpleNamespace(definition=ModelDefinition.model_validate({
        "root": "people", "nodes": [{"id": "people", "table": "people", "label": "People"}],
        "scopes": scopes,
    }), catalog_fingerprint="")
    services = SimpleNamespace(admin_config=SimpleNamespace(console=SimpleNamespace(maximum_statements_per_run=20)))
    monkeypatch.setattr(routes, "load_model", lambda *args: model)
    monkeypatch.setattr(routes, "model_catalog", lambda *args: {
        "fingerprint": "", "tables": [{"name": "people", "columns": [{"name": "id", "dataType": "integer"}]}], "relationships": [],
    })
    tile = {"id": "tile", "title": "People", "kind": "detail", "detailFields": [FIELD]}
    changes = {"selections": {"required": selection}} if target == "dashboard" else {
        "tiles": [{**tile, "selections": {"conditional": selection}}],
    }
    with pytest.raises(ValueError, match=message):
        routes.validate_dashboard(services, "alice", create(**changes))
    # A model input can still be left empty for the viewer to enter later.
    if target == "dashboard":
        routes.validate_dashboard(services, "alice", create(selections={"required": {"alternativeId": "choose"}}))
    else:
        routes.validate_dashboard(services, "alice", create(tiles=[{**tile, "selections": {"conditional": {"alternativeId": "choose"}}}]))
    # Other valid options retain their inputs while the viewer switches back
    # and forth; both dashboard slicers and tile overrides may save that state.
    for scope in model.definition.scopes:
        scope.alternatives.append(type(scope.alternatives[0]).model_validate({
            "id": "other", "inputs": [{"id": "retained", "type": "text"}], "conditions": [],
        }))
    retained = {"alternativeId": "choose", "values": {"value": "current", "retained": "other option"}}
    if target == "dashboard":
        routes.validate_dashboard(services, "alice", create(selections={"required": retained}))
    else:
        routes.validate_dashboard(services, "alice", create(tiles=[{**tile, "selections": {"conditional": retained}}]))


@pytest.mark.parametrize("change", ["removed_field", "removed_source"])
def test_dashboard_repairs_and_model_upgrade_are_one_atomic_save(monkeypatch, change):
    from fastapi.testclient import TestClient
    from schemii.main import create_app, create_services
    from schemii.schemer import dashboard_routes as routes
    from schemii.schemer.dashboard_store import DashboardStorageUnavailableError
    from schemii.schemoo.models import ModelCreate, ModelDefinition, ModelUpdate

    services = create_services()
    app = create_app(services=services)
    owner = "user_local_prototype"
    catalog = {"fingerprint": "", "namespace": "public", "tables": [{"name": "people", "columns": [
        {"name": "id", "dataType": "integer"}, {"name": "name", "dataType": "text"},
    ]}], "relationships": []}
    monkeypatch.setattr(routes, "model_catalog", lambda *args: catalog)
    definition = ModelDefinition.model_validate({
        "root": "people", "nodes": [{"id": "people", "table": "people", "label": "People"},
            {"id": "other", "table": "people", "label": "Other role"}],
        "edges": [{"id": "join", "kind": "logical", "source": "people", "sourceColumn": "id",
            "target": "other", "targetColumn": "id", "enabled": True}],
        "exposedFields": [FIELD, {"table": "people", "column": "name"}, {"table": "other", "column": "id"}],
    })
    with TestClient(app, base_url="http://localhost") as client:
        connection = client.post("/api/v1/connections", json={"name": "Warehouse", "host": "localhost",
            "database": "warehouse", "username": "reader"}).json()
        model = services.models.create(owner, ModelCreate(name="People", connection_id=connection["id"],
            database="warehouse", namespace="public"))
        model = services.models.update(owner, model.id, ModelUpdate(expected_revision=1, name="People", definition=definition))
        old_field = {"table": "other", "column": "id"} if change == "removed_source" else {"table": "people", "column": "name"}
        original_tile = {"id": "tile", "title": "People", "kind": "detail", "detailFields": [old_field]}
        body = {"name": "Repairable", "modelId": model.id, "modelRevision": 2, "tiles": [original_tile]}
        created = client.post("/api/v1/schemer/dashboards", json=body)
        assert created.status_code == 201, created.text
        saved = created.json()
        path = "/api/v1/schemer/dashboards/" + saved["id"]
        if change == "removed_source":
            definition = definition.model_copy(update={"nodes": definition.nodes[:1], "edges": [], "exposedFields": definition.exposedFields[:2]})
        else:
            definition = definition.model_copy(update={"exposedFields": [definition.exposedFields[0], definition.exposedFields[2]]})
        model = services.models.update(owner, model.id, ModelUpdate(expected_revision=2, name="People", definition=definition))
        request = {**body, "expectedRevision": saved["revision"], "modelRevision": model.revision}
        failed = client.put(path, json=request)
        assert failed.status_code == 422, failed.text
        assert client.get(path).json() == saved
        repaired = {**request, "tiles": [{**original_tile, "detailFields": [FIELD]}]}
        assert client.put(path, json={**repaired, "modelRevision": 2}).status_code == 409
        assert client.get(path).json() == saved
        update = services.dashboards.update

        def unavailable(*args):
            raise DashboardStorageUnavailableError("Storage temporarily unavailable")

        monkeypatch.setattr(services.dashboards, "update", unavailable)
        assert client.put(path, json=repaired).status_code == 503
        assert client.get(path).json() == saved
        monkeypatch.setattr(services.dashboards, "update", update)
        response = client.put(path, json=repaired)
        assert response.status_code == 200, response.text
        upgraded = response.json()
        assert upgraded["revision"] == saved["revision"] + 1
        assert upgraded["modelRevision"] == model.revision
        assert upgraded["tiles"][0]["detailFields"] == [{**FIELD, "aggregate": "none"}]
        assert client.put(path, json=repaired).json()["error"]["code"] == "dashboard_revision_conflict"
        assert client.get(path).json() == upgraded


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "10", 2147483648])
def test_topn_requires_a_strict_positive_bounded_integer(value):
    with pytest.raises(ValidationError):
        DashboardTile.model_validate({"id": "tile", "title": "People", "kind": "detail", "detailFields": [FIELD], "topN": value})


def test_report_controls_roundtrip_independently_of_batch_size_and_reject_bad_predicate_values():
    body = {"id": "tile", "title": "People", "kind": "aggregate", "measures": [{**FIELD, "aggregate": "count"}],
            "sort": [{**FIELD, "aggregate": "count", "direction": "desc"}], "topN": 500,
            "aggregateFilters": [{**FIELD, "aggregate": "count", "operator": "gte", "value": 20}], "limit": 1}
    tile = DashboardTile.model_validate(body)
    assert tile.top_n == 500 and tile.limit == 1 and tile.sort[0].nulls == "last"
    assert DashboardTile.model_validate(tile.model_dump(by_alias=True)) == tile
    for item in [{"operator": "gt", "value": None}, {"operator": "is_null", "value": 1}, {"operator": "eq", "value": float("nan")}, {"operator": "eq", "value": [1]}]:
        with pytest.raises(ValidationError):
            DashboardTile.model_validate({**body, "aggregateFilters": [{**FIELD, "aggregate": "count", **item}]})
    with pytest.raises(ValidationError, match="unique"):
        DashboardTile.model_validate({**body, "sort": body["sort"] * 2})


@pytest.mark.parametrize("value", [9007199254740993, -9007199254740993, 0.1, -0.125, 1e-8])
def test_backend_numeric_predicate_json_types_are_not_restricted_by_editor_precision(value):
    import json
    from schemii.schemer.dashboard_models import AggregateFilter
    payload = {**FIELD, "aggregate": "count", "operator": "gte", "value": value}
    parsed = AggregateFilter.model_validate_json(json.dumps(payload))
    assert parsed.value == value and type(parsed.value) is type(value)
    assert json.loads(parsed.model_dump_json(by_alias=True))["value"] == value


def test_persisted_pivot_round_trip_keeps_ordered_axes_and_all_total_options():
    tile = {'id': 'pivot', 'title': 'People pivot', 'kind': 'pivot',
        'rowDimensions': [{**FIELD, 'column': 'region'}, {**FIELD, 'column': 'team'}],
        'columnDimensions': [{**FIELD, 'column': 'type'}],
        'measures': [{**FIELD, 'aggregate': 'count_distinct'}], 'detailFields': [FIELD],
        'pivotTotals': {'rowSubtotals': False, 'columnSubtotals': True, 'rowTotals': True,
            'columnTotals': False, 'grandTotal': True, 'maximumColumns': 4, 'maximumRows': 8}}
    repo = InMemoryDashboardRepository()
    saved = repo.create('alice', create(tiles=[tile]))
    wire = saved.model_dump(mode='json', by_alias=True)['tiles'][0]
    assert [field['column'] for field in wire['rowDimensions']] == ['region', 'team']
    assert wire['pivotTotals'] == tile['pivotTotals']
    assert wire['dimensions'] == []
    wire['rowDimensions'].reverse()
    assert repo.get('alice', saved.id).tiles[0].row_dimensions[0].column == 'region'


def test_calculated_measure_and_exact_kpi_configuration_roundtrip_without_rows():
    tile={'id':'kpi','title':'Share','kind':'kpi','hiddenMeasures':[{**FIELD,'aggregate':'count'}],
        'calculations':[{'id':'share','label':'Share','operation':'percent_of',
            'left':{'output':{**FIELD,'aggregate':'count'},'scope':'group'},
            'right':{'output':{**FIELD,'aggregate':'count'},'scope':'source_population'}}],
        'kpiComparison':{'label':'Target','baseline':{'constant':'9007199254740993.000001'},'favorable':'higher'}}
    repo=InMemoryDashboardRepository();saved=repo.create('alice',create(tiles=[tile]))
    reopened=repo.get('alice',saved.id).model_dump(mode='json',by_alias=True)
    assert reopened['tiles'][0]['calculations'][0]['id']=='share'
    assert reopened['tiles'][0]['kpiComparison']['baseline']['constant']=='9007199254740993.000001'
    saved.tiles[0].calculations[0].label='Local draft'
    assert repo.get('alice',saved.id).tiles[0].calculations[0].label=='Share'
    with pytest.raises(ValidationError):
        create(tiles=[{**tile,'calculations':[{**tile['calculations'][0],'sql':'SELECT 1'}]}])
    assert 'rows' not in reopened['tiles'][0]
