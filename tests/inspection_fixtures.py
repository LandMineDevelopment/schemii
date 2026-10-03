"""Read-only installed-graph documents; never share an application or its services."""

from __future__ import annotations

from dataclasses import dataclass
import os
from types import MappingProxyType, SimpleNamespace
from typing import Any, Mapping

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from schemii.common import developer_inspection, system_inspection
from schemii.common.api import inspection as route_inspection
from schemii.common.postgres import inspection as database_inspection
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
class InspectionResponse:
    status_code: int
    cache_control: str | None
    payload: Mapping[str, Any] | None
    derivations: Mapping[str, int]


@dataclass(frozen=True)
class InspectionRun:
    construction_derivations: Mapping[str, int]
    responses: Mapping[str, tuple[InspectionResponse, ...]]

    @property
    def snapshot(self) -> Mapping[str, Any]:
        payload = self.responses["/_developer/inspection"][0].payload
        assert payload is not None
        return payload


@dataclass(frozen=True)
class InspectionBaseline:
    snapshot: Mapping[str, Any]
    route_order: tuple[str, ...]
    run: InspectionRun

    @property
    def documents(self) -> Mapping[str, Any]:
        return self.snapshot["documents"]


def _capture_installed_run(
    counters: dict[str, int], *, repeat_compatibility: bool
) -> tuple[InspectionRun, tuple[str, ...]]:
    # Build a new app and its owners each time. Only immutable observations leave
    # this helper; neither client lifetime nor mutable services are shared.
    application = create_app(
        create_services(RuntimeConfig.from_env(BASELINE_ENV), AdminConfig()),
        developer_inspection=True,
    )
    construction_derivations = _freeze(dict(counters))
    route_order = tuple(
        f"{method.lower()}:{route.path}"
        for route in system_inspection.public_route_contexts(application)
        if system_inspection.is_first_party(route.endpoint)
        for method in sorted(route.methods)
    )
    api = TestClient(application, base_url="http://localhost")
    responses = {}
    try:
        paths = [("/_developer/inspection", 1)]
        if repeat_compatibility:
            paths.extend((f"/_developer/{name}", 2) for name in counters)
            paths.append(("/openapi.json", 1))
        for path, attempts in paths:
            observed = []
            for attempt in range(attempts):
                response = api.get(path)
                observed.append(
                    InspectionResponse(
                        response.status_code,
                        response.headers.get("cache-control"),
                        _freeze(response.json()) if attempt == 0 else None,
                        _freeze(dict(counters)),
                    )
                )
            responses[path] = tuple(observed)
    finally:
        api.close()
    return InspectionRun(
        construction_derivations, MappingProxyType(responses)
    ), route_order


def _build_installed_run(initial_counts, *, repeat_compatibility):
    # Isolate configuration and instrumentation for each fresh application.
    with pytest.MonkeyPatch.context() as patch:
        for name in tuple(os.environ):
            if name.startswith("SCHEMII_"):
                patch.delenv(name)
        for name, value in BASELINE_ENV.items():
            patch.setenv(name, value)
        counters = dict(initial_counts)
        builders = (
            (route_inspection, "build_developer_route_document", "routes"),
            (database_inspection, "build_developer_database_document", "database"),
            (system_inspection, "build_developer_system_document", "system"),
        )
        for module, name, key in builders:
            original = getattr(module, name)

            def tracked(application, *, original=original, key=key):
                counters[key] += 1
                return original(application)

            patch.setattr(module, name, tracked)
        return _capture_installed_run(
            counters, repeat_compatibility=repeat_compatibility
        )


@pytest.fixture(scope="session")
def inspection_baseline() -> InspectionBaseline:
    # Share genuine observed HTTP documents, not a mutable app or a stub graph.
    first, route_order = _build_installed_run(
        {"routes": 0, "database": 0, "system": 0}, repeat_compatibility=True
    )
    return InspectionBaseline(first.snapshot, route_order, first)


@pytest.fixture(scope="session")
def inspection_rebuilt_baseline(inspection_baseline) -> InspectionRun:
    # Only the once-per-app/determinism case needs a second real build. Targeted
    # graph or canonical HTTP cases should not pay for this unused application.
    second, _ = _build_installed_run(
        inspection_baseline.run.construction_derivations, repeat_compatibility=False
    )
    return second


@pytest.fixture
def inspection_http_documents(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Exercise serializer/opt-in wiring with a small, freshly derived document.

    Exact installed payload serialization is separately observed in the two real
    runs above. These four checks need a valid private payload, not that graph.
    """
    snapshot = developer_inspection.build_developer_inspection_snapshot(
        small_inspection_app()
    )
    monkeypatch.setattr(
        developer_inspection,
        "build_developer_inspection_snapshot",
        lambda application: snapshot,
    )
    return snapshot


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
