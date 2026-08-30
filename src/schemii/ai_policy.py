from __future__ import annotations

import hashlib
import json
from typing import Any

from .metadata.errors import MetadataStoreError


POLICY_SCHEMA_VERSION = 1
DEFAULT_AGENT_ID = "default"
MODES = ("disabled", "every_action", "once_per_chat", "automatic")
POSTGRES_CAPABILITIES = ("schema", "structured_read", "structured_write", "raw_read", "raw_write")
APP_RESOURCE_CAPABILITIES = ("dashboard_read", "dashboard_write")
CAPABILITY_ALIASES = {
    "structured": "structured_read",
    "write": "structured_write",
    "rawread": "raw_read",
    "rawwrite": "raw_write",
}
LEGACY_SCHEMER_TIERS = {
    "metadata": ("dashboard_read",),
    "dashboard": ("dashboard_read", "dashboard_write"),
    "data": ("dashboard_read", "dashboard_write", "structured_read", "raw_read"),
}
APPLICATION_CAPABILITIES = {
    "schemii": POSTGRES_CAPABILITIES,
    "schemer": ("structured_read", "raw_read", "dashboard_read", "dashboard_write"),
}
SAFETY_FLOORS = {
    "schema": "every_action",
    "structured_read": "automatic",
    "structured_write": "every_action",
    "raw_read": "every_action",
    "raw_write": "every_action",
    "dashboard_read": "automatic",
    "dashboard_write": "every_action",
}
BOUND_RANGES = {
    "rowsDisclosed": (1, 10000),
    "rowsWritten": (1, 10000),
    "pagesInspected": (1, 100),
    "rawStatements": (1, 20),
    "operationTimeoutMs": (1000, 300000),
    "agentConcurrency": (1, 16),
}
LOCAL_SETTINGS_ACTION = {"type": "open_local_settings", "path": "/api/ai/settings"}
TARGET_CAPABILITIES = frozenset({"structured_read", "structured_write", "raw_read", "raw_write"})
LEGACY_CAPABILITIES = {
    "schemii": {
        "schema": "schema", "structured": "structured_read", "write": "structured_write",
        "rawread": "raw_read", "rawwrite": "raw_write",
    },
    "schemer": {
        "metadata": "dashboard_read", "dashboard": "dashboard_write", "data": "raw_read",
    },
}


def canonical_capability(value: Any) -> str:
    if not isinstance(value, str):
        raise MetadataStoreError("invalid_metadata", "AI capability name is invalid", status=400)
    return CAPABILITY_ALIASES.get(value, value)


def legacy_schemer_capabilities(tier: Any) -> tuple[str, ...]:
    try:
        return LEGACY_SCHEMER_TIERS[tier]
    except (KeyError, TypeError) as exc:
        raise MetadataStoreError("invalid_metadata", "Legacy Schemer access tier is invalid", status=400) from exc


def default_policy(application: str) -> dict[str, Any]:
    supported = _supported(application)
    return validate_policy(application, {
        "schemaVersion": POLICY_SCHEMA_VERSION,
        "capabilities": {capability: "disabled" for capability in supported},
        "bounds": {name: None for name in BOUND_RANGES},
    })


def validate_policy(application: str, value: Any) -> dict[str, Any]:
    supported = _supported(application)
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "capabilities", "bounds"}:
        raise MetadataStoreError("invalid_metadata", "AI policy fields are invalid", status=400)
    if value["schemaVersion"] != POLICY_SCHEMA_VERSION:
        raise MetadataStoreError("invalid_metadata", "AI policy schemaVersion is unsupported", status=400)
    capabilities = value["capabilities"]
    if not isinstance(capabilities, dict) or set(capabilities) != set(supported):
        raise MetadataStoreError("invalid_metadata", "AI policy capabilities do not match the application support matrix", status=400)
    if any(mode not in MODES for mode in capabilities.values()):
        raise MetadataStoreError("invalid_metadata", "AI capability mode is invalid", status=400)
    bounds = value["bounds"]
    if not isinstance(bounds, dict) or set(bounds) != set(BOUND_RANGES):
        raise MetadataStoreError("invalid_metadata", "AI policy bounds fields are invalid", status=400)
    normalized_bounds: dict[str, int | None] = {}
    for name, limits in BOUND_RANGES.items():
        bound = bounds[name]
        if bound is not None and (isinstance(bound, bool) or not isinstance(bound, int) or not limits[0] <= bound <= limits[1]):
            raise MetadataStoreError("invalid_metadata", f"AI policy bound {name} is outside its allowed range", status=400)
        normalized_bounds[name] = bound
    configured = {name: capabilities[name] for name in supported}
    return {"schemaVersion": POLICY_SCHEMA_VERSION, "capabilities": configured, "bounds": normalized_bounds}


