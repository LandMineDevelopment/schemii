from __future__ import annotations

import pytest
from pglast import ast, parse_sql
from pydantic import ValidationError

from schemii.schemii.designs.models import (
    DesignColumn, DesignKeyConstraint, DesignRelationship, DesignTable, DesignView,
    SchemiiDesignContent,
)
from schemii.schemii.migrations.conversions import (
    compile_conversion, conversion_candidates, expression_sql, type_sql,
)
from schemii.schemii.migrations.models import ColumnTypeConversionChoice
from schemii.schemii.migrations.planner import compile_migration_steps


def identifier(kind, token):
    return f"{kind}_{token * 32}"


def designs(*, source="text", target="varchar(4)", name="label", default=None):
    column = DesignColumn(id=identifier("column", "a"), name=name,
                          data_type=source, default_expression=default)
    table = DesignTable(id=identifier("table", "b"), name="records", columns=[column])
    live = SchemiiDesignContent(tables=[table])
    desired = live.model_copy(deep=True)
    desired.tables[0].columns[0].data_type = target
    return live, desired


def compiled(live, desired, *, strategy="strict", expression=None):
    before, after = live.tables[0], desired.tables[0]
    old, new = before.columns[0], after.columns[0]
    return compile_conversion("public", before, after, old, new,
                              ColumnTypeConversionChoice(column_id=new.id,
                                                         strategy=strategy, expression=expression))


@pytest.mark.parametrize("expression", [
    "label); DROP TABLE records; SELECT (label", "(SELECT label FROM records)",
    "other_column", "records.label", "pg_sleep(10)", "nextval('seq')",
    "public.lower(label)", "sum(label)", "count(*)", "lower(label) OVER ()",
    "label::public.custom_type", "label::text[]", "label OPERATOR(public.+) 1",
])
def test_custom_expression_rejects_queries_and_unapproved_execution(expression):
    with pytest.raises(ValueError):
        expression_sql(expression, "label")


@pytest.mark.parametrize("expression", [
    "lower(label)", "coalesce(label, 'missing')", "CASE WHEN label IS NULL THEN 'x' ELSE label END",
    "label || 'suffix'", "substring(label, 1, 4)", "label::varchar(4)",
])
def test_custom_expression_remains_one_parseable_scalar_expression(expression):
    result = expression_sql(expression, "label")
    parsed = parse_sql(f"SELECT {result}")
    assert len(parsed) == 1
    assert isinstance(parsed[0].stmt, ast.SelectStmt)
    assert parsed[0].stmt.fromClause is None
    assert expression_sql(result, "label") == result


def test_strict_choice_cannot_smuggle_custom_expression():
    with pytest.raises(ValidationError):
        ColumnTypeConversionChoice(column_id=identifier("column", "a"),
                                   strategy="strict", expression="NULL")
    with pytest.raises(ValidationError):
        ColumnTypeConversionChoice(column_id=identifier("column", "a"), strategy="custom", expression=" ")


@pytest.mark.parametrize("target", ["public.custom", "text[]", "text); DROP TABLE x; SELECT (NULL::text"])
def test_target_type_is_restricted(target):
    with pytest.raises(ValueError):
        type_sql(target)


def test_renamed_and_quoted_columns_use_live_names_only_in_preview():
    live, desired = designs(name='Old "Label')
    desired.tables[0].name = "renamed_records"
    desired.tables[0].columns[0].name = "New Label"
    conversion = compiled(live, desired, strategy="custom", expression='lower("New Label")')
    assert '"Old ""Label"' in conversion.validation_sql
    assert '"public"."records"' in conversion.validation_sql
    assert '"New Label"' in conversion.expression
    assert "pg_catalog.lower" in conversion.expression
    assert conversion.review.column_id == live.tables[0].columns[0].id
    assert conversion.guard_sql is None
    assert len(conversion_candidates(live, desired)) == 1


def test_strict_guard_checks_round_trip_including_nulls_and_runs_before_alter():
    live, desired = designs(default="'test'::text")
    conversion = compiled(live, desired)
    assert "IS DISTINCT FROM" in conversion.validation_sql
    assert "COLLATE \"C\"" in conversion.validation_sql
    assert "SC001" in conversion.guard_sql
    steps, blockers = compile_migration_steps("public", live, desired,
        column_type_conversions={conversion.review.column_id: conversion})
    assert not blockers
    operations = [step.operation for step in steps]
    assert operations == ["lock_for_conversion", "validate_conversion", "drop_default_for_conversion",
                          "alter_type", "alter_default"]
    assert "ACCESS EXCLUSIVE" in steps[0].sql
    assert "USING" in steps[3].sql
    assert "SET DEFAULT 'test'::text" in steps[4].sql
    for step in steps:
        assert len(parse_sql(step.sql)) == 1


def test_custom_conversion_is_destructive_and_does_not_claim_preservation():
    live, desired = designs()
    conversion = compiled(live, desired, strategy="custom", expression="left(label, 4)")
    steps, blockers = compile_migration_steps("public", live, desired,
        column_type_conversions={conversion.review.column_id: conversion})
    assert not blockers
    assert [step.operation for step in steps] == ["lock_for_conversion", "alter_type"]
    assert steps[-1].destructive
    assert conversion.guard_sql is None
    assert "count(" in conversion.validation_sql


def test_conversion_preserves_fk_by_recreating_after_both_columns_change():
    live, desired = designs(source="bigint", target="integer", name="id")
    parent = live.tables[0]
    parent.keys = [DesignKeyConstraint(id=identifier("key", "c"), name="records_pkey",
                                      kind="primary", column_ids=[parent.columns[0].id])]
    child = DesignTable(id=identifier("table", "d"), name="children", columns=[
        DesignColumn(id=identifier("column", "e"), name="record_id", data_type="bigint")])
    live.tables.append(child)
    live.relationships.append(DesignRelationship(id=identifier("relationship", "f"),
        name="children_record_fk", source_table_id=child.id,
        source_column_ids=[child.columns[0].id], target_table_id=parent.id,
        target_column_ids=[parent.columns[0].id]))
    desired = live.model_copy(deep=True)
    conversions = {}
    for before, after in zip(live.tables, desired.tables):
        after.columns[0].data_type = "integer"
        choice = ColumnTypeConversionChoice(column_id=after.columns[0].id, strategy="strict")
        conversions[choice.column_id] = compile_conversion("public", before, after,
            before.columns[0], after.columns[0], choice)
    steps, blockers = compile_migration_steps("public", live, desired, column_type_conversions=conversions)
    assert not blockers
    drops = [i for i, step in enumerate(steps) if step.object_kind == "relationship" and step.operation == "drop"]
    creates = [i for i, step in enumerate(steps) if step.object_kind == "relationship" and step.operation == "create"]
    alters = [i for i, step in enumerate(steps) if step.operation == "alter_type"]
    assert len(drops) == len(creates) == 1
    assert len(alters) == 2
    assert drops[0] < min(alters) < max(alters) < creates[0]
    assert "REFERENCES" in steps[creates[0]].sql


def test_explicit_conversion_still_blocks_dependent_view():
    live, desired = designs()
    view = DesignView(id=identifier("view", "c"), name="labels", kind="view",
                      definition="SELECT label FROM records")
    live.views.append(view)
    desired.views.append(view.model_copy(deep=True))
    conversion = compiled(live, desired)
    steps, blockers = compile_migration_steps("public", live, desired,
        column_type_conversions={conversion.review.column_id: conversion})
    assert not steps
    assert [warning.code for warning in blockers] == ["column_type_dependency_requires_review"]
    assert "labels" in blockers[0].message
