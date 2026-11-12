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
