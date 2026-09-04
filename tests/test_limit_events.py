from dataclasses import asdict

from fastapi.testclient import TestClient

from schemii.common.connections.service import ConnectionService
from schemii.common.connections.store import InMemoryConnectionRepository
from schemii.common.metadata.factory import MetadataRepositories
from schemii.common.metadata.limit_events import InMemoryLimitEventRecorder
from schemii.main import ApplicationServices, create_app
from schemii.schemii.designs.store import InMemoryDesignRepository
from schemii.schemii.workspaces.store import InMemoryWorkspaceRepository


def test_reached_limit_returns_actionable_error_and_records_only_safe_metadata() -> None:
    connections = InMemoryConnectionRepository(max_connections_per_owner=1)
    recorder = InMemoryLimitEventRecorder()
    designs = InMemoryDesignRepository()
    workspaces = InMemoryWorkspaceRepository(designs=designs)
    services = ApplicationServices(
        metadata=MetadataRepositories(
            connections=connections,
            limit_events=recorder,
        ),
        connections=ConnectionService(connections, (workspaces,)),
        postgres=object(),
        workspaces=workspaces,
        designs=designs,
    )
    connection = {
        "name": "Primary",
        "host": "localhost",
        "database": "analytics",
        "username": "reader",
        "password": "must-never-be-logged",
        "sslMode": "require",
    }

    with TestClient(create_app(services), base_url="http://localhost") as api:
        assert api.post("/api/v1/connections", json=connection).status_code == 201
        connection["name"] = "Second"
        rejected = api.post("/api/v1/connections", json=connection)

    assert rejected.status_code == 409
    error = rejected.json()["error"]
    assert error["code"] == "connection_limit_reached"
    assert error["details"] == {
        "resource": "saved_connections",
        "limitName": "resources.maximum_connections_per_user",
        "limit": 1,
        "observed": 1,
    }
    assert "Delete an unused connection" in error["message"]

    events = recorder.events()
    assert len(events) == 1
    event = events[0]
    assert event.owner_id == "user_local_prototype"
    assert event.path == "/api/v1/connections"
    assert event.notice.configured_limit == 1
    serialized = repr(asdict(event))
    assert "must-never-be-logged" not in serialized
    assert "analytics" not in serialized


def test_limit_event_retention_is_bounded() -> None:
    from datetime import datetime, timezone

    from schemii.common.metadata.limit_events import (
        LimitEvent,
        LimitEventNotice,
    )

    recorder = InMemoryLimitEventRecorder(maximum_events=2)
    for index in range(3):
        recorder.record(
            LimitEvent(
                notice=LimitEventNotice(
                    resource="test",
                    limit_name="test.limit",
                    configured_limit=1,
                    observed_value=index + 1,
                ),
                occurred_at=datetime.now(timezone.utc),
                error_code=f"limit_{index}",
            )
        )

    assert [event.error_code for event in recorder.events()] == ["limit_1", "limit_2"]
