"""Translate one immutable PostgreSQL catalog into a new desired design."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from schemii.common.postgres.models import PostgresCatalog, PostgresColumn
from schemii.common.postgres.query_analysis import QueryDefinitionError, parse_query_definition
from schemii.common.postgres.schema_source import (
    SchemaSourceError,
    check_expression,
    expression_column_names,
    index_source,
    target_independent_routine,
    target_independent_trigger,
)
from schemii.schemii.workspaces.models import (
    WorkspaceImportIssue,
    WorkspaceImportSummary,
)

from .models import (
    DesignCheckConstraint,
    DesignColumn,
    DesignFunction,
    DesignIndex,
    DesignKeyConstraint,
    DesignObjectPosition,
    DesignRelationship,
    DesignTable,
    DesignTrigger,
    DesignType,
    DesignView,
    SchemiiDesignContent,
    SchemiiDesignLayoutContent,
)
from .store import validate_design_content


@dataclass(frozen=True, slots=True)
class ImportedDesign:
    """A validated desired design and its immutable import provenance."""

    content: SchemiiDesignContent
    layout: SchemiiDesignLayoutContent
    summary: WorkspaceImportSummary


def _semantic_id(catalog: PostgresCatalog, prefix: str, *parts: str) -> str:
    source = "\0".join((catalog.fingerprint, prefix, *parts)).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(source).hexdigest()[:32]}"


def _issue(
    issues: list[WorkspaceImportIssue],
    category: str,
    object_name: str,
    reason: str,
) -> None:
    issues.append(
        WorkspaceImportIssue(
            category=category,
            object_name=object_name,
            reason=reason,
        )
    )


def _ordered_expression_column_names(
    expression: str,
    columns: tuple[PostgresColumn, ...],
) -> tuple[str, ...]:
    """Return expression dependencies in stable physical-column order.

    Dependency arrays are metadata sets, unlike key and index column arrays where
    order changes PostgreSQL semantics.  The browser also stores expression
    dependencies in table order, so imported catalogs must use the same canonical
    order to keep migration verification stable.
    """

    referenced = set(
        expression_column_names(expression, {column.name for column in columns})
    )
    return tuple(
        column.name
        for column in sorted(columns, key=lambda item: item.ordinal)
        if column.name in referenced
    )


def _layout(content: SchemiiDesignContent) -> SchemiiDesignLayoutContent:
    tables = content.tables
    table_width = max(1, math.ceil(math.sqrt(len(tables))))
    objects = [
        DesignObjectPosition(
            object_id=table.id,
            layer="tables",
            x=float(100 + (index % table_width) * 380),
            y=float(100 + (index // table_width) * 340),
        )
        for index, table in enumerate(tables)
    ]
    view_width = max(1, math.ceil(math.sqrt(len(content.views))))
    objects.extend(
        DesignObjectPosition(
            object_id=view.id,
            layer="views",
            x=float(100 + (index % view_width) * 420),
            y=float(100 + (index // view_width) * 360),
        )
        for index, view in enumerate(content.views)
    )
    return SchemiiDesignLayoutContent(objects=objects)


def import_postgres_catalog(catalog: PostgresCatalog) -> ImportedDesign:
    """Derive every representable design object without modifying PostgreSQL."""

    issues: list[WorkspaceImportIssue] = []
    types: list[DesignType] = []
    for source_type in catalog.types:
        try:
            types.append(
                DesignType(
                    id=_semantic_id(catalog, "type", source_type.kind, source_type.name),
                    definition=source_type.definition,
                )
            )
        except ValueError as error:
            _issue(issues, "type", source_type.name, str(error))

    tables: list[DesignTable] = []
    table_ids: dict[str, str] = {}
    column_ids: dict[tuple[str, str], str] = {}

    for source_table in catalog.tables:
        object_name = f"{source_table.namespace}.{source_table.name}"
        if not source_table.columns:
            _issue(
                issues,
                "table",
                object_name,
                "Tables without columns are not represented by the desired-design model",
            )
            continue
        table_id = _semantic_id(catalog, "table", source_table.name)
        table_ids[source_table.name] = table_id
        columns: list[DesignColumn] = []
        for source_column in sorted(source_table.columns, key=lambda item: item.ordinal):
            column_id = _semantic_id(
                catalog,
                "column",
                source_table.name,
                source_column.name,
            )
            column_ids[(source_table.name, source_column.name)] = column_id
            generated_expression = (
                source_column.default_expression
                if source_column.generated is not None
                else None
            )
            generated_source_ids: list[str] = []
            if generated_expression:
                try:
                    generated_source_ids = [
                        _semantic_id(catalog, "column", source_table.name, name)
                        for name in _ordered_expression_column_names(
                            generated_expression,
                            source_table.columns,
                        )
                    ]
                except SchemaSourceError as error:
                    _issue(
                        issues,
                        "column",
                        f"{object_name}.{source_column.name}",
                        str(error),
                    )
                    generated_expression = None
            if (
                source_column.collation_name not in {None, "default"}
                or source_column.collation_schema not in {None, "pg_catalog"}
            ):
                _issue(
                    issues,
                    "column",
                    f"{object_name}.{source_column.name}",
                    "Non-default column collation is not represented by the desired-design model",
                )
            columns.append(
                DesignColumn(
                    id=column_id,
                    name=source_column.name,
                    data_type=source_column.data_type,
                    nullable=source_column.nullable,
                    default_expression=(
                        source_column.default_expression
                        if source_column.generated is None
                        and source_column.identity is None
                        else None
                    ),
                    identity=source_column.identity,
                    generated_expression=generated_expression,
                    generated_source_column_ids=generated_source_ids,
                )
            )

        keys: list[DesignKeyConstraint] = []
        source_keys = [
            *((source_table.primary_key,) if source_table.primary_key else ()),
            *source_table.unique_constraints,
        ]
        for source_key in source_keys:
            ids = [
                column_ids[(source_table.name, name)]
                for name in source_key.columns
            ]
            keys.append(
                DesignKeyConstraint(
                    id=_semantic_id(
                        catalog,
                        "key",
                        source_table.name,
                        source_key.name,
                    ),
                    name=source_key.name,
                    kind=(
                        "primary"
                        if source_table.primary_key is source_key
                        else "unique"
                    ),
                    column_ids=ids,
                )
            )
            if source_key.deferrable or source_key.initially_deferred:
                _issue(
                    issues,
                    "constraint",
                    f"{object_name}.{source_key.name}",
                    "Key deferrability is not represented by the desired-design model",
                )
            if not source_key.validated:
                _issue(
                    issues,
                    "constraint",
                    f"{object_name}.{source_key.name}",
                    "Unvalidated key state is not represented by the desired-design model",
                )

        checks: list[DesignCheckConstraint] = []
        for source_check in source_table.checks:
            try:
                expression = check_expression(source_check.definition)
                dependencies = [
                    column_ids[(source_table.name, name)]
                    for name in _ordered_expression_column_names(
                        expression,
                        source_table.columns,
                    )
                ]
            except (SchemaSourceError, KeyError) as error:
                _issue(
                    issues,
                    "constraint",
                    f"{object_name}.{source_check.name}",
                    str(error) or "The check references an unavailable column",
                )
                continue
            checks.append(
                DesignCheckConstraint(
                    id=_semantic_id(
                        catalog,
                        "check",
                        source_table.name,
                        source_check.name,
                    ),
                    name=source_check.name,
                    expression=expression,
                    column_ids=dependencies,
                )
            )
            if not source_check.validated:
                _issue(
                    issues,
                    "constraint",
                    f"{object_name}.{source_check.name}",
                    "NOT VALID check state is not represented by the desired-design model",
                )

        indexes: list[DesignIndex] = []
        for source_index in source_table.indexes:
            try:
                parsed = index_source(source_index.definition)
                direct_ids = [
                    column_ids[(source_table.name, name)] for name in parsed.columns
                ]
                include_ids = [
                    column_ids[(source_table.name, name)]
                    for name in parsed.included_columns
                ]
                expression_ids = [
                    column_ids[(source_table.name, name)]
                    for name in _ordered_expression_column_names(
                        parsed.expression,
                        source_table.columns,
                    )
                ] if parsed.expression else []
                predicate_ids = [
                    column_ids[(source_table.name, name)]
                    for name in _ordered_expression_column_names(
                        parsed.predicate,
                        source_table.columns,
                    )
                ] if parsed.predicate else []
            except (SchemaSourceError, KeyError) as error:
                _issue(
                    issues,
                    "index",
                    f"{object_name}.{source_index.name}",
                    str(error) or "The index references an unavailable column",
                )
                continue
            indexes.append(
                DesignIndex(
                    id=_semantic_id(
                        catalog,
                        "index",
                        source_table.name,
                        source_index.name,
                    ),
                    name=parsed.name,
                    method=parsed.method,
                    column_ids=direct_ids,
                    include_column_ids=include_ids,
                    expression=parsed.expression,
                    expression_source_column_ids=expression_ids,
                    predicate=parsed.predicate,
                    predicate_column_ids=predicate_ids,
                    unique=parsed.unique,
                )
            )
            if not source_index.valid:
                _issue(
                    issues,
                    "index",
                    f"{object_name}.{source_index.name}",
                    "Invalid PostgreSQL index state is not represented by the desired-design model",
                )

        if source_table.kind == "partitioned_table" or source_table.is_partition:
            _issue(
                issues,
                "table",
                object_name,
                "Partitioning is not represented by the desired-design model",
            )
        for constraint in source_table.not_null_constraints:
            _issue(
                issues,
                "constraint",
                f"{object_name}.{constraint.name}",
                "Named NOT NULL constraint identity is represented only as column nullability",
            )
        for constraint in source_table.exclusion_constraints:
            _issue(
                issues,
                "constraint",
                f"{object_name}.{constraint.name}",
                "Exclusion constraints are not represented by the desired-design model",
            )
        tables.append(
            DesignTable(
                id=table_id,
                name=source_table.name,
                columns=columns,
                keys=keys,
                checks=checks,
                indexes=indexes,
            )
        )

    relationships: list[DesignRelationship] = []
    table_by_name = {table.name: table for table in tables}
    for source_relationship in catalog.relationships:
        object_name = (
            f"{source_relationship.source_namespace}."
            f"{source_relationship.source_table}.{source_relationship.name}"
        )
        source = table_by_name.get(source_relationship.source_table)
        target = (
            table_by_name.get(source_relationship.target_table)
            if source_relationship.target_namespace == catalog.namespace
            else None
        )
        if source is None or target is None:
            _issue(
                issues,
                "relationship",
                object_name,
                "Foreign keys outside the imported namespace are not represented",
            )
            continue
        try:
            source_ids = [
                column_ids[(source.name, name)]
                for name in source_relationship.source_columns
            ]
            target_ids = [
                column_ids[(target.name, name)]
                for name in source_relationship.target_columns
            ]
        except KeyError:
            _issue(
                issues,
                "relationship",
                object_name,
                "The foreign key references a column omitted from the imported design",
            )
            continue
        relationships.append(
            DesignRelationship(
                id=_semantic_id(
                    catalog,
                    "relationship",
                    source_relationship.source_table,
                    source_relationship.name,
                ),
                name=source_relationship.name,
                source_table_id=source.id,
                source_column_ids=source_ids,
                target_table_id=target.id,
                target_column_ids=target_ids,
                on_update=source_relationship.on_update,
                on_delete=source_relationship.on_delete,
                deferrable=source_relationship.deferrable,
                initially_deferred=source_relationship.initially_deferred,
            )
        )
        if source_relationship.match_type != "SIMPLE":
            _issue(
                issues,
                "relationship",
                object_name,
                f"MATCH {source_relationship.match_type} is not represented by the desired-design model",
            )
        if not source_relationship.validated:
            _issue(
                issues,
                "relationship",
                object_name,
                "NOT VALID foreign-key state is not represented by the desired-design model",
            )

    functions: list[DesignFunction] = []
    for source_function in catalog.functions:
        identity = f"{source_function.name}({source_function.identity_arguments})"
        try:
            definition = target_independent_routine(
                source_function.definition,
                catalog.namespace,
            )
            functions.append(
                DesignFunction(
                    id=_semantic_id(catalog, "function", identity),
                    name=source_function.name,
                    kind=source_function.kind,
                    arguments=source_function.arguments,
                    identity_arguments=source_function.identity_arguments,
                    return_type=source_function.return_type,
                    language=source_function.language,
                    definition=definition,
                )
            )
        except (SchemaSourceError, ValueError) as error:
            _issue(issues, "routine", identity, str(error))

    views: list[DesignView] = []
    for source_view, kind in (
        *((view, "view") for view in catalog.views),
        *((view, "materialized_view") for view in catalog.materialized_views),
    ):
        try:
            parse_query_definition(source_view.query_definition)
            views.append(
                DesignView(
                    id=_semantic_id(catalog, "view", kind, source_view.name),
                    name=source_view.name,
                    kind=kind,
                    definition=source_view.query_definition,
                    populate_on_create=(
                        source_view.populated
                        if kind == "materialized_view"
                        else None
                    ),
                )
            )
        except (QueryDefinitionError, ValueError) as error:
            _issue(issues, "view", source_view.name, str(error))

    triggers: list[DesignTrigger] = []
    for source_table in catalog.tables:
        for source_trigger in source_table.triggers:
            object_name = f"{source_table.name}.{source_trigger.name}"
            if source_table.name not in table_by_name:
                _issue(
                    issues,
                    "trigger",
                    object_name,
                    "The trigger target was omitted from the imported design",
                )
                continue
            try:
                definition = target_independent_trigger(
                    source_trigger.definition,
                    catalog.namespace,
                )
                triggers.append(
                    DesignTrigger(
                        id=_semantic_id(
                            catalog,
                            "trigger",
                            source_table.name,
                            source_trigger.name,
                        ),
                        name=source_trigger.name,
                        relation_name=source_table.name,
                        timing="before",
                        events=["insert"],
                        orientation="row",
                        function_name="placeholder",
                        constraint=False,
                        deferrable=False,
                        initially_deferred=False,
                        definition=definition,
                    )
                )
            except (SchemaSourceError, ValueError) as error:
                _issue(issues, "trigger", object_name, str(error))
                continue
            if source_trigger.enabled != "origin":
                _issue(
                    issues,
                    "trigger",
                    object_name,
                    f"Trigger enabled mode {source_trigger.enabled!r} is not represented by the desired-design model",
                )

    content = SchemiiDesignContent(
        types=types,
        tables=tables,
        relationships=relationships,
        functions=functions,
        views=views,
        triggers=triggers,
    )
    validate_design_content(content)
    imported_objects = {
        "tables": len(content.tables),
        "columns": sum(len(table.columns) for table in content.tables),
        "keys": sum(len(table.keys) for table in content.tables),
        "checks": sum(len(table.checks) for table in content.tables),
        "indexes": sum(len(table.indexes) for table in content.tables),
        "relationships": len(content.relationships),
        "functions": len(content.functions),
        "views": len(content.views),
        "triggers": len(content.triggers),
        "types": len(content.types),
    }
    summary = WorkspaceImportSummary(
        catalog_fingerprint=catalog.fingerprint,
        catalog_captured_at=catalog.captured_at,
        complete=not issues,
        imported_objects=imported_objects,
        issues=issues,
    )
    return ImportedDesign(
        content=content,
        layout=_layout(content),
        summary=summary,
    )
