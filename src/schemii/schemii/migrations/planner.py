"""Pure server-side three-way reconciliation and PostgreSQL DDL planning."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from typing import AbstractSet, Any, Iterable, Literal

from schemii.common.postgres.models import PostgresCatalog
from schemii.common.postgres.query_analysis import referenced_relations
from schemii.schemii.designs.importer import import_postgres_catalog
from schemii.schemii.designs.models import (
    DesignColumn,
    DesignFunction,
    DesignIndex,
    DesignRelationship,
    DesignTable,
    DesignTrigger,
    DesignType,
    DesignView,
    SchemiiDesignContent,
)
from schemii.schemii.designs.store import design_fingerprint, validate_design_content

from .models import (
    MigrationConflict,
    MigrationExternalChange,
    MigrationStep,
    MigrationWarning,
)
from .type_changes import TypeChangeDecision, classify_type_change


_MISSING = object()
_CREATE_ROUTINE = re.compile(r"^\s*CREATE\s+(FUNCTION|PROCEDURE)\b", re.IGNORECASE)


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _identifier(prefix: str, *values: Any) -> str:
    digest = hashlib.sha256(
        "\0".join(_canonical(value) for value in values).encode("utf-8")
    ).hexdigest()[:32]
    return f"{prefix}_{digest}"


def _quote(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'


def _qualified(namespace: str, name: str) -> str:
    return f"{_quote(namespace)}.{_quote(name)}"


def _statement(source: str) -> str:
    return source.strip().rstrip(";") + ";"


def _kind(path: str) -> Literal[
    "schema",
    "type",
    "table",
    "column",
    "constraint",
    "index",
    "relationship",
    "function",
    "procedure",
    "view",
    "materialized_view",
    "trigger",
]:
    if ".columns[" in path:
        return "column"
    if any(token in path for token in (".keys[", ".checks[")):
        return "constraint"
    if ".indexes[" in path:
        return "index"
    if path.startswith("types["):
        return "type"
    if path.startswith("tables["):
        return "table"
    if path.startswith("relationships["):
        return "relationship"
    if path.startswith("functions["):
        return "function"
    if path.startswith("views["):
        return "view"
    if path.startswith("triggers["):
        return "trigger"
    return "schema"


def _display_path(path: str) -> str:
    return path.replace("[", ".").replace("]", "").strip(".")


def _operation(before: Any, after: Any) -> Literal["added", "removed", "changed", "renamed"]:
    if before is _MISSING:
        return "added"
    if after is _MISSING:
        return "removed"
    return "changed"


def _value(value: Any) -> Any:
    return None if value is _MISSING else copy.deepcopy(value)


def _copy(value: Any) -> Any:
    return _MISSING if value is _MISSING else copy.deepcopy(value)


_UNORDERED_DEPENDENCY_FIELDS = {
    "column_ids",
    "expression_source_column_ids",
    "generated_source_column_ids",
    "predicate_column_ids",
}


def _unordered_dependency_path(path: str) -> bool:
    field = path.rsplit(".", 1)[-1]
    if field not in _UNORDERED_DEPENDENCY_FIELDS:
        return False
    if field == "column_ids":
        return ".checks[" in path
    return True


def _equivalent_value(path: str, left: Any, right: Any) -> bool:
    if left is _MISSING or right is _MISSING:
        return left is right
    if (
        path.endswith(".columns")
        and _id_list(left)
        and _id_list(right)
    ):
        left_items = _item_map(left)
        right_items = _item_map(right)
        return left_items.keys() == right_items.keys() and all(
            _equivalent_value(
                f"{path}[{identifier}]",
                left_items[identifier],
                right_items[identifier],
            )
            for identifier in left_items
        )
    if (
        _unordered_dependency_path(path)
        and isinstance(left, list)
        and isinstance(right, list)
        and all(isinstance(item, str) for item in (*left, *right))
    ):
        return set(left) == set(right)
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _equivalent_value(
                f"{path}.{key}" if path else key,
                left[key],
                right[key],
            )
            for key in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _equivalent_value(f"{path}[{index}]", left_item, right_item)
            for index, (left_item, right_item) in enumerate(zip(left, right))
        )
    return left == right


def _id_list(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, dict) and isinstance(item.get("id"), str)
        for item in value
    )


def _item_map(value: Any) -> dict[str, dict[str, Any]]:
    if value is _MISSING:
        return {}
    return {item["id"]: item for item in value}


@dataclass(slots=True)
class ReconciliationResult:
    merged: SchemiiDesignContent | None
    live: SchemiiDesignContent
    external_changes: list[MigrationExternalChange]
    conflicts: list[MigrationConflict]
    warnings: list[MigrationWarning]
    import_complete: bool


@dataclass(frozen=True, slots=True)
class ColumnOrderDifference:
    """One logical display order that differs from PostgreSQL physical order."""

    table_id: str
    table_name: str
    current_order: tuple[str, ...]
    desired_order: tuple[str, ...]
    blocking_reasons: tuple[str, ...] = ()


class _Merger:
    def __init__(self, resolutions: dict[str, str] | None = None) -> None:
        self.external: list[MigrationExternalChange] = []
        self.conflicts: list[MigrationConflict] = []
        self.resolutions = resolutions or {}

    def merge(self, baseline: Any, desired: Any, live: Any, path: str) -> Any:
        if desired is _MISSING and live is _MISSING:
            return _MISSING
        if _equivalent_value(path, desired, live):
            return _copy(desired)

        if baseline is not _MISSING:
            if desired is _MISSING and live is not _MISSING:
                return self._conflict(path, baseline, desired, live, "structural")
            if live is _MISSING and desired is not _MISSING:
                return self._conflict(path, baseline, desired, live, "structural")

        if all(value is _MISSING or isinstance(value, dict) for value in (baseline, desired, live)):
            result: dict[str, Any] = {}
            keys: list[str] = []
            for source in (baseline, desired, live):
                if isinstance(source, dict):
                    keys.extend(key for key in source if key not in keys)
            for key in keys:
                merged = self.merge(
                    baseline.get(key, _MISSING) if isinstance(baseline, dict) else _MISSING,
                    desired.get(key, _MISSING) if isinstance(desired, dict) else _MISSING,
                    live.get(key, _MISSING) if isinstance(live, dict) else _MISSING,
                    f"{path}.{key}" if path else key,
                )
                if merged is not _MISSING:
                    result[key] = merged
            return result

        present_lists = [value for value in (baseline, desired, live) if value is not _MISSING]
        if present_lists and all(_id_list(value) for value in present_lists):
            baseline_items = _item_map(baseline)
            desired_items = _item_map(desired)
            live_items = _item_map(live)
            order: list[str] = []
            for source in (desired, baseline, live):
                if source is _MISSING:
                    continue
                order.extend(item["id"] for item in source if item["id"] not in order)
            result = []
            for identifier in order:
                merged = self.merge(
                    baseline_items.get(identifier, _MISSING),
                    desired_items.get(identifier, _MISSING),
                    live_items.get(identifier, _MISSING),
                    f"{path}[{identifier}]",
                )
                if merged is not _MISSING:
                    result.append(merged)
            return result

        if _equivalent_value(path, desired, baseline):
            if not _equivalent_value(path, live, baseline):
                self._external(path, baseline, live)
            return _copy(live)
        if _equivalent_value(path, live, baseline):
            return _copy(desired)

        return self._conflict(path, baseline, desired, live, "direct")

    def _external(self, path: str, baseline: Any, live: Any) -> None:
        identifier = _identifier("mec", path, _value(baseline), _value(live))
        operation = _operation(baseline, live)
        self.external.append(
            MigrationExternalChange(
                id=identifier,
                object_kind=_kind(path),
                object_path=_display_path(path),
                operation=operation,
                summary=f"PostgreSQL {operation} {_display_path(path)} outside Schemii",
                baseline_value=_value(baseline),
                live_value=_value(live),
            )
        )

    def _conflict(
        self,
        path: str,
        baseline: Any,
        desired: Any,
        live: Any,
        category: Literal["direct", "structural", "dependency", "opaque"],
    ) -> Any:
        identifier = _identifier(
            "mcf",
            path,
            _value(baseline),
            _value(desired),
            _value(live),
        )
        resolution = self.resolutions.get(identifier)
        if resolution == "pull_live":
            return _copy(live)
        if resolution == "keep_design":
            return _copy(desired)
        self.conflicts.append(
            MigrationConflict(
                id=identifier,
                category=category,
                object_kind=_kind(path),
                object_path=_display_path(path),
                summary=(
                    f"Schemii and PostgreSQL changed {_display_path(path)} differently"
                ),
                baseline_value=_value(baseline),
                design_value=_value(desired),
                live_value=_value(live),
            )
        )
        return _copy(desired)


def _identity_maps(content: dict[str, Any]) -> dict[str, dict[Any, str]]:
    maps: dict[str, dict[Any, str]] = {
        "types": {},
        "tables": {},
        "functions": {},
        "views": {},
        "triggers": {},
        "relationships": {},
    }
    table_names: dict[str, str] = {}
    for item in content.get("types", []):
        maps["types"][(item.get("kind"), item.get("name"))] = item["id"]
    for table in content.get("tables", []):
        maps["tables"][table.get("name")] = table["id"]
        table_names[table["id"]] = table.get("name")
        for category in ("columns", "keys", "checks", "indexes"):
            maps.setdefault(category, {})[(table.get("name"),)] = {
                child.get("name"): child["id"] for child in table.get(category, [])
            }
    for item in content.get("functions", []):
        maps["functions"][(
            item.get("kind"), item.get("name"), item.get("identity_arguments")
        )] = item["id"]
    for item in content.get("views", []):
        maps["views"][(item.get("kind"), item.get("name"))] = item["id"]
    for item in content.get("triggers", []):
        maps["triggers"][(item.get("relation_name"), item.get("name"))] = item["id"]
    for item in content.get("relationships", []):
        source_name = table_names.get(item.get("source_table_id"))
        maps["relationships"][(source_name, item.get("name"))] = item["id"]
    return maps


def _aligned_content(
    source: SchemiiDesignContent,
    *references: SchemiiDesignContent,
) -> SchemiiDesignContent:
    """Align live imported IDs to baseline/desired identities by source names."""

    document = source.model_dump(mode="json")
    reference_maps = [
        _identity_maps(reference.model_dump(mode="json")) for reference in references
    ]
    replacements: dict[str, str] = {}

    def candidate(category: str, key: Any) -> str | None:
        for mapping in reference_maps:
            found = mapping.get(category, {}).get(key)
            if isinstance(found, str):
                return found
        return None

    live_table_names = {table["id"]: table.get("name") for table in document["tables"]}
    for item in document["types"]:
        replacement = candidate("types", (item.get("kind"), item.get("name")))
        if replacement:
            replacements[item["id"]] = replacement
    for table in document["tables"]:
        table_name = table.get("name")
        replacement = candidate("tables", table_name)
        if replacement:
            replacements[table["id"]] = replacement
        for category in ("columns", "keys", "checks", "indexes"):
            for child in table.get(category, []):
                matched = None
                for mapping in reference_maps:
                    names = mapping.get(category, {}).get((table_name,), {})
                    if isinstance(names, dict) and isinstance(names.get(child.get("name")), str):
                        matched = names[child.get("name")]
                        break
                if matched:
                    replacements[child["id"]] = matched
    for item in document["functions"]:
        replacement = candidate(
            "functions",
            (item.get("kind"), item.get("name"), item.get("identity_arguments")),
        )
        if replacement:
            replacements[item["id"]] = replacement
    for item in document["views"]:
        replacement = candidate("views", (item.get("kind"), item.get("name")))
        if replacement:
            replacements[item["id"]] = replacement
    for item in document["triggers"]:
        replacement = candidate("triggers", (item.get("relation_name"), item.get("name")))
        if replacement:
            replacements[item["id"]] = replacement
    for item in document["relationships"]:
        source_name = live_table_names.get(item.get("source_table_id"))
        replacement = candidate("relationships", (source_name, item.get("name")))
        if replacement:
            replacements[item["id"]] = replacement

    def replace(value: Any) -> Any:
        if isinstance(value, str):
            return replacements.get(value, value)
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        return value

    return SchemiiDesignContent.model_validate(replace(document))


def catalog_design(
    catalog: PostgresCatalog,
    *references: SchemiiDesignContent,
) -> tuple[SchemiiDesignContent, list[MigrationWarning], bool]:
    imported = import_postgres_catalog(catalog)
    content = _aligned_content(imported.content, *references)
    warnings = [
        MigrationWarning(
            code="unrepresented_postgresql_object",
            message=issue.reason,
            object_path=f"{issue.category}.{issue.object_name}",
        )
        for issue in imported.summary.issues
    ]
    return content, warnings, imported.summary.complete


def reconcile_designs(
    baseline: SchemiiDesignContent,
    desired: SchemiiDesignContent,
    catalog: PostgresCatalog,
    *,
    resolutions: dict[str, str] | None = None,
) -> ReconciliationResult:
    live, warnings, complete = catalog_design(catalog, baseline, desired)
    merger = _Merger(resolutions)
    merged_document = merger.merge(
        baseline.model_dump(mode="json"),
        desired.model_dump(mode="json"),
        live.model_dump(mode="json"),
        "",
    )
    merged: SchemiiDesignContent | None = None
    try:
        merged = SchemiiDesignContent.model_validate(merged_document)
        validate_design_content(merged)
    except ValueError as error:
        merger.conflicts.append(
            MigrationConflict(
                id=_identifier("mcf", "schema.structure", str(error)),
                category="structural",
                object_kind="schema",
                object_path="schema.structure",
                summary=f"The combined schema is invalid: {error}",
                baseline_value=None,
                design_value=None,
                live_value=None,
                allowed_resolutions=["pull_live"],
            )
        )
    if not complete:
        merger.conflicts.append(
            MigrationConflict(
                id=_identifier("mcf", "schema.coverage", catalog.fingerprint),
                category="opaque",
                object_kind="schema",
                object_path="schema.coverage",
                summary="PostgreSQL contains objects the desired-design model cannot preserve losslessly",
                baseline_value=None,
                design_value=None,
                live_value=[warning.model_dump(mode="json") for warning in warnings],
                allowed_resolutions=["pull_live"],
            )
        )
    return ReconciliationResult(
        merged=merged,
        live=live,
        external_changes=merger.external,
        conflicts=merger.conflicts,
        warnings=warnings,
        import_complete=complete,
    )


@dataclass(slots=True)
class _PendingStep:
    phase: int
    kind: Any
    path: str
    operation: str
    sql: str
    destructive: bool = False
    requires_lock: bool = True
    data_movement: bool = False
    order: int = 0


def _column_definition(column: DesignColumn) -> str:
    parts = [_quote(column.name), column.data_type.strip()]
    if column.generated_expression is not None:
        parts.append(f"GENERATED ALWAYS AS ({column.generated_expression.strip()}) STORED")
    elif column.identity is not None:
        identity = "ALWAYS" if column.identity == "always" else "BY DEFAULT"
        parts.append(f"GENERATED {identity} AS IDENTITY")
    elif column.default_expression is not None:
        parts.append(f"DEFAULT {column.default_expression.strip()}")
    if not column.nullable:
        parts.append("NOT NULL")
    return " ".join(parts)


def _table_maps(content: SchemiiDesignContent) -> tuple[dict[str, DesignTable], dict[str, DesignColumn]]:
    tables = {table.id: table for table in content.tables}
    columns = {column.id: column for table in content.tables for column in table.columns}
    return tables, columns


def _create_table(namespace: str, table: DesignTable, columns: dict[str, DesignColumn]) -> str:
    definitions = [_column_definition(column) for column in table.columns]
    for key in table.keys:
        names = ", ".join(_quote(columns[item].name) for item in key.column_ids)
        kind = "PRIMARY KEY" if key.kind == "primary" else "UNIQUE"
        definitions.append(f"CONSTRAINT {_quote(key.name)} {kind} ({names})")
    for check in table.checks:
        definitions.append(
            f"CONSTRAINT {_quote(check.name)} CHECK ({check.expression.strip()})"
        )
    body = ",\n  ".join(definitions)
    return f"CREATE TABLE {_qualified(namespace, table.name)} (\n  {body}\n);"


def _create_index(namespace: str, table: DesignTable, index: DesignIndex, columns: dict[str, DesignColumn]) -> str:
    entries = [_quote(columns[item].name) for item in index.column_ids]
    if index.expression:
        entries.append(index.expression.strip())
    unique = "UNIQUE " if index.unique else ""
    sql = (
        f"CREATE {unique}INDEX {_quote(index.name)} ON {_qualified(namespace, table.name)} "
        f"USING {_quote(index.method)} ({', '.join(entries)})"
    )
    if index.predicate:
        sql += f" WHERE {index.predicate.strip()}"
    return sql + ";"


def _relationship_sql(
    namespace: str,
    relationship: DesignRelationship,
    tables: dict[str, DesignTable],
    columns: dict[str, DesignColumn],
) -> str:
    source = tables[relationship.source_table_id]
    target = tables[relationship.target_table_id]
    source_columns = ", ".join(
        _quote(columns[item].name) for item in relationship.source_column_ids
    )
    target_columns = ", ".join(
        _quote(columns[item].name) for item in relationship.target_column_ids
    )
    sql = (
        f"ALTER TABLE {_qualified(namespace, source.name)} ADD CONSTRAINT "
        f"{_quote(relationship.name)} FOREIGN KEY ({source_columns}) REFERENCES "
        f"{_qualified(namespace, target.name)} ({target_columns}) "
        f"ON UPDATE {relationship.on_update} ON DELETE {relationship.on_delete}"
    )
    if relationship.deferrable:
        sql += " DEFERRABLE"
        if relationship.initially_deferred:
            sql += " INITIALLY DEFERRED"
    return sql + ";"


def _constraint_sql(namespace: str, table: DesignTable, item: Any, columns: dict[str, DesignColumn]) -> str:
    if hasattr(item, "kind"):
        names = ", ".join(_quote(columns[value].name) for value in item.column_ids)
        kind = "PRIMARY KEY" if item.kind == "primary" else "UNIQUE"
        definition = f"{kind} ({names})"
    else:
        definition = f"CHECK ({item.expression.strip()})"
    return (
        f"ALTER TABLE {_qualified(namespace, table.name)} ADD CONSTRAINT "
        f"{_quote(item.name)} {definition};"
    )


def _by_id(values: Iterable[Any]) -> dict[str, Any]:
    return {value.id: value for value in values}


def _same_dependency_set(left: Iterable[str], right: Iterable[str]) -> bool:
    return set(left) == set(right)


def _same_except_dependency_order(left: Any, right: Any, *fields: str) -> bool:
    if type(left) is not type(right):
        return False
    left_value = left.model_dump(mode="json")
    right_value = right.model_dump(mode="json")
    for field in fields:
        left_value[field] = sorted(left_value[field])
        right_value[field] = sorted(right_value[field])
    return left_value == right_value


def _same_check(left: Any, right: Any) -> bool:
    return _same_except_dependency_order(left, right, "column_ids")


def _same_index(left: DesignIndex, right: DesignIndex) -> bool:
    return _same_except_dependency_order(
        left,
        right,
        "expression_source_column_ids",
        "predicate_column_ids",
    )


def _column_type_changes(
    live_tables: dict[str, DesignTable],
    desired_tables: dict[str, DesignTable],
) -> dict[str, TypeChangeDecision]:
    changes: dict[str, TypeChangeDecision] = {}
    for table_id in set(live_tables) & set(desired_tables):
        before_columns = _by_id(live_tables[table_id].columns)
        after_columns = _by_id(desired_tables[table_id].columns)
        for column_id in set(before_columns) & set(after_columns):
            decision = classify_type_change(
                before_columns[column_id].data_type,
                after_columns[column_id].data_type,
            )
            if decision.disposition != "equivalent":
                changes[column_id] = decision
    return changes


def _dependent_view_names(
    namespace: str,
    table_name: str,
    live: SchemiiDesignContent,
    desired: SchemiiDesignContent,
) -> list[str]:
    names: set[str] = set()
    for view in [*live.views, *desired.views]:
        references = referenced_relations(
            view.definition,
            current_namespace=namespace,
        )
        if (namespace, table_name) in references:
            names.add(view.name)
    return sorted(names)


def tables_requiring_empty_for_required_columns(
    live: SchemiiDesignContent,
    desired: SchemiiDesignContent,
) -> frozenset[str]:
    """Return physical tables whose new required columns cannot populate rows."""

    live_tables = _by_id(live.tables)
    desired_tables = _by_id(desired.tables)
    required: set[str] = set()
    for table_id in set(live_tables) & set(desired_tables):
        before = live_tables[table_id]
        old_column_ids = {column.id for column in before.columns}
        for column in desired_tables[table_id].columns:
            if (
                column.id not in old_column_ids
                and not column.nullable
                and column.default_expression is None
                and column.identity is None
                and column.generated_expression is None
            ):
                required.add(before.name)
    return frozenset(required)


def column_order_differences(
    namespace: str,
    live: SchemiiDesignContent,
    desired: SchemiiDesignContent,
) -> list[ColumnOrderDifference]:
    """Find physical-order differences without treating them as schema deltas."""

    live_tables = _by_id(live.tables)
    desired_tables = _by_id(desired.tables)
    live_relationships = _by_id(live.relationships)
    desired_relationships = _by_id(desired.relationships)
    live_triggers = _by_id(live.triggers)
    desired_triggers = _by_id(desired.triggers)
    differences: list[ColumnOrderDifference] = []
    for table_id in sorted(set(live_tables) & set(desired_tables)):
        before = live_tables[table_id]
        after = desired_tables[table_id]
        current_ids = tuple(column.id for column in before.columns)
        desired_ids = tuple(column.id for column in after.columns)
        if current_ids == desired_ids:
            continue
        reasons: list[str] = []
        before_columns = _by_id(before.columns)
        after_columns = _by_id(after.columns)
        if set(current_ids) != set(desired_ids):
            reasons.append("Apply this table's column additions or removals before rebuilding its physical order.")
        elif any(before_columns[item] != after_columns[item] for item in current_ids):
            reasons.append("Apply this table's column edits before rebuilding its physical order.")
        if before.name != after.name:
            reasons.append("Apply the table rename before rebuilding its physical order.")
        if before.keys != after.keys or before.checks != after.checks or before.indexes != after.indexes:
            reasons.append("Apply this table's constraint and index edits before rebuilding its physical order.")
        for relationship_id in sorted(set(live_relationships) | set(desired_relationships)):
            old_relationship = live_relationships.get(relationship_id)
            new_relationship = desired_relationships.get(relationship_id)
            involved = any(
                relationship is not None
                and table_id in {relationship.source_table_id, relationship.target_table_id}
                for relationship in (old_relationship, new_relationship)
            )
            if involved and old_relationship != new_relationship:
                reasons.append("Apply foreign-key edits involving this table before rebuilding its physical order.")
                break
        for trigger_id in sorted(set(live_triggers) | set(desired_triggers)):
            old_trigger = live_triggers.get(trigger_id)
            new_trigger = desired_triggers.get(trigger_id)
            involved = any(
                trigger is not None and trigger.relation_name in {before.name, after.name}
                for trigger in (old_trigger, new_trigger)
            )
            if involved and old_trigger != new_trigger:
                reasons.append("Apply trigger edits on this table before rebuilding its physical order.")
                break
        dependent_views = _dependent_view_names(namespace, before.name, live, desired)
        if dependent_views:
            reasons.append(
                "Rebuild or remove dependent views first: " + ", ".join(dependent_views)
            )
        if any(column.generated_expression is not None for column in after.columns):
            reasons.append("Physical reordering of generated columns is not yet lossless.")
        if any(
            column.default_expression is not None
            and re.search(r"\bnextval\s*\(", column.default_expression, re.IGNORECASE)
            for column in after.columns
        ):
            reasons.append("Serial-sequence defaults must be converted to identity columns before physical reordering.")
        differences.append(
            ColumnOrderDifference(
                table_id=table_id,
                table_name=after.name,
                current_order=tuple(column.name for column in before.columns),
                desired_order=tuple(column.name for column in after.columns),
                blocking_reasons=tuple(dict.fromkeys(reasons)),
            )
        )
    return differences


def physical_column_order_mismatches(
    live: SchemiiDesignContent,
    desired: SchemiiDesignContent,
    table_ids: AbstractSet[str],
) -> list[str]:
    """Return selected tables whose inspected physical order missed the review."""

    live_tables = _by_id(live.tables)
    desired_tables = _by_id(desired.tables)
    mismatches: list[str] = []
    for table_id in sorted(table_ids):
        before = live_tables.get(table_id)
        after = desired_tables.get(table_id)
        if before is None or after is None or [item.id for item in before.columns] != [
            item.id for item in after.columns
        ]:
            mismatches.append(after.name if after is not None else table_id)
    return mismatches


def preserve_column_display_order(
    inspected: SchemiiDesignContent,
    preferred: SchemiiDesignContent,
) -> SchemiiDesignContent:
    """Keep app-owned column order while retaining PostgreSQL-derived column facts."""

    document = inspected.model_dump(mode="json")
    preferred_tables = _by_id(preferred.tables)
    for table in document["tables"]:
        preferred_table = preferred_tables.get(table["id"])
        if preferred_table is None:
            continue
        by_id = {column["id"]: column for column in table["columns"]}
        ordered = [
            by_id[column.id]
            for column in preferred_table.columns
            if column.id in by_id
        ]
        ordered_ids = {column["id"] for column in ordered}
        ordered.extend(
            column for column in table["columns"] if column["id"] not in ordered_ids
        )
        table["columns"] = ordered
    return SchemiiDesignContent.model_validate(document)


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _append_column_order_rebuild(
    pending: list[_PendingStep],
    namespace: str,
    live: SchemiiDesignContent,
    desired: SchemiiDesignContent,
    table_id: str,
    *,
    contains_data: bool,
) -> None:
    """Rebuild one unchanged table's columns in place inside the migration transaction."""

    live_tables, live_columns = _table_maps(live)
    desired_tables, desired_columns = _table_maps(desired)
    before = live_tables[table_id]
    after = desired_tables[table_id]
    table_path = f"tables.{after.name}.column_order"
    qualified_table = _qualified(namespace, before.name)
    pending.append(_PendingStep(
        1,
        "table",
        table_path,
        "lock_for_physical_reorder",
        f"LOCK TABLE {qualified_table} IN ACCESS EXCLUSIVE MODE;",
        data_movement=contains_data,
    ))
    backup_name = f"schemii_reorder_{hashlib.sha256(table_id.encode('utf-8')).hexdigest()[:20]}"
    column_names = ", ".join(_quote(column.name) for column in before.columns)
    if contains_data:
        pending.append(_PendingStep(
            2,
            "table",
            table_path,
            "stage_rows_for_physical_reorder",
            (
                f"CREATE TEMP TABLE {_quote(backup_name)} ON COMMIT DROP AS "
                f"SELECT {column_names} FROM {qualified_table};"
            ),
            data_movement=True,
        ))

    for relationship in live.relationships:
        if table_id not in {relationship.source_table_id, relationship.target_table_id}:
            continue
        source = live_tables[relationship.source_table_id]
        pending.append(_PendingStep(
            5,
            "relationship",
            f"relationships.{relationship.name}",
            "drop_for_physical_reorder",
            (
                f"ALTER TABLE {_qualified(namespace, source.name)} DROP CONSTRAINT "
                f"{_quote(relationship.name)};"
            ),
        ))
    for trigger in live.triggers:
        if trigger.relation_name != before.name:
            continue
        pending.append(_PendingStep(
            6,
            "trigger",
            f"triggers.{before.name}.{trigger.name}",
            "drop_for_physical_reorder",
            f"DROP TRIGGER {_quote(trigger.name)} ON {qualified_table};",
        ))
    for index in before.indexes:
        pending.append(_PendingStep(
            7,
            "index",
            f"tables.{before.name}.indexes.{index.name}",
            "drop_for_physical_reorder",
            f"DROP INDEX {_qualified(namespace, index.name)};",
        ))
    for item in [*before.keys, *before.checks]:
        pending.append(_PendingStep(
            8,
            "constraint",
            f"tables.{before.name}.constraints.{item.name}",
            "drop_for_physical_reorder",
            f"ALTER TABLE {qualified_table} DROP CONSTRAINT {_quote(item.name)};",
        ))
    for ordinal, column in enumerate(before.columns):
        pending.append(_PendingStep(
            9,
            "column",
            f"tables.{before.name}.columns.{column.name}",
            "remove_for_physical_reorder",
            f"ALTER TABLE {qualified_table} DROP COLUMN {_quote(column.name)};",
            destructive=contains_data,
            data_movement=contains_data,
            order=ordinal,
        ))
    if contains_data:
        pending.append(_PendingStep(
            10,
            "table",
            table_path,
            "clear_staged_rows_for_physical_reorder",
            f"TRUNCATE TABLE {qualified_table};",
            destructive=True,
            data_movement=True,
        ))
    for ordinal, column in enumerate(after.columns):
        pending.append(_PendingStep(
            40,
            "column",
            f"tables.{after.name}.columns.{column.name}",
            "restore_in_physical_order",
            f"ALTER TABLE {qualified_table} ADD COLUMN {_column_definition(column)};",
            data_movement=contains_data,
            order=ordinal,
        ))
    if contains_data:
        identity_override = " OVERRIDING SYSTEM VALUE" if any(
            column.identity is not None for column in after.columns
        ) else ""
        pending.append(_PendingStep(
            45,
            "table",
            table_path,
            "restore_rows_after_physical_reorder",
            (
                f"INSERT INTO {qualified_table} ({column_names}){identity_override} "
                f"SELECT {column_names} FROM pg_temp.{_quote(backup_name)};"
            ),
            data_movement=True,
        ))
    for column in after.columns:
        if column.identity is None:
            continue
        sequence_lookup = (
            f"pg_get_serial_sequence({_literal(_qualified(namespace, after.name))}, "
            f"{_literal(column.name)})::regclass"
        )
        pending.append(_PendingStep(
            46,
            "column",
            f"tables.{after.name}.columns.{column.name}",
            "synchronize_identity_after_physical_reorder",
            (
                f"SELECT setval({sequence_lookup}, "
                f"COALESCE(GREATEST(MAX({_quote(column.name)}), 1), 1), "
                f"MAX({_quote(column.name)}) IS NOT NULL) FROM {qualified_table};"
            ),
            data_movement=contains_data,
        ))
    for item in [*after.keys, *after.checks]:
        pending.append(_PendingStep(
            60,
            "constraint",
            f"tables.{after.name}.constraints.{item.name}",
            "restore_after_physical_reorder",
            _constraint_sql(namespace, after, item, desired_columns),
        ))
    for index in after.indexes:
        pending.append(_PendingStep(
            65,
            "index",
            f"tables.{after.name}.indexes.{index.name}",
            "restore_after_physical_reorder",
            _create_index(namespace, after, index, desired_columns),
        ))
    for relationship in desired.relationships:
        if table_id not in {relationship.source_table_id, relationship.target_table_id}:
            continue
        pending.append(_PendingStep(
            70,
            "relationship",
            f"relationships.{relationship.name}",
            "restore_after_physical_reorder",
            _relationship_sql(namespace, relationship, desired_tables, desired_columns),
        ))
    for trigger in desired.triggers:
        if trigger.relation_name != after.name:
            continue
        pending.append(_PendingStep(
            80,
            "trigger",
            f"triggers.{after.name}.{trigger.name}",
            "restore_after_physical_reorder",
            _statement(trigger.definition),
        ))


