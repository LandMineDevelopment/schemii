"""Schemii policy over the shared bounded PostgreSQL Console gateway."""

from __future__ import annotations

from dataclasses import dataclass
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import csv
import io
import logging
import threading
import secrets
from typing import Callable, Iterator

from schemii.common.connections.models import ResolvedPostgresConnection
from schemii.common.connections.service import ConnectionService
from schemii.common.connections.store import ConnectionNotFoundError
from schemii.common.query_executions.errors import ConsoleServiceError
from schemii.common.metadata.limit_events import (
    LimitEventNotice,
    LimitEventRecorder,
    new_limit_event,
)
from schemii.common.postgres import (
    PostgresConsoleCancelledError,
    PostgresConsoleLimitError,
    PostgresConsoleQueryError,
    PostgresConsoleReadSession,
    PostgresConsoleTransaction,
    PostgresCommitUncertainError,
    PostgresConnectionCapacityError,
    PostgresGateway,
    PostgresGatewayError,
)
from schemii.common.postgres.console.execution import (
    ConsoleStatementValidationError,
    MAX_CONSOLE_STATEMENTS,
    validate_read_only_statements,
    validate_transaction_statements,
)
from schemii.common.postgres.console.models import (
    ConsoleExecution,
    ConsoleExecutionCreate,
    ManagedReadCreate,
    ConsoleResultPage,
    ConsoleSettings,
    ConsoleTransaction,
    ConsoleTransactionCommand,
    ConsoleTransactionCreate,
    ConsoleTransactionExecutionCreate,
)
from schemii.common.postgres.console.execution import ConsoleQueryResult
from schemii.schemii.workspaces.store import (
    WorkspaceNotFoundError,
    WorkspaceRepository,
)

from .repository import (
    ConsoleConflictError,
    ConsoleExecutionRecord,
    ConsoleNotFoundError,
    ConsoleRepository,
    ConsoleRepositoryError,
    ConsoleLimitReachedError,
    ConsoleResultGoneError,
    ConsoleStorageUnavailableError,
    ConsoleTarget,
)
from .models import (
    ConsoleHistoryEntry,
    ConsoleSavedQuery,
    ConsoleSavedQueryCreate,
    ConsoleSavedQueryUpdate,
)


DEFAULT_CONSOLE_SETTINGS = ConsoleSettings(
    revision=1,
    write_intent=False,
    default_mode="managed_read",
    statement_limit=MAX_CONSOLE_STATEMENTS,
    row_page_size=100,
)
LOGGER = logging.getLogger(__name__)
CONSOLE_TRANSACTION_IDLE_TTL = timedelta(minutes=5)
CONSOLE_TRANSACTION_MAXIMUM_TTL = timedelta(minutes=30)


@dataclass(slots=True)
class _ActiveConsoleTransaction:
    owner_id: str
    workspace_id: str
    console_id: str
    target: ResolvedPostgresConnection
    postgres: PostgresConsoleTransaction
    lock: threading.RLock


@dataclass(slots=True)
class _TransientConsoleResult:
    owner_id: str
    workspace_id: str | None
    execution_id: str
    result_id: str
    query: ConsoleQueryResult
    page_size: int
    expires_at: datetime
    read_session: PostgresConsoleReadSession | None = None
    active_exports: int = 0


@dataclass(slots=True)
class _ActiveConsoleReadSession:
    owner_id: str
    workspace_id: str | None
    connection_id: str
    connection_revision: int
    postgres: PostgresConsoleReadSession


