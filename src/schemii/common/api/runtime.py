"""Fail-closed deployment settings for the process-owned ASGI application."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from typing import Mapping


class DeploymentMode(str, Enum):
    LOCAL_DEVELOPMENT = "local-development"
    AUTHENTICATED = "authenticated"


class TargetEgressMode(str, Enum):
    INTERNAL_ONLY = "internal-only"
    EXTERNAL = "external"


@dataclass(frozen=True)
class RuntimeConfig:
    deployment_mode: DeploymentMode
    target_egress_mode: TargetEgressMode
    developer_inspection: bool

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RuntimeConfig":
        values = os.environ if env is None else env
        try:
            deployment_mode = DeploymentMode(
                values.get("SCHEMII_DEPLOYMENT_MODE", "").strip()
            )
        except ValueError as error:
            raise ValueError(
                "SCHEMII_DEPLOYMENT_MODE must be explicitly set to local-development"
            ) from error
        try:
            target_egress_mode = TargetEgressMode(
                values.get("SCHEMII_TARGET_EGRESS_MODE", "").strip()
            )
        except ValueError as error:
            raise ValueError(
                "SCHEMII_TARGET_EGRESS_MODE must be explicitly set to internal-only"
            ) from error
        inspection_value = values.get("SCHEMII_DEVELOPER_INSPECTION", "0")
        if inspection_value not in {"0", "1"}:
            raise ValueError("SCHEMII_DEVELOPER_INSPECTION must be 0 or 1")
        if deployment_mode is DeploymentMode.AUTHENTICATED:
            raise ValueError(
                "authenticated deployment mode requires an identity adapter that is not yet configured"
            )
        if target_egress_mode is TargetEgressMode.EXTERNAL:
            raise ValueError(
                "external target egress requires authenticated deployment mode"
            )
        return cls(
            deployment_mode=deployment_mode,
            target_egress_mode=target_egress_mode,
            developer_inspection=inspection_value == "1",
        )
