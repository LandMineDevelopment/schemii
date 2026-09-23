import pytest
from pydantic import ValidationError

from schemii.schemii.workspaces.models import (
    SchemiiWorkspaceLayoutUpdate,
    TableColumnDisplayOrder,
    TablePosition,
    WorkspaceCreateRecord,
    WorkspaceMetadataUpdate,
)
from schemii.schemii.workspaces.store import (
    InMemoryWorkspaceRepository,
    WorkspaceLimitError,
    WorkspaceConflictError,
    WorkspaceNotFoundError,
)


def workspace_request(namespace: str = "public") -> WorkspaceCreateRecord:
    return WorkspaceCreateRecord(
        connection_id="pg_0123456789abcdef0123456789abcdef",
        database="analytics",
        namespace=namespace,
    )


def test_rename_preserves_target_and_layout_with_owner_revision_guard() -> None:
    repository = InMemoryWorkspaceRepository()
    original = repository.create("owner", workspace_request())
    original = repository.update_layout("owner", original.id, SchemiiWorkspaceLayoutUpdate(
        expected_revision=1, expected_connection_revision=1,
        tables=[TablePosition(name="orders", x=100, y=20)],
        column_orders=[TableColumnDisplayOrder(name="orders", columns=["name", "id"])],
    ))
    request = WorkspaceMetadataUpdate(expected_revision=original.revision, name="  My schema  ")
    with pytest.raises(WorkspaceNotFoundError):
        repository.rename("other", original.id, request)
    renamed = repository.rename("owner", original.id, request)
    assert renamed.name == "My schema"
    assert renamed.revision == original.revision + 1
    assert renamed.model_dump(exclude={"name", "revision", "updated_at"}) == original.model_dump(exclude={"name", "revision", "updated_at"})
    with pytest.raises(WorkspaceConflictError):
        repository.rename("owner", original.id, request)
    assert repository.get("owner", original.id) == renamed
    for name in (" ", "x" * 129):
        with pytest.raises(ValidationError):
            WorkspaceMetadataUpdate(expected_revision=renamed.revision, name=name)


def test_workspace_targets_preserve_exact_postgres_identifiers() -> None:
    request = workspace_request(namespace=" reporting ")
    position = TablePosition(name=" orders ", x=0, y=0)

    assert request.namespace == " reporting "
    assert position.name == " orders "

    with pytest.raises(ValidationError):
        TablePosition(name="orders", x=True, y=0)

    with pytest.raises(ValidationError, match="unique names"):
        TableColumnDisplayOrder(name="orders", columns=["id", "id"])


def test_workspace_request_accepts_only_complete_or_absent_targets() -> None:
    local = WorkspaceCreateRecord(name="Local")

    assert local.connection_id is None
    assert local.database is None
    assert local.namespace is None

    with pytest.raises(ValidationError):
        WorkspaceCreateRecord(name="Partial", database="analytics")


def test_workspace_kind_and_database_identity_are_fixed_at_creation() -> None:
    repository = InMemoryWorkspaceRepository()
    local = repository.create("owner", WorkspaceCreateRecord(name="Local"))
    database = repository.create("owner", workspace_request())

    assert local.connection_id is None
    assert database.connection_id is not None


def test_managed_workspace_keeps_actor_and_credential_owners_distinct() -> None:
    repository = InMemoryWorkspaceRepository()
    connection_id = "pg_0123456789abcdef0123456789abcdef"
    workspace = repository.create(
        "friend",
        WorkspaceCreateRecord(
            connection_id=connection_id,
            connection_owner_id="role-organization-writer",
            database="analytics",
            namespace="public",
        ),
    )

    assert repository.get("friend", workspace.id).connection_owner_id == "role-organization-writer"
    with pytest.raises(WorkspaceNotFoundError):
        repository.get("role-organization-writer", workspace.id)
    assert repository.count_for_connection("role-organization-writer", connection_id) == 1
    assert repository.count_for_connection("friend", connection_id) == 0
    assert repository.dependencies_for_connection("role-organization-writer", connection_id)[0].resource_id == workspace.id
    assert "connection_owner_id" not in workspace.model_dump()

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

    order_limited = InMemoryWorkspaceRepository(
        max_column_display_order_entries_per_owner=1
    )
    ordered = order_limited.create("owner", workspace_request())
    with pytest.raises(WorkspaceLimitError) as order_error:
        order_limited.update_layout(
            "owner",
            ordered.id,
            SchemiiWorkspaceLayoutUpdate(
                expected_revision=1,
                expected_connection_revision=1,
                tables=[],
                column_orders=[
                    TableColumnDisplayOrder(
                        name="orders",
                        columns=["id", "created_at"],
                    )
                ],
            ),
        )
    assert order_error.value.category == "column display order entry"
    assert order_error.value.limit == 1


def test_column_display_orders_are_optional_and_replaced_as_one_layout_revision() -> None:
    repository = InMemoryWorkspaceRepository()
    workspace = repository.create("owner", workspace_request())
    custom = TableColumnDisplayOrder(
        name="orders",
        columns=["created_at", "id"],
    )

    saved = repository.update_layout(
        "owner",
        workspace.id,
        SchemiiWorkspaceLayoutUpdate(
            expected_revision=1,
            expected_connection_revision=1,
            tables=[],
            column_orders=[custom],
        ),
    )
    preserved = repository.update_layout(
        "owner",
        workspace.id,
        SchemiiWorkspaceLayoutUpdate(
            expected_revision=2,
            expected_connection_revision=1,
            tables=[],
        ),
    )
    cleared = repository.update_layout(
        "owner",
        workspace.id,
        SchemiiWorkspaceLayoutUpdate(
            expected_revision=3,
            expected_connection_revision=1,
            tables=[],
            column_orders=[],
        ),
    )

    assert saved.column_orders == [custom]
    assert preserved.column_orders == [custom]
    assert cleared.column_orders == []
