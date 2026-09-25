from __future__ import annotations

from datetime import datetime, timezone

import pytest

from schemii.common.postgres.models import (
    PostgresCheckConstraint,
    PostgresColumn,
    PostgresTable,
    build_postgres_catalog,
)
from schemii.schemii.designs.importer import import_postgres_catalog
from schemii.schemii.designs.models import (
    DesignCheckConstraint,
    DesignColumn,
    DesignFunction,
    DesignIndex,
    DesignKeyConstraint,
    DesignRelationship,
    DesignTable,
    DesignTrigger,
    DesignView,
    SchemiiDesignContent,
)
from schemii.schemii.migrations.planner import (
    catalog_design,
    column_order_differences,
    compile_migration_steps,
    preserve_column_display_order,
    reconcile_designs,
    tables_requiring_empty_for_required_columns,
)
from schemii.schemii.migrations.checks import equivalent_check_expressions
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


def _routine(definition: str) -> DesignFunction:
    return DesignFunction.model_validate({"id": _id("function", "a"), "definition": definition})


def test_postgresql_check_rewrites_do_not_create_migration_or_drift() -> None:
    catalog = build_postgres_catalog(
        database="analytics",
        namespace="public",
        server_version="17.2",
        server_version_num=170002,
        server_timezone="UTC",
        tables=(PostgresTable(
            namespace="public",
            name="salary_bands",
            kind="table",
            is_partition=False,
            columns=(
                PostgresColumn(name="minimum_salary", ordinal=1, data_type="numeric", nullable=True),
                PostgresColumn(name="maximum_salary", ordinal=2, data_type="numeric", nullable=True),
                PostgresColumn(name="employment_status", ordinal=3, data_type="text", nullable=True),
            ),
            checks=(
                PostgresCheckConstraint(
                    name="salary_range_check",
                    table="salary_bands",
                    columns=("minimum_salary", "maximum_salary"),
                    definition=(
                        "CHECK (((minimum_salary >= (0)::numeric) AND "
                        "(maximum_salary >= minimum_salary)))"
                    ),
                    validated=True,
                ),
                PostgresCheckConstraint(
                    name="employment_status_check",
                    table="salary_bands",
                    columns=("employment_status",),
                    definition=(
                        "CHECK ((employment_status = ANY (ARRAY['active'::text, "
                        "'leave'::text, 'terminated'::text])))"
                    ),
                    validated=True,
                ),
            ),
        ),),
        relationships=(),
        functions=(),
        views=(),
        materialized_views=(),
        captured_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    live, warnings, complete = catalog_design(catalog)
    assert complete and warnings == []
    desired = live.model_copy(deep=True)
    checks = {check.name: check for check in desired.tables[0].checks}
    checks["salary_range_check"].expression = (
        "minimum_salary >= 0 AND maximum_salary >= minimum_salary"
    )
    checks["employment_status_check"].expression = (
        "employment_status IN ('active', 'leave', 'terminated')"
    )

    steps, blockers = compile_migration_steps("public", live, desired)
    review = reconcile_designs(live, desired, catalog)

    assert steps == []
    assert blockers == []
    assert review.conflicts == []
    assert review.external_changes == []
    assert review.merged is not None

    checks["salary_range_check"].expression = (
        "minimum_salary >= 1 AND maximum_salary >= minimum_salary"
    )
    checks["employment_status_check"].expression = (
        "employment_status IN ('active', 'inactive', 'terminated')"
    )
    changed_steps, changed_blockers = compile_migration_steps("public", live, desired)

    assert changed_blockers == []
    assert [step.operation for step in changed_steps] == ["drop", "drop", "create", "create"]


def test_check_comparison_keeps_casts_that_change_arithmetic() -> None:
    assert not equivalent_check_expressions(
        "minimum_salary / 2 > 0",
        "minimum_salary / CAST(2 AS numeric) > 0",
        {"minimum_salary": "numeric"},
    )
    assert not equivalent_check_expressions(
        "minimum_salary >= 1.234",
        "minimum_salary >= CAST(1.234 AS numeric(3, 2))",
        {"minimum_salary": "numeric"},
    )


@pytest.mark.parametrize(
    ("definition", "code"),
    [
        ("CREATE FUNCTION renamed(integer) RETURNS integer LANGUAGE sql AS $$ SELECT $1 $$",
         "routine_identity_change_unsupported"),
        ("CREATE FUNCTION measure(bigint) RETURNS integer LANGUAGE sql AS $$ SELECT 1 $$",
         "routine_identity_change_unsupported"),
        ("CREATE FUNCTION measure(integer, integer) RETURNS integer LANGUAGE sql AS $$ SELECT $1 $$",
         "routine_identity_change_unsupported"),
        ("CREATE PROCEDURE measure(integer) LANGUAGE sql AS $$ SELECT 1 $$",
         "routine_identity_change_unsupported"),
        ("CREATE FUNCTION measure(integer) RETURNS bigint LANGUAGE sql AS $$ SELECT $1 $$",
         "routine_return_type_change_unsupported"),
        ("CREATE FUNCTION measure(integer) RETURNS SETOF integer LANGUAGE sql AS $$ SELECT $1 $$",
         "routine_return_type_change_unsupported"),
    ],
)
def test_routine_identity_and_return_changes_are_blocked(definition: str, code: str) -> None:
    live = SchemiiDesignContent(functions=[_routine(
        "CREATE FUNCTION measure(integer) RETURNS integer LANGUAGE sql AS $$ SELECT $1 $$"
    )])
    desired = SchemiiDesignContent(functions=[_routine(definition)])

    steps, blockers = compile_migration_steps("public", live, desired)

    assert steps == []
    assert [warning.code for warning in blockers] == [code]
    assert blockers[0].object_path.startswith(f"functions.{desired.functions[0].name}(")
    assert "restore the original" in blockers[0].message


@pytest.mark.parametrize("kind", ["function", "procedure"])
@pytest.mark.parametrize("prefix", ["CREATE", "CREATE OR REPLACE"])
def test_same_identity_routine_body_edits_remain_replaceable(kind: str, prefix: str) -> None:
    returns = "RETURNS integer" if kind == "function" else ""
    original = f"{prefix} {kind.upper()} measure(integer) {returns} LANGUAGE sql AS $$ SELECT 1 $$"
    updated = original.replace("SELECT 1", "SELECT 2")
    live = SchemiiDesignContent(functions=[_routine(original)])
    desired = SchemiiDesignContent(functions=[_routine(updated)])

    steps, blockers = compile_migration_steps("public", live, desired)

    assert blockers == []
    assert len(steps) == 1
    assert steps[0].operation == "replace"
    assert steps[0].sql.startswith(f"CREATE OR REPLACE {kind.upper()} measure(")
    assert "SELECT 2" in steps[0].sql
    assert not steps[0].destructive


@pytest.mark.parametrize(
    ("before_args", "after_args", "returns"),
    [
        ("value integer", "renamed integer", "integer"),
        ("value integer", "integer", "integer"),
        ("value integer DEFAULT 1", "value integer", "integer"),
        ("OUT value integer, OUT label text", "OUT value bigint, OUT label text", "record"),
        ("OUT value integer, OUT label text", "OUT renamed integer, OUT label text", "record"),
        ("OUT value integer, OUT label text", "OUT value integer", "record"),
    ],
)
def test_incompatible_parameter_declarations_are_blocked(
    before_args: str, after_args: str, returns: str,
) -> None:
    def content(arguments: str) -> SchemiiDesignContent:
        return SchemiiDesignContent(functions=[_routine(
            f"CREATE FUNCTION measure({arguments}) RETURNS {returns} LANGUAGE sql AS $$ SELECT 1 $$"
        )])

    steps, blockers = compile_migration_steps("public", content(before_args), content(after_args))

    assert steps == []
    assert [warning.code for warning in blockers] == ["routine_parameter_change_unsupported"]


@pytest.mark.parametrize(
    ("before_args", "after_args"),
    [
        ("integer", "value integer"),
        ("value integer", "IN value integer"),
        ("value integer", "value integer DEFAULT 1"),
        ("value integer DEFAULT 1", "value integer DEFAULT 2"),
    ],
)
def test_compatible_parameter_declarations_remain_replaceable(before_args: str, after_args: str) -> None:
    def content(arguments: str) -> SchemiiDesignContent:
        return SchemiiDesignContent(functions=[_routine(
            f"CREATE FUNCTION measure({arguments}) RETURNS integer LANGUAGE sql AS $$ SELECT 1 $$"
        )])

    steps, blockers = compile_migration_steps("public", content(before_args), content(after_args))

    assert blockers == []
    assert len(steps) == 1
    assert steps[0].operation == "replace"


@pytest.mark.parametrize(
    ("before_kind", "after_kind", "name"),
    [
        ("view", "view", "renamed"),
        ("materialized_view", "materialized_view", "renamed"),
        ("view", "materialized_view", "counts"),
        ("materialized_view", "view", "counts"),
    ],
)
def test_view_identity_changes_are_blocked(before_kind: str, after_kind: str, name: str) -> None:
    live = SchemiiDesignContent(views=[DesignView(
        id=_id("view", "b"), name="counts", kind=before_kind, definition="SELECT 1 AS total",
    )])
    desired = SchemiiDesignContent(views=[DesignView(
        id=_id("view", "b"), name=name, kind=after_kind, definition="SELECT 2 AS total",
    )])

    steps, blockers = compile_migration_steps("public", live, desired)

    assert steps == []
    assert [warning.code for warning in blockers] == ["view_identity_change_unsupported"]
    assert blockers[0].object_path == f"views.{name}"


def test_ordinary_view_query_edits_remain_replaceable() -> None:
    live = SchemiiDesignContent(views=[DesignView(
        id=_id("view", "b"), name="counts", kind="view", definition="SELECT 1 AS total",
    )])
    desired = live.model_copy(deep=True)
    desired.views[0].definition = "SELECT 2 AS total"

    steps, blockers = compile_migration_steps("public", live, desired)

    assert blockers == []
    assert len(steps) == 1
    assert steps[0].sql == 'CREATE OR REPLACE VIEW "public"."counts" AS\nSELECT 2 AS total;'


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


def test_index_include_columns_are_preserved_in_compiled_sql() -> None:
    key = _column("a", "organization_id", "uuid")
    included = _column("b", "personnel_id", "uuid")
    table = _table("c", "slate_fact", [key, included])
    desired = SchemiiDesignContent(tables=[table.model_copy(update={
        "indexes": [DesignIndex(
            id=_id("index", "d"),
            name="slate_fact_org_idx",
            column_ids=[key.id],
            include_column_ids=[included.id],
        )],
    })])

    steps, blockers = compile_migration_steps("public", SchemiiDesignContent(), desired)

    assert blockers == []
    assert steps[1].sql == (
        'CREATE INDEX "slate_fact_org_idx" ON "public"."slate_fact" '
        'USING "btree" ("organization_id") INCLUDE ("personnel_id");'
    )


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


def test_column_order_remains_metadata_only_until_rebuild_is_selected() -> None:
    first = _column("a", "first", "text")
    second = _column("b", "second", "integer")
    table = _table("c", "examples", [first, second])
    live = SchemiiDesignContent(tables=[table])
    desired = live.model_copy(deep=True)
    desired.tables[0].columns.reverse()

    differences = column_order_differences("public", live, desired)
    steps, blockers = compile_migration_steps("public", live, desired)

    assert steps == []
    assert blockers == []
    assert len(differences) == 1
    assert differences[0].current_order == ("first", "second")
    assert differences[0].desired_order == ("second", "first")
    assert differences[0].blocking_reasons == ()


def test_populated_physical_reorder_stages_rows_and_is_destructive() -> None:
    first = _column("a", "first", "text")
    second = _column("b", "second", "integer")
    table = _table("c", "examples", [first, second])
    live = SchemiiDesignContent(tables=[table])
    desired = live.model_copy(deep=True)
    desired.tables[0].columns.reverse()

    steps, blockers = compile_migration_steps(
        "public",
        live,
        desired,
        rebuild_table_ids={table.id},
        populated_rebuild_table_ids={table.id},
    )

    assert blockers == []
    assert steps[0].operation == "lock_for_physical_reorder"
    assert any(step.operation == "stage_rows_for_physical_reorder" for step in steps)
    assert any(
        step.operation == "clear_staged_rows_for_physical_reorder"
        and step.sql == 'TRUNCATE TABLE "public"."examples";'
        for step in steps
    )
    assert any(step.operation == "restore_rows_after_physical_reorder" for step in steps)
    assert any(step.destructive for step in steps)
    added = [step.sql for step in steps if step.operation == "restore_in_physical_order"]
    assert added == [
        'ALTER TABLE "public"."examples" ADD COLUMN "second" integer;',
        'ALTER TABLE "public"."examples" ADD COLUMN "first" text;',
    ]


def test_empty_physical_reorder_skips_row_copy_and_destructive_flag() -> None:
    first = _column("a", "first", "text")
    second = _column("b", "second", "integer")
    table = _table("c", "examples", [first, second])
    live = SchemiiDesignContent(tables=[table])
    desired = live.model_copy(deep=True)
    desired.tables[0].columns.reverse()

    steps, blockers = compile_migration_steps(
        "public",
        live,
        desired,
        rebuild_table_ids={table.id},
    )

    assert blockers == []
    assert not any("rows_for_physical_reorder" in step.operation for step in steps)
    assert not any(step.destructive for step in steps)


def test_physical_reorder_restores_dependent_objects_for_empty_and_populated_tables() -> None:
    identifier = _column("a", "id", "bigint", nullable=False, identity="by_default")
    label = _column("b", "label", "text")
    parent_key = DesignKeyConstraint(
        id=_id("key", "c"),
        name="parents_pkey",
        kind="primary",
        column_ids=[identifier.id],
    )
    parent_index = DesignIndex(
        id=_id("index", "d"),
        name="parents_label_idx",
        column_ids=[label.id],
    )
    parents = _table(
        "e",
        "parents",
        [identifier, label],
        keys=[parent_key],
        indexes=[parent_index],
    )
    parent_id = _column("f", "parent_id", "bigint")
    children = _table("a", "children", [parent_id])
    relationship = DesignRelationship(
        id=_id("relationship", "b"),
        name="children_parent_id_fkey",
        source_table_id=children.id,
        source_column_ids=[parent_id.id],
        target_table_id=parents.id,
        target_column_ids=[identifier.id],
    )
    trigger = DesignTrigger.model_validate({
        "id": _id("trigger", "c"),
        "definition": (
            "CREATE TRIGGER parents_changed AFTER UPDATE ON parents "
            "FOR EACH ROW EXECUTE FUNCTION audit_parent();"
        ),
    })
    live = SchemiiDesignContent(
        tables=[parents, children],
        relationships=[relationship],
        triggers=[trigger],
    )
    desired = live.model_copy(deep=True)
    desired.tables[0].columns.reverse()

    for populated in (False, True):
        steps, blockers = compile_migration_steps(
            "public",
            live,
            desired,
            rebuild_table_ids={parents.id},
            populated_rebuild_table_ids={parents.id} if populated else set(),
        )

        assert blockers == []
        operations_by_kind = {
            kind: [step.operation for step in steps if step.object_kind == kind]
            for kind in ("constraint", "index", "relationship", "trigger")
        }
        assert operations_by_kind == {
            "constraint": ["drop_for_physical_reorder", "restore_after_physical_reorder"],
            "index": ["drop_for_physical_reorder", "restore_after_physical_reorder"],
            "relationship": ["drop_for_physical_reorder", "restore_after_physical_reorder"],
            "trigger": ["drop_for_physical_reorder", "restore_after_physical_reorder"],
        }
        assert any(
            step.operation == "synchronize_identity_after_physical_reorder"
            for step in steps
        )
        assert any(step.operation == "stage_rows_for_physical_reorder" for step in steps) is populated
        assert any(step.operation == "restore_rows_after_physical_reorder" for step in steps) is populated


def test_inspected_content_keeps_the_saved_app_column_order() -> None:
    first = _column("a", "first", "text")
    second = _column("b", "second", "integer")
    inspected = SchemiiDesignContent(tables=[_table("c", "examples", [first, second])])
    preferred = inspected.model_copy(deep=True)
    preferred.tables[0].columns.reverse()

    preserved = preserve_column_display_order(inspected, preferred)

    assert [column.name for column in preserved.tables[0].columns] == ["second", "first"]
    assert preserved.tables[0].columns[0].data_type == "integer"


def test_saved_display_order_is_not_reported_as_external_catalog_drift() -> None:
    catalog = build_postgres_catalog(
        database="analytics",
        namespace="public",
        server_version="17.2",
        server_version_num=170002,
        server_timezone="UTC",
        tables=(
            PostgresTable(
                namespace="public",
                name="examples",
                kind="table",
                is_partition=False,
                columns=(
                    PostgresColumn(
                        name="first",
                        ordinal=1,
                        data_type="text",
                        nullable=True,
                    ),
                    PostgresColumn(
                        name="second",
                        ordinal=2,
                        data_type="integer",
                        nullable=True,
                    ),
                ),
            ),
        ),
        relationships=(),
        functions=(),
        views=(),
        materialized_views=(),
        captured_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    saved = import_postgres_catalog(catalog).content
    saved.tables[0].columns.reverse()

    result = reconcile_designs(saved, saved, catalog)

    assert result.external_changes == []
    assert result.conflicts == []
    assert result.merged is not None
    assert [column.name for column in result.merged.tables[0].columns] == [
        "second",
        "first",
    ]


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
