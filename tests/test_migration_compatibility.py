"""Operation-specific preservation evidence, separate from editable coverage."""

from datetime import datetime, timezone

import pytest

from schemii.common.postgres.models import (
    PostgresColumn,
    PostgresDependency,
    PostgresObjectIdentity,
    PostgresSafetyEvidence,
    PostgresPreservation,
    PostgresTable,
    build_postgres_catalog,
)
from schemii.schemii.designs.importer import import_postgres_catalog
from schemii.schemii.migrations.planner import (
    compile_migration_steps,
    migration_compatibility,
    preservation_matches,
    reconcile_designs,
)


def relation(name, namespace="public"):
    return PostgresObjectIdentity(kind="relation", namespace=namespace, name=name)


def mixed(*, unknown=False, edge=False, safety=True):
    evidence = PostgresSafetyEvidence(
        preservation=tuple(
            PostgresPreservation(
                identity=relation(name),
                relation_kind="p" if name == "protected" else "r",
                owner="author",
                actor="author",
                acl=(),
                reloptions=(),
                tablespace=None,
                access_method="heap",
                partition_bound=None,
                partition_parents=(),
                indexes=(),
                default_grantees=(),
                properties="{}",
            )
            for name in ("protected", "ordinary")
        ),
        dependencies=(
            PostgresDependency(
                source=relation("protected"), target=relation("ordinary"), kind="fk"
            ),
        )
        if edge
        else (),
        dependency_complete=not unknown,
        unknown_reasons=("untracked dynamic routine",) if unknown else (),
    )
    return build_postgres_catalog(
        database="data",
        namespace="public",
        server_version="17.2",
        server_version_num=170002,
        server_timezone="UTC",
        tables=tuple(
            PostgresTable(
                namespace="public",
                name=name,
                kind="partitioned_table" if name == "protected" else "table",
                is_partition=False,
                partition_key="RANGE (id)" if name == "protected" else None,
                columns=(
                    PostgresColumn(
                        name="id", ordinal=1, data_type="bigint", nullable=False
                    ),
                ),
            )
            for name in ("protected", "ordinary")
        ),
        relationships=(),
        functions=(),
        views=(),
        materialized_views=(),
        captured_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        safety=evidence if safety else None,
    )


def change(source, name):
    content = import_postgres_catalog(source).content
    changed = content.model_copy(deep=True)
    next(table for table in changed.tables if table.name == name).columns[
        0
    ].nullable = True
    return content, changed


def test_mixed_schema_unaffected_operation_is_proven_while_import_stays_incomplete():
    catalog = mixed()
    live, desired = change(catalog, "ordinary")
    imported = import_postgres_catalog(catalog)
    assert not imported.summary.complete
    assert imported.summary.issues[0].identity == relation("protected")
    compatibility = migration_compatibility(catalog, live, desired)
    assert compatibility.operation_safe and not compatibility.editable_complete
    result = reconcile_designs(live, desired, catalog)
    assert result.conflicts == [] and not result.import_complete
    steps, blockers = compile_migration_steps("public", result.live, result.merged)
    assert len(steps) == 1 and not blockers and "ordinary" in steps[0].sql
    assert preservation_matches(catalog, catalog, compatibility)


@pytest.mark.parametrize(
    "options", [{"unknown": True}, {"edge": True}, {"safety": False}]
)
def test_unknown_protected_or_historical_boundary_does_not_gain_new_authority(options):
    catalog = mixed(**options)
    live, desired = change(catalog, "ordinary")
    assert not migration_compatibility(catalog, live, desired).operation_safe
    assert any(
        item.category == "opaque"
        for item in reconcile_designs(live, desired, catalog).conflicts
    )


def test_direct_protected_change_and_opaque_drift_are_rejected():
    catalog = mixed()
    live, desired = change(catalog, "protected")
    compatibility = migration_compatibility(catalog, live, desired)
    assert not compatibility.operation_safe
    before = catalog
    altered = catalog.tables[0].model_copy(update={"partition_key": "RANGE ((id + 1))"})
    after = build_postgres_catalog(
        **catalog.model_dump(exclude={"fingerprint", "captured_at", "tables"}),
        tables=(altered, catalog.tables[1]),
        captured_at=catalog.captured_at,
    )
    assert before.fingerprint != after.fingerprint
    assert not preservation_matches(before, after, compatibility)


