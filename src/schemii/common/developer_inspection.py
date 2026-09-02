"""One immutable, versioned developer-inspection snapshot per application run."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import FastAPI

from schemii.common import system_inspection
from schemii.common.api import inspection as route_inspection
from schemii.common.postgres import inspection as database_inspection


DEVELOPER_INSPECTION_PATH = "/_developer/inspection"


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_developer_inspection_snapshot(application: FastAPI) -> dict[str, Any]:
    """Build all related documents against the same registered application graph."""

    documents = {
        "routes": route_inspection.build_developer_route_document(application),
        "database": database_inspection.build_developer_database_document(application),
        "system": system_inspection.build_developer_system_document(application),
        "openapi": application.openapi(),
    }
    document_digests = {
        name: _digest(document) for name, document in documents.items()
    }
    return {
        "schemaVersion": 1,
        "generation": "application-startup",
        "snapshotId": _digest(document_digests),
        "documentDigests": document_digests,
        "documents": documents,
    }


def install_developer_inspection(application: FastAPI) -> None:
    """Install one canonical snapshot and compatibility views of its documents."""

    snapshot = build_developer_inspection_snapshot(application)
    documents = snapshot["documents"]

    @application.get(DEVELOPER_INSPECTION_PATH, include_in_schema=False)
    def developer_inspection() -> dict[str, Any]:
        return snapshot

    @application.get(route_inspection.DEVELOPER_ROUTES_PATH, include_in_schema=False)
    def developer_routes() -> dict[str, Any]:
        return documents["routes"]

    @application.get(database_inspection.DEVELOPER_DATABASE_PATH, include_in_schema=False)
    def developer_database() -> dict[str, Any]:
        return documents["database"]

    @application.get(system_inspection.DEVELOPER_SYSTEM_PATH, include_in_schema=False)
    def developer_system() -> dict[str, Any]:
        return documents["system"]
