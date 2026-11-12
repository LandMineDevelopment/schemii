"""Actual merge and metadata service boundaries; no target DDL or live fixtures."""

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from schemii.common.connections.models import ResolvedPostgresConnection
from schemii.common.connections.store import ConnectionNotFoundError
from schemii.common.postgres.errors import PostgresQueryError
from schemii.common.postgres.models import build_postgres_catalog
from schemii.schemii.designs.deployment import merge_content, namespace_warnings
from schemii.schemii.designs.importer import import_postgres_catalog
from schemii.schemii.designs.models import (
    DesignDeploymentCopyRequest,
    DesignDeploymentReviewRequest,
    SchemiiDesignContent,
    SchemiiDesignReplace,
)
from schemii.schemii.designs.store import (
    DesignValidationError,
    InMemoryDesignRepository,
)
from schemii.schemii.migrations.repository import InMemoryMigrationRepository
from schemii.schemii.migrations.service import MigrationService, MigrationServiceError
from schemii.schemii.workspaces.models import (
    WorkspaceCreateRecord,
    WorkspaceMetadataUpdate,
)
from schemii.schemii.workspaces.store import InMemoryWorkspaceRepository


OWNER = "owner"


def content(offset=0, *, data_type="bigint", extra=False):
    return SchemiiDesignContent.model_validate(
        {
            "tables": [
                {
                    "id": f"table_{offset + 1:032x}",
                    "name": "Quoted table",
                    "columns": [
                        {
                            "id": f"column_{offset + 2:032x}",
                            "name": "ID",
                            "dataType": data_type,
                        },
                        *(
                            [
                                {
                                    "id": f"column_{offset + 3:032x}",
                                    "name": "target_only",
                                    "dataType": "text",
                                }
                            ]
                            if extra
                            else []
                        ),
                    ],
                    "keys": [
                        {
                            "id": f"key_{offset + 4:032x}",
                            "name": "pk",
                            "kind": "primary",
                            "columnIds": [f"column_{offset + 2:032x}"],
                        }
                    ],
                }
            ]
        }
    )


class Access:
    def for_product(self, product):
        return self

    def __init__(self):
        self.denied = False
        self.connection = ResolvedPostgresConnection(
            id="pg_" + "1" * 32,
            owner_id=OWNER,
            revision=1,
            name="Target",
            host="source.test",
            port=5432,
            database="target",
            username="author",
            ssl_mode="require",
            connect_timeout=5,
        )

    @contextmanager
    def use(self, owner, connection_id):
        if self.denied or owner != OWNER or connection_id != self.connection.id:
            raise ConnectionNotFoundError("Connection unavailable")
        yield self.connection


