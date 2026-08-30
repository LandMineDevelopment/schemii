from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .ai_policy import LEGACY_CAPABILITIES, MODES, canonical_capability
from .schemii_ai_actions import normalize_schemii_action, schemii_action_approval_floor, schemii_action_capability
from .schemer_ai_actions import normalize_schemer_action


ActionNormalizer = Callable[[Any, str], dict[str, Any]]
ActionClassifier = Callable[[Any], str | None]


@dataclass(frozen=True)
class AiToolContract:
    name: str
    schema: str
    action_type: str
    normalizer: ActionNormalizer
    capability: str | None
    approval_floor: ActionClassifier
    executor_adapter: str
    supported_app: str


def _schemii(name: str, action_type: str, capability: str | None, adapter: str | None = None, floor: str | None = None) -> AiToolContract:
    return AiToolContract(name, f"ai/workspace/.opencode/tools/{name}.ts", action_type, normalize_schemii_action, capability, lambda action: schemii_action_approval_floor(action) or floor, adapter or action_type, "schemii")


def _schemer(name: str, action_type: str, capability: str | None, adapter: str | None = None, floor: str | None = None) -> AiToolContract:
    return AiToolContract(name, f"ai/schemer-workspace/.opencode/tools/{name}.ts", action_type, normalize_schemer_action, capability, lambda _action: floor, adapter or action_type, "schemer")


SCHEMII_TOOL_CONTRACTS: Mapping[str, AiToolContract] = MappingProxyType({item.name: item for item in (
    _schemii("schema_read_query", "schema_read_query", "rawread", floor="every_action"),
    _schemii("schema_data_read", "data_read", "structured"),
    _schemii("schema_raw_write", "raw_write", "rawwrite", floor="every_action"),
    _schemii("schema_connection_setup", "connection_setup", None, floor="every_action"),
    _schemii("schema_project_open", "open_project", None, floor="every_action"),
    _schemii("schema_project_create", "create_project", "schema"),
    _schemii("schema_populate", "populate_schema", "schema"),
    _schemii("schema_add_table", "add_table", "schema"),
    _schemii("schema_rename_table", "rename_table", "schema"),
    _schemii("schema_add_column", "add_column", "schema"),
    _schemii("schema_update_column", "update_column", "schema"),
    _schemii("schema_delete_element", "delete_element", "schema", floor="every_action"),
    _schemii("schema_add_relationship", "add_relationship", "schema"),
    _schemii("schema_connection_open", "open_connection", None, floor="every_action"),
    _schemii("schema_migration_preview", "migration_preview", "schema"),
    _schemii("schema_insert_rows_preview", "insert_rows_preview", "write"),
    _schemii("schema_create_view_preview", "create_view_preview", "write"),
)})

SCHEMER_TOOL_CONTRACTS: Mapping[str, AiToolContract] = MappingProxyType({item.name: item for item in (
    _schemer("schemer_read_query", "read_query", "raw_read", floor="every_action"),
    _schemer("schemer_dashboard_open", "dashboard_open", "dashboard_read"),
    _schemer("schemer_dashboard_create", "dashboard_create", "dashboard_write", floor="every_action"),
    _schemer("schemer_widget_create", "widget_create", "dashboard_write", floor="every_action"),
    _schemer("schemer_widget_rename", "widget_rename", "dashboard_write", floor="every_action"),
    _schemer("schemer_widget_duplicate", "widget_duplicate", "dashboard_write", floor="every_action"),
    _schemer("schemer_widget_delete", "widget_delete", "dashboard_write", floor="every_action"),
)})

AI_TOOL_CONTRACTS: Mapping[str, Mapping[str, AiToolContract]] = MappingProxyType({
    "schemii": SCHEMII_TOOL_CONTRACTS,
    "schemer": SCHEMER_TOOL_CONTRACTS,
})

SCHEMII_SERVER_ACTIONS: Mapping[str, tuple[str, str]] = MappingProxyType({
    "schema_batch": ("schema", "model"),
    "migration_apply": ("schema", "server_apply"),
    "postgres_write_apply": ("write", "server_apply"),
})


def contract_for_action(application: str, action: Any) -> AiToolContract | None:
    action_type = action.get("type") if isinstance(action, dict) else None
    return next((item for item in AI_TOOL_CONTRACTS[application].values() if item.action_type == action_type), None)


