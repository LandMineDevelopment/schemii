import pytest
from pydantic import ValidationError

from schemii.schemii.workspaces.models import (
    SchemiiWorkspaceCreate,
    SchemiiWorkspaceLayoutUpdate,
    TablePosition,
)
from schemii.schemii.workspaces.store import (
    InMemoryWorkspaceRepository,
    WorkspaceLimitError,
)


def workspace_request(namespace: str = "public") -> SchemiiWorkspaceCreate:
    return SchemiiWorkspaceCreate(
        connection_id="pg_0123456789abcdef0123456789abcdef",
        database="analytics",
        namespace=namespace,
    )


def test_workspace_targets_preserve_exact_postgres_identifiers() -> None:
    request = workspace_request(namespace=" reporting ")
    position = TablePosition(name=" orders ", x=0, y=0)

    assert request.namespace == " reporting "
    assert position.name == " orders "

    with pytest.raises(ValidationError):
        TablePosition(name="orders", x=True, y=0)


def test_workspace_and_aggregate_position_counts_are_bounded() -> None:
    workspace_limited = InMemoryWorkspaceRepository(max_workspaces_per_owner=1)
    workspace_limited.create("owner", workspace_request())
    with pytest.raises(WorkspaceLimitError) as workspace_error:
        workspace_limited.create("owner", workspace_request())
    assert workspace_error.value.category == "workspace"

    position_limited = InMemoryWorkspaceRepository(max_table_positions_per_owner=1)
    first = position_limited.create("owner", workspace_request())
    second = position_limited.create("owner", workspace_request(namespace="other"))
    position_limited.update_layout(
        "owner",
        first.id,
        SchemiiWorkspaceLayoutUpdate(
            expected_revision=1,
            expected_connection_revision=1,
            tables=[TablePosition(name="orders", x=0, y=0)],
        ),
    )

    with pytest.raises(WorkspaceLimitError) as position_error:
        position_limited.update_layout(
            "owner",
            second.id,
            SchemiiWorkspaceLayoutUpdate(
                expected_revision=1,
                expected_connection_revision=1,
                tables=[TablePosition(name="customers", x=0, y=0)],
            ),
        )

    assert position_error.value.category == "table position"
    assert position_error.value.limit == 1