def compile_migration_steps(
    namespace: str,
    live: SchemiiDesignContent,
    desired: SchemiiDesignContent,
    *,
    empty_tables: AbstractSet[str] = frozenset(),
    rebuild_table_ids: AbstractSet[str] = frozenset(),
    populated_rebuild_table_ids: AbstractSet[str] = frozenset(),
) -> tuple[list[MigrationStep], list[MigrationWarning]]:
    """Compile a conservative exact delta from reviewed live to merged desired."""

    pending: list[_PendingStep] = []
    blocking: list[MigrationWarning] = []
    live_tables, live_columns = _table_maps(live)
    desired_tables, desired_columns = _table_maps(desired)
    type_changes = _column_type_changes(live_tables, desired_tables)
    safe_type_change_ids = {
        column_id
        for column_id, decision in type_changes.items()
        if decision.disposition == "safe"
    }
    order_differences = {
        item.table_id: item
        for item in column_order_differences(namespace, live, desired)
    }
    for table_id in sorted(rebuild_table_ids):
        difference = order_differences.get(table_id)
        if difference is None:
            blocking.append(MigrationWarning(
                code="physical_column_order_not_available",
                message="This table no longer has a physical column-order difference to rebuild.",
                object_path=f"tables.{table_id}.column_order",
            ))
            continue
        if difference.blocking_reasons:
            blocking.append(MigrationWarning(
                code="physical_column_order_rebuild_blocked",
                message=" ".join(difference.blocking_reasons),
                object_path=f"tables.{difference.table_name}.column_order",
            ))
            continue
        _append_column_order_rebuild(
            pending,
            namespace,
            live,
            desired,
            table_id,
            contains_data=table_id in populated_rebuild_table_ids,
        )

    live_relationships = _by_id(live.relationships)
    desired_relationships = _by_id(desired.relationships)
    for identifier in sorted(set(live_relationships) | set(desired_relationships)):
        before = live_relationships.get(identifier)
        after = desired_relationships.get(identifier)
        changed_relationship_column = bool(
            safe_type_change_ids
            & set(
                [
                    *(before.source_column_ids if before is not None else ()),
                    *(before.target_column_ids if before is not None else ()),
                    *(after.source_column_ids if after is not None else ()),
                    *(after.target_column_ids if after is not None else ()),
                ]
            )
        )
        if before == after and not changed_relationship_column:
            continue
        if before is not None:
            table = live_tables[before.source_table_id]
            pending.append(_PendingStep(
                10, "relationship", f"relationships.{before.name}", "drop",
                f"ALTER TABLE {_qualified(namespace, table.name)} DROP CONSTRAINT {_quote(before.name)};",
                destructive=after is None,
            ))
        if after is not None:
            pending.append(_PendingStep(
                70, "relationship", f"relationships.{after.name}", "create",
                _relationship_sql(namespace, after, desired_tables, desired_columns),
            ))

    for identifier in sorted(set(live_tables) | set(desired_tables)):
        before = live_tables.get(identifier)
        after = desired_tables.get(identifier)
        if before is None and after is not None:
            pending.append(_PendingStep(
                40, "table", f"tables.{after.name}", "create",
                _create_table(namespace, after, desired_columns),
            ))
            for index in after.indexes:
                pending.append(_PendingStep(
                    65, "index", f"tables.{after.name}.indexes.{index.name}", "create",
                    _create_index(namespace, after, index, desired_columns),
                ))
            continue
        if before is not None and after is None:
            pending.append(_PendingStep(
                30, "table", f"tables.{before.name}", "drop",
                f"DROP TABLE {_qualified(namespace, before.name)};",
                destructive=True,
                data_movement=True,
            ))
            continue
        assert before is not None and after is not None
        active_name = before.name
        if before.name != after.name:
            pending.append(_PendingStep(
                35, "table", f"tables.{before.name}", "rename",
                f"ALTER TABLE {_qualified(namespace, before.name)} RENAME TO {_quote(after.name)};",
            ))
            active_name = after.name

        before_columns = _by_id(before.columns)
        after_columns = _by_id(after.columns)
        for column_id in sorted(set(before_columns) | set(after_columns)):
            old = before_columns.get(column_id)
            new = after_columns.get(column_id)
            path_name = new.name if new is not None else old.name
            path = f"tables.{after.name}.columns.{path_name}"
            if old is None and new is not None:
                cannot_populate = (
                    not new.nullable
                    and new.default_expression is None
                    and new.identity is None
                    and new.generated_expression is None
                )
                if cannot_populate and before.name not in empty_tables:
                    blocking.append(MigrationWarning(
                        code="required_column_population_required",
                        message=(
                            f"Adding required column {after.name}.{new.name} needs a default, "
                            "identity, generated expression, or an empty live table"
                        ),
                        object_path=path,
                    ))
                    continue
                pending.append(_PendingStep(
                    45, "column", path, "add",
                    f"ALTER TABLE {_qualified(namespace, active_name)} ADD COLUMN {_column_definition(new)};",
                    data_movement=bool(
                        new.default_expression is not None
                        or new.identity is not None
                        or new.generated_expression is not None
                    ),
                ))
                continue
            if old is not None and new is None:
                pending.append(_PendingStep(
                    20, "column", path, "drop",
                    f"ALTER TABLE {_qualified(namespace, active_name)} DROP COLUMN {_quote(old.name)};",
                    destructive=True,
                    data_movement=True,
                ))
                continue
            assert old is not None and new is not None
            current_column_name = old.name
            if old.name != new.name:
                pending.append(_PendingStep(
                    42, "column", path, "rename",
                    f"ALTER TABLE {_qualified(namespace, active_name)} RENAME COLUMN {_quote(old.name)} TO {_quote(new.name)};",
                ))
                current_column_name = new.name
            type_change = classify_type_change(old.data_type, new.data_type)
            if type_change.disposition == "safe":
                dependent_generated = sorted(
                    column.name
                    for column in after.columns
                    if column_id in column.generated_source_column_ids
                )
                dependent_views = _dependent_view_names(
                    namespace,
                    after.name,
                    live,
                    desired,
                )
                if dependent_generated or dependent_views:
                    dependencies = [
                        *(f"generated column {after.name}.{name}" for name in dependent_generated),
                        *(f"view {name}" for name in dependent_views),
                    ]
                    blocking.append(MigrationWarning(
                        code="column_type_dependency_requires_review",
                        message=(
                            f"Changing {after.name}.{new.name} requires rebuilding "
                            + ", ".join(dependencies)
                        ),
                        object_path=path,
                    ))
                else:
                    pending.append(_PendingStep(
                        48,
                        "column",
                        path,
                        "alter_type",
                        (
                            f"ALTER TABLE {_qualified(namespace, active_name)} ALTER COLUMN "
                            f"{_quote(current_column_name)} TYPE {new.data_type.strip()};"
                        ),
                        data_movement=True,
                    ))
            elif type_change.disposition == "blocked":
                blocking.append(MigrationWarning(
                    code="column_type_conversion_required",
                    message=(
                        f"Changing {after.name}.{new.name} from {old.data_type} to "
                        f"{new.data_type} requires a reviewed USING expression: "
                        f"{type_change.reason}"
                    ),
                    object_path=path,
                ))
            if (
                old.identity != new.identity
                or old.generated_expression != new.generated_expression
                or not _same_dependency_set(
                    old.generated_source_column_ids,
                    new.generated_source_column_ids,
                )
            ):
                blocking.append(MigrationWarning(
                    code="column_generation_change_unsupported",
                    message=f"Changing generated or identity behavior for {after.name}.{new.name} is not yet lossless",
                    object_path=path,
                ))
            if old.default_expression != new.default_expression and new.generated_expression is None:
                if new.default_expression is None:
                    action = "DROP DEFAULT"
                else:
                    action = f"SET DEFAULT {new.default_expression.strip()}"
                pending.append(_PendingStep(
                    50, "column", path, "alter_default",
                    f"ALTER TABLE {_qualified(namespace, active_name)} ALTER COLUMN {_quote(current_column_name)} {action};",
                ))
            if old.nullable != new.nullable:
                action = "DROP NOT NULL" if new.nullable else "SET NOT NULL"
                pending.append(_PendingStep(
                    52, "column", path, "alter_nullability",
                    f"ALTER TABLE {_qualified(namespace, active_name)} ALTER COLUMN {_quote(current_column_name)} {action};",
                    data_movement=not new.nullable,
                ))

        for category, before_items, after_items in (
            ("keys", before.keys, after.keys),
            ("checks", before.checks, after.checks),
        ):
            old_map = _by_id(before_items)
            new_map = _by_id(after_items)
            for item_id in sorted(set(old_map) | set(new_map)):
                old = old_map.get(item_id)
                new = new_map.get(item_id)
                if old == new or (
                    category == "checks"
                    and old is not None
                    and new is not None
                    and _same_check(old, new)
                ):
                    continue
                if old is not None:
                    pending.append(_PendingStep(
                        15, "constraint", f"tables.{after.name}.{category}.{old.name}", "drop",
                        f"ALTER TABLE {_qualified(namespace, active_name)} DROP CONSTRAINT {_quote(old.name)};",
                        destructive=new is None,
                    ))
                if new is not None:
                    pending.append(_PendingStep(
                        60, "constraint", f"tables.{after.name}.{category}.{new.name}", "create",
                        _constraint_sql(namespace, after, new, desired_columns),
                    ))

        old_indexes = _by_id(before.indexes)
        new_indexes = _by_id(after.indexes)
        for index_id in sorted(set(old_indexes) | set(new_indexes)):
            old = old_indexes.get(index_id)
            new = new_indexes.get(index_id)
            if old == new or (
                old is not None
                and new is not None
                and _same_index(old, new)
            ):
                continue
            if old is not None:
                pending.append(_PendingStep(
                    12, "index", f"tables.{after.name}.indexes.{old.name}", "drop",
                    f"DROP INDEX {_qualified(namespace, old.name)};",
                    destructive=new is None,
                ))
            if new is not None:
                pending.append(_PendingStep(
                    65, "index", f"tables.{after.name}.indexes.{new.name}", "create",
                    _create_index(namespace, after, new, desired_columns),
                ))

    live_types = _by_id(live.types)
    desired_types = _by_id(desired.types)
    for identifier in sorted(set(live_types) | set(desired_types)):
        before = live_types.get(identifier)
        after = desired_types.get(identifier)
        if before == after:
            continue
        if before is None and after is not None:
            pending.append(_PendingStep(38, "type", f"types.{after.name}", "create", _statement(after.definition)))
        elif before is not None and after is None:
            pending.append(_PendingStep(
                32, "type", f"types.{before.name}", "drop",
                f"DROP {'DOMAIN' if before.kind == 'domain' else 'TYPE'} {_qualified(namespace, before.name)};",
                destructive=True,
            ))
        else:
            blocking.append(MigrationWarning(
                code="type_replacement_unsupported",
                message=f"Changing type {after.name} requires a specialized PostgreSQL type migration",
                object_path=f"types.{after.name}",
            ))

    live_functions = _by_id(live.functions)
    desired_functions = _by_id(desired.functions)
    for identifier in sorted(set(live_functions) | set(desired_functions)):
        before: DesignFunction | None = live_functions.get(identifier)
        after: DesignFunction | None = desired_functions.get(identifier)
        if before == after:
            continue
        if before is not None and after is None:
            kind = "PROCEDURE" if before.kind == "procedure" else "FUNCTION"
            pending.append(_PendingStep(
                18, before.kind, f"functions.{before.name}({before.identity_arguments})", "drop",
                f"DROP {kind} {_qualified(namespace, before.name)}({before.identity_arguments.strip()});",
                destructive=True,
            ))
        if after is not None:
            source = _statement(after.definition)
            operation = "create"
            if before is not None:
                source = _CREATE_ROUTINE.sub(lambda match: f"CREATE OR REPLACE {match.group(1).upper()}", source, count=1)
                operation = "replace"
            pending.append(_PendingStep(
                55, after.kind, f"functions.{after.name}({after.identity_arguments})", operation, source,
                destructive=before is not None and before.return_type != after.return_type,
            ))

    live_views = _by_id(live.views)
    desired_views = _by_id(desired.views)
    for identifier in sorted(set(live_views) | set(desired_views)):
        before: DesignView | None = live_views.get(identifier)
        after: DesignView | None = desired_views.get(identifier)
        if before == after:
            continue
        if before is not None and after is None:
            kind = "MATERIALIZED VIEW" if before.kind == "materialized_view" else "VIEW"
            pending.append(_PendingStep(
                14, before.kind, f"views.{before.name}", "drop",
                f"DROP {kind} {_qualified(namespace, before.name)};",
                destructive=True,
            ))
        if after is not None:
            if after.kind == "materialized_view" and before is not None:
                blocking.append(MigrationWarning(
                    code="materialized_view_replacement_unsupported",
                    message=f"Replacing materialized view {after.name} requires preservation analysis",
                    object_path=f"views.{after.name}",
                ))
                continue
            prefix = "CREATE OR REPLACE VIEW" if before is not None else (
                "CREATE MATERIALIZED VIEW" if after.kind == "materialized_view" else "CREATE VIEW"
            )
            sql = f"{prefix} {_qualified(namespace, after.name)} AS\n{after.definition.strip().rstrip(';')}"
            if after.kind == "materialized_view":
                sql += "\nWITH DATA" if after.populate_on_create else "\nWITH NO DATA"
            pending.append(_PendingStep(
                75, after.kind, f"views.{after.name}", "replace" if before else "create", sql + ";",
            ))

    live_triggers = _by_id(live.triggers)
    desired_triggers = _by_id(desired.triggers)
    for identifier in sorted(set(live_triggers) | set(desired_triggers)):
        before: DesignTrigger | None = live_triggers.get(identifier)
        after: DesignTrigger | None = desired_triggers.get(identifier)
        if before == after:
            continue
        if before is not None:
            pending.append(_PendingStep(
                11, "trigger", f"triggers.{before.relation_name}.{before.name}", "drop",
                f"DROP TRIGGER {_quote(before.name)} ON {_qualified(namespace, before.relation_name)};",
                destructive=after is None,
            ))
        if after is not None:
            pending.append(_PendingStep(
                80, "trigger", f"triggers.{after.relation_name}.{after.name}", "create",
                _statement(after.definition),
            ))

    unique_pending: dict[tuple[int, str], _PendingStep] = {}
    for item in pending:
        unique_pending.setdefault((item.phase, item.sql), item)
    pending = list(unique_pending.values())
    pending.sort(key=lambda item: (item.phase, item.order, item.path, item.operation))
    steps = [
        MigrationStep(
            index=index,
            object_kind=item.kind,
            object_path=item.path,
            operation=item.operation,
            sql=item.sql,
            destructive=item.destructive,
            requires_lock=item.requires_lock,
            data_movement=item.data_movement,
        )
        for index, item in enumerate(pending, start=1)
    ]
    return steps, blocking


def new_baseline_content(
    catalog: PostgresCatalog,
    desired: SchemiiDesignContent,
) -> tuple[SchemiiDesignContent, list[MigrationWarning], bool]:
    """Create the first live synchronization point using desired IDs by name."""

    return catalog_design(catalog, desired)


def content_fingerprint(content: SchemiiDesignContent) -> str:
    return design_fingerprint(content)


def random_plan_id() -> str:
    return f"mpl_{secrets.token_hex(16)}"