def _server_schemii_contract(action: Any, origin: Any = None) -> tuple[str, str | None] | None:
    action_type = action.get("type") if isinstance(action, dict) else None
    if not isinstance(action_type, str):
        return None
    server_contract = SCHEMII_SERVER_ACTIONS.get(action_type)
    if server_contract is None:
        return None
    capability, expected_origin = server_contract
    if origin is not None and origin != expected_origin:
        raise ValueError("server-issued action origin is invalid")
    if action_type == "schema_batch":
        if set(action) != {"type", "actions", "requiresConfirmation"} or action.get("requiresConfirmation") is not True:
            raise ValueError("schema batch contract is invalid")
        actions = action.get("actions")
        if not isinstance(actions, list) or not 2 <= len(actions) <= 5:
            raise ValueError("schema batch contract is invalid")
        for item in actions:
            contract = contract_for_action("schemii", item)
            if contract is None or contract.capability != "schema" or contract.normalizer(item, "schema-read-write") != item:
                raise ValueError("schema batch action contract is invalid")
    elif action_type == "migration_apply":
        fields = {
            "type", "profileId", "database", "namespace", "planId", "destructive", "reviewDigest",
            "requiresConfirmation",
        }
        if (
            set(action) != fields or action.get("requiresConfirmation") is not True
            or not isinstance(action.get("destructive"), bool)
            or not all(isinstance(action.get(key), str) and action[key] for key in ("profileId", "database", "namespace", "planId"))
            or not isinstance(action.get("reviewDigest"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", action["reviewDigest"])
        ):
            raise ValueError("migration apply contract is invalid")
    else:
        fields = {
            "type", "writeKind", "profileId", "database", "namespace", "relation", "planId", "reviewDigest",
            "effectsDigest", "rowCount", "reviewedPlan", "requiresConfirmation",
        }
        reviewed = action.get("reviewedPlan")
        if (
            set(action) != fields or action.get("requiresConfirmation") is not True
            or action.get("writeKind") not in {"insert_rows", "create_view"}
            or not all(isinstance(action.get(key), str) and action[key] for key in ("profileId", "database", "namespace", "relation", "planId"))
            or not isinstance(action.get("reviewDigest"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", action["reviewDigest"])
            or not isinstance(reviewed, dict)
            or reviewed.get("applyPlanId") != action.get("planId")
            or reviewed.get("planDigest") != action.get("reviewDigest")
        ):
            raise ValueError("PostgreSQL write apply contract is invalid")
        if action["writeKind"] == "insert_rows":
            if (
                not isinstance(action.get("rowCount"), int) or isinstance(action.get("rowCount"), bool) or action["rowCount"] < 0
                or not isinstance(action.get("effectsDigest"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", action["effectsDigest"])
                or reviewed.get("rowCount") != action["rowCount"]
                or reviewed.get("effectsDigest") != action["effectsDigest"]
            ):
                raise ValueError("insert apply contract is invalid")
        elif action.get("rowCount") is not None or action.get("effectsDigest") is not None:
            raise ValueError("view apply contract is invalid")
    return capability, schemii_action_approval_floor(action)


def effective_schemii_contract(action: Any) -> tuple[str | None, str | None]:
    """Classify model and server-issued Schemii actions through one policy contract."""
    contract = contract_for_action("schemii", action)
    if contract is not None:
        return contract.capability, contract.approval_floor(action)
    server_contract = _server_schemii_contract(action)
    if server_contract is None:
        schemii_action_capability(action)
        raise ValueError("action authority contract is invalid")
    return server_contract


def action_authority(
    application: str, action: Any, capability: Any, effective_mode: Any, *, origin: Any = None,
) -> tuple[str | None, str]:
    """Recompute an action's capability and non-relaxable approval mode server-side."""
    contract = contract_for_action(application, action)
    server_contract = _server_schemii_contract(action, origin) if application == "schemii" and contract is None else None
    expected_origin = "model"
    if contract is None:
        action_type = action.get("type") if isinstance(action, dict) else None
        expected_origin = SCHEMII_SERVER_ACTIONS.get(action_type, (None, None))[1] if isinstance(action_type, str) else None
    if (contract is None and server_contract is None) or origin != expected_origin or effective_mode not in MODES[1:]:
        raise ValueError("action authority contract is invalid")
    capability_contract = contract.capability if contract is not None else server_contract[0]
    expected = None if capability_contract is None else canonical_capability(capability_contract)
    actual = None if capability is None else LEGACY_CAPABILITIES[application].get(capability, canonical_capability(capability))
    if expected is not None and actual != expected:
        raise ValueError("action capability binding does not match its server contract")
    floor = contract.approval_floor(action) if contract is not None else server_contract[1]
    if floor is not None and floor not in MODES[1:]:
        raise ValueError("action approval floor is invalid")
    mode = effective_mode if floor is None else MODES[min(MODES.index(effective_mode), MODES.index(floor))]
    return expected, mode
