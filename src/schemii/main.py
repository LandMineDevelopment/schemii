"""Assemble the Schemii API application."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import AsyncIterator

from fastapi import APIRouter, FastAPI

from schemii.common.api import (
    install_api_error_handlers,
    install_api_middleware,
)
from schemii.common.api.models import ApiErrorResponse
from schemii.common.api.routes import router as runtime_router
from schemii.common.api.runtime import RuntimeConfig, TargetEgressMode
from schemii.common.admin_config import AdminConfig
from schemii.common.ai.credential_lifecycle import CredentialExpiryWorker, router as activity_router
from schemii.common.ai.model_catalog import ModelCatalogWorker, ZenModelCatalog
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
from schemii.common.postgres import (
    PostgresCatalogLimits,
    PostgresGateway,
    PsycopgPostgresGateway,
)
from schemii.schemer.routes import router as schemer_router
from schemii.schemii.designs.postgres_store import PostgresDesignRepository
from schemii.schemii.designs.store import DesignRepository, InMemoryDesignRepository
from schemii.schemii.frontend import install_schemii_frontend
from schemii.common.frontend import install_common_frontend
from schemii.schemii.ai.repository import AiRepository, InMemoryAiRepository, PostgresAiRepository
from schemii.schemii.ai.service import AiService
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
from schemii.schemoo.frontend import install_schemoo_frontend
from schemii.schemoo.catalog import ModelCatalogs
from schemii.schemoo.store import ModelRepository, InMemoryModelRepository, PostgresModelRepository
from schemii.schemoo.metadata.migrations import MIGRATION_PACKAGE as SCHEMOO_METADATA_MIGRATION_PACKAGE
from schemii.common.query_executions.routes import router as query_executions_router


@dataclass(frozen=True)
class ApplicationServices:
    metadata: MetadataRepositories
    connections: ConnectionService
    postgres: PostgresGateway
    workspaces: WorkspaceRepository
    designs: DesignRepository
    migrations: MigrationService | None = None
    console: ConsoleService | None = None
    admin_config: AdminConfig = AdminConfig()
    ai_repository: AiRepository | None = None
    models: ModelRepository | None = None
    model_catalogs: ModelCatalogs | None = None


def create_services(
    runtime_config: RuntimeConfig | None = None,
    admin_config: AdminConfig | None = None,
) -> ApplicationServices:
    selected_runtime = runtime_config or RuntimeConfig.from_env()
    selected_admin = admin_config or AdminConfig.from_env()
    metadata = create_metadata_repositories(
        migration_packages=(
            COMMON_METADATA_MIGRATION_PACKAGE,
            SCHEMII_METADATA_MIGRATION_PACKAGE,
            SCHEMOO_METADATA_MIGRATION_PACKAGE,
        ),
        maximum_connections_per_owner=(
            selected_admin.resources.maximum_connections_per_user
        ),
        limit_event_retention_days=selected_admin.limit_events.retention_days,
        maximum_limit_events=selected_admin.limit_events.maximum_events,
        credential_inactivity_days=selected_admin.ai.credential_inactivity_days,
        credential_expiration_enabled=selected_admin.ai.credential_expiration_enabled,
    )
    history_limit = selected_admin.resources.design_history_actions_per_workspace
    designs: DesignRepository = (
        PostgresDesignRepository(
            metadata.connection_factory,
            history_action_limit=history_limit,
        )
        if metadata.connection_factory is not None
        else InMemoryDesignRepository(history_action_limit=history_limit)
    )
    workspaces: WorkspaceRepository = (
        PostgresWorkspaceRepository(
            metadata.connection_factory,
            max_workspaces_per_owner=(
                selected_admin.resources.maximum_workspaces_per_user
            ),
        )
        if metadata.connection_factory is not None
        else InMemoryWorkspaceRepository(
            designs=designs,
            max_workspaces_per_owner=(
                selected_admin.resources.maximum_workspaces_per_user
            ),
        )
    )
    models = (PostgresModelRepository(metadata.connection_factory,
              maximum_models_per_owner=selected_admin.resources.maximum_models_per_user,
              maximum_document_bytes=selected_admin.resources.maximum_model_document_bytes)
              if metadata.connection_factory is not None else InMemoryModelRepository(
              maximum_models_per_owner=selected_admin.resources.maximum_models_per_user,
              maximum_document_bytes=selected_admin.resources.maximum_model_document_bytes))
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
        (workspaces, models),
        target_policy=target_policy,
    )
    migration_repository = (
        PostgresMigrationRepository(
            metadata.connection_factory,
            history_action_limit=history_limit,
        )
        if metadata.connection_factory is not None
        else InMemoryMigrationRepository(designs)
    )
    mutation_guard = getattr(designs, "set_mutation_guard", None)
    if callable(mutation_guard):
        mutation_guard(migration_repository.has_active_execution)
    postgres = PsycopgPostgresGateway(
        limits=PostgresCatalogLimits(
            max_namespaces=selected_admin.postgres_catalog.maximum_namespaces,
            max_tables=selected_admin.postgres_catalog.maximum_tables,
            max_columns=selected_admin.postgres_catalog.maximum_columns,
            max_constraints=selected_admin.postgres_catalog.maximum_constraints,
            max_indexes=selected_admin.postgres_catalog.maximum_indexes,
            max_triggers=selected_admin.postgres_catalog.maximum_triggers,
            max_functions=selected_admin.postgres_catalog.maximum_functions,
            max_views=selected_admin.postgres_catalog.maximum_views,
            max_types=selected_admin.postgres_catalog.maximum_types,
            max_total_objects=(
                selected_admin.postgres_catalog.maximum_total_objects
            ),
            max_definition_bytes=(
                selected_admin.postgres_catalog.maximum_definition_bytes
            ),
            max_total_text_bytes=(
                selected_admin.postgres_catalog.maximum_total_text_bytes
            ),
        ),
        maximum_connections=selected_admin.postgres_connections.maximum_total,
        maximum_connections_per_identity=(
            selected_admin.postgres_connections.maximum_per_identity
        ),
        connection_acquire_timeout=(
            selected_admin.postgres_connections.acquire_timeout_seconds
        ),
        maximum_console_statements=(
            selected_admin.console.maximum_statements_per_run
        ),
        console_page_memory_bytes=selected_admin.console_results.page_memory_bytes,
        console_maximum_cell_bytes=(
            selected_admin.console_results.maximum_cell_bytes
        ),
        catalog_statement_timeout_seconds=(
            selected_admin.postgres_timeouts.catalog_statement_seconds
        ),
        migration_statement_timeout_seconds=(
            selected_admin.postgres_timeouts.migration_statement_seconds
        ),
        lock_timeout_seconds=selected_admin.postgres_timeouts.lock_wait_seconds,
        console_idle_transaction_seconds=(
            selected_admin.console.transaction_idle_seconds
        ),
    )
    migrations = MigrationService(
        repository=migration_repository,
        connections=connections,
        postgres=postgres,
        workspaces=workspaces,
        designs=designs,
        plan_ttl=timedelta(seconds=selected_admin.migrations.review_ttl_seconds),
        execution_lease_ttl=timedelta(
            seconds=selected_admin.migrations.execution_lease_seconds
        ),
        limit_events=metadata.limit_events,
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
        result_ttl=timedelta(
            seconds=selected_admin.console_results.result_ttl_seconds
        ),
        row_page_size=selected_admin.console_results.page_rows,
        page_memory_bytes=selected_admin.console_results.page_memory_bytes,
        maximum_live_read_sessions=(
            selected_admin.console_results.maximum_live_read_sessions
        ),
        maximum_live_read_sessions_per_identity=min(
            selected_admin.console_results.maximum_live_read_sessions,
            max(1, selected_admin.postgres_connections.maximum_per_identity - 1),
        ),
        query_history_limit=selected_admin.console_history.limit,
        statement_limit=selected_admin.console.maximum_statements_per_run,
        transaction_idle_ttl=timedelta(
            seconds=selected_admin.console.transaction_idle_seconds
        ),
        transaction_maximum_ttl=timedelta(
            seconds=selected_admin.console.transaction_maximum_seconds
        ),
        maximum_saved_queries_per_workspace=(
            selected_admin.console.maximum_saved_queries_per_workspace
        ),
        limit_events=metadata.limit_events,
    )
    ai_repository: AiRepository = (
        PostgresAiRepository(metadata.connection_factory, selected_admin.ai)
        if metadata.connection_factory is not None
        else InMemoryAiRepository(selected_admin.ai)
    )
    return ApplicationServices(
        metadata=metadata,
        connections=connections,
        postgres=postgres,
        workspaces=workspaces,
        designs=designs,
        migrations=migrations,
        console=console,
        admin_config=selected_admin,
        ai_repository=ai_repository,
        models=models,
        model_catalogs=ModelCatalogs(maximum_entries=selected_admin.schemoo.maximum_cached_catalogs,
                                    refresh_seconds=selected_admin.schemoo.catalog_refresh_seconds),
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
                plan_ttl=timedelta(
                    seconds=active_services.admin_config.migrations.review_ttl_seconds
                ),
                execution_lease_ttl=timedelta(
                    seconds=active_services.admin_config.migrations.execution_lease_seconds
                ),
                limit_events=active_services.metadata.limit_events,
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
                result_ttl=timedelta(
                    seconds=active_services.admin_config.console_results.result_ttl_seconds
                ),
                row_page_size=active_services.admin_config.console_results.page_rows,
                page_memory_bytes=(
                    active_services.admin_config.console_results.page_memory_bytes
                ),
                maximum_live_read_sessions=(
                    active_services.admin_config.console_results.maximum_live_read_sessions
                ),
                maximum_live_read_sessions_per_identity=min(
                    active_services.admin_config.console_results.maximum_live_read_sessions,
                    max(
                        1,
                        active_services.admin_config.postgres_connections.maximum_per_identity
                        - 1,
                    ),
                ),
                query_history_limit=active_services.admin_config.console_history.limit,
                statement_limit=(
                    active_services.admin_config.console.maximum_statements_per_run
                ),
                transaction_idle_ttl=timedelta(
                    seconds=active_services.admin_config.console.transaction_idle_seconds
                ),
                transaction_maximum_ttl=timedelta(
                    seconds=active_services.admin_config.console.transaction_maximum_seconds
                ),
                maximum_saved_queries_per_workspace=(
                    active_services.admin_config.console.maximum_saved_queries_per_workspace
                ),
                limit_events=active_services.metadata.limit_events,
            ),
        )
    migration_worker = MigrationExecutionWorker(
        active_services.migrations.execution_coordinator,
        poll_interval_seconds=active_services.admin_config.migrations.worker_poll_seconds,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        await asyncio.to_thread(application.state.ai_service.recover_interrupted)
        active_services.migrations.set_execution_waker(migration_worker.notify)
        await migration_worker.start()
        credential_worker = CredentialExpiryWorker(
            active_services.metadata.ai_credentials,
            enabled=active_services.admin_config.ai.credential_expiration_enabled,
        )
        await credential_worker.start()
        catalog_worker = (
            ModelCatalogWorker(application.state.ai_model_catalog)
            if application.state.ai_model_catalog is not None else None
        )
        if catalog_worker is not None:
            await catalog_worker.start()
        try:
            yield
        finally:
            if catalog_worker is not None:
                await catalog_worker.stop()
            await credential_worker.stop()
            assert active_services.console is not None
            active_services.console.close()
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
    from schemii.common.ai.prototype import PiClient, router as pi_router

    application.state.services = active_services
    application.state.pi_client = PiClient.from_env()
    application.state.ai_model_catalog = (
        ZenModelCatalog(
            refresh_seconds=active_services.admin_config.ai.catalog_refresh_seconds,
            max_stale_seconds=active_services.admin_config.ai.catalog_max_stale_seconds,
        )
        if active_services.admin_config.ai.enabled and application.state.pi_client is not None
        else None
    )
    application.include_router(pi_router)
    application.include_router(activity_router)
    application.include_router(query_executions_router, prefix="/api/v1/common")
    from schemii.common.ai.pi import PiRuntime
    ai_runtime = (
        PiRuntime(application.state.pi_client, active_services.metadata.ai_credentials,
                  application.state.ai_model_catalog, active_services.admin_config.ai)
        if application.state.pi_client is not None else None
    )
    application.state.ai_service = AiService(
        active_services.ai_repository
        or InMemoryAiRepository(active_services.admin_config.ai),
        ai_runtime,
        active_services,
    )
    application.state.migration_worker = migration_worker
    install_api_middleware(application)
    install_api_error_handlers(application)

    for router in COMMON_ROUTERS:
        application.include_router(router)

    for router in PRODUCT_ROUTERS:
        application.include_router(router)

    if developer_inspection:
        install_developer_inspection(application)
    install_common_frontend(application)
    install_schemii_frontend(application)
    install_schemoo_frontend(application)

    return application


runtime_config = RuntimeConfig.from_env()
app = create_app(
    runtime_config=runtime_config,
    developer_inspection=runtime_config.developer_inspection,
)