def effective_capabilities(application: str, policy: dict[str, Any]) -> dict[str, dict[str, str]]:
    validated = validate_policy(application, policy)
    result = {}
    for capability in _supported(application):
        configured = validated["capabilities"][capability]
        floor = SAFETY_FLOORS[capability]
        effective = MODES[min(MODES.index(configured), MODES.index(floor))]
        result[capability] = {"configuredMode": configured, "effectiveMode": effective, "safetyFloor": floor}
    return result


def effective_bounds(policy: dict[str, Any]) -> dict[str, int | None]:
    return dict(policy["bounds"])


def canonical_policy_json(policy: dict[str, Any]) -> str:
    return json.dumps(policy, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))


def policy_digest(policy: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_policy_json(policy).encode("ascii")).hexdigest()


def effective_chat_snapshot(
    settings: dict[str, Any], requested: Any, *, target_verified: bool,
    disclosure_class: str, requested_modes: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Narrow one immutable agent revision into a chat-owned effective snapshot."""
    application = settings["application"]
    aliases = LEGACY_CAPABILITIES[application]
    requested_names = set(requested or ())
    if any(name not in aliases for name in requested_names):
        raise MetadataStoreError("invalid_metadata", "Legacy AI capability selection is invalid", status=400)
    requested_canonical = (
        {capability for name in requested_names for capability in legacy_schemer_capabilities(name)}
        if application == "schemer"
        else {aliases[name] for name in requested_names}
    )
    narrowed_modes = requested_modes or {}
    capabilities = {}
    for name, authority in settings["capabilities"].items():
        configured = authority["configuredMode"] if name in requested_canonical else "disabled"
        legacy_name = next((legacy for legacy, canonical in aliases.items() if canonical == name), None)
        requested_mode = narrowed_modes.get(legacy_name) if legacy_name is not None else None
        if requested_mode is not None:
            if requested_mode not in MODES[1:]:
                raise MetadataStoreError("invalid_metadata", "Legacy AI approval mode is invalid", status=400)
            configured = MODES[min(MODES.index(configured), MODES.index(requested_mode))]
        if name in TARGET_CAPABILITIES and not target_verified:
            configured = "disabled"
        floor = authority["safetyFloor"]
        effective = MODES[min(MODES.index(configured), MODES.index(floor))]
        capabilities[name] = {
            "agentConfiguredMode": authority["configuredMode"],
            "configuredMode": configured,
            "effectiveMode": effective,
            "safetyFloor": floor,
            "safetyFloorReason": None if effective == configured else "non_relaxable_approval_floor",
        }
    return {
        "version": 2,
        "application": application,
        "agentId": settings["agentId"],
        "agentPolicyRevision": settings["revision"],
        "agentPolicyRevisionId": settings["policyRevisionId"],
        "agentPolicySchemaVersion": settings["schemaVersion"],
        "policyDigest": settings["policyDigest"],
        "capabilities": capabilities,
        "bounds": dict(settings["effectiveBounds"]),
        "disclosureClass": disclosure_class,
        "targetVerified": target_verified,
    }


def capability_unavailable(
    application: str, capability: Any, *, agent_id: str = DEFAULT_AGENT_ID,
    current_mode: str = "disabled", policy_revision: int | None = None,
    target_required: bool | None = None,
) -> MetadataStoreError:
    canonical = canonical_capability(capability)
    return MetadataStoreError(
        "capability_unavailable", "The requested AI capability is unavailable", status=403,
        details={
            "application": application,
            "agentId": agent_id,
            "requiredCapability": canonical,
            "capability": canonical,
            "currentMode": current_mode,
            "policyRevision": policy_revision,
            "targetRequired": canonical in TARGET_CAPABILITIES if target_required is None else target_required,
            "reason": "unsupported_product_capability" if canonical not in _supported(application) else "disabled_capability",
            "supportedCapabilities": list(_supported(application)),
            "settingsAction": dict(LOCAL_SETTINGS_ACTION),
        },
    )


def _supported(application: str) -> tuple[str, ...]:
    try:
        return APPLICATION_CAPABILITIES[application]
    except KeyError as exc:
        raise MetadataStoreError("invalid_metadata", "AI policy application is invalid", status=400) from exc
