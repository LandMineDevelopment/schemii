"""Read-only installed-graph documents; never share an application or its services."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from types import MappingProxyType, SimpleNamespace
from typing import Any, Mapping

from fastapi import FastAPI
import pytest

from schemii.common import developer_inspection, system_inspection
from schemii.common.admin_config import AdminConfig
from schemii.common.api.runtime import RuntimeConfig
from schemii.common.postgres.gateway import PostgresGateway
from schemii.main import create_app, create_services


BASELINE_ENV = MappingProxyType(
    {
        "SCHEMII_DEPLOYMENT_MODE": "local-development",
        "SCHEMII_TARGET_EGRESS_MODE": "internal-only",
        "SCHEMII_ALLOWED_TARGET_HOSTS": "localhost,127.0.0.1,postgres,demo-postgres",
        "SCHEMII_STORAGE_MODE": "memory",
        "SCHEMII_AUTH_ENABLED": "0",
        "SCHEMII_DEVELOPER_INSPECTION": "0",
    }
)


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class InspectionBaseline:
    snapshot: Mapping[str, Any]
    route_order: tuple[str, ...]

    @property
    def documents(self) -> Mapping[str, Any]:
        return self.snapshot["documents"]


@pytest.fixture(scope="session")
def inspection_baseline() -> InspectionBaseline:
    # Construction also reads auth/Pi/metadata environment settings. Isolate all
    # Schemii settings during this build, then restore the caller's environment.
    with pytest.MonkeyPatch.context() as patch:
        for name in tuple(os.environ):
            if name.startswith("SCHEMII_"):
                patch.delenv(name)
        for name, value in BASELINE_ENV.items():
            patch.setenv(name, value)
        runtime = RuntimeConfig.from_env(BASELINE_ENV)
        application = create_app(create_services(runtime, AdminConfig()))
        route_order = tuple(
            f"{method.lower()}:{route.path}"
            for route in system_inspection.public_route_contexts(application)
            if system_inspection.is_first_party(route.endpoint)
            for method in sorted(route.methods)
        )
        snapshot = developer_inspection.build_developer_inspection_snapshot(application)
    # Neither the app, mutable services, clients nor lifespans escape this fixture.
    return InspectionBaseline(_freeze(snapshot), route_order)


@pytest.fixture
def inspection_http_documents(
    inspection_baseline: InspectionBaseline, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep opt-in HTTP wiring checks cheap; the canonical HTTP test builds for real."""
    # Pydantic's response serializer requires ordinary JSON containers. Give this
    # consumer a private copy; the shared baseline remains recursively read-only.
    snapshot = json.loads(json.dumps(inspection_baseline.snapshot, default=dict))
    monkeypatch.setattr(
        developer_inspection,
        "build_developer_inspection_snapshot",
        lambda application: snapshot,
    )


class InspectionGateway(PostgresGateway):
    def test_connection(self, connection):
        raise AssertionError("Snapshot construction must not execute a gateway")


def _minimal_endpoint() -> dict[str, bool]:
    return {"ok": True}


# First-party source inspection intentionally includes these tiny test subjects.
InspectionGateway.__module__ = "schemii.test_inspection"
InspectionGateway.test_connection.__module__ = "schemii.test_inspection"
_minimal_endpoint.__module__ = "schemii.test_inspection"


def small_inspection_app(gateway: PostgresGateway | None = None) -> FastAPI:
    application = FastAPI()
    application.state.services = SimpleNamespace(
        postgres=gateway if gateway is not None else InspectionGateway()
    )
    application.get("/example", response_model=None)(_minimal_endpoint)
    return application
