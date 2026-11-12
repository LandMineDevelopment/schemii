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
        version=2,
        preservation=tuple(
            PostgresPreservation(
                identity=relation(name),
                relation_kind="p" if name == "protected" else "r",
                owner="author",
                actor="author",
                acl=(),
                column_acl=(),
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
        column_acl=(),
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
            version=2,
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


@pytest.mark.parametrize("namespace", ["public", 'Sales.eu "quoted"'])
@pytest.mark.parametrize("protected_name", ["orders", 'orders.part "quoted"'])
def test_dotted_display_names_never_choose_the_protected_object_identity(
    namespace, protected_name
):
    before = editable_catalog()
    ordinary = before.tables[0]
    orders = ordinary.model_copy(
        update={
            "namespace": namespace,
            "name": protected_name,
            "columns": (
                ordinary.columns[0].model_copy(
                    update={
                        "name": "region.code",
                        "collation_schema": "pg_catalog",
                        "collation_name": "C",
                    }
                ),
            ),
        }
    )
    collision = ordinary.model_copy(
        update={"namespace": namespace, "name": protected_name + ".region"}
    )
    descriptor = before.safety.preservation[0]
    identities = [
        relation(name, namespace)
        for name in (protected_name, protected_name + ".region")
    ]
    catalog = build_postgres_catalog(
        **before.model_dump(
            exclude={"fingerprint", "captured_at", "namespace", "tables", "safety"}
        ),
        namespace=namespace,
        tables=(orders, collision),
        safety=before.safety.model_copy(
            update={
                "preservation": tuple(
                    descriptor.model_copy(update={"identity": identity})
                    for identity in identities
                )
            }
        ),
        captured_at=before.captured_at,
    )
    imported = import_postgres_catalog(catalog)
    assert imported.summary.issues[0].identity == identities[0]
    assert imported.summary.issues[0].protected_scope == [identities[0]]
    desired = imported.content.model_copy(deep=True)
    desired.tables = [item for item in desired.tables if item.name != protected_name]
    compatibility = migration_compatibility(catalog, imported.content, desired)
    assert not compatibility.operation_safe
    after = build_postgres_catalog(
        **catalog.model_dump(
            exclude={"fingerprint", "captured_at", "tables", "safety"}
        ),
        tables=(collision,),
        safety=catalog.safety.model_copy(
            update={"preservation": (catalog.safety.preservation[1],)}
        ),
        captured_at=catalog.captured_at,
    )
    assert not preservation_matches(catalog, after, compatibility)


def test_legacy_issue_display_label_cannot_create_identity_authority():
    from schemii.schemii.designs.importer import _structured_issues
    from schemii.schemii.workspaces.models import WorkspaceImportIssue

    issue = WorkspaceImportIssue(
        category="column",
        object_name="public.ordinary.id",
        reason="Legacy issue has no source identity",
    )
    result = _structured_issues(editable_catalog(), [issue])[0]
    assert result.identity is None and not result.dependency_complete
    assert any("identity is unavailable" in reason for reason in result.unknown_reasons)


def test_cte_and_nested_query_bindings_keep_strict_namespace_qualification():
    from schemii.common.postgres.models import PostgresView
    from schemii.schemii.migrations.planner import view_definitions_equivalent

    before = editable_catalog()
    qualified = "WITH ordinary AS (SELECT 2 AS id) SELECT id FROM public.ordinary"
    shadowed = "WITH ordinary AS (SELECT 2 AS id) SELECT id FROM ordinary"
    assert not view_definitions_equivalent("public", qualified, shadowed, before)
    assert not view_definitions_equivalent(
        "public",
        "SELECT id FROM ordinary WHERE EXISTS (WITH ordinary AS (SELECT 2 AS id) SELECT id FROM public.ordinary)",
        "SELECT id FROM ordinary WHERE EXISTS (WITH ordinary AS (SELECT 2 AS id) SELECT id FROM ordinary)",
        before,
    )
    view = PostgresView(
        namespace="public",
        name="shown",
        columns=before.tables[0].columns,
        query_definition=qualified,
    )
    descriptor = before.safety.preservation[0].model_copy(
        update={"identity": relation("shown"), "relation_kind": "v"}
    )
    catalog = build_postgres_catalog(
        **before.model_dump(exclude={"fingerprint", "captured_at", "views", "safety"}),
        views=(view,),
        safety=before.safety.model_copy(
            update={"preservation": (*before.safety.preservation, descriptor)}
        ),
        captured_at=before.captured_at,
    )
    live = import_postgres_catalog(catalog).content
    desired = live.model_copy(deep=True)
    desired.views[0].definition = shadowed
    steps, blockers = compile_migration_steps("public", live, desired, catalog=catalog)
    assert len(steps) == 1 and not blockers and "CREATE OR REPLACE VIEW" in steps[0].sql
    assert relation("shown") in migration_compatibility(catalog, live, desired).impact


def identity_sequence_catalog(
    *, owner_proof=True, identity=True, edge=True, table_owner="author"
):
    from schemii.common.postgres.models import PostgresIdentitySequenceOwner

    before = editable_catalog()
    table = before.tables[0].model_copy(
        update={
            "name": "orders",
            "columns": (
                before.tables[0]
                .columns[0]
                .model_copy(update={"identity": "by_default" if identity else None}),
            ),
        }
    )
    descriptor = before.safety.preservation[0].model_copy(
        update={"identity": relation("orders"), "owner": table_owner}
    )
    sequence = descriptor.model_copy(
        update={
            "identity": relation("orders_id_seq"),
            "relation_kind": "S",
            "owner": "author",
            "access_method": None,
            "identity_sequence_owner": PostgresIdentitySequenceOwner(
                table=relation("orders"),
                column="id",
                ordinal=1,
                table_owner=table_owner,
                dependency_kind="i",
            )
            if owner_proof
            else None,
        }
    )
    return build_postgres_catalog(
        **before.model_dump(exclude={"fingerprint", "captured_at", "tables", "safety"}),
        tables=(table,),
        safety=before.safety.model_copy(
            update={
                "preservation": (descriptor, sequence),
                "dependencies": (
                    PostgresDependency(
                        source=sequence.identity, target=descriptor.identity, kind="i"
                    ),
                )
                if edge
                else (),
            }
        ),
        captured_at=before.captured_at,
    )


def test_exact_identity_sequence_machinery_does_not_quarantine_supported_create_or_edit():
    from schemii.schemii.designs.models import DesignColumn, DesignTable

    after = identity_sequence_catalog()
    before = build_postgres_catalog(
        **after.model_dump(exclude={"fingerprint", "captured_at", "tables", "safety"}),
        tables=(),
        safety=after.safety.model_copy(update={"preservation": (), "dependencies": ()}),
        captured_at=after.captured_at,
    )
    live = import_postgres_catalog(before).content
    desired = live.model_copy(deep=True)
    desired.tables = [
        DesignTable(
            id="table_" + "a" * 32,
            name="orders",
            columns=[
                DesignColumn(
                    id="column_" + "b" * 32,
                    name="id",
                    data_type="bigint",
                    nullable=False,
                    identity="by_default",
                )
            ],
        )
    ]
    steps, blockers = compile_migration_steps("public", live, desired, catalog=before)
    assert (
        len(steps) == 1
        and "GENERATED BY DEFAULT AS IDENTITY" in steps[0].sql
        and not blockers
    )
    assert migration_compatibility(before, live, desired).operation_safe
    assert import_postgres_catalog(after).summary.complete
    assert migration_compatibility(after, live, desired).operation_safe
    current = import_postgres_catalog(after).content
    edited = current.model_copy(deep=True)
    edited.tables[0].columns.append(
        DesignColumn(id="column_" + "c" * 32, name="added", data_type="text")
    )
    assert migration_compatibility(after, current, edited).operation_safe


@pytest.mark.parametrize(
    "options",
    [
        {"owner_proof": False},
        {"identity": False},
        {"table_owner": "other_owner"},
    ],
)
def test_standalone_unknown_or_mismatched_sequence_ownership_stays_protected(options):
    catalog = identity_sequence_catalog(**options)
    imported = import_postgres_catalog(catalog)
    assert not imported.summary.complete
    assert any(
        issue.identity == relation("orders_id_seq") for issue in imported.summary.issues
    )
    desired = imported.content.model_copy(deep=True)
    desired.tables = []
    assert not migration_compatibility(
        catalog, imported.content, desired
    ).operation_safe


def test_materialized_column_grants_bind_drift_block_replacement_but_allow_refresh():
    from schemii.common.postgres.models import PostgresColumnGrant
    from schemii.schemii.migrations.planner import (
        materialized_maintenance,
        materialized_preservation_matches,
    )

    before = materialized_catalog()
    old = before.safety.preservation[0]
    grant = PostgresColumnGrant(
        column="value",
        ordinal=1,
        grantor="author",
        grantee="column_reader",
        privilege="SELECT",
        grantable=True,
    )
    catalog = build_postgres_catalog(
        **before.model_dump(exclude={"fingerprint", "captured_at", "safety"}),
        safety=before.safety.model_copy(
            update={"preservation": (old.model_copy(update={"column_acl": (grant,)}),)}
        ),
        captured_at=before.captured_at,
    )
    view = import_postgres_catalog(catalog).content.views[0]
    report, _ = materialized_maintenance(catalog, view, "replace")
    assert not report.eligible and any(
        "Column-level" in reason for reason in report.blocking_reasons
    )
    refresh, _ = materialized_maintenance(catalog, view, "refresh")
    assert refresh.eligible
    assert catalog.fingerprint != before.fingerprint
    after = build_postgres_catalog(
        **catalog.model_dump(
            exclude={"fingerprint", "captured_at", "materialized_views", "safety"}
        ),
        materialized_views=(
            catalog.materialized_views[0].model_copy(update={"populated": True}),
        ),
        safety=catalog.safety.model_copy(update={"preservation": (old,)}),
        captured_at=catalog.captured_at,
    )
    assert not materialized_preservation_matches(catalog, after, [refresh])


def test_missing_internal_identity_edge_cannot_claim_complete_evidence():
    with pytest.raises(ValueError, match="exact internal dependency"):
        identity_sequence_catalog(edge=False)


def test_quoted_routine_overloads_keep_exact_source_identity_signatures():
    from schemii.common.postgres.models import PostgresFunction

    before = editable_catalog()
    namespace = 'Sales.eu "quoted"'
    routines = tuple(
        PostgresFunction(
            namespace=namespace,
            name="parse.fn",
            kind="function",
            identity_arguments=signature,
            arguments=signature,
            return_type="integer",
            language="sql",
            definition="SELECT 1",
        )
        for signature in ("value integer", "value text")
    )
    catalog = build_postgres_catalog(
        **before.model_dump(
            exclude={
                "fingerprint",
                "captured_at",
                "namespace",
                "functions",
                "tables",
                "safety",
            }
        ),
        namespace=namespace,
        functions=routines,
        tables=(),
        safety=before.safety.model_copy(
            update={
                "preservation": (),
                "dependency_complete": False,
                "unknown_reasons": ("Routine bodies are unproven",),
            }
        ),
        captured_at=before.captured_at,
    )
    issues = import_postgres_catalog(catalog).summary.issues
    assert len(issues) == 2
    assert {issue.identity for issue in issues} == {
        PostgresObjectIdentity(
            kind="routine", namespace=namespace, name="parse.fn", signature=signature
        )
        for signature in ("value integer", "value text")
    }


def ordinary_not_null_catalog(**constraint_changes):
    from schemii.common.postgres.models import PostgresNotNullConstraint

    source = mixed()
    constraint = PostgresNotNullConstraint(
        **{
            "name": 'exact "NN"',
            "table": "ordinary",
            "columns": ("id",),
            "definition": "NOT NULL id",
            "validated": True,
            "is_local": True,
            "inheritance_count": 0,
            "no_inherit": False,
            **constraint_changes,
        }
    )
    table = source.tables[1].model_copy(update={"not_null_constraints": (constraint,)})
    return build_postgres_catalog(
        **source.model_dump(
            exclude={
                "fingerprint",
                "captured_at",
                "tables",
                "safety",
                "server_version",
                "server_version_num",
            }
        ),
        tables=(table,),
        server_version="18.0",
        server_version_num=180000,
        safety=source.safety.model_copy(
            update={"preservation": (source.safety.preservation[1],)}
        ),
        captured_at=source.captured_at,
    )


def test_native_not_null_name_roundtrips_without_assuming_generated_name_origin():
    from schemii.schemii.migrations.planner import new_baseline_content

    catalog = ordinary_not_null_catalog()
    imported = import_postgres_catalog(catalog)
    assert imported.summary.complete
    current = imported.content
    assert current.tables[0].columns[0].not_null_constraint_name == 'exact "NN"'
    created = current.model_copy(deep=True)
    created.tables[0].columns[0].not_null_constraint_name = None
    candidate, issues, complete = new_baseline_content(catalog, created)
    assert complete and not issues
    assert candidate.tables[0].columns[0].not_null_constraint_name == 'exact "NN"'
    assert compile_migration_steps("public", candidate, created, catalog=catalog) == (
        [],
        [],
    )
    empty = current.model_copy(update={"tables": []})
    steps, blockers = compile_migration_steps("public", empty, current, catalog=catalog)
    assert not blockers and 'CONSTRAINT "exact ""NN""" NOT NULL' in steps[0].sql
    renamed = current.model_copy(deep=True)
    renamed.tables[0].columns[0].name = "renamed.id"
    renamed.tables[0].columns[0].not_null_constraint_name = "new.name"
    steps, blockers = compile_migration_steps(
        "public", current, renamed, catalog=catalog
    )
    assert not blockers
    assert any(
        'RENAME CONSTRAINT "exact ""NN""" TO "new.name"' in step.sql for step in steps
    )
    nullable = current.model_copy(deep=True)
    nullable.tables[0].columns[0].nullable = True
    nullable.tables[0].columns[0].not_null_constraint_name = None
    steps, blockers = compile_migration_steps(
        "public", current, nullable, catalog=catalog
    )
    assert not blockers and len(steps) == 1 and "DROP NOT NULL" in steps[0].sql
    drop_review = reconcile_designs(current, nullable, catalog)
    assert (
        not drop_review.conflicts and drop_review.merged.tables[0].columns[0].nullable
    )
    keep_review = reconcile_designs(current, created, catalog)
    assert (
        not keep_review.conflicts
        and keep_review.merged.tables[0].columns[0].not_null_constraint_name
        == 'exact "NN"'
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"is_local": None},
        {"inheritance_count": None},
        {"no_inherit": None},
        {"is_local": False},
        {"inheritance_count": 1},
        {"no_inherit": True},
        {"validated": False},
    ],
)
def test_not_null_unknown_inherited_noinherit_and_unvalidated_contracts_stay_protected(
    changes,
):
    catalog = ordinary_not_null_catalog(**changes)
    imported = import_postgres_catalog(catalog)
    assert not imported.summary.complete and imported.summary.issues
    assert imported.content.tables[0].columns[0].not_null_constraint_name is None
    changed = imported.content.model_copy(deep=True)
    changed.tables[0].columns[0].nullable = True
    assert not migration_compatibility(
        catalog, imported.content, changed
    ).operation_safe