def materialized_catalog(
    *,
    populated=False,
    dependents=False,
    unknown=False,
    actor="author",
    invalid_index=False,
    grantor="author",
    reloptions=(),
):
    import json
    from schemii.common.postgres.models import (
        PostgresGrant,
        PostgresIndex,
        PostgresMaterializedView,
    )

    name = 'Saved "totals"'
    index = PostgresIndex(
        name="saved_idx",
        table=name,
        definition='CREATE INDEX saved_idx ON public."Saved ""totals""" (value)',
        method="btree",
        unique=False,
        valid=not invalid_index,
    )
    descriptor = PostgresPreservation(
        identity=relation(name),
        relation_kind="m",
        owner="author",
        actor=actor,
        acl=(
            PostgresGrant(
                grantor=grantor, grantee="reader", privilege="SELECT", grantable=True
            ),
        ),
        reloptions=reloptions,
        tablespace=None,
        access_method="heap",
        partition_bound=None,
        partition_parents=(),
        indexes=(index,),
        default_grantees=("default_reader",),
        properties=json.dumps(
            {
                "persistence": "p",
                "row_security": False,
                "force_row_security": False,
                "replica_identity": "d",
            }
        ),
    )
    edges = [
        PostgresDependency(
            source=relation("saved_idx"), target=relation(name), kind="a"
        )
    ]
    if dependents:
        edges.append(
            PostgresDependency(
                source=relation("external_report", "other"),
                target=relation(name),
                kind="n",
            )
        )
    return build_postgres_catalog(
        database="analytics",
        namespace="public",
        server_version="17.2",
        server_version_num=170002,
        server_timezone="UTC",
        tables=(),
        relationships=(),
        functions=(),
        views=(),
        materialized_views=(
            PostgresMaterializedView(
                namespace="public",
                name=name,
                columns=(
                    PostgresColumn(
                        name="value", ordinal=1, data_type="integer", nullable=True
                    ),
                ),
                query_definition="SELECT 1 AS value",
                populated=populated,
            ),
        ),
        captured_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        safety=PostgresSafetyEvidence(
            preservation=(descriptor,),
            dependencies=tuple(edges),
            dependency_complete=not unknown,
            unknown_reasons=("dynamic references",) if unknown else (),
        ),
    )


def test_same_name_materialized_replacement_replays_index_acl_and_preserves_population_intent():
    from schemii.schemii.migrations.planner import materialized_maintenance

    catalog = materialized_catalog()
    live = import_postgres_catalog(catalog).content
    desired = live.model_copy(deep=True)
    desired.views[0].definition = "SELECT 2 AS value"
    report, _ = materialized_maintenance(catalog, desired.views[0], "replace")
    assert report.eligible and report.populated_after is False
    steps, blockers = compile_migration_steps("public", live, desired, catalog=catalog)
    assert not blockers
    sql = "\n".join(step.sql for step in steps)
    assert 'DROP MATERIALIZED VIEW "public"."Saved ""totals""";' in sql
    assert "WITH NO DATA" in sql and "CREATE INDEX saved_idx" in sql
    assert (
        'GRANT SELECT ON TABLE "public"."Saved ""totals""" TO "reader" WITH GRANT OPTION;'
        in sql
    )
    assert '"default_reader"' in sql and "CASCADE" not in sql
    assert migration_compatibility(
        catalog, live, desired, verified_maintenance=(desired.views[0].name,)
    ).operation_safe


@pytest.mark.parametrize(
    "options",
    [
        {"dependents": True},
        {"unknown": True},
        {"actor": "different"},
        {"invalid_index": True},
        {"grantor": "other"},
        {"reloptions": ("fillfactor=80",)},
    ],
)
def test_unproven_materialized_replacement_keeps_explicit_blockers(options):
    from schemii.schemii.migrations.planner import materialized_maintenance

    catalog = materialized_catalog(**options)
    live = import_postgres_catalog(catalog).content
    desired = live.model_copy(deep=True)
    desired.views[0].definition = "SELECT 2 AS value"
    report, _ = materialized_maintenance(catalog, desired.views[0], "replace")
    assert not report.eligible and report.blocking_reasons
    steps, blockers = compile_migration_steps("public", live, desired, catalog=catalog)
    assert blockers and not steps


def test_current_population_is_observed_state_not_repeating_create_or_refresh_intent():
    from schemii.schemii.migrations.planner import new_baseline_content

    empty = materialized_catalog(populated=False)
    desired = import_postgres_catalog(empty).content
    populated = materialized_catalog(populated=True)
    live, _, _ = new_baseline_content(populated, desired)
    assert live.views[0].populate_on_create is False
    steps, blockers = compile_migration_steps(
        "public", live, desired, catalog=populated
    )
    assert steps == [] and blockers == []


