"""Controlled route/baseline failures; no database or application server needed."""

import pytest

from schemii.common.errors import MetadataCapacityError
from schemii.common.postgres.models import PostgresColumn
from schemii.schemii.migrations.repository import PostgresMigrationRepository
from test_api import client, create_connection, create_workspace


@pytest.fixture
def inspector():
    api, gateway = client()
    gateway.columns = (
        PostgresColumn(name="id", ordinal=1, data_type="uuid", nullable=False),
    )
    connection = create_connection(api)
    workspace = create_workspace(api, connection["id"])
    path = f"/api/v1/schemii/workspaces/{workspace['id']}/design"
    design = api.get(path).json()
    column = design["content"]["tables"][0]["columns"][0]
    gateway.connections.clear()
    with api:
        yield api, gateway, path, design, column


def test_unchanged_uuid_uses_persisted_baseline_without_target_io(inspector):
    api, gateway, path, design, column = inspector
    history = api.get(f"{path}/history").json()
    assert history["baseline"]["kind"] == "postgresql"
    assert history["baseline"]["fingerprint"] == design["fingerprint"]
    assert history["designRevision"] == design["revision"]
    response = api.post(
        f"{path}/column-type-analysis",
        json={"columnId": column["id"], "targetType": "uuid"},
    )
    assert response.status_code == 200
    assert response.json()["requiresConversion"] is False
    assert response.json()["sourceType"] == "uuid"
    assert gateway.connections == []


def test_saved_desired_type_is_not_the_live_source_type(inspector):
    api, gateway, path, design, column = inspector
    column["dataType"] = "text"
    saved = api.put(
        path,
        json={
            "expectedDesignRevision": design["revision"],
            "content": design["content"],
        },
    )
    assert saved.status_code == 200
    history = api.get(f"{path}/history").json()
    assert history["baseline"]["fingerprint"] != saved.json()["fingerprint"]
    response = api.post(
        f"{path}/column-type-analysis",
        json={"columnId": column["id"], "targetType": "text"},
    )
    assert response.status_code == 200
    assert response.json()["requiresConversion"] is True
    assert response.json()["sourceType"] == "uuid"
    assert gateway.connections == []


def test_metadata_admission_failure_is_real_503_even_for_unchanged_uuid(inspector):
    api, gateway, path, _, column = inspector

    def reject_metadata_connection():
        raise MetadataCapacityError("owned controlled rejection")

    api.app.state.services.migrations._repository = PostgresMigrationRepository(
        reject_metadata_connection,
    )
    response = api.post(
        f"{path}/column-type-analysis",
        json={"columnId": column["id"], "targetType": "uuid"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "migration_metadata_unavailable"
    assert gateway.connections == []
