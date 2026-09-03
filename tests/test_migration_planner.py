from __future__ import annotations

from schemii.schemii.designs.models import (
    DesignCheckConstraint,
    DesignColumn,
    DesignIndex,
    DesignKeyConstraint,
    DesignRelationship,
    DesignTable,
    DesignView,
    SchemiiDesignContent,
)
from schemii.schemii.migrations.planner import (
    compile_migration_steps,
    tables_requiring_empty_for_required_columns,
)
from schemii.schemii.migrations.type_changes import classify_type_change


def _id(kind: str, value: str) -> str:
    return f"{kind}_{value * 32}"


def _column(value: str, name: str, data_type: str, **changes: object) -> DesignColumn:
    return DesignColumn(
        id=_id("column", value),
        name=name,
        data_type=data_type,
        **changes,
    )


def _table(
    value: str,
    name: str,
    columns: list[DesignColumn],
    *,
    keys: list[DesignKeyConstraint] | None = None,
    checks: list[DesignCheckConstraint] | None = None,
    indexes: list[DesignIndex] | None = None,
) -> DesignTable:
    return DesignTable(
        id=_id("table", value),
        name=name,
        columns=columns,
        keys=keys or [],
        checks=checks or [],
        indexes=indexes or [],
    )


def test_type_classifier_only_allows_provably_non_narrowing_changes() -> None:
    assert classify_type_change("int4", "integer").disposition == "equivalent"
    assert classify_type_change("integer", "bigint").disposition == "safe"
    assert classify_type_change("varchar(40)", "character varying(80)").disposition == "safe"
    assert classify_type_change("numeric(8, 2)", "numeric(10, 4)").disposition == "safe"
    assert classify_type_change("timestamptz(3)", "timestamp(6) with time zone").disposition == "safe"
    assert classify_type_change("bigint", "integer").disposition == "blocked"
    assert classify_type_change("text", "varchar(20)").disposition == "blocked"
    assert classify_type_change("jsonb", "text").disposition == "blocked"


def test_safe_type_change_preserves_postgresql_managed_checks_and_indexes() -> None:
    name = _column("a", "name", "character varying(40)")
    table = _table(
        "b",
        "customers",
        [name],
        checks=[
            DesignCheckConstraint(
                id=_id("check", "c"),
                name="customers_name_check",
                expression="name <> ''",
                column_ids=[name.id],
            )
        ],
        indexes=[
            DesignIndex(
                id=_id("index", "d"),
                name="customers_name_idx",
                column_ids=[name.id],
            )
        ],
    )
    live = SchemiiDesignContent(tables=[table])
    desired = live.model_copy(deep=True)
    desired.tables[0].columns[0].data_type = "varchar(80)"

    steps, blockers = compile_migration_steps("public", live, desired)

    assert blockers == []
    assert [step.operation for step in steps] == ["alter_type"]
    assert steps[0].sql == (
        'ALTER TABLE "public"."customers" ALTER COLUMN "name" TYPE varchar(80);'
    )
    assert steps[0].data_movement is True


def test_ambiguous_or_narrowing_type_change_is_blocked() -> None:
    table = _table("a", "events", [_column("b", "payload", "jsonb")])
    live = SchemiiDesignContent(tables=[table])
    desired = live.model_copy(deep=True)
    desired.tables[0].columns[0].data_type = "text"

    steps, blockers = compile_migration_steps("public", live, desired)

    assert steps == []
    assert [warning.code for warning in blockers] == ["column_type_conversion_required"]
    assert "reviewed USING expression" in blockers[0].message


def test_required_column_uses_exact_empty_table_evidence() -> None:
    table = _table("a", "events", [_column("b", "id", "bigint", nullable=False)])
    live = SchemiiDesignContent(tables=[table])
    desired = live.model_copy(deep=True)
    desired.tables[0].columns.append(
        _column("c", "tenant_id", "bigint", nullable=False)
    )

    assert tables_requiring_empty_for_required_columns(live, desired) == {"events"}
    blocked_steps, blockers = compile_migration_steps("public", live, desired)
    allowed_steps, allowed_blockers = compile_migration_steps(
        "public",
        live,
        desired,
        empty_tables={"events"},
    )

    assert blocked_steps == []
    assert [warning.code for warning in blockers] == ["required_column_population_required"]
    assert allowed_blockers == []
    assert [step.operation for step in allowed_steps] == ["add"]
    assert 'ADD COLUMN "tenant_id" bigint NOT NULL' in allowed_steps[0].sql


def test_required_column_with_default_does_not_need_empty_table() -> None:
    table = _table("a", "events", [_column("b", "id", "bigint", nullable=False)])
    live = SchemiiDesignContent(tables=[table])
    desired = live.model_copy(deep=True)
    desired.tables[0].columns.append(
        _column(
            "c",
            "state",
            "text",
            nullable=False,
            default_expression="'new'::text",
        )
    )

    steps, blockers = compile_migration_steps("public", live, desired)

    assert tables_requiring_empty_for_required_columns(live, desired) == frozenset()
    assert blockers == []
    assert len(steps) == 1
    assert steps[0].data_movement is True


def test_foreign_key_is_rebuilt_around_safe_type_changes() -> None:
    customer_id = _column("a", "id", "integer", nullable=False)
    order_customer_id = _column("b", "customer_id", "integer", nullable=False)
    customer_key = DesignKeyConstraint(
        id=_id("key", "c"),
        name="customers_pkey",
        kind="primary",
        column_ids=[customer_id.id],
    )
    customers = _table("d", "customers", [customer_id], keys=[customer_key])
    orders = _table("e", "orders", [order_customer_id])
    relationship = DesignRelationship(
        id=_id("relationship", "f"),
        name="orders_customer_id_fkey",
        source_table_id=orders.id,
        source_column_ids=[order_customer_id.id],
        target_table_id=customers.id,
        target_column_ids=[customer_id.id],
    )
    live = SchemiiDesignContent(
        tables=[customers, orders],
        relationships=[relationship],
    )
    desired = live.model_copy(deep=True)
    for table in desired.tables:
        table.columns[0].data_type = "bigint"

    steps, blockers = compile_migration_steps("public", live, desired)

    assert blockers == []
    assert [step.operation for step in steps] == [
        "drop",
        "alter_type",
        "alter_type",
        "create",
    ]
    assert steps[0].object_kind == "relationship"
    assert steps[-1].object_kind == "relationship"


def test_safe_type_change_reports_source_derived_view_dependency() -> None:
    table = _table("a", "events", [_column("b", "label", "varchar(20)")])
    view = DesignView(
        id=_id("view", "c"),
        name="event_labels",
        kind="view",
        definition="SELECT label FROM events",
    )
    live = SchemiiDesignContent(tables=[table], views=[view])
    desired = live.model_copy(deep=True)
    desired.tables[0].columns[0].data_type = "varchar(40)"

    steps, blockers = compile_migration_steps("public", live, desired)

    assert steps == []
    assert [warning.code for warning in blockers] == [
        "column_type_dependency_requires_review"
    ]
    assert "view event_labels" in blockers[0].message