@pytest.fixture
def fixture():
    designs = InMemoryDesignRepository()
    workspaces = InMemoryWorkspaceRepository(designs=designs)
    migrations = InMemoryMigrationRepository(designs)
    access = Access()
    catalog = build_postgres_catalog(
        database="target",
        namespace='A "schema"',
        server_version="17.2",
        server_version_num=170002,
        server_timezone="UTC",
        tables=(),
        relationships=(),
        functions=(),
        views=(),
        materialized_views=(),
        captured_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    inspections = []

    def inspect(connection, namespace):
        inspections.append((connection.id, namespace))
        return catalog

    postgres = SimpleNamespace(introspect=inspect)
    service = MigrationService(
        repository=migrations,
        connection_access=access,
        postgres=postgres,
        workspaces=workspaces,
        designs=designs,
    )
    source = workspaces.create(OWNER, WorkspaceCreateRecord(name="Detached"))
    designs.replace(
        OWNER,
        source.id,
        SchemiiDesignReplace(expected_design_revision=0, content=content()),
    )
    target = service.create_import_workspace(
        OWNER,
        WorkspaceCreateRecord(
            name="Target",
            connection_id=access.connection.id,
            connection_owner_id=OWNER,
            database="target",
            namespace=catalog.namespace,
        ),
        import_postgres_catalog(catalog),
        1,
        catalog,
    )
    target_design = designs.get(OWNER, target.id)
    designs.replace(
        OWNER,
        target.id,
        SchemiiDesignReplace(
            expected_design_revision=target_design.revision,
            content=content(100, data_type="integer", extra=True),
        ),
    )
    return SimpleNamespace(
        service=service,
        designs=designs,
        workspaces=workspaces,
        migrations=migrations,
        access=access,
        source=source,
        target=target,
        catalog=catalog,
        postgres=postgres,
        inspections=inspections,
    )


def review(f, resolutions=None):
    return f.service.review_design_deployment(
        OWNER,
        f.source.id,
        DesignDeploymentReviewRequest(
            target_workspace_id=f.target.id, resolutions=resolutions or {}
        ),
    )


def accepted(f, resolution="incoming"):
    first = review(f)
    return review(f, {item.key: resolution for item in first.collisions})


def test_initial_review_exposes_choices_before_validating_provisional_generated_columns(
    fixture,
):
    def generated(offset, incoming):
        ids = {
            name: f"column_{offset + index:032x}"
            for index, name in enumerate(["c", "a", "b"], 1)
        }
        columns = [
            {"id": ids[name], "name": name, "dataType": "integer"}
            for name in ["c", "a"]
        ]
        if incoming:
            columns.append(
                {
                    "id": ids["b"],
                    "name": "b",
                    "dataType": "integer",
                    "generatedExpression": "a + 1",
                    "generatedSourceColumnIds": [ids["a"]],
                }
            )
        else:
            columns[1].update(
                generatedExpression="c + 1", generatedSourceColumnIds=[ids["c"]]
            )
        return SchemiiDesignContent.model_validate(
            {
                "tables": [
                    {"id": f"table_{offset + 4:032x}", "name": "T", "columns": columns}
                ]
            }
        )

    f = fixture
    for workspace, incoming, offset in [(f.source, True, 0), (f.target, False, 100)]:
        before = f.designs.get(OWNER, workspace.id)
        f.designs.replace(
            OWNER,
            workspace.id,
            SchemiiDesignReplace(
                expected_design_revision=before.revision,
                content=generated(offset, incoming),
            ),
        )
    first = review(f)
    assert not first.ready and len(first.collisions) == 1
    choices = {first.collisions[0].key: "incoming"}
    resolved = review(f, choices)
    assert resolved.ready
    with pytest.raises(DesignValidationError):
        merge_content(
            generated(0, True),
            generated(100, False),
            f.target.id,
            {first.collisions[0].key: "keep"},
        )


def copy_request(preview):
    return DesignDeploymentCopyRequest(
        target_workspace_id=preview.target_workspace_id,
        resolutions={item.key: item.resolution for item in preview.collisions},
        review_digest=preview.review_digest,
    )


def test_copy_preserves_source_target_only_children_identity_history_and_baseline(
    fixture,
):
    f = fixture
    source = f.designs.get(OWNER, f.source.id)
    target = f.designs.get(OWNER, f.target.id)
    baseline = f.migrations.current_baseline(OWNER, f.target.id)
    first = review(f)
    assert not first.ready and len(first.collisions) == 1
    assert f.designs.get(OWNER, f.target.id) == target  # Preview is read-only.
    preview = accepted(f)
    mutation = f.service.copy_design_deployment(
        OWNER, f.source.id, copy_request(preview)
    )
    assert f.designs.get(OWNER, f.source.id) == source
    assert mutation.design.revision == target.revision + 1
    table = mutation.design.content.tables[0]
    assert table.id == target.content.tables[0].id
    assert table.columns[0].id == target.content.tables[0].columns[0].id
    assert table.columns[0].data_type == "bigint"
    assert table.keys[0].column_ids == [table.columns[0].id]
    assert table.columns[1].name == "target_only"
    assert f.migrations.current_baseline(OWNER, f.target.id) == baseline
    assert f.workspaces.get(OWNER, f.target.id) == f.target
    assert (
        f.designs.undo(OWNER, f.target.id, mutation.design.revision).content
        == target.content
    )
    assert {namespace for _, namespace in f.inspections} == {f.target.namespace}


def test_keep_collision_leaves_existing_target_metadata_unchanged(fixture):
    f = fixture
    target = f.designs.get(OWNER, f.target.id)
    preview = accepted(f, "keep")
    assert preview.summary.change_count == 0
    result = f.service.copy_design_deployment(OWNER, f.source.id, copy_request(preview))
    assert result.design == target


@pytest.mark.parametrize(
    "change",
    [
        "source",
        "target",
        "source_workspace",
        "target_workspace",
        "connection",
        "baseline",
    ],
)
def test_review_rejects_each_stale_input_without_copying(fixture, change):
    f = fixture
    preview = accepted(f)
    if change in {"source", "target"}:
        workspace = getattr(f, change)
        previous = f.designs.get(OWNER, workspace.id)
        updated = previous.content.model_copy(deep=True)
        updated.tables[0].columns[0].nullable = (
            not updated.tables[0].columns[0].nullable
        )
        f.designs.replace(
            OWNER,
            workspace.id,
            SchemiiDesignReplace(
                expected_design_revision=previous.revision, content=updated
            ),
        )
    elif change.endswith("_workspace"):
        workspace = f.source if change == "source_workspace" else f.target
        f.workspaces.rename(
            OWNER,
            workspace.id,
            WorkspaceMetadataUpdate(
                expected_revision=workspace.revision, name="Renamed"
            ),
        )
    elif change == "connection":
        f.access.connection = f.access.connection.model_copy(update={"revision": 2})
    else:
        baseline = f.migrations.current_baseline(OWNER, f.target.id)
        f.migrations.create_baseline(
            owner_id=OWNER,
            workspace_id=f.target.id,
            connection_id=f.access.connection.id,
            connection_owner_id=OWNER,
            connection_revision=1,
            database="target",
            namespace=f.target.namespace,
            design_revision=baseline.design_revision,
            content=baseline.content,
            catalog=f.catalog,
            complete=True,
            issues=[],
            source="drift_reconciliation",
            expected_predecessor_id=baseline.id,
        )
    before = f.designs.get(OWNER, f.target.id)
    with pytest.raises(MigrationServiceError):
        f.service.copy_design_deployment(OWNER, f.source.id, copy_request(preview))
    assert f.designs.get(OWNER, f.target.id) == before


def test_atomic_guard_catches_source_change_after_service_review(fixture, monkeypatch):
    f = fixture
    preview = accepted(f)
    original = f.designs.replace
    target = f.designs.get(OWNER, f.target.id)

    def interleave(owner, workspace, request, **kwargs):
        source = f.designs.get(OWNER, f.source.id)
        original(
            owner,
            f.source.id,
            SchemiiDesignReplace(
                expected_design_revision=source.revision, content=content(500)
            ),
        )
        return original(owner, workspace, request, **kwargs)

    monkeypatch.setattr(f.designs, "replace", interleave)
    with pytest.raises(MigrationServiceError, match="changed"):
        f.service.copy_design_deployment(OWNER, f.source.id, copy_request(preview))
    assert f.designs.get(OWNER, f.target.id) == target


@pytest.mark.parametrize("failure", ["denied", "offline", "save"])
def test_denial_inspection_or_copy_failure_preserves_both_designs(
    fixture, monkeypatch, failure
):
    f = fixture
    preview = accepted(f)
    source, target = (
        f.designs.get(OWNER, f.source.id),
        f.designs.get(OWNER, f.target.id),
    )
    if failure == "denied":
        f.access.denied = True
    elif failure == "offline":
        monkeypatch.setattr(
            f.postgres,
            "introspect",
            lambda *_: (_ for _ in ()).throw(PostgresQueryError()),
        )
    else:
        monkeypatch.setattr(
            f.designs,
            "replace",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                DesignValidationError("copy failed")
            ),
        )
    with pytest.raises(MigrationServiceError):
        f.service.copy_design_deployment(OWNER, f.source.id, copy_request(preview))
    assert f.designs.get(OWNER, f.source.id) == source
    assert f.designs.get(OWNER, f.target.id) == target


