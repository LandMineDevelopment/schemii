"""Real PostgreSQL round trips for the current-only semantic model repository."""

import pytest
from pydantic import SecretStr

from schemii.common.connections.models import PostgresConnectionCreate
from schemii.common.connections.service import ConnectionService
from schemii.common.connections.store import ConnectionInUseError
from schemii.schemoo.models import ModelCreate, ModelUpdate, LayoutUpdate, ExploreUpdate
from schemii.schemoo.store import PostgresModelRepository, ModelConflictError, ModelNotFoundError


def test_durable_semantic_model_roundtrip_and_connection_guard(postgres_metadata):
    harness = postgres_metadata
    store = PostgresModelRepository(harness.connection_factory)
    connection = harness.repositories.connections.create(harness.owner_id, PostgresConnectionCreate(
        name="Semantic source", host="application-postgres", port=5432, database="application_data",
        username="application_user", password=SecretStr("target-only-secret")))
    model = store.create(harness.owner_id, ModelCreate(name="People", connection_id=connection.id,
        database=connection.database, namespace="public", definition={"root": "people", "nodes": [
            {"id": "people", "table": "people", "label": "People"}]}))
    reopened = PostgresModelRepository(harness.connection_factory)
    assert reopened.get(harness.owner_id, model.id) == model
    assert reopened.list("someone_else") == []
    with pytest.raises(ModelNotFoundError):
        reopened.get("someone_else", model.id)
    layout = reopened.update_layout(harness.owner_id, model.id, LayoutUpdate(expected_revision=1,
        layout={"positions": [{"id": "people", "x": 10, "y": 20}]}))
    assert layout.revision == 1 and layout.layout_revision == 2
    saved = reopened.update(harness.owner_id, model.id, ModelUpdate(expected_revision=1,
        name="Staffing", definition=model.definition))
    assert saved.layout == layout.layout and saved.revision == 2
    explored = reopened.update_explore(harness.owner_id, model.id, ExploreUpdate(expected_revision=1,
        explore={"fields": [{"table": "people", "column": "name"}]}))
    assert explored.explore_revision == 2 and explored.revision == 2
    with pytest.raises(ModelConflictError):
        reopened.delete(harness.owner_id, model.id, 1)
    service = ConnectionService(harness.repositories.connections, (reopened,))
    with pytest.raises(ConnectionInUseError):
        service.delete(harness.owner_id, connection.id, connection.revision)
    reopened.delete(harness.owner_id, model.id, 2)
    service.delete(harness.owner_id, connection.id, connection.revision)
    assert reopened.list(harness.owner_id) == []
