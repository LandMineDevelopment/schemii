"""Single source of truth for model-visible Schemii tools."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class ToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AddTableColumn(ToolModel):
    name: str = Field(min_length=1, max_length=63)
    data_type: str = Field(min_length=1, max_length=512)
    nullable: bool = True
    default_expression: str | None = Field(default=None, max_length=262_144)
    identity: Literal["always", "by_default"] | None = None
    generated_expression: str | None = Field(default=None, max_length=262_144)


class AddTableKey(ToolModel):
    kind: Literal["primary", "unique"]
    columns: list[str] = Field(min_length=1, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=63)


class AddTable(ToolModel):
    type: Literal["add_table"]
    name: str = Field(min_length=1, max_length=63)
    columns: list[AddTableColumn] = Field(min_length=1, max_length=100)
    keys: list[AddTableKey] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_key_columns(self) -> "AddTable":
        names = [column.name for column in self.columns]
        if len(names) != len(set(names)):
            raise ValueError("add_table column names must be unique")
        if sum(key.kind == "primary" for key in self.keys) > 1:
            raise ValueError("add_table accepts only one primary key")
        missing = sorted(
            {name for key in self.keys for name in key.columns if name not in names}
        )
        if missing:
            raise ValueError(f"add_table key refers to an unknown column: {missing[0]}")
        return self


class RenameTable(ToolModel):
    type: Literal["rename_table"]
    table_id: str
    name: str = Field(min_length=1, max_length=63)


class AddColumn(ToolModel):
    type: Literal["add_column"]
    table_id: str
    column: dict[str, Any]


class UpdateColumn(ToolModel):
    type: Literal["update_column"]
    table_id: str
    column_id: str
    changes: dict[str, Any]


class PutTopLevelObject(ToolModel):
    """Create or replace any top-level object in the desired design."""

    type: Literal["put_top_level_object"]
    collection: Literal[
        "types", "tables", "relationships", "functions", "views", "triggers"
    ]
    object: dict[str, Any]


class PutTableMember(ToolModel):
    """Create or replace a table-owned column, key, check, or index."""

    type: Literal["put_table_member"]
    table_id: str
    collection: Literal["columns", "keys", "checks", "indexes"]
    object: dict[str, Any]


class DeleteObject(ToolModel):
    type: Literal["delete_object"]
    object_id: str


DesignAction = Annotated[
    AddTable
    | RenameTable
    | AddColumn
    | UpdateColumn
    | PutTopLevelObject
    | PutTableMember
    | DeleteObject,
    Field(discriminator="type"),
]
DESIGN_ACTION = TypeAdapter(DesignAction)


@dataclass(frozen=True)
class ToolProposal:
    capability: str
    action_type: str
    summary: str
    action: dict[str, Any]
    destructive: bool = False


# OpenCode receives only product tools. Generic filesystem, shell, web, and
# delegation tools are disabled because Schemii owns authority.
TOOLS = {
    "schemii_design_change": True,
    "schemii_read_query": True,
    "schemii_open_console": True,
    "schemii_review_migration": True,
    "skill": False,
    "bash": False,
    "shell": False,
    "read": False,
    "write": False,
    "edit": False,
    "apply_patch": False,
    "glob": False,
    "grep": False,
    "webfetch": False,
    "websearch": False,
    "task": False,
}

TOOL_CAPABILITIES = {
    "schemii_design_change": "design_changes",
    "schemii_read_query": "raw_sql_read",
    "schemii_open_console": "raw_sql_write",
    "schemii_review_migration": "design_changes",
}

CAPABILITY_LABELS = {
    "design_changes": "Propose design changes",
    "live_catalog": "Inspect live catalog",
    "structured_data_read": "Use selected result rows",
    "raw_sql_read": "Prepare read queries",
    "raw_sql_write": "Prepare write SQL",
}


def tools_for_capabilities(capabilities: Any) -> dict[str, bool]:
    """Return the exact tool set authorized by the current chat policy."""

    return {
        name: bool(getattr(capabilities, TOOL_CAPABILITIES[name], False))
        if name in TOOL_CAPABILITIES
        else enabled
        for name, enabled in TOOLS.items()
    }


def authority_manifest(revision: int, capabilities: Any) -> dict[str, Any]:
    """Describe current authority without relying on earlier chat messages."""

    values = {
        name: {
            "enabled": bool(getattr(capabilities, name, False)),
            "permissionLabel": label,
        }
        for name, label in CAPABILITY_LABELS.items()
    }
    tools = tools_for_capabilities(capabilities)
    return {
        "policyRevision": revision,
        "currentTurnAuthoritative": True,
        "assistantCanApproveOrApply": False,
        "capabilities": values,
        "contextSources": {
            "liveCatalog": {
                "enabled": capabilities.live_catalog,
                "requiredPermission": CAPABILITY_LABELS["live_catalog"],
                "location": "CONTEXT.liveCatalog",
            },
            "selectedResultRows": {
                "enabled": capabilities.structured_data_read,
                "requiredPermission": CAPABILITY_LABELS["structured_data_read"],
                "location": "CONTEXT.queryResult",
            },
        },
        "enabledTools": [name for name, enabled in tools.items() if enabled],
        "disabledTools": [
            {
                "tool": name,
                "requiredPermission": CAPABILITY_LABELS[TOOL_CAPABILITIES[name]],
            }
            for name, enabled in tools.items()
            if name in TOOL_CAPABILITIES and not enabled
        ],
    }


def permission_label(capability: str) -> str:
    return CAPABILITY_LABELS.get(capability, capability.replace("_", " "))


def normalize_tool_call(name: str, value: dict[str, Any]) -> ToolProposal:
    """Convert untrusted model arguments into a typed proposal envelope."""

    if name == "schemii_design_change":
        raw_action = value.get("action")
        if isinstance(raw_action, str):
            try:
                raw_action = json.loads(raw_action)
            except json.JSONDecodeError as error:
                raise ValueError("action must be a JSON object") from error
        action = DESIGN_ACTION.validate_python(raw_action).model_dump()
        summary = str(value.get("summary") or "").strip()
        if not summary:
            summary = action["type"].replace("_", " ")
        return ToolProposal(
            "design_changes",
            "design_change",
            summary[:2048],
            action,
            action["type"] == "delete_object",
        )
    if name == "schemii_read_query":
        sql = str(value.get("sql", "")).strip()
        if not sql or len(sql.encode()) > 1024 * 1024:
            raise ValueError("sql is required")
        return ToolProposal(
            "raw_sql_read",
            "data_read",
            str(value.get("summary") or "Run read-only query")[:2048],
            {"sql": sql},
        )
    if name == "schemii_open_console":
        sql = str(value.get("sql", "")).strip()
        if not sql:
            raise ValueError("sql is required")
        return ToolProposal(
            "raw_sql_write",
            "console_script",
            str(value.get("summary") or "Open SQL in Console")[:2048],
            {"sql": sql},
            True,
        )
    if name == "schemii_review_migration":
        return ToolProposal(
            "design_changes",
            "migration_review",
            str(value.get("summary") or "Review migration")[:2048],
            {},
        )
    raise ValueError(f"Unknown assistant tool: {name}")
