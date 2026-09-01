"""Assemble the Schemii API application."""

import os
from dataclasses import dataclass

from fastapi import APIRouter, FastAPI

from schemii.common.api import (
    install_api_error_handlers,
    install_api_middleware,
)
from schemii.common.api.models import ApiErrorResponse
from schemii.common.api.inspection import install_developer_route_inspection
from schemii.common.api.routes import router as runtime_router
from schemii.common.ai.routes import router as ai_provider_router
from schemii.common.connections.routes import router as connections_router
from schemii.common.connections.service import ConnectionService
from schemii.common.metadata import MetadataRepositories, create_metadata_repositories
from schemii.common.postgres import PostgresGateway, PsycopgPostgresGateway
from schemii.common.postgres.inspection import install_developer_database_inspection
from schemii.common.system_inspection import install_developer_system_inspection
from schemii.schemer.routes import router as schemer_router
from schemii.schemii.frontend import install_schemii_frontend
from schemii.schemii.routes import router as schemii_router
from schemii.schemii.workspaces.store import (
    InMemoryWorkspaceRepository,
    WorkspaceRepository,
)
from schemii.schemii.workspaces.postgres_store import PostgresWorkspaceRepository
from schemii.schemoo.routes import router as schemoo_router


@dataclass(frozen=True)
class ApplicationServices:
    metadata: MetadataRepositories
    connections: ConnectionService
    postgres: PostgresGateway
    workspaces: WorkspaceRepository


def create_services() -> ApplicationServices:
    metadata = create_metadata_repositories()
    workspaces: WorkspaceRepository = (
        PostgresWorkspaceRepository(metadata.connection_factory)
        if metadata.connection_factory is not None
        else InMemoryWorkspaceRepository()
    )
    connections = ConnectionService(metadata.connections, (workspaces,))
    return ApplicationServices(
        metadata=metadata,
        connections=connections,
        postgres=PsycopgPostgresGateway(),
        workspaces=workspaces,
    )


COMMON_ROUTERS: tuple[APIRouter, ...] = (
    runtime_router,
    connections_router,
    ai_provider_router,
)


PRODUCT_ROUTERS: tuple[APIRouter, ...] = (
    schemii_router,
    schemoo_router,
    schemer_router,
)


def create_app(
    services: ApplicationServices | None = None,
    *,
    developer_inspection: bool = False,
) -> FastAPI:
    """Create the API and connect each product router."""
    application = FastAPI(
        title="Schemii",
        version="0.1.0",
        description="Unified API for Schemii, Schemoo, and Schemer",
        responses={
            400: {"model": ApiErrorResponse, "description": "Invalid request"},
            404: {"model": ApiErrorResponse, "description": "Resource not found"},
            409: {"model": ApiErrorResponse, "description": "State conflict"},
            422: {"model": ApiErrorResponse, "description": "Contract validation failed"},
            500: {"model": ApiErrorResponse, "description": "Internal server error"},
            502: {"model": ApiErrorResponse, "description": "PostgreSQL operation failed"},
            503: {"model": ApiErrorResponse, "description": "Required service unavailable"},
        },
    )
    application.state.services = services or create_services()
    install_api_middleware(application)
    install_api_error_handlers(application)

    for router in COMMON_ROUTERS:
        application.include_router(router)

    for router in PRODUCT_ROUTERS:
        application.include_router(router)

    if developer_inspection:
        install_developer_route_inspection(application)
        install_developer_database_inspection(application)
        install_developer_system_inspection(application)
    install_schemii_frontend(application)

    return application


app = create_app(developer_inspection=os.environ.get("SCHEMII_DEVELOPER_INSPECTION") == "1")
