"""Schemii policy over the shared bounded PostgreSQL Console gateway."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading
from typing import Callable

from schemii.common.connections.models import ResolvedPostgresConnection
from schemii.common.connections.service import ConnectionService
from schemii.common.connections.store import ConnectionNotFoundError
from schemii.common.postgres import (
    PostgresConsoleCancelledError,
    PostgresConsoleLimitError,
    PostgresConsoleQueryError,
    PostgresGateway,
    PostgresGatewayError,
)
from schemii.common.postgres.console.execution import (
    ConsoleStatementValidationError,
    MAX_CONSOLE_STATEMENTS,
    validate_read_only_statements,
)
from schemii.common.postgres.console.models import (
    ConsoleExecution,
    ConsoleExecutionCreate,
    ConsoleResultPage,
    ConsoleSettings,
)
from schemii.schemii.workspaces.store import (
    WorkspaceNotFoundError,
    WorkspaceRepository,
)

from .repository import (
    ConsoleConflictError,
    ConsoleNotFoundError,
    ConsoleRepository,
    ConsoleRepositoryError,
    ConsoleResultGoneError,
    ConsoleStorageUnavailableError,
    ConsoleTarget,
)


DEFAULT_CONSOLE_SETTINGS = ConsoleSettings(
    revision=1,
    write_intent=False,
    default_mode="managed_read",
    statement_limit=MAX_CONSOLE_STATEMENTS,
    row_page_size=100,
)


class ConsoleServiceError(RuntimeError):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
        retryable: bool = False,
    ) -> None:
        self.status = status
        self.code = code
        self.details = details or {}
        self.retryable = retryable
        super().__init__(message)


class ConsoleService:
    def __init__(
        self,
        *,
        repository: ConsoleRepository,
        connections: ConnectionService,
        postgres: PostgresGateway,
        workspaces: WorkspaceRepository,
        result_ttl: timedelta = timedelta(minutes=15),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if result_ttl <= timedelta(0):
            raise ValueError("Console result TTL must be positive")
        self._repository = repository
        self._connections = connections
        self._postgres = postgres
        self._workspaces = workspaces
        self._result_ttl = result_ttl
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._active_targets: dict[str, ResolvedPostgresConnection] = {}
        self._active_targets_lock = threading.RLock()
        self._repository.recover_interrupted(self._clock())

    def settings(self, owner_id: str) -> ConsoleSettings:
        del owner_id
        return DEFAULT_CONSOLE_SETTINGS.model_copy(deep=True)

    def reserve(
        self,
        owner_id: str,
        workspace_id: str,
        request: ConsoleExecutionCreate,
    ) -> ConsoleExecution:
        if request.mode != "managed_read":
            raise ConsoleServiceError(
                422,
                "console_mode_not_available",
                "Only managed read mode is available",
            )
        if request.expected_settings_revision != DEFAULT_CONSOLE_SETTINGS.revision:
            raise ConsoleServiceError(
                409,
                "console_settings_changed",
                "Console settings changed after the editor loaded",
                details={"currentRevision": DEFAULT_CONSOLE_SETTINGS.revision},
            )
        try:
            statements = validate_read_only_statements(request.statements)
        except ConsoleStatementValidationError as error:
            raise ConsoleServiceError(
                422,
                error.code,
                str(error),
                details={"statementIndex": error.statement_index},
            ) from error
        workspace = self._workspace(owner_id, workspace_id)
        if workspace.revision != request.expected_workspace_revision:
            raise ConsoleServiceError(
                409,
                "console_workspace_changed",
                "Workspace changed after the Console loaded",
                details={"currentRevision": workspace.revision},
            )
        if (
            workspace.connection_id is None
            or workspace.database is None
            or workspace.namespace is None
        ):
            raise ConsoleServiceError(
                409,
                "console_target_required",
                "Attach this workspace to PostgreSQL before running SQL",
            )
        try:
            profile = self._connections.get(owner_id, workspace.connection_id)
        except ConnectionNotFoundError as error:
            raise ConsoleServiceError(
                409,
                "console_connection_missing",
                "The workspace PostgreSQL connection no longer exists",
            ) from error
        if profile.database != workspace.database:
            raise ConsoleServiceError(
                409,
                "console_database_changed",
                "The workspace database no longer matches its connection",
            )
        try:
            record = self._repository.reserve(
                owner_id,
                workspace_id,
                request.console_id,
                workspace.revision,
                ConsoleTarget(
                    connection_id=profile.id,
                    connection_revision=profile.revision,
                    database=workspace.database,
                    namespace=workspace.namespace,
                ),
                statements,
                DEFAULT_CONSOLE_SETTINGS.row_page_size,
                self._clock(),
            )
        except ConsoleRepositoryError as error:
            raise self._repository_error(error) from error
        return record.execution

    def run(self, owner_id: str, execution_id: str) -> None:
        """Execute one previously returned receipt; failures become durable state."""

        try:
            record = self._repository.claim(owner_id, execution_id, self._clock())
            if record is None:
                return
            with self._connections.use(owner_id, record.target.connection_id) as target:
                if (
                    target.revision != record.target.connection_revision
                    or target.database != record.target.database
                ):
                    self._repository.fail(
                        owner_id,
                        execution_id,
                        code="console_target_changed",
                        message="PostgreSQL connection changed before execution began",
                        statement_index=None,
                        cancelled=False,
                        now=self._clock(),
                    )
                    return
                with self._active_targets_lock:
                    self._active_targets[execution_id] = target
                try:

                    def publish(backend_pid: int) -> bool:
                        return self._repository.publish_backend(
                            owner_id,
                            execution_id,
                            backend_pid,
                            self._clock(),
                        )

                    results = self._postgres.execute_console(
                        target,
                        record.statements,
                        on_started=publish,
                    )
                finally:
                    with self._active_targets_lock:
                        self._active_targets.pop(execution_id, None)
            self._repository.succeed(
                owner_id,
                execution_id,
                results,
                self._clock() + self._result_ttl,
                self._clock(),
            )
        except Exception as error:
            self._record_run_failure(owner_id, execution_id, error)

    def get(self, owner_id: str, workspace_id: str, execution_id: str) -> ConsoleExecution:
        try:
            record = self._repository.get(owner_id, execution_id)
        except ConsoleRepositoryError as error:
            raise self._repository_error(error) from error
        if record.execution.workspace_id != workspace_id:
            raise ConsoleServiceError(404, "console_execution_not_found", "Console execution was not found")
        return record.execution

    def cancel(self, owner_id: str, workspace_id: str, execution_id: str) -> ConsoleExecution:
        try:
            existing = self._repository.get(owner_id, execution_id)
            if existing.execution.workspace_id != workspace_id:
                raise ConsoleNotFoundError()
            record = self._repository.request_cancel(
                owner_id, execution_id, self._clock()
            )
        except ConsoleRepositoryError as error:
            raise self._repository_error(error) from error
        if record.execution.status == "running" and record.backend_pid is not None:
            try:
                with self._active_targets_lock:
                    target = self._active_targets.get(execution_id)
                if target is not None:
                    self._postgres.cancel_console(target, record.backend_pid)
            except PostgresGatewayError:
                pass
        return self._repository.get(owner_id, execution_id).execution

    def page(self, owner_id, workspace_id, execution_id, result_id, cursor) -> ConsoleResultPage:
        try:
            return self._repository.page(
                owner_id, workspace_id, execution_id, result_id, cursor, self._clock()
            )
        except ConsoleRepositoryError as error:
            raise self._repository_error(error) from error

    def close_result(self, owner_id, workspace_id, execution_id, result_id) -> None:
        try:
            self._repository.close_result(
                owner_id, workspace_id, execution_id, result_id, self._clock()
            )
        except ConsoleRepositoryError as error:
            raise self._repository_error(error) from error

    def _workspace(self, owner_id: str, workspace_id: str):
        try:
            return self._workspaces.get(owner_id, workspace_id)
        except WorkspaceNotFoundError as error:
            raise ConsoleServiceError(404, "workspace_not_found", str(error)) from error

    def _record_run_failure(self, owner_id: str, execution_id: str, error: Exception) -> None:
        try:
            current = self._repository.get(owner_id, execution_id)
            cancelled = current.cancel_requested or isinstance(
                error, PostgresConsoleCancelledError
            )
            if isinstance(error, (PostgresConsoleQueryError, PostgresConsoleLimitError)):
                statement_index = error.statement_index
                code = error.code
                message = str(error)
            elif isinstance(error, PostgresGatewayError):
                statement_index = None
                code = error.code
                message = str(error)
            else:
                statement_index = None
                code = "console_execution_failed"
                message = "Console execution failed before producing a result"
            self._repository.fail(
                owner_id,
                execution_id,
                code="postgres_console_cancelled" if cancelled else code,
                message="Console execution was cancelled" if cancelled else message,
                statement_index=statement_index,
                cancelled=cancelled,
                now=self._clock(),
            )
        except ConsoleRepositoryError:
            return

    @staticmethod
    def _repository_error(error: ConsoleRepositoryError) -> ConsoleServiceError:
        if isinstance(error, ConsoleNotFoundError):
            return ConsoleServiceError(404, "console_execution_not_found", "Console execution was not found")
        if isinstance(error, ConsoleResultGoneError):
            return ConsoleServiceError(410, "console_result_gone", "Console result was closed, expired, or already advanced")
        if isinstance(error, ConsoleConflictError):
            return ConsoleServiceError(409, error.code, str(error))
        if isinstance(error, ConsoleStorageUnavailableError):
            return ConsoleServiceError(503, "console_storage_unavailable", str(error), retryable=True)
        return ConsoleServiceError(500, "console_repository_error", "Console metadata operation failed")