@pytest.fixture
def maintenance_service(request):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from schemii.common.connections.models import ResolvedPostgresConnection
    from schemii.common.postgres.gateway import PostgresMigrationResult
    from schemii.schemii.designs.store import InMemoryDesignRepository
    from schemii.schemii.migrations.repository import InMemoryMigrationRepository
    from schemii.schemii.migrations.service import MigrationService
    from schemii.schemii.workspaces.models import WorkspaceCreateRecord
    from schemii.schemii.workspaces.store import InMemoryWorkspaceRepository

    designs = InMemoryDesignRepository()
    workspaces = InMemoryWorkspaceRepository(designs=designs)
    repository = InMemoryMigrationRepository(designs)
    connection = ResolvedPostgresConnection(
        id="pg_" + "1" * 32,
        owner_id="owner",
        revision=1,
        name="Source",
        host="source.test",
        database=(getattr(request, "param", None) or materialized_catalog()).database,
        username="author",
        port=5432,
        ssl_mode="prefer",
        connect_timeout=3,
    )

    class Access:
        @contextmanager
        def use(self, owner, connection_id):
            assert (owner, connection_id) == ("owner", connection.id)
            yield connection

    class Gateway:
        def __init__(self):
            self.catalog = getattr(request, "param", None) or materialized_catalog()
            self.after_catalog = None
            self.calls = 0

        def introspect(self, *_):
            return self.catalog

        def execute_migration(
            self,
            _connection,
            _namespace,
            fingerprint,
            statements,
            *,
            on_started,
            on_intended,
            **_,
        ):
            assert fingerprint == self.catalog.fingerprint
            self.calls += 1
            on_started("42", {"database": "analytics"})
            after = self.after_catalog or materialized_catalog(populated=True)
            try:
                on_intended(after)
            except Exception as error:
                from schemii.common.postgres.errors import (
                    PostgresMigrationExecutionError,
                )

                raise PostgresMigrationExecutionError(len(statements)) from error
            self.catalog = after
            return PostgresMigrationResult(
                transaction_id="42",
                target_identity={"database": "analytics"},
                catalog=after,
                completed_step_count=len(statements),
            )

    gateway = Gateway()
    service = MigrationService(
        repository=repository,
        connection_access=Access(),
        postgres=gateway,
        workspaces=workspaces,
        designs=designs,
    )
    workspace = service.create_import_workspace(
        "owner",
        WorkspaceCreateRecord(
            name="Owned cache",
            connection_id=connection.id,
            connection_owner_id="owner",
            database=connection.database,
            namespace="public",
        ),
        import_postgres_catalog(gateway.catalog),
        1,
        gateway.catalog,
    )
    return SimpleNamespace(
        service=service,
        workspace=workspace,
        designs=designs,
        repository=repository,
        gateway=gateway,
    )


def test_reviewed_refresh_is_one_shot_acknowledged_and_keeps_honest_coverage(
    maintenance_service,
):
    from schemii.schemii.migrations.models import (
        MigrationPlanCreate,
        MigrationExecutionCreate,
    )
    from schemii.schemii.migrations.repository import ExecutionWork
    from schemii.schemii.migrations.service import MigrationServiceError

    f = maintenance_service
    design = f.designs.get("owner", f.workspace.id)
    request = MigrationPlanCreate(
        expected_workspace_revision=f.workspace.revision,
        expected_design_revision=design.revision,
        allow_destructive=True,
        refresh_materialized_view_ids=[design.content.views[0].id],
    )
    plan = f.service.create_plan("owner", f.workspace.id, request)
    assert plan.apply_capable and plan.compatibility.operation_safe
    assert not plan.compatibility.editable_complete
    assert (
        len(plan.steps) == 1
        and plan.steps[0].operation == "refresh"
        and "CONCURRENTLY" not in plan.steps[0].sql
    )
    assert plan.requires_materialized_maintenance_acknowledgement
    assert plan.materialized_maintenance[0].preserved_indexes == ["saved_idx"]
    assert f.gateway.calls == 0
    assert plan.materialized_refresh_candidates[0].eligible
    assert plan.materialized_refresh_candidates[0].view_id == design.content.views[0].id
    with pytest.raises(MigrationServiceError, match="acknowledge"):
        f.service.create_execution(
            "owner",
            plan.id,
            MigrationExecutionCreate(
                review_digest=plan.review_digest, confirm_destructive=True
            ),
        )
    reservation = f.service.create_execution(
        "owner",
        plan.id,
        MigrationExecutionCreate(
            review_digest=plan.review_digest,
            confirm_destructive=True,
            confirm_materialized_maintenance=True,
        ),
    )
    work = f.service.execution_coordinator.claim_next("worker")
    assert work is not None
    result = f.service.execution_coordinator.process(work)
    assert result.status == "succeeded" and result.sync_status == "succeeded"
    assert f.gateway.calls == 1
    baseline = f.repository.current_baseline("owner", f.workspace.id)
    assert baseline.complete is False and baseline.issues
    with pytest.raises(MigrationServiceError) as error:
        f.service.preview_baseline_reset("owner", f.workspace.id)
    assert error.value.code == "baseline_reset_incomplete"
    current = f.designs.get("owner", f.workspace.id)
    again = f.service.create_plan(
        "owner",
        f.workspace.id,
        MigrationPlanCreate(
            expected_workspace_revision=f.workspace.revision,
            expected_design_revision=current.revision,
        ),
    )
    assert again.steps == []
    f.service.execution_coordinator.process(
        ExecutionWork(
            kind="sync", record=f.repository.get_execution("owner", reservation.id)
        )
    )
    assert f.gateway.calls == 1