def test_multiple_not_null_names_do_not_import_a_guessed_single_constraint():
    before = ordinary_not_null_catalog()
    table = before.tables[0]
    constraints = table.not_null_constraints + (
        table.not_null_constraints[0].model_copy(update={"name": "another"}),
    )
    catalog = build_postgres_catalog(
        **before.model_dump(exclude={"fingerprint", "captured_at", "tables"}),
        tables=(table.model_copy(update={"not_null_constraints": constraints}),),
        captured_at=before.captured_at,
    )
    result = import_postgres_catalog(catalog)
    assert not result.summary.complete and len(result.summary.issues) == 2
    assert result.content.tables[0].columns[0].not_null_constraint_name is None


def test_named_not_null_does_not_guess_an_unknown_native_identity_or_erase_a_concurrent_rename():
    unknown = ordinary_not_null_catalog(is_local=None)
    live = import_postgres_catalog(unknown).content
    desired = live.model_copy(deep=True)
    desired.tables[0].columns[0].not_null_constraint_name = "requested"
    _, blockers = compile_migration_steps("public", live, desired, catalog=unknown)
    assert any(
        item.code == "not_null_constraint_identity_unavailable" for item in blockers
    )
    known = ordinary_not_null_catalog()
    baseline = import_postgres_catalog(known).content
    nullable = baseline.model_copy(deep=True)
    nullable.tables[0].columns[0].nullable = True
    nullable.tables[0].columns[0].not_null_constraint_name = None
    changed = ordinary_not_null_catalog(name="concurrently_renamed")
    assert reconcile_designs(baseline, nullable, changed).conflicts
    older = build_postgres_catalog(
        **known.model_dump(
            exclude={
                "fingerprint",
                "captured_at",
                "server_version",
                "server_version_num",
            }
        ),
        server_version="17.0",
        server_version_num=170000,
        captured_at=known.captured_at,
    )
    _, blockers = compile_migration_steps(
        "public", baseline.model_copy(update={"tables": []}), baseline, catalog=older
    )
    assert any(
        item.code == "named_not_null_requires_postgresql_18" for item in blockers
    )


