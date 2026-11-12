"""Reviewed metadata copies with semantic identities; never executes target SQL."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pglast import ast, parse_sql
from pglast.visitors import Visitor

from .history import design_change_summary
from .models import (
    DesignDeploymentCollision,
    DesignDeploymentPreview,
    SchemiiDesignContent,
)
from .store import (
    DesignValidationError,
    authored_content_document,
    validate_design_content,
)


GROUPS = ("types", "tables", "relationships", "functions", "views", "triggers")
CHILDREN = ("columns", "keys", "checks", "indexes")
REFERENCES = {
    "source_table_id",
    "target_table_id",
    "source_column_ids",
    "target_column_ids",
    "column_ids",
    "include_column_ids",
    "expression_source_column_ids",
    "predicate_column_ids",
    "generated_source_column_ids",
}


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def _objects(content: SchemiiDesignContent) -> dict[tuple[str, ...], dict[str, Any]]:
    document = content.model_dump(mode="json")
    tables = {table.id: table.name for table in content.tables}
    result = {}
    for group in GROUPS:
        for item in document[group]:
            if group == "relationships":
                key = (group, tables[item["source_table_id"]], item["name"])
            elif group == "functions":
                key = (group, item["name"], item["identity_arguments"])
            elif group == "triggers":
                key = (group, item["relation_name"], item["name"])
            else:
                key = (group, item["name"])
            if group == "tables":
                for child_group in CHILDREN:
                    for child in item[child_group]:
                        result[(*key, child_group, child["name"])] = child
                item = {
                    name: value for name, value in item.items() if name not in CHILDREN
                }
            result[key] = item
    return result


def _remap(value: Any, identities: dict[str, str], field: str = "") -> Any:
    if isinstance(value, dict):
        return {key: _remap(item, identities, key) for key, item in value.items()}
    if isinstance(value, list):
        return [_remap(item, identities, field) for item in value]
    if field == "id" or field in REFERENCES:
        return identities.get(value, value)
    return value


def _display_object(item: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
    if "definition" in item:
        return {"definition": item["definition"]}

    def display(value: Any, field: str = "") -> Any:
        if isinstance(value, list):
            return [display(child, field) for child in value]
        if field in REFERENCES:
            return labels.get(value, value)
        return value

    return {key: display(value, key) for key, value in item.items() if key != "id"}


def merge_content(
    source: SchemiiDesignContent,
    target: SchemiiDesignContent,
    target_workspace_id: str,
    resolutions: dict[str, str],
):
    """Align by semantic names, retain target-only leaves, and remap every ID edge."""
    incoming, retained = _objects(source), _objects(target)
    identities = {}
    used = {item["id"] for item in retained.values()}
    for key, item in incoming.items():
        if key in retained:
            identities[item["id"]] = retained[key]["id"]
        else:
            candidate = item["id"]
            if candidate in used:
                candidate = (
                    candidate.split("_", 1)[0]
                    + "_"
                    + digest([target_workspace_id, key, item["id"]])[:32]
                )
            identities[item["id"]] = candidate
            used.add(candidate)
    merged = dict(retained)
    labels = {item["id"]: " / ".join(key) for key, item in retained.items()}
    labels.update(
        {identities[item["id"]]: " / ".join(key) for key, item in incoming.items()}
    )
    collisions = []
    known = set()
    for key, original in incoming.items():
        item = _remap(original, identities)
        before = retained.get(key)
        if before is None or before == item:
            merged[key] = item
            continue
        collision_key = digest(key)
        known.add(collision_key)
        resolution = resolutions.get(collision_key)
        collisions.append(
            DesignDeploymentCollision(
                key=collision_key,
                label=" / ".join(key),
                target=_display_object(before, labels),
                incoming=_display_object(item, labels),
                resolution=resolution,
            )
        )
        if resolution == "incoming":
            merged[key] = item
    if set(resolutions) - known:
        raise DesignValidationError(
            "Collision choices no longer match this review. Review the copy again."
        )
    document = {group: [] for group in GROUPS}
    for key, item in merged.items():
        if key[0] == "tables" and len(key) > 2:
            continue
        if key[0] == "tables":
            item = {
                **item,
                **{
                    group: [
                        child
                        for child_key, child in merged.items()
                        if child_key[:2] == key
                        and len(child_key) == 4
                        and child_key[2] == group
                    ]
                    for group in CHILDREN
                },
            }
        document[key[0]].append(item)
    try:
        content = SchemiiDesignContent.model_validate(document)
        validate_design_content(content)
    except ValueError as error:
        raise DesignValidationError(
            "These collision choices do not form a valid design. Keep the target definitions or revise the detached design before copying."
        ) from error
    return content, collisions


class _QualifiedReferences(Visitor):
    def __init__(self) -> None:
        super().__init__()
        self.schemas: set[str] = set()

    def visit_RangeVar(self, ancestors: Any, node: ast.RangeVar) -> None:
        if node.schemaname:
            self.schemas.add(node.schemaname)

    def visit_TypeName(self, ancestors: Any, node: ast.TypeName) -> None:
        if node.names and len(node.names) > 1:
            self.schemas.add(node.names[0].sval)

    def visit_FuncCall(self, ancestors: Any, node: ast.FuncCall) -> None:
        if len(node.funcname) > 1:
            self.schemas.add(node.funcname[0].sval)


def namespace_warnings(content: SchemiiDesignContent, namespace: str) -> list[str]:
    """Preserve intentional qualifications; never rewrite SQL text or body literals."""
    warnings = [
        f"Unqualified SQL names use target namespace {json.dumps(namespace, ensure_ascii=False)}. Explicit schema names stay unchanged."
    ]
    visitor = _QualifiedReferences()
    for item in [*content.types, *content.functions, *content.views, *content.triggers]:
        visitor(parse_sql(item.definition))
    external = sorted(visitor.schemas - {namespace, "pg_catalog"})
    if external:
        warnings.append(
            "Authored SQL explicitly references other schemas: "
            + ", ".join(json.dumps(name, ensure_ascii=False) for name in external)
            + ". Check these names in migration review."
        )
    if content.functions:
        warnings.append(
            "Routine bodies and dynamic SQL are preserved verbatim. Review their schema references and any SET search_path clauses in the migration SQL."
        )
    return warnings


def build_preview(
    source: Any,
    target: Any,
    source_workspace: Any,
    target_workspace: Any,
    connection: Any,
    baseline: Any,
    baseline_content: SchemiiDesignContent,
    live_fingerprint: str,
    live_drift: bool,
    resolutions: dict[str, str],
):
    content, collisions = merge_content(
        source.content, target.content, target.workspace_id, resolutions
    )
    warnings = namespace_warnings(content, target_workspace.namespace)
    summary = design_change_summary(target.content, content)
    binding = {
        "source": [
            source_workspace.model_dump(mode="json"),
            source.revision,
            source.fingerprint,
        ],
        "target": [
            target_workspace.model_dump(mode="json"),
            target.revision,
            target.fingerprint,
        ],
        "connection": [
            connection.id,
            connection.revision,
            connection.owner_id,
            connection.database,
            connection.host,
            connection.port,
            connection.username,
        ],
        "baseline": baseline.model_dump(mode="json"),
        "liveCatalogFingerprint": live_fingerprint,
        "resolutions": resolutions,
        "content": authored_content_document(content),
    }
    return DesignDeploymentPreview(
        source_workspace_id=source.workspace_id,
        target_workspace_id=target.workspace_id,
        source_revision=source.revision,
        target_revision=target.revision,
        target_label=f"{connection.database} · {target_workspace.namespace} · {connection.username}",
        target_changes=design_change_summary(baseline_content, target.content),
        live_drift=live_drift,
        summary=summary,
        collisions=collisions,
        warnings=warnings,
        ready=all(item.resolution for item in collisions),
        review_digest=digest(binding),
    ), content