def test_unresolved_or_forged_collision_choice_cannot_copy(fixture):
    f = fixture
    preview = review(f)
    with pytest.raises(MigrationServiceError):
        f.service.copy_design_deployment(
            OWNER,
            f.source.id,
            DesignDeploymentCopyRequest(
                target_workspace_id=f.target.id, review_digest=preview.review_digest
            ),
        )
    with pytest.raises(MigrationServiceError, match="choices"):
        review(f, {"forged": "incoming"})


def test_relationships_and_generated_constraints_remap_to_existing_column_ids():
    source, target = content(), content(100)
    document = source.model_dump()
    document["tables"][0]["columns"].append(
        {
            "id": "column_" + "a" * 32,
            "name": "next_id",
            "data_type": "bigint",
            "generated_expression": '"ID" + 1',
            "generated_source_column_ids": [source.tables[0].columns[0].id],
        }
    )
    source = SchemiiDesignContent.model_validate(document)
    merged, collisions = merge_content(source, target, "ws_" + "1" * 32, {})
    assert not collisions
    assert merged.tables[0].columns[1].generated_source_column_ids == [
        target.tables[0].columns[0].id
    ]


def test_namespace_warnings_preserve_external_and_quoted_sql_without_text_rewrite():
    design = SchemiiDesignContent.model_validate(
        {
            "views": [
                {
                    "id": "view_" + "a" * 32,
                    "name": "External",
                    "kind": "view",
                    "definition": 'SELECT * FROM "External.Schema"."Quoted.Table"',
                }
            ],
            "functions": [
                {
                    "id": "function_" + "b" * 32,
                    "definition": "CREATE FUNCTION sample() RETURNS text LANGUAGE SQL AS $$ SELECT 'public.keep_me' $$",
                }
            ],
        }
    )
    before = design.model_dump()
    warnings = namespace_warnings(design, 'A "schema"')
    assert any("External.Schema" in value for value in warnings)
    assert any("Routine bodies" in value for value in warnings)
    assert design.model_dump() == before


