"""Assemble the Schemii API application."""

from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import AsyncIterator

from fastapi import APIRouter, FastAPI

from schemii.common.api import (
    install_api_error_handlers,
    install_api_middleware,
)
from schemii.common.api.models import ApiErrorResponse
from schemii.common.api.routes import router as runtime_router
from schemii.common.api.runtime import RuntimeConfig, TargetEgressMode
from schemii.common.ai.routes import router as ai_provider_router
from schemii.common.connections.routes import router as connections_router
from schemii.common.connections.policy import (
    CompositeConnectionTargetPolicy,
    InternalOnlyConnectionTargetPolicy,
)
from schemii.common.connections.service import ConnectionService
from schemii.common.developer_inspection import install_developer_inspection
from schemii.common.metadata import MetadataRepositories, create_metadata_repositories
from schemii.common.metadata.migrations import (
    MIGRATION_PACKAGE as COMMON_METADATA_MIGRATION_PACKAGE,
)
from schemii.common.postgres import PostgresGateway, PsycopgPostgresGateway
from schemii.schemer.routes import router as schemer_router
from schemii.schemii.designs.postgres_store import PostgresDesignRepository
from schemii.schemii.designs.store import DesignRepository, InMemoryDesignRepository
from schemii.schemii.frontend import install_schemii_frontend
from schemii.schemii.console.repository import (
    InMemoryConsoleRepository,
    PostgresConsoleRepository,
)
from schemii.schemii.console.service import ConsoleService
from schemii.schemii.migrations.repository import (
    InMemoryMigrationRepository,
    PostgresMigrationRepository,
)
from schemii.schemii.migrations.service import MigrationService
from schemii.schemii.migrations.worker import MigrationExecutionWorker
from schemii.schemii.metadata import (
    MIGRATION_PACKAGE as SCHEMII_METADATA_MIGRATION_PACKAGE,
)
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
    designs: DesignRepository
    migrations: MigrationService | None = None
    console: ConsoleService | None = None


def create_services(
    runtime_config: RuntimeConfig | None = None,
) -> ApplicationServices:
    selected_runtime = runtime_config or RuntimeConfig.from_env()
    metadata = create_metadata_repositories(
        migration_packages=(
            COMMON_METADATA_MIGRATION_PACKAGE,
            SCHEMII_METADATA_MIGRATION_PACKAGE,
        )
    )
    designs: DesignRepository = (
        PostgresDesignRepository(metadata.connection_factory)
        if metadata.connection_factory is not None
        else InMemoryDesignRepository()
    )
    workspaces: WorkspaceRepository = (
        PostgresWorkspaceRepository(metadata.connection_factory)
        if metadata.connection_factory is not None
        else InMemoryWorkspaceRepository(designs=designs)
    )
    target_policy = metadata.target_policy
    if selected_runtime.target_egress_mode is TargetEgressMode.INTERNAL_ONLY:
        target_policy = CompositeConnectionTargetPolicy(
            (
                target_policy,
                InternalOnlyConnectionTargetPolicy.from_hosts(
                    selected_runtime.allowed_target_hosts
                ),
            )
        )
    connections = ConnectionService(
        metadata.connections,
        (workspaces,),
        target_policy=target_policy,
    )
    migration_repository = (
        PostgresMigrationRepository(metadata.connection_factory)
        if metadata.connection_factory is not None
        else InMemoryMigrationRepository(designs)
    )
    mutation_guard = getattr(designs, "set_mutation_guard", None)
    if callable(mutation_guard):
        mutation_guard(migration_repository.has_active_execution)
    postgres = PsycopgPostgresGateway()
    migrations = MigrationService(
        repository=migration_repository,
        connections=connections,
        postgres=postgres,
        workspaces=workspaces,
        designs=designs,
    )
    console_repository = (
        PostgresConsoleRepository(metadata.connection_factory)
        if metadata.connection_factory is not None
        else InMemoryConsoleRepository()
    )
    console = ConsoleService(
        repository=console_repository,
        connections=connections,
        postgres=postgres,
        workspaces=workspaces,
    )
    return ApplicationServices(
        metadata=metadata,
        connections=connections,
        postgres=postgres,
        workspaces=workspaces,
        designs=designs,
        migrations=migrations,
        console=console,
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
    runtime_config: RuntimeConfig | None = None,
    developer_inspection: bool = False,
) -> FastAPI:
    """Create the API and connect each product router."""
    active_services = services or create_services(runtime_config)
    if active_services.migrations is None:
        if active_services.metadata.durable:
            raise RuntimeError(
                "Durable application services require an explicit durable migration repository"
            )
        migration_repository = InMemoryMigrationRepository(active_services.designs)
        mutation_guard = getattr(active_services.designs, "set_mutation_guard", None)
        if callable(mutation_guard):
            mutation_guard(migration_repository.has_active_execution)
        active_services = replace(
            active_services,
            migrations=MigrationService(
                repository=migration_repository,
                connections=active_services.connections,
                postgres=active_services.postgres,
                workspaces=active_services.workspaces,
                designs=active_services.designs,
            ),
        )
    assert active_services.migrations is not None
    if active_services.console is None:
        active_services = replace(
            active_services,
            console=ConsoleService(
                repository=InMemoryConsoleRepository(),
                connections=active_services.connections,
                postgres=active_services.postgres,
                workspaces=active_services.workspaces,
            ),
        )
    migration_worker = MigrationExecutionWorker(
        active_services.migrations.execution_coordinator
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        active_services.migrations.set_execution_waker(migration_worker.notify)
        await migration_worker.start()
        try:
            yield
        finally:
            active_services.migrations.set_execution_waker(None)
            await migration_worker.stop()

    application = FastAPI(
        title="Schemii",
        version="0.1.0",
        description="Unified API for Schemii, Schemoo, and Schemer",
        lifespan=lifespan,
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
    application.state.services = active_services
    application.state.migration_worker = migration_worker
    install_api_middleware(application)
    install_api_error_handlers(application)

    for router in COMMON_ROUTERS:
        application.include_router(router)

    for router in PRODUCT_ROUTERS:
        application.include_router(router)

    if developer_inspection:
        install_developer_inspection(application)
    install_schemii_frontend(application)

    return application


runtime_config = RuntimeConfig.from_env()
app = create_app(
    runtime_config=runtime_config,
    developer_inspection=runtime_config.developer_inspection,
)