class ConsoleService:
    def __init__(
        self,
        *,
        repository: ConsoleRepository,
        connections: ConnectionService,
        postgres: PostgresGateway,
        workspaces: WorkspaceRepository,
        result_ttl: timedelta = timedelta(minutes=15),
        row_page_size: int = 100,
        page_memory_bytes: int = 4 * 1024 * 1024,
        maximum_live_read_sessions: int = 12,
        maximum_live_read_sessions_per_identity: int = 4,
        query_history_limit: int = 100,
        statement_limit: int = MAX_CONSOLE_STATEMENTS,
        transaction_idle_ttl: timedelta = CONSOLE_TRANSACTION_IDLE_TTL,
        transaction_maximum_ttl: timedelta = CONSOLE_TRANSACTION_MAXIMUM_TTL,
        maximum_saved_queries_per_workspace: int = 100,
        limit_events: LimitEventRecorder | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if result_ttl <= timedelta(0):
            raise ValueError("Console result TTL must be positive")
        if not 1 <= row_page_size <= 1000:
            raise ValueError("Console row page size must be between 1 and 1000")
        if query_history_limit < 0:
            raise ValueError("Console query history limit cannot be negative")
        if page_memory_bytes < 1:
            raise ValueError("Console page memory limit must be positive")
        if maximum_live_read_sessions < 1:
            raise ValueError("Maximum live Console read sessions must be positive")
        if not 1 <= maximum_live_read_sessions_per_identity <= maximum_live_read_sessions:
            raise ValueError("Per-identity Console read session capacity is invalid")
        if statement_limit < 1:
            raise ValueError("Console statement limit must be positive")
        if transaction_idle_ttl <= timedelta(0):
            raise ValueError("Console transaction idle TTL must be positive")
        if transaction_maximum_ttl < transaction_idle_ttl:
            raise ValueError("Console transaction maximum TTL cannot be shorter than idle TTL")
        if maximum_saved_queries_per_workspace < 1:
            raise ValueError("Saved query limit must be positive")
        self._repository = repository
        self._connections = connections
        self._postgres = postgres
        self._workspaces = workspaces
        self._result_ttl = result_ttl
        self._row_page_size = row_page_size
        self._page_memory_bytes = page_memory_bytes
        self._maximum_live_read_sessions = maximum_live_read_sessions
        self._maximum_live_read_sessions_per_identity = (
            maximum_live_read_sessions_per_identity
        )
        self._query_history_limit = query_history_limit
        self._statement_limit = statement_limit
        self._transaction_idle_ttl = transaction_idle_ttl
        self._transaction_maximum_ttl = transaction_maximum_ttl
        self._maximum_saved_queries_per_workspace = maximum_saved_queries_per_workspace
        self._limit_events = limit_events
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._active_targets: dict[str, ResolvedPostgresConnection] = {}
        self._active_targets_lock = threading.RLock()
        self._active_transactions: dict[str, _ActiveConsoleTransaction] = {}
        self._active_transactions_lock = threading.RLock()
        self._transient_results: dict[str, _TransientConsoleResult] = {}
        self._result_cursors: dict[str, tuple[str, int, datetime]] = {}
        self._transient_results_lock = threading.RLock()
        self._active_read_sessions: OrderedDict[
            str, _ActiveConsoleReadSession
        ] = OrderedDict()
        self._repository.recover_interrupted(self._clock())
        self._repository.recover_transactions(self._clock())
        self._repository.prune_operational_receipts(
            self._clock() - self._result_ttl
        )
        self._repository.prune_history(self._query_history_limit)

    def settings(self, owner_id: str) -> ConsoleSettings:
        try:
            revision, page_size = self._repository.settings(owner_id)
        except ConsoleRepositoryError as error:
            raise self._repository_error(error) from error
        return DEFAULT_CONSOLE_SETTINGS.model_copy(
            update={
                "revision": revision,
                "row_page_size": min(page_size or self._row_page_size, self._row_page_size),
                "maximum_row_page_size": self._row_page_size,
                "statement_limit": self._statement_limit,
            },
            deep=True,
        )

    def update_settings(self, owner_id: str, expected_revision: int, page_size: int) -> ConsoleSettings:
        if page_size > self._row_page_size:
            raise ConsoleServiceError(
                422,
                "console_page_size_limit",
                f"Rows per page cannot exceed the administrator limit of {self._row_page_size}",
                details={"maximumRowPageSize": self._row_page_size},
                limit_event=LimitEventNotice(
                    resource="console_page_rows",
                    limit_name="console.row_page_size",
                    configured_limit=self._row_page_size,
                    observed_value=page_size,
                ),
            )
        try:
            self._repository.update_settings(owner_id, expected_revision, page_size)
        except ConsoleRepositoryError as error:
            raise self._repository_error(error) from error
        return self.settings(owner_id)

    def history(
        self, owner_id: str, workspace_id: str, limit: int | None
    ) -> list[ConsoleHistoryEntry]:
        """Return bounded replay history without projecting execution telemetry."""

        self._require_workspace(owner_id, workspace_id)
        effective_limit = self._query_history_limit
        if limit is not None:
            effective_limit = min(effective_limit, limit)
        if effective_limit == 0:
            return []
        try:
            return self._repository.list_history(
                owner_id, workspace_id, effective_limit
            )
        except ConsoleRepositoryError as error:
            raise self._repository_error(error) from error

    def saved_queries(self, owner_id: str, workspace_id: str) -> list[ConsoleSavedQuery]:
        self._require_workspace(owner_id, workspace_id)
        try:
            return self._repository.list_saved_queries(owner_id, workspace_id)
        except ConsoleRepositoryError as error:
            raise self._repository_error(error) from error

    def create_saved_query(
        self, owner_id: str, workspace_id: str, request: ConsoleSavedQueryCreate
    ) -> ConsoleSavedQuery:
        self._require_workspace(owner_id, workspace_id)
        try:
            return self._repository.create_saved_query(
                owner_id,
                workspace_id,
                request.name,
                request.sql,
                request.starter,
                self._clock(),
                self._maximum_saved_queries_per_workspace,
            )
        except ConsoleLimitReachedError as error:
            limit_name = "console.maximum_saved_queries_per_workspace"
            raise ConsoleServiceError(
                409,
                "console_saved_query_limit_reached",
                f"This workspace already has {error.limit} saved queries. Delete an unused saved query or ask the administrator to raise {limit_name}.",
                details={"resource": "saved_queries", "limitName": limit_name, "limit": error.limit, "observed": error.observed},
                limit_event=LimitEventNotice(
                    resource="saved_queries",
                    limit_name=limit_name,
                    configured_limit=error.limit,
                    observed_value=error.observed,
                ),
            ) from error
        except ConsoleRepositoryError as error:
            raise self._repository_error(error) from error

    def update_saved_query(
        self,
        owner_id: str,
        workspace_id: str,
        query_id: str,
        request: ConsoleSavedQueryUpdate,
    ) -> ConsoleSavedQuery:
        self._require_workspace(owner_id, workspace_id)
        try:
            return self._repository.update_saved_query(
                owner_id,
                workspace_id,
                query_id,
                request.expected_revision,
                request.name,
                request.sql,
                self._clock(),
            )
        except ConsoleNotFoundError as error:
            raise ConsoleServiceError(
                404,
                "console_saved_query_not_found",
                "Saved query was not found",
            ) from error
        except ConsoleRepositoryError as error:
            raise self._repository_error(error) from error

    def delete_saved_query(
        self, owner_id: str, workspace_id: str, query_id: str, expected_revision: int
    ) -> None:
        self._require_workspace(owner_id, workspace_id)
        try:
            self._repository.delete_saved_query(
                owner_id, workspace_id, query_id, expected_revision
            )
        except ConsoleNotFoundError as error:
            raise ConsoleServiceError(
                404,
                "console_saved_query_not_found",
                "Saved query was not found",
            ) from error
        except ConsoleRepositoryError as error:
            raise self._repository_error(error) from error

    def _require_workspace(self, owner_id: str, workspace_id: str) -> None:
        try:
            workspace = self._workspaces.get(owner_id, workspace_id)
        except WorkspaceNotFoundError as error:
            raise ConsoleServiceError(404, "workspace_not_found", str(error)) from error
        if not workspace.connection_id or not workspace.database or not workspace.namespace:
            raise ConsoleServiceError(
                409,
                "console_target_required",
                "A database-backed workspace is required for SQL Console data",
            )

    def reserve(
        self,
        owner_id: str,
        workspace_id: str,
        request: ConsoleExecutionCreate,
    ) -> ConsoleExecution:
        workspace, target = self._workspace_target(
            owner_id, workspace_id, request.expected_workspace_revision
        )
        return self._reserve_read(owner_id, workspace_id, workspace.revision, target, request)

    def reserve_read_target(
        self,
        owner_id: str,
        *,
        connection_id: str,
        database: str,
        namespace: str,
        console_id: str,
        statements: list[str],
    ) -> ConsoleExecution:
        """Reserve product-compiled reads without manufacturing a Schemii workspace.

        The caller owns model/parameter authorization. This boundary still resolves
        the owner's credentials, validates read-only SQL, and uses the same bounded
        execution runtime as the SQL Console. Receipts expire normally; these reads
        do not enter the workspace's human-authored query history.
        """
        try:
            profile = self._connections.get(owner_id, connection_id)
        except ConnectionNotFoundError as error:
            raise ConsoleServiceError(
                404, "console_connection_missing", "The PostgreSQL connection was not found"
            ) from error
        if profile.database != database:
            raise ConsoleServiceError(
                409, "console_database_changed", "The database no longer matches its connection"
            )
        if not namespace or "\x00" in namespace:
            raise ConsoleServiceError(422, "invalid_namespace", "A valid schema is required")
        request = ManagedReadCreate(
            console_id=console_id,
            expected_settings_revision=self.settings(owner_id).revision,
            mode="managed_read",
            statements=statements,
        )
        target = ConsoleTarget(profile.id, profile.revision, database, namespace)
        return self._reserve_read(owner_id, None, None, target, request)

    def _reserve_read(
        self, owner_id: str, workspace_id: str | None, workspace_revision: int | None,
        target: ConsoleTarget, request: ManagedReadCreate,
    ) -> ConsoleExecution:
        try:
            self._repository.prune_operational_receipts(
                self._clock() - self._result_ttl
            )
        except ConsoleRepositoryError as error:
            raise self._repository_error(error) from error
        if request.mode != "managed_read":
            raise ConsoleServiceError(
                422,
                "console_mode_not_available",
                "Only managed read mode is available",
            )
        preferences = self._validate_settings_revision(owner_id, request.expected_settings_revision)
        try:
            statements = validate_read_only_statements(
                request.statements,
                statement_limit=self._statement_limit,
            )
        except ConsoleStatementValidationError as error:
            raise ConsoleServiceError(
                422,
                error.code,
                str(error),
                details=self._statement_error_details(error),
                limit_event=self._statement_limit_event(error),
            ) from error
        try:
            record = self._repository.reserve(
                owner_id,
                workspace_id,
                request.console_id,
                workspace_revision,
                target,
                statements,
                preferences.row_page_size,
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
                        accepted = self._repository.publish_backend(
                            owner_id,
                            execution_id,
                            backend_pid,
                            self._clock(),
                        )
                        if accepted:
                            self._record_history(record)
                        return accepted

                    open_read_session = getattr(
                        self._postgres, "open_console_read_session", None
                    )
                    read_session: PostgresConsoleReadSession | None = None
                    if callable(open_read_session):
                        self._make_read_session_capacity(record.target)
                        read_session = open_read_session(
                            target,
                            record.target.namespace,
                            record.statements,
                            on_started=publish,
                            page_memory_bytes=self._page_memory_bytes,
                        )
                        results = read_session.results
                    else:
                        results = self._postgres.execute_console(
                            target,
                            record.target.namespace,
                            record.statements,
                            on_started=publish,
                        )
                finally:
                    with self._active_targets_lock:
                        self._active_targets.pop(execution_id, None)
            expires_at = self._clock() + self._result_ttl
            completed = self._repository.succeed(
                owner_id,
                execution_id,
                results,
                expires_at,
                self._clock(),
            )
            self._retain_results(
                record,
                completed.execution,
                results,
                expires_at,
                read_session=read_session,
            )
        except Exception as error:
            if "read_session" in locals() and read_session is not None:
                read_session.close()
            self._record_run_failure(owner_id, execution_id, error)

    def create_transaction(
        self,
        owner_id: str,
        workspace_id: str,
        request: ConsoleTransactionCreate,
    ) -> ConsoleTransaction:
        """Open one exact-target PostgreSQL transaction and durable ownership receipt."""

        self._validate_settings_revision(owner_id, request.expected_settings_revision)
        workspace, target = self._workspace_target(
            owner_id,
            workspace_id,
            request.expected_workspace_revision,
        )
        now = self._clock()
        maximum_expires_at = now + self._transaction_maximum_ttl
        expires_at = min(now + self._transaction_idle_ttl, maximum_expires_at)
        with self._active_transactions_lock:
            existing = next((
                (identifier, session)
                for identifier, session in self._active_transactions.items()
                if session.owner_id == owner_id and session.workspace_id == workspace_id
            ), None)
            if existing is not None:
                identifier, session = existing
                record = self._transaction_record(owner_id, workspace_id, identifier)
                if record.transaction.status in {"open", "failed"}:
                    if session.console_id == request.console_id and record.target == target:
                        return record.transaction
                    raise ConsoleServiceError(
                        409,
                        "console_transaction_active",
                        "This workspace already has an open Console transaction",
                    )
            postgres_transaction: PostgresConsoleTransaction | None = None
            try:
                with self._connections.use(owner_id, target.connection_id) as resolved:
                    if (
                        resolved.revision != target.connection_revision
                        or resolved.database != target.database
                    ):
                        raise ConsoleServiceError(
                            409,
                            "console_target_changed",
                            "PostgreSQL connection changed while opening the transaction",
                        )
                    postgres_transaction = self._postgres.open_console_transaction(
                        resolved,
                        target.namespace,
                    )
                    record = self._repository.create_transaction(
                        owner_id,
                        workspace_id,
                        request.console_id,
                        workspace.revision,
                        target,
                        postgres_transaction.backend_pid,
                        now,
                        expires_at,
                        maximum_expires_at,
                    )
                    self._active_transactions[record.transaction.id] = (
                        _ActiveConsoleTransaction(
                            owner_id=owner_id,
                            workspace_id=workspace_id,
                            console_id=request.console_id,
                            target=resolved,
                            postgres=postgres_transaction,
                            lock=threading.RLock(),
                        )
                    )
                    postgres_transaction = None
                    return record.transaction
            except ConsoleServiceError:
                raise
            except ConsoleRepositoryError as error:
                raise self._repository_error(error) from error
            except PostgresGatewayError as error:
                raise self._postgres_error(error) from error
            finally:
                if postgres_transaction is not None:
                    try:
                        postgres_transaction.rollback()
                    except PostgresGatewayError:
                        postgres_transaction.close()

    def get_transaction(
        self,
        owner_id: str,
        workspace_id: str,
        transaction_id: str,
    ) -> ConsoleTransaction:
        return self._transaction_record(owner_id, workspace_id, transaction_id).transaction

    def reserve_transaction_execution(
        self,
        owner_id: str,
        workspace_id: str,
        transaction_id: str,
        request: ConsoleTransactionExecutionCreate,
    ) -> ConsoleExecution:
        try:
            script = validate_transaction_statements(
                request.statements,
                statement_limit=self._statement_limit,
            )
        except ConsoleStatementValidationError as error:
            raise ConsoleServiceError(
                422,
                error.code,
                str(error),
                details=self._statement_error_details(error),
                limit_event=self._statement_limit_event(error),
            ) from error
        if script.terminal_action is not None:
            raise ConsoleServiceError(
                422,
                "console_transaction_command_separate",
                "Run COMMIT or ROLLBACK as its own Console action",
            )
        if not script.statements:
            raise ConsoleServiceError(
                422,
                "console_statement_empty",
                "Enter at least one PostgreSQL statement",
            )
        session = self._session(owner_id, workspace_id, transaction_id)
        with session.lock:
            record = self._transaction_record(owner_id, workspace_id, transaction_id)
            if record.transaction.status == "failed":
                raise ConsoleServiceError(
                    409,
                    "console_transaction_failed",
                    "PostgreSQL rejected an earlier statement; roll back this transaction",
                )
            if record.transaction.status != "open":
                raise ConsoleServiceError(
                    409,
                    "console_transaction_not_open",
                    "The Console transaction is not open",
                )
            if record.transaction.revision != request.expected_revision:
                raise ConsoleServiceError(
                    409,
                    "console_transaction_changed",
                    "The Console transaction changed after the editor loaded",
                    details={"currentRevision": record.transaction.revision},
                )
            now = self._clock()
            expires_at = min(
                now + self._transaction_idle_ttl,
                record.maximum_expires_at,
            )
            if expires_at <= now:
                self._expire_live_transaction(owner_id, transaction_id, session, now)
                raise ConsoleServiceError(
                    409,
                    "console_transaction_expired",
                    "The Console transaction reached its maximum lifetime",
                )
            try:
                execution = self._repository.reserve(
                    owner_id,
                    workspace_id,
                    record.transaction.console_id,
                    record.workspace_revision,
                    record.target,
                    script.statements,
                    self.settings(owner_id).row_page_size,
                    now,
                    transaction_id=transaction_id,
                    expected_transaction_revision=request.expected_revision,
                    transaction_expires_at=expires_at,
                )
            except ConsoleRepositoryError as error:
                raise self._repository_error(error) from error
            return execution.execution

    def run_transaction(self, owner_id: str, execution_id: str) -> None:
        """Run a reserved execution on its already-open PostgreSQL connection."""

        transaction_id: str | None = None
        try:
            record = self._repository.claim(owner_id, execution_id, self._clock())
            if record is None:
                return
            transaction_id = record.execution.transaction_id
            if transaction_id is None:
                raise RuntimeError("Transaction execution has no transaction receipt")
            session = self._session(
                owner_id,
                record.execution.workspace_id,
                transaction_id,
            )
            with session.lock:
                transaction = self._transaction_record(
                    owner_id,
                    record.execution.workspace_id,
                    transaction_id,
                )
                if transaction.transaction.status != "open":
                    raise ConsoleServiceError(
                        409,
                        "console_transaction_not_open",
                        "The Console transaction is not open",
                    )
                if not self._repository.publish_backend(
                    owner_id,
                    execution_id,
                    transaction.backend_pid,
                    self._clock(),
                ):
                    raise PostgresConsoleCancelledError()
                self._record_history(record)
                with self._active_targets_lock:
                    self._active_targets[execution_id] = session.target
                try:
                    results = session.postgres.execute(record.statements)
                finally:
                    with self._active_targets_lock:
                        self._active_targets.pop(execution_id, None)
                expires_at = self._clock() + self._result_ttl
                completed = self._repository.succeed(
                    owner_id,
                    execution_id,
                    results,
                    expires_at,
                    self._clock(),
                )
                self._retain_results(record, completed.execution, results, expires_at)
        except Exception as error:
            self._record_run_failure(owner_id, execution_id, error)
            if transaction_id is not None:
                try:
                    self._repository.fail_transaction(
                        owner_id,
                        transaction_id,
                        self._clock(),
                    )
                except ConsoleRepositoryError:
                    pass

    def commit_transaction(
        self,
        owner_id: str,
        workspace_id: str,
        transaction_id: str,
        request: ConsoleTransactionCommand,
    ) -> ConsoleTransaction:
        return self._finish_transaction(
            owner_id,
            workspace_id,
            transaction_id,
            request.expected_revision,
            "commit",
        )

    def rollback_transaction(
        self,
        owner_id: str,
        workspace_id: str,
        transaction_id: str,
        request: ConsoleTransactionCommand,
    ) -> ConsoleTransaction:
        return self._finish_transaction(
            owner_id,
            workspace_id,
            transaction_id,
            request.expected_revision,
            "rollback",
        )

    def close(self) -> None:
        """Roll back every process-bound transaction during application shutdown."""

        with self._active_transactions_lock:
            sessions = list(self._active_transactions.items())
            self._active_transactions.clear()
        now = self._clock()
        for transaction_id, session in sessions:
            with session.lock:
                try:
                    session.postgres.rollback()
                except PostgresGatewayError:
                    session.postgres.close()
                try:
                    self._repository.expire_transaction(
                        session.owner_id,
                        transaction_id,
                        now,
                    )
                except ConsoleRepositoryError:
                    pass
        with self._transient_results_lock:
            read_sessions = [
                active.postgres for active in self._active_read_sessions.values()
            ]
            self._active_read_sessions.clear()
            self._transient_results.clear()
            self._result_cursors.clear()
        for read_session in read_sessions:
            read_session.close()

    def get_owned(self, owner_id: str, execution_id: str) -> ConsoleExecution:
        """Resolve an exact owner-bound receipt for product-neutral result routes."""
        try:
            return self._repository.get(owner_id, execution_id).execution
        except ConsoleRepositoryError as error:
            raise self._repository_error(error) from error

    def get(self, owner_id: str, workspace_id: str | None, execution_id: str) -> ConsoleExecution:
        try:
            record = self._repository.get(owner_id, execution_id)
        except ConsoleRepositoryError as error:
            raise self._repository_error(error) from error
        if record.execution.workspace_id != workspace_id:
            raise ConsoleServiceError(404, "console_execution_not_found", "Console execution was not found")
        return record.execution

    def cancel(self, owner_id: str, workspace_id: str | None, execution_id: str) -> ConsoleExecution:
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
            receipt = self._repository.get(owner_id, execution_id)
        except ConsoleRepositoryError as error:
            raise self._repository_error(error) from error
        if receipt.execution.workspace_id != workspace_id:
            raise ConsoleServiceError(
                404, "console_execution_not_found", "Console execution was not found"
            )
        now = self._clock()
        with self._transient_results_lock:
            self._purge_transient_results(now)
            result = self._transient_results.get(result_id)
            if (
                result is None
                or result.owner_id != owner_id
                or result.workspace_id != workspace_id
                or result.execution_id != execution_id
            ):
                raise ConsoleServiceError(
                    410,
                    "console_result_replay_required",
                    "This result is no longer in transient memory; run the query again",
                )
            offset = 0
            if cursor is not None:
                cursor_value = self._result_cursors.pop(cursor, None)
                if (
                    cursor_value is None
                    or cursor_value[0] != result_id
                    or cursor_value[2] <= now
                ):
                    raise ConsoleServiceError(
                        410,
                        "console_result_gone",
                        "This result page cursor has expired or was already used",
                    )
                offset = cursor_value[1]
            if result.read_session is None:
                rows = result.query.rows[offset : offset + result.page_size]
            else:
                try:
                    if execution_id in self._active_read_sessions:
                        self._active_read_sessions.move_to_end(execution_id)
                    fetched = result.read_session.page(
                        result.query.statement_index, offset, result.page_size
                    )
                    rows = fetched
                except PostgresGatewayError as error:
                    if isinstance(error, PostgresConsoleCancelledError):
                        self._discard_read_snapshot(execution_id)
                    raise self._postgres_error(error) from error
            next_offset = offset + len(rows)
            next_cursor = None
            row_count = result.query.row_count
            buffered_rows = False
            if result.read_session is not None:
                has_buffered_rows = getattr(result.read_session, "has_buffered_rows", None)
                if callable(has_buffered_rows):
                    buffered_rows = has_buffered_rows(result.query.statement_index)
            has_more = (len(fetched) == result.page_size or buffered_rows) if result.read_session is not None and result.query.row_count is None else (
                next_offset < (row_count if row_count is not None else len(result.query.rows)))
            if has_more:
                next_cursor = f"crc_{secrets.token_hex(16)}"
                self._result_cursors[next_cursor] = (
                    result_id, next_offset, result.expires_at
                )
            return ConsoleResultPage(
                execution_id=execution_id,
                result_id=result_id,
                columns=list(result.query.columns),
                rows=[list(row) for row in rows],
                next_cursor=next_cursor,
                truncated=result.query.truncated,
                expires_at=result.expires_at,
            )

    def close_result(self, owner_id, workspace_id, execution_id, result_id) -> None:
        try:
            self._repository.close_result(
                owner_id, workspace_id, execution_id, result_id, self._clock()
            )
        except ConsoleRepositoryError as error:
            raise self._repository_error(error) from error
        with self._transient_results_lock:
            self._transient_results.pop(result_id, None)
            self._result_cursors = {
                token: value for token, value in self._result_cursors.items()
                if value[0] != result_id
            }
            self._close_unreferenced_read_sessions()

    def export_csv(
        self,
        owner_id: str,
        workspace_id: str | None,
        execution_id: str,
        result_id: str,
    ) -> Iterator[bytes]:
        """Stream a transient result without buffering it in metadata or a file."""

        try:
            receipt = self._repository.get(owner_id, execution_id)
        except ConsoleRepositoryError as error:
            raise self._repository_error(error) from error
        if receipt.execution.workspace_id != workspace_id:
            raise ConsoleServiceError(
                404, "console_execution_not_found", "Console execution was not found"
            )
        now = self._clock()
        with self._transient_results_lock:
            self._purge_transient_results(now)
            result = self._transient_results.get(result_id)
            if (
                result is None
                or result.owner_id != owner_id
                or result.workspace_id != workspace_id
                or result.execution_id != execution_id
            ):
                raise ConsoleServiceError(
                    410,
                    "console_result_replay_required",
                    "This result is no longer available; run the query again before exporting",
                )
            result.active_exports += 1

        def stream() -> Iterator[bytes]:
            offset = 0
            try:
                buffer = io.StringIO(newline="")
                writer = csv.writer(buffer)
                writer.writerow([column.name for column in result.query.columns])
                yield buffer.getvalue().encode("utf-8")
                row_count = result.query.row_count
                while row_count is None or offset < row_count:
                    if result.read_session is None:
                        rows = result.query.rows[
                            offset : offset + result.page_size
                        ]
                    else:
                        export_page = getattr(
                            result.read_session, "export_page", result.read_session.page
                        )
                        rows = export_page(
                            result.query.statement_index, offset, result.page_size
                        )
                    if not rows:
                        break
                    buffer = io.StringIO(newline="")
                    writer = csv.writer(buffer)
                    writer.writerows(rows)
                    yield buffer.getvalue().encode("utf-8")
                    offset += len(rows)
            except PostgresConsoleCancelledError:
                with self._transient_results_lock:
                    self._discard_read_snapshot(execution_id)
                raise
            finally:
                with self._transient_results_lock:
                    current = self._transient_results.get(result_id)
                    if current is not None:
                        current.active_exports = max(0, current.active_exports - 1)

        return stream()

    def _discard_read_snapshot(self, execution_id: str) -> None:
        """A cancelled FETCH aborts its transaction; release it for replay.

        Caller holds the transient-results lock. Do not label a user-requested
        cancellation as a capacity eviction or discard another execution.
        """
        active = self._active_read_sessions.pop(execution_id, None)
        result_ids = {
            result_id for result_id, result in self._transient_results.items()
            if result.execution_id == execution_id
        }
        for result_id in result_ids:
            self._transient_results.pop(result_id, None)
        self._result_cursors = {
            token: value for token, value in self._result_cursors.items()
            if value[0] not in result_ids
        }
        if active is not None:
            active.postgres.close()

    def _record_history(self, record: ConsoleExecutionRecord) -> None:
        if self._query_history_limit == 0 or record.execution.workspace_id is None:
            return
        ran_at = self._clock()
        for statement in record.statements:
            try:
                self._repository.record_history(
                    record.owner_id,
                    record.execution.workspace_id,
                    statement,
                    ran_at,
                    self._query_history_limit,
                )
            except ConsoleRepositoryError:
                # History is convenience state and must never prevent PostgreSQL
                # execution after the authoritative target accepted the request.
                return

    def _retain_results(
        self,
        record: ConsoleExecutionRecord,
        execution: ConsoleExecution,
        results: tuple[ConsoleQueryResult, ...],
        expires_at: datetime,
        *,
        read_session: PostgresConsoleReadSession | None = None,
    ) -> None:
        summaries = {item.statement_index: item for item in execution.results}
        with self._transient_results_lock:
            self._purge_transient_results(self._clock())
            for query in results:
                summary = summaries.get(query.statement_index)
                if summary is None:
                    continue
                self._transient_results[summary.id] = _TransientConsoleResult(
                    owner_id=record.owner_id,
                    workspace_id=execution.workspace_id,
                    execution_id=execution.id,
                    result_id=summary.id,
                    query=query,
                    page_size=record.page_size,
                    expires_at=expires_at,
                    read_session=read_session,
                )
            if read_session is not None:
                self._active_read_sessions[execution.id] = _ActiveConsoleReadSession(
                    owner_id=record.owner_id,
                    workspace_id=execution.workspace_id,
                    connection_id=record.target.connection_id,
                    connection_revision=record.target.connection_revision,
                    postgres=read_session,
                )
                self._active_read_sessions.move_to_end(execution.id)
                while (
                    len(self._active_read_sessions)
                    > self._maximum_live_read_sessions
                ):
                    evicted_execution, evicted = self._active_read_sessions.popitem(
                        last=False
                    )
                    if any(
                        transient.execution_id == evicted_execution
                        and transient.active_exports > 0
                        for transient in self._transient_results.values()
                    ):
                        self._active_read_sessions[evicted_execution] = evicted
                        self._active_read_sessions.move_to_end(evicted_execution)
                        break
                    self._evict_read_session(evicted_execution, evicted)

    def _purge_transient_results(self, now: datetime) -> None:
        expired = {
            result_id for result_id, result in self._transient_results.items()
            if result.expires_at <= now and result.active_exports == 0
        }
        for result_id in expired:
            self._transient_results.pop(result_id, None)
        self._result_cursors = {
            token: value for token, value in self._result_cursors.items()
            if value[0] not in expired and value[2] > now
        }
        self._close_unreferenced_read_sessions()

    def _close_unreferenced_read_sessions(self) -> None:
        referenced = {
            result.execution_id for result in self._transient_results.values()
            if result.read_session is not None
        }
        for execution_id in list(self._active_read_sessions):
            if execution_id in referenced:
                continue
            self._active_read_sessions.pop(execution_id).postgres.close()

    def _make_read_session_capacity(self, target: ConsoleTarget) -> None:
        """Release the oldest inactive cursor before gateway admission blocks."""

        with self._transient_results_lock:
            matching = [
                (execution_id, active)
                for execution_id, active in self._active_read_sessions.items()
                if active.connection_id == target.connection_id
                and active.connection_revision == target.connection_revision
            ]
            while len(matching) >= self._maximum_live_read_sessions_per_identity:
                candidate = next(
                    (
                        value for value in matching
                        if not any(
                            transient.execution_id == value[0]
                            and transient.active_exports > 0
                            for transient in self._transient_results.values()
                        )
                    ),
                    None,
                )
                if candidate is None:
                    return
                execution_id, active = candidate
                self._active_read_sessions.pop(execution_id, None)
                self._evict_read_session(
                    execution_id,
                    active,
                    limit_name="console.results.maximum_live_read_sessions_per_identity",
                    limit=self._maximum_live_read_sessions_per_identity,
                )
                matching = [value for value in matching if value[0] != execution_id]

    def _evict_read_session(
        self,
        execution_id: str,
        active: _ActiveConsoleReadSession,
        *,
        limit_name: str = "console.results.maximum_live_read_sessions",
        limit: int | None = None,
    ) -> None:
        active.postgres.close()
        result_ids = {
            result_id for result_id, transient in self._transient_results.items()
            if transient.execution_id == execution_id
        }
        for result_id in result_ids:
            self._transient_results.pop(result_id, None)
        self._result_cursors = {
            token: value for token, value in self._result_cursors.items()
            if value[0] not in result_ids
        }
        self._record_limit_event(
            LimitEventNotice(
                resource="console_live_read_sessions",
                limit_name=limit_name,
                configured_limit=limit or self._maximum_live_read_sessions,
                observed_value=(limit or self._maximum_live_read_sessions) + 1,
                outcome="evicted",
            ),
            error_code="console_read_session_evicted",
            owner_id=active.owner_id,
            workspace_id=active.workspace_id,
        )

    @staticmethod
    def _statement_error_details(error: ConsoleStatementValidationError) -> dict[str, object]:
        details: dict[str, object] = {"statementIndex": error.statement_index}
        if error.limit is not None:
            details.update({
                "resource": "console_statements",
                "limitName": "console.maximum_statements_per_run",
                "limit": error.limit,
                "observed": error.observed,
            })
        return details

    @staticmethod
    def _statement_limit_event(error: ConsoleStatementValidationError) -> LimitEventNotice | None:
        if error.limit is None:
            return None
        return LimitEventNotice(
            resource="console_statements",
            limit_name="console.maximum_statements_per_run",
            configured_limit=error.limit,
            observed_value=error.observed,
        )

    def _record_limit_event(
        self,
        notice: LimitEventNotice,
        *,
        error_code: str,
        owner_id: str,
        workspace_id: str,
    ) -> None:
        if self._limit_events is None:
            return
        try:
            self._limit_events.record(
                new_limit_event(
                    notice,
                    error_code=error_code,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )
            )
        except Exception:
            # Resource cleanup must succeed even if observability is unavailable.
            LOGGER.exception(
                "Could not record Console limit event",
                extra={"workspace_id": workspace_id, "error_code": error_code},
            )

    def _finish_transaction(
        self,
        owner_id: str,
        workspace_id: str,
        transaction_id: str,
        expected_revision: int,
        action: str,
    ) -> ConsoleTransaction:
        session = self._session(owner_id, workspace_id, transaction_id)
        with session.lock:
            record = self._transaction_record(owner_id, workspace_id, transaction_id)
            if record.transaction.revision != expected_revision:
                raise ConsoleServiceError(
                    409,
                    "console_transaction_changed",
                    "The Console transaction changed after the confirmation opened",
                    details={"currentRevision": record.transaction.revision},
                )
            if record.transaction.status not in {"open", "failed"}:
                return record.transaction
            if action == "commit" and record.transaction.status == "failed":
                raise ConsoleServiceError(
                    409,
                    "console_transaction_failed",
                    "This transaction is aborted and can only be rolled back",
                )
            terminal_status = "committed" if action == "commit" else "rolled_back"
            try:
                if action == "commit":
                    session.postgres.commit()
                else:
                    session.postgres.rollback()
            except PostgresConsoleCancelledError as error:
                # The shared gateway fences a stopped AI before COMMIT is
                # dispatched. Keep the still-open transaction available for
                # the user's explicit commit/rollback instead of orphaning it.
                raise self._postgres_error(error) from error
            except PostgresCommitUncertainError as error:
                try:
                    uncertain = self._repository.finish_transaction(
                        owner_id,
                        workspace_id,
                        transaction_id,
                        expected_revision,
                        "uncertain",
                        self._clock(),
                    )
                except ConsoleRepositoryError:
                    uncertain = record
                self._drop_session(transaction_id)
                raise ConsoleServiceError(
                    502,
                    "console_transaction_commit_uncertain",
                    "The connection ended before PostgreSQL confirmed the commit outcome",
                    details={"transactionRevision": uncertain.transaction.revision},
                ) from error
            except PostgresGatewayError as error:
                self._drop_session(transaction_id)
                raise self._postgres_error(error) from error
            self._drop_session(transaction_id)
            try:
                finished = self._repository.finish_transaction(
                    owner_id,
                    workspace_id,
                    transaction_id,
                    expected_revision,
                    terminal_status,
                    self._clock(),
                )
            except ConsoleRepositoryError as error:
                raise ConsoleServiceError(
                    503,
                    "console_transaction_receipt_unavailable",
                    f"PostgreSQL {terminal_status.replace('_', ' ')}, but its receipt could not be saved",
                    details={"outcome": terminal_status},
                    retryable=True,
                ) from error
            return finished.transaction

    def _session(
        self,
        owner_id: str,
        workspace_id: str,
        transaction_id: str,
    ) -> _ActiveConsoleTransaction:
        record = self._transaction_record(owner_id, workspace_id, transaction_id)
        with self._active_transactions_lock:
            session = self._active_transactions.get(transaction_id)
        if (
            session is None
            or session.owner_id != owner_id
            or session.workspace_id != workspace_id
        ):
            if record.transaction.status in {"open", "failed"}:
                try:
                    self._repository.expire_transaction(
                        owner_id,
                        transaction_id,
                        self._clock(),
                    )
                except ConsoleRepositoryError:
                    pass
            raise ConsoleServiceError(
                409,
                "console_transaction_interrupted",
                "The server process owning this transaction ended; PostgreSQL rolled it back",
            )
        return session

    def _transaction_record(
        self,
        owner_id: str,
        workspace_id: str,
        transaction_id: str,
    ):
        try:
            record = self._repository.get_transaction(owner_id, transaction_id)
        except ConsoleNotFoundError as error:
            raise ConsoleServiceError(
                404,
                "console_transaction_not_found",
                "Console transaction was not found",
            ) from error
        except ConsoleRepositoryError as error:
            raise self._repository_error(error) from error
        if record.transaction.workspace_id != workspace_id:
            raise ConsoleServiceError(
                404,
                "console_transaction_not_found",
                "Console transaction was not found",
            )
        now = self._clock()
        if (
            record.transaction.status in {"open", "failed"}
            and (
                record.transaction.expires_at <= now
                or record.maximum_expires_at <= now
            )
        ):
            with self._active_transactions_lock:
                session = self._active_transactions.get(transaction_id)
            if session is not None:
                self._expire_live_transaction(owner_id, transaction_id, session, now)
            else:
                try:
                    self._repository.expire_transaction(owner_id, transaction_id, now)
                except ConsoleRepositoryError as error:
                    raise self._repository_error(error) from error
            try:
                return self._repository.get_transaction(owner_id, transaction_id)
            except ConsoleRepositoryError as error:
                raise self._repository_error(error) from error
        return record

    def _expire_live_transaction(
        self,
        owner_id: str,
        transaction_id: str,
        session: _ActiveConsoleTransaction,
        now: datetime,
    ) -> None:
        self._drop_session(transaction_id)
        try:
            session.postgres.rollback()
        except PostgresGatewayError:
            session.postgres.close()
        try:
            self._repository.expire_transaction(owner_id, transaction_id, now)
        except ConsoleRepositoryError:
            pass

    def _drop_session(self, transaction_id: str) -> None:
        with self._active_transactions_lock:
            self._active_transactions.pop(transaction_id, None)

    def _workspace_target(
        self,
        owner_id: str,
        workspace_id: str,
        expected_revision: int,
    ):
        workspace = self._workspace(owner_id, workspace_id)
        if workspace.revision != expected_revision:
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
                "SQL requires a workspace connected to PostgreSQL",
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
        return workspace, ConsoleTarget(
            connection_id=profile.id,
            connection_revision=profile.revision,
            database=workspace.database,
            namespace=workspace.namespace,
        )

    def _validate_settings_revision(self, owner_id: str, revision: int) -> ConsoleSettings:
        preferences = self.settings(owner_id)
        if revision != preferences.revision:
            raise ConsoleServiceError(
                409,
                "console_settings_changed",
                "Console settings changed after the editor loaded",
                details={"currentRevision": preferences.revision},
            )
        return preferences

    @staticmethod
    def _postgres_error(error: PostgresGatewayError) -> ConsoleServiceError:
        if isinstance(error, PostgresConsoleLimitError) and error.limit is not None:
            return ConsoleServiceError(
                422,
                error.code,
                str(error),
                details={
                    "resource": error.resource,
                    "limitName": error.limit_name,
                    "limit": error.limit,
                    "observed": error.observed,
                    "statementIndex": error.statement_index,
                },
                limit_event=LimitEventNotice(
                    resource=error.resource,
                    limit_name=error.limit_name,
                    configured_limit=error.limit,
                    observed_value=error.observed,
                ),
            )
        if isinstance(error, PostgresConnectionCapacityError):
            return ConsoleServiceError(
                503,
                error.code,
                str(error),
                retryable=True,
                details={
                    "resource": "postgres_connections",
                    "limitName": error.limit_name,
                    "limit": error.limit,
                    "observed": error.observed,
                },
                limit_event=LimitEventNotice(
                    resource="postgres_connections",
                    limit_name=error.limit_name,
                    configured_limit=error.limit,
                    observed_value=error.observed,
                ),
            )
        return ConsoleServiceError(502, error.code, str(error))

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
                if isinstance(error, PostgresConsoleLimitError) and error.limit is not None:
                    self._record_limit_event(
                        LimitEventNotice(
                            resource=error.resource,
                            limit_name=error.limit_name,
                            configured_limit=error.limit,
                            observed_value=error.observed,
                        ),
                        error_code=error.code,
                        owner_id=owner_id,
                        workspace_id=current.execution.workspace_id,
                    )
            elif isinstance(error, PostgresGatewayError):
                statement_index = None
                code = error.code
                message = str(error)
                if isinstance(error, PostgresConnectionCapacityError):
                    self._record_limit_event(
                        LimitEventNotice(
                            resource="postgres_connections",
                            limit_name=error.limit_name,
                            configured_limit=error.limit,
                            observed_value=error.observed,
                        ),
                        error_code=error.code,
                        owner_id=owner_id,
                        workspace_id=current.execution.workspace_id,
                    )
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