def test_parser_equivalence_accepts_only_resolved_default_schema_and_simple_column_qualification():
    from schemii.schemii.migrations.planner import view_definitions_equivalent

    catalog = mixed()
    assert view_definitions_equivalent(
        "public",
        "SELECT id * 10 AS value FROM ordinary",
        "SELECT ordinary.id * 10 AS value FROM public.ordinary;",
        catalog,
    )
    assert not view_definitions_equivalent(
        "public",
        "SELECT id * 10 FROM ordinary",
        "SELECT id * 11 FROM ordinary",
        catalog,
    )
    assert not view_definitions_equivalent(
        "public", "SELECT id FROM ordinary", "SELECT id::text FROM ordinary", catalog
    )
    assert not view_definitions_equivalent(
        "public", "SELECT id FROM ordinary", "SELECT id FROM external.ordinary", catalog
    )


def test_format_only_definition_change_does_not_invent_a_replacement_before_refresh(
    maintenance_service,
):
    from schemii.schemii.designs.models import SchemiiDesignReplace
    from schemii.schemii.migrations.models import MigrationPlanCreate

    f = maintenance_service
    current = f.designs.get("owner", f.workspace.id)
    desired = current.content.model_copy(deep=True)
    desired.views[0].definition = "SELECT (1) AS value;"
    changed = f.designs.replace(
        "owner",
        f.workspace.id,
        SchemiiDesignReplace(
            expected_design_revision=current.revision, content=desired
        ),
    )
    plan = f.service.create_plan(
        "owner",
        f.workspace.id,
        MigrationPlanCreate(
            expected_workspace_revision=f.workspace.revision,
            expected_design_revision=changed.revision,
            allow_destructive=True,
            refresh_materialized_view_ids=[desired.views[0].id],
        ),
    )
    assert plan.apply_capable
    assert [item.operation for item in plan.materialized_maintenance] == ["refresh"]
    assert len(plan.steps) == 1


@pytest.mark.parametrize("maintenance_service", [mixed()], indirect=True)
def test_commit_guard_keeps_protected_bytes_even_when_losing_the_feature_makes_import_complete(
    maintenance_service,
):
    from schemii.schemii.designs.models import SchemiiDesignReplace
    from schemii.schemii.migrations.models import (
        MigrationPlanCreate,
        MigrationExecutionCreate,
    )

    f = maintenance_service
    current = f.designs.get("owner", f.workspace.id)
    desired = current.content.model_copy(deep=True)
    next(item for item in desired.tables if item.name == "ordinary").columns[
        0
    ].nullable = True
    changed = f.designs.replace(
        "owner",
        f.workspace.id,
        SchemiiDesignReplace(
            expected_design_revision=current.revision, content=desired
        ),
    )
    plan = f.service.create_plan(
        "owner",
        f.workspace.id,
        MigrationPlanCreate(
            expected_workspace_revision=f.workspace.revision,
            expected_design_revision=changed.revision,
        ),
    )
    assert plan.apply_capable and not plan.compatibility.editable_complete
    before = f.gateway.catalog
    tables = tuple(
        table.model_copy(update={"kind": "table", "partition_key": None})
        if table.name == "protected"
        else table.model_copy(
            update={
                "columns": (table.columns[0].model_copy(update={"nullable": True}),)
            }
        )
        for table in before.tables
    )
    safety = before.safety.model_copy(
        update={
            "preservation": tuple(
                item.model_copy(update={"relation_kind": "r"})
                for item in before.safety.preservation
            )
        }
    )
    f.gateway.after_catalog = build_postgres_catalog(
        **before.model_dump(exclude={"fingerprint", "captured_at", "tables", "safety"}),
        tables=tables,
        safety=safety,
        captured_at=before.captured_at,
    )
    assert import_postgres_catalog(f.gateway.after_catalog).summary.complete
    f.service.create_execution(
        "owner", plan.id, MigrationExecutionCreate(review_digest=plan.review_digest)
    )
    work = f.service.execution_coordinator.claim_next("worker")
    result = f.service.execution_coordinator.process(work)
    assert result.status == "failed" and result.commit_outcome == "rolled_back"
    assert result.error_code == "migration_result_mismatch"
    assert f.gateway.catalog == before
    assert f.repository.current_baseline("owner", f.workspace.id).catalog == before