def test_live_drift_is_visible_and_invalidates_a_reviewed_copy(fixture, monkeypatch):
    f = fixture
    preview = accepted(f)
    assert preview.target_changes.change_count > 0
    assert preview.live_drift is False
    changed = f.catalog.model_copy(update={"fingerprint": "f" * 64})
    monkeypatch.setattr(f.postgres, "introspect", lambda *_: changed)
    current = accepted(f)
    assert current.live_drift is True
    before = f.designs.get(OWNER, f.target.id)
    with pytest.raises(MigrationServiceError):
        f.service.copy_design_deployment(OWNER, f.source.id, copy_request(preview))
    assert f.designs.get(OWNER, f.target.id) == before


def test_active_target_migration_blocks_copy_without_mutation(fixture):
    f = fixture
    preview = accepted(f)
    before = f.designs.get(OWNER, f.target.id)
    f.designs.set_mutation_guard(lambda owner, workspace: workspace == f.target.id)
    with pytest.raises(MigrationServiceError):
        f.service.copy_design_deployment(OWNER, f.source.id, copy_request(preview))
    assert f.designs.get(OWNER, f.target.id) == before


def test_owner_fencing_and_saved_target_requirement(fixture):
    f = fixture
    for owner, target in [("other", f.target.id), (OWNER, f.source.id)]:
        with pytest.raises(MigrationServiceError):
            f.service.review_design_deployment(
                owner,
                f.source.id,
                DesignDeploymentReviewRequest(target_workspace_id=target),
            )


def test_http_contract_exposes_preview_and_atomic_copy(fixture):
    from fastapi.testclient import TestClient
    from schemii.common.connections.store import InMemoryConnectionRepository
    from schemii.common.metadata.factory import MetadataRepositories
    from schemii.common.metadata.models import Principal, get_current_principal
    from schemii.main import ApplicationServices, create_app

    f = fixture
    app = create_app(
        ApplicationServices(
            metadata=MetadataRepositories(connections=InMemoryConnectionRepository()),
            connections=f.access,
            postgres=f.postgres,
            workspaces=f.workspaces,
            designs=f.designs,
            migrations=f.service,
        )
    )
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        user_id=OWNER, authentication_source="local_prototype"
    )
    api = TestClient(app, base_url="http://localhost")
    root = f"/api/v1/schemii/workspaces/{f.source.id}/design"
    first = api.post(
        root + "/deployment-review", json={"targetWorkspaceId": f.target.id}
    )
    assert first.status_code == 200
    assert first.json()["ready"] is False
    choices = {item["key"]: "incoming" for item in first.json()["collisions"]}
    reviewed = api.post(
        root + "/deployment-review",
        json={"targetWorkspaceId": f.target.id, "resolutions": choices},
    )
    assert reviewed.status_code == 200 and reviewed.json()["ready"] is True
    saved = api.post(
        root + "/deployment-copy",
        json={
            "targetWorkspaceId": f.target.id,
            "resolutions": choices,
            "reviewDigest": reviewed.json()["reviewDigest"],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["design"]["workspaceId"] == f.target.id


@pytest.mark.parametrize("mutation", ["baseline", "workspace"])
def test_in_memory_copy_serializes_baseline_and_workspace_authority_through_commit(
    fixture, monkeypatch, mutation
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    f = fixture
    preview = accepted(f)
    baseline = f.migrations.current_baseline(OWNER, f.target.id)
    original_replace = f.designs.replace
    inside, attempted, finished = Event(), Event(), Event()

    def concurrent_change():
        assert inside.wait(2)
        attempted.set()
        if mutation == "baseline":
            f.migrations.create_baseline(
                owner_id=OWNER,
                workspace_id=f.target.id,
                connection_id=f.access.connection.id,
                connection_owner_id=OWNER,
                connection_revision=1,
                database="target",
                namespace=f.target.namespace,
                design_revision=baseline.design_revision,
                content=baseline.content,
                catalog=f.catalog,
                complete=True,
                issues=[],
                source="drift_reconciliation",
                expected_predecessor_id=baseline.id,
            )
        else:
            f.workspaces.rename(
                OWNER,
                f.target.id,
                WorkspaceMetadataUpdate(
                    expected_revision=f.target.revision, name="After copy"
                ),
            )
        finished.set()

    def held_copy(owner, workspace, body, **kwargs):
        inside.set()
        assert attempted.wait(2)
        assert not finished.wait(
            0.05
        )  # The mutation cannot overtake the reviewed save.
        return original_replace(owner, workspace, body, **kwargs)

    monkeypatch.setattr(f.designs, "replace", held_copy)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(concurrent_change)
        copied = f.service.copy_design_deployment(
            OWNER, f.source.id, copy_request(preview)
        )
        future.result(timeout=2)
    assert finished.is_set()
    assert copied.design.content.tables[0].columns[0].data_type == "bigint"