def view_binding_catalog(query):
    from schemii.common.postgres.models import PostgresMaterializedView

    namespace = 'Cache "schema"'
    return build_postgres_catalog(
        database="data",
        namespace=namespace,
        server_version="18.0",
        server_version_num=180000,
        server_timezone="UTC",
        captured_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        tables=(
            PostgresTable(
                namespace=namespace,
                name="source_data",
                kind="table",
                is_partition=False,
                columns=(
                    PostgresColumn(
                        name="value", ordinal=1, data_type="integer", nullable=True
                    ),
                ),
            ),
        ),
        relationships=(),
        functions=(),
        views=(),
        materialized_views=(
            PostgresMaterializedView(
                namespace=namespace,
                name="saved",
                columns=(
                    PostgresColumn(
                        name="value", ordinal=1, data_type="integer", nullable=True
                    ),
                ),
                query_definition=query,
                populated=True,
            ),
        ),
    )


def test_search_path_only_live_view_qualification_is_not_a_three_way_edit_conflict():
    catalog = view_binding_catalog('SELECT value FROM "Cache ""schema""".source_data')
    baseline = import_postgres_catalog(catalog).content
    baseline.views[0].definition = "SELECT value FROM source_data"
    desired = baseline.model_copy(deep=True)
    desired.views[
        0
    ].definition = 'SELECT value FROM "Cache ""schema""".source_data WHERE value >= 2'
    result = reconcile_designs(baseline, desired, catalog)
    assert not result.conflicts and not result.external_changes
    assert result.merged.views[0].definition == desired.views[0].definition


@pytest.mark.parametrize(
    "baseline_query,live_query",
    [
        (
            "SELECT value FROM external.source_data",
            'SELECT value FROM "Cache ""schema""".source_data',
        ),
        (
            "WITH source_data AS (SELECT 1 AS value) SELECT value FROM source_data",
            'WITH source_data AS (SELECT 1 AS value) SELECT value FROM "Cache ""schema""".source_data',
        ),
    ],
)
def test_three_way_view_comparison_retains_external_and_cte_binding_changes(
    baseline_query, live_query
):
    catalog = view_binding_catalog(live_query)
    baseline = import_postgres_catalog(catalog).content
    baseline.views[0].definition = baseline_query
    desired = baseline.model_copy(deep=True)
    desired.views[0].definition = baseline_query + " WHERE value >= 2"
    assert reconcile_designs(baseline, desired, catalog).conflicts