def editable_catalog():
    before = mixed()
    return build_postgres_catalog(
        **before.model_dump(exclude={"fingerprint", "captured_at", "tables", "safety"}),
        tables=(before.tables[1],),
        safety=before.safety.model_copy(
            update={"preservation": (before.safety.preservation[1],)}
        ),
        captured_at=before.captured_at,
    )


@pytest.mark.parametrize("maintenance_service", [editable_catalog()], indirect=True)
def test_commit_guard_rechecks_original_impact_against_new_unknown_dependencies(
    maintenance_service,
):
    from schemii.schemii.designs.models import SchemiiDesignReplace
    from schemii.schemii.migrations.models import (
        MigrationPlanCreate,
        MigrationExecutionCreate,
    )

    f = maintenance_service
    # Fully editable input needs the operation footprint checked again even when
    # convergence itself has no remaining edits and no protected objects.
    before = f.gateway.catalog
    current = f.designs.get("owner", f.workspace.id)
    desired = current.content.model_copy(deep=True)
    desired.tables[0].columns[0].nullable = True
    changed = f.designs.replace(
        "owner",
        f.workspace.id,
        SchemiiDesignReplace(
            expected_design_revision=current.revision, content=desired
        ),
    )
    plan = f.service.create_plan(
        "owner",
        f.workspace.id,
        MigrationPlanCreate(
            expected_workspace_revision=f.workspace.revision,
            expected_design_revision=changed.revision,
        ),
    )
    assert plan.apply_capable and plan.compatibility.editable_complete
    f.gateway.after_catalog = build_postgres_catalog(
        **before.model_dump(exclude={"fingerprint", "captured_at", "tables", "safety"}),
        tables=(
            before.tables[0].model_copy(
                update={
                    "columns": (
                        before.tables[0]
                        .columns[0]
                        .model_copy(update={"nullable": True}),
                    )
                }
            ),
        ),
        safety=before.safety.model_copy(
            update={
                "dependency_complete": False,
                "unknown_reasons": ("untracked new dependency",),
            }
        ),
        captured_at=before.captured_at,
    )
    assert import_postgres_catalog(f.gateway.after_catalog).summary.complete
    f.service.create_execution(
        "owner", plan.id, MigrationExecutionCreate(review_digest=plan.review_digest)
    )
    work = f.service.execution_coordinator.claim_next("worker")
    result = f.service.execution_coordinator.process(work)
    assert result.status == "failed" and result.commit_outcome == "rolled_back"
    assert result.error_code == "migration_result_mismatch"
    assert f.gateway.catalog == before
    assert f.repository.current_baseline("owner", f.workspace.id).catalog == before


def test_opaque_policy_definitions_affect_drift_coverage_and_preservation():
    import json

    catalog = editable_catalog()
    descriptor = catalog.safety.preservation[0]
    descriptor = descriptor.model_copy(
        update={
            "properties": json.dumps(
                {"policies": [{"name": "policy", "using": "id > 0"}]}
            )
        }
    )
    before = build_postgres_catalog(
        **catalog.model_dump(exclude={"fingerprint", "captured_at", "safety"}),
        safety=catalog.safety.model_copy(
            update={
                "preservation": (descriptor,),
                "dependency_complete": False,
                "unknown_reasons": ("Unmodeled policy dependency",),
            }
        ),
        captured_at=catalog.captured_at,
    )
    imported = import_postgres_catalog(before)
    assert not imported.summary.complete
    assert imported.summary.issues[0].identity == relation("ordinary")
    changed = descriptor.model_copy(
        update={
            "properties": json.dumps(
                {"policies": [{"name": "policy", "using": "id > 1"}]}
            )
        }
    )
    after = build_postgres_catalog(
        **before.model_dump(exclude={"fingerprint", "captured_at", "safety"}),
        safety=before.safety.model_copy(update={"preservation": (changed,)}),
        captured_at=before.captured_at,
    )
    assert before.fingerprint != after.fingerprint
    live, desired = change(before, "ordinary")
    assert not migration_compatibility(before, live, desired).operation_safe
