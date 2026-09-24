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
    routes.validate_dashboard(services, "alice", create(tiles=[tile], optionalFilters=["optional"],
        selections={"optional": {"active": True}}))
    # Removed model scopes from an older dashboard are inert and remain readable
    # until the next client save prunes them.
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
