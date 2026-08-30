from __future__ import annotations

from typing import Any

from .build_identity import build_identity
from .dashboard_store import DashboardStoreError
from .http_access import HttpAccessPolicy
from .metadata import MetadataStoreError
from .opencode_service import OpenCodeService, OpenCodeServiceError


def readiness_report(
    metadata_authority: Any,
    opencode: OpenCodeService | None,
    postgres: Any,
    maintenance: Any = None,
    dashboard_store: Any = None,
    access_policy: HttpAccessPolicy = HttpAccessPolicy(),
) -> tuple[int, dict[str, Any]]:
    components: dict[str, Any] = {}
    ready = True
    components["httpAccess"] = {
        "required": True,
        "status": "available",
        "mode": "public-origin" if access_policy.public_origins else "loopback-only",
        "behindLoopbackProxy": access_policy.behind_loopback_proxy,
        "publicOrigins": [
            f"https://{origin.hostname}{f':{origin.port}' if origin.port != 443 else ''}"
            for origin in access_policy.public_origins
        ],
    }
    try:
        metadata = metadata_authority.health()
        components["metadata"] = {"required": True, "status": "available", **metadata}
    except MetadataStoreError as error:
        ready = False
        components["metadata"] = {
            "required": True, "status": "unavailable", "error": error.to_dict()["error"],
        }

    if opencode is None or not getattr(opencode, "enabled", True):
        components["opencode"] = {"required": False, "status": "disabled"}
    else:
        try:
            health = getattr(opencode, "health", None)
            result = health() if health else opencode.status()
            components["opencode"] = {"required": False, "status": "available", **result}
        except OpenCodeServiceError as error:
            components["opencode"] = {
                "required": False, "status": "degraded", "error": error.payload["error"],
            }

    target_readiness = getattr(postgres, "target_readiness", None)
    execution_metrics = getattr(postgres, "execution_metrics", None)
    components["targets"] = target_readiness() if target_readiness else {
        "required": False, "status": "unknown", "configured": 0, "profiles": {},
    }
    components["postgresExecution"] = execution_metrics() if execution_metrics else {"status": "unknown"}
    structured_metrics = getattr(postgres, "structured_result_metrics", None)
    components["structuredResults"] = structured_metrics() if structured_metrics else {
        "status": "unknown", "processLocal": True,
    }
    if dashboard_store is not None:
        try:
            components["dashboardStore"] = {
                "required": True, "status": "available", **dashboard_store.health(),
            }
        except DashboardStoreError as error:
            ready = False
            components["dashboardStore"] = {
                "required": True, "status": "unavailable", "error": error.payload["error"],
            }
    if maintenance is not None:
        maintenance_health = maintenance.health()
        components["aiOperationMaintenance"] = maintenance_health
        if maintenance_health["status"] != "available":
            ready = False
    report = {"ready": ready, "build": build_identity(), "components": components}
    if ready:
        report["metadata"] = metadata
    return (200 if ready else 503), report
