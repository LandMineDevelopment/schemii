"""Durable migration execution, recovery, and post-commit synchronization."""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from schemii.common.connections.service import ConnectionService
from schemii.common.connections.store import ConnectionNotFoundError
from schemii.common.postgres import PostgresGateway
from schemii.common.postgres.errors import (
    PostgresCommitUncertainError,
    PostgresGatewayError,
    PostgresMigrationExecutionError,
    PostgresMigrationPreconditionError,
    PostgresMigrationStaleError,
)
from schemii.common.postgres.models import PostgresCatalog
from schemii.schemii.designs.models import SchemiiDesignContent, SchemiiDesignReplace
from schemii.schemii.designs.store import (
    DesignConflictError,
    DesignRepository,
    DesignWorkspaceNotFoundError,
)
from schemii.schemii.workspaces.store import (
    WorkspaceNotFoundError,
    WorkspaceRepository,
)

from .errors import (
    MigrationServiceError,
    migration_repository_conflict,
    migration_storage_error,
)
from .models import (
    MigrationExecution,
    MigrationExecutionCreate,
    MigrationExecutionStatus,
    MigrationReconciliationRequest,
)
from .planner import compile_migration_steps, content_fingerprint, new_baseline_content
from .repository import (
    ExecutionRecord,
    ExecutionReservation,
    ExecutionWork,
    MigrationConflictError,
    MigrationNotFoundError,
    MigrationRepository,
    MigrationStorageUnavailableError,
    PlanRecord,
)


LOGGER = logging.getLogger(__name__)


class MigrationExecutionCoordinator:
    """Own the durable queue and every target-side execution state transition."""

    def __init__(
        self,
        *,
        repository: MigrationRepository,
        connections: ConnectionService,
        postgres: PostgresGateway,
        workspaces: WorkspaceRepository,
        designs: DesignRepository,
        lease_ttl: timedelta = timedelta(minutes=2),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if lease_ttl <= timedelta(0):
            raise ValueError("migration execution lease TTL must be positive")
        self._repository = repository
        self._connections = connections
        self._postgres = postgres
        self._workspaces = workspaces
        self._designs = designs
        self._lease_ttl = lease_ttl
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def lease_ttl(self) -> timedelta:
        return self._lease_ttl

    def reserve(
        self,
        owner_id: str,
        record: PlanRecord,
        request: MigrationExecutionCreate,
    ) -> ExecutionReservation:
        """Persist an idempotent receipt without performing target I/O."""

        try:
            return self._repository.reserve_execution(
                owner_id,
                record.plan.id,
                request.review_digest,
                request.confirm_destructive,
                request.confirm_external_changes,
                reserved_at=self._clock(),
            )
        except MigrationConflictError as error:
            raise migration_repository_conflict(error) from error
        except MigrationStorageUnavailableError as error:
            raise migration_storage_error(error) from error

    def claim_next(self, worker_id: str) -> ExecutionWork | None:
        now = self._clock()
        try:
            return self._repository.claim_next_execution(
                claimed_at=now,
                lease_owner=worker_id,
                lease_expires_at=now + self._lease_ttl,
            )
        except MigrationStorageUnavailableError as error:
            raise migration_storage_error(error) from error

    def renew(self, work: ExecutionWork) -> bool:
        lease_owner = work.record.lease_owner
        if lease_owner is None:
            return False
        try:
            return self._repository.renew_execution_lease(
                work.record.owner_id,
                work.record.execution.id,
                lease_owner=lease_owner,
                lease_expires_at=self._lease_deadline(),
            )
        except MigrationStorageUnavailableError as error:
            raise migration_storage_error(error) from error

    def process(self, work: ExecutionWork) -> MigrationExecution:
        """Run one claimed unit; target DDL is entered only for ``execute`` work."""

        try:
            if work.kind == "execute":
                return self._execute_claimed(work.record)
            if work.kind == "reconcile":
                return self._recover_claimed(work.record)
            if work.kind == "sync":
                record = self._plan(work.record.owner_id, work.record.execution.plan_id)
                return self._sync_committed(work.record.owner_id, record, work.record)
            raise RuntimeError(f"Unsupported migration work kind: {work.kind}")
        except MigrationServiceError as error:
            return self._record_processing_failure(
                work,
                error_code=error.code,
                error_detail=error.details or None,
            )
        except Exception:
            LOGGER.exception(
                "Unexpected migration worker failure",
                extra={"execution_id": work.record.execution.id, "work_kind": work.kind},
            )
            return self._record_processing_failure(
                work,
                error_code="migration_worker_failed",
            )

    def reconcile(
        self,
        owner_id: str,
        execution_id: str,
        request: MigrationReconciliationRequest,
    ) -> MigrationExecution:
        """Manually resolve uncertain commit evidence without replaying target SQL."""

        execution_record = self._execution(owner_id, execution_id)
        execution = execution_record.execution
        if execution.revision != request.expected_execution_revision:
            raise MigrationServiceError(
                409,
                "migration_execution_changed",
                "The migration execution changed after it was opened",
                details={"currentExecutionRevision": execution.revision},
            )
        now = self._clock()
        if (
            execution_record.lease_owner is not None
            and execution_record.lease_expires_at is not None
            and execution_record.lease_expires_at > now
        ):
            raise MigrationServiceError(
                409,
                "migration_execution_lease_active",
                "The migration execution is still owned by an active worker",
                details={"recoveryAvailableAt": execution_record.lease_expires_at},
                retryable=True,
            )
        if execution.commit_outcome == "committed":
            return self._sync_committed(
                owner_id,
                self._plan(owner_id, execution.plan_id),
                execution_record,
            )
        if execution.status not in {
            "reserved",
            "applying",
            "uncertain",
            "reconciliation_required",
        }:
            return execution
        if not execution.transaction_id:
            return self._transition_execution(
                execution_record,
                status="failed",
                commit_outcome="rolled_back",
                error_code="migration_never_started",
                recover_expired=True,
            ).execution
        return self._reconcile_transaction(
            owner_id,
            self._plan(owner_id, execution.plan_id),
            execution_record,
            automatic=False,
        )

    def list_recoverable(self, limit: int = 100) -> list[ExecutionRecord]:
        if not 1 <= limit <= 250:
            raise ValueError("recoverable execution limit must be between 1 and 250")
        return self._repository.list_recoverable_executions(self._clock(), limit)

    def _execute_claimed(self, execution_record: ExecutionRecord) -> MigrationExecution:
        owner_id = execution_record.owner_id
        record = self._plan(owner_id, execution_record.execution.plan_id)
        plan = record.plan
        workspace = self._workspace(owner_id, plan.workspace_id)
        design = self._design(owner_id, plan.workspace_id)
        if (
            workspace.connection_id != record.authority.connection_id
            or workspace.database != record.authority.database
            or workspace.namespace != record.authority.namespace
        ):
            return self._fail_before_apply(
                execution_record,
                "workspace_changed",
                "The workspace target changed after the migration review",
            )
        if design.revision != plan.design_revision or design.fingerprint != plan.design_fingerprint:
            return self._fail_before_apply(
                execution_record,
                "design_changed",
                "The desired design changed after the migration review",
            )
        try:
            baseline = self._repository.current_baseline(owner_id, plan.workspace_id)
        except MigrationStorageUnavailableError as error:
            raise migration_storage_error(error) from error
        if (
            baseline is None
            or baseline.id != record.baseline_id
            or baseline.revision != plan.baseline_revision
        ):
            return self._fail_before_apply(
                execution_record,
                "baseline_changed",
                "The PostgreSQL synchronization baseline changed after the migration review",
                details={
                    "reviewedBaselineId": record.baseline_id,
                    "reviewedBaselineRevision": plan.baseline_revision,
                    "currentBaselineId": baseline.id if baseline else None,
                    "currentBaselineRevision": baseline.revision if baseline else None,
                },
            )

        executor = getattr(self._postgres, "execute_migration", None)
        if not callable(executor):
            return self._fail_before_apply(
                execution_record,
                "postgres_migration_execution_unavailable",
                "The configured PostgreSQL gateway does not support migrations",
                status=503,
            )

        intended: dict[str, Any] = {}

        def on_started(transaction_id: str, target_identity: dict[str, Any]) -> None:
            # The gateway invokes this after its final catalog check and before
            # the first DDL statement. Only ``reserved`` work is replayable.
            nonlocal execution_record
            execution_record = self._transition_execution(
                execution_record,
                status="applying",
                transaction_id=transaction_id,
                target_identity=target_identity,
            )

        def on_intended(catalog: Any) -> None:
            nonlocal execution_record
            candidate, issues, complete = new_baseline_content(
                catalog,
                record.authority.merged_content,
            )
            remaining, blockers = compile_migration_steps(
                record.authority.namespace,
                candidate,
                record.authority.merged_content,
            )
            if not complete or issues or remaining or blockers:
                raise MigrationServiceError(
                    409,
                    "migration_result_mismatch",
                    "PostgreSQL did not produce the complete reviewed schema; the transaction was rolled back",
                    details={
                        "remainingStepCount": len(remaining),
                        "issues": [
                            item.model_dump(mode="json", by_alias=True)
                            for item in issues
                        ],
                        "blockers": [
                            item.model_dump(mode="json", by_alias=True)
                            for item in blockers
                        ],
                    },
                )
            intended.update(
                {
                    "catalog": catalog.model_dump(mode="json"),
                    "content": candidate.model_dump(mode="json"),
                    "catalogFingerprint": catalog.fingerprint,
                    "designFingerprint": content_fingerprint(candidate),
                }
            )
            execution_record = self._transition_execution(
                execution_record,
                status="applying",
                completed_step_count=len(plan.steps),
                intended_result=intended,
            )

        try:
            with self._connections.use(
                owner_id, record.authority.connection_id
            ) as connection:
                if connection.revision != record.authority.connection_revision:
                    return self._fail_before_apply(
                        execution_record,
                        "connection_changed",
                        "The PostgreSQL connection changed after the migration review",
                    )
                execution_arguments: dict[str, Any] = {
                    "on_started": on_started,
                    "on_intended": on_intended,
                }
                if record.authority.required_empty_tables:
                    execution_arguments["required_empty_tables"] = (
                        record.authority.required_empty_tables
                    )
                result = executor(
                    connection,
                    record.authority.namespace,
                    record.authority.live_catalog.fingerprint,
                    [step.sql for step in plan.steps],
                    **execution_arguments,
                )
        except PostgresMigrationStaleError as error:
            self._transition_execution(
                execution_record,
                status="failed",
                commit_outcome="rolled_back",
                error_code=error.code,
                error_detail={"currentCatalogFingerprint": error.current_fingerprint},
            )
            raise MigrationServiceError(
                409,
                error.code,
                str(error),
                details={"currentCatalogFingerprint": error.current_fingerprint},
            ) from error
        except PostgresMigrationPreconditionError as error:
            details = {"tables": list(error.tables)}
            self._transition_execution(
                execution_record,
                status="failed",
                commit_outcome="rolled_back",
                error_code=error.code,
                error_detail=details,
            )
            raise MigrationServiceError(
                409,
                error.code,
                str(error),
                details=details,
            ) from error
        except PostgresCommitUncertainError as error:
            return self._transition_execution(
                execution_record,
                status="uncertain",
                commit_outcome="uncertain",
                error_code=error.code,
                error_detail={"reconciliationRequired": True},
            ).execution
        except MigrationServiceError:
            raise
        except PostgresMigrationExecutionError as error:
            callback_error = error.__cause__
            if isinstance(callback_error, MigrationServiceError):
                if callback_error.code == "migration_result_mismatch":
                    self._transition_execution(
                        execution_record,
                        status="failed",
                        commit_outcome="rolled_back",
                        error_code=callback_error.code,
                        error_detail=callback_error.details,
                    )
                raise callback_error from error
            self._transition_execution(
                execution_record,
                status="failed",
                completed_step_count=error.completed_step_count,
                commit_outcome="rolled_back",
                error_code=error.code,
            )
            raise MigrationServiceError(409, error.code, str(error)) from error
        except (ConnectionNotFoundError, PostgresGatewayError) as error:
            code = getattr(error, "code", "connection_not_found")
            self._transition_execution(
                execution_record,
                status="failed",
                commit_outcome="rolled_back",
                error_code=code,
            )
            raise MigrationServiceError(502, code, str(error), retryable=True) from error

        committed = self._transition_execution(
            execution_record,
            status="succeeded",
            completed_step_count=result.completed_step_count,
            transaction_id=result.transaction_id,
            target_identity=result.target_identity,
            intended_result=intended,
            commit_outcome="committed",
            sync_status="pending",
            error_code=None,
            error_detail=None,
        )
        return self._sync_committed(owner_id, record, committed)

    def _recover_claimed(self, execution_record: ExecutionRecord) -> MigrationExecution:
        execution = execution_record.execution
        if not execution.transaction_id:
            return self._transition_execution(
                execution_record,
                status="failed",
                commit_outcome="rolled_back",
                error_code="migration_never_started",
            ).execution
        return self._reconcile_transaction(
            execution_record.owner_id,
            self._plan(execution_record.owner_id, execution.plan_id),
            execution_record,
            automatic=True,
        )

    def _reconcile_transaction(
        self,
        owner_id: str,
        plan: PlanRecord,
        execution_record: ExecutionRecord,
        *,
        automatic: bool,
    ) -> MigrationExecution:
        execution = execution_record.execution
        assert execution.transaction_id is not None
        status_reader = getattr(self._postgres, "transaction_status", None)
        if not callable(status_reader):
            if automatic:
                return self._transition_execution(
                    execution_record,
                    status="reconciliation_required",
                    commit_outcome="uncertain",
                    error_code="postgres_transaction_status_unavailable",
                ).execution
            raise MigrationServiceError(
                503,
                "postgres_transaction_status_unavailable",
                "The configured PostgreSQL gateway cannot reconcile transactions",
            )
        try:
            with self._connections.use(
                owner_id, plan.authority.connection_id
            ) as connection:
                recovery = status_reader(connection, execution.transaction_id)
        except (ConnectionNotFoundError, PostgresGatewayError) as error:
            if automatic:
                return self._transition_execution(
                    execution_record,
                    status="reconciliation_required",
                    commit_outcome="uncertain",
                    error_code=getattr(error, "code", "postgres_reconciliation_failed"),
                ).execution
            raise MigrationServiceError(
                502,
                getattr(error, "code", "postgres_reconciliation_failed"),
                str(error),
                retryable=True,
            ) from error
        if (
            execution_record.target_identity is None
            or recovery.target_identity != execution_record.target_identity
        ):
            return self._transition_execution(
                execution_record,
                status="reconciliation_required",
                commit_outcome="uncertain",
                error_code="migration_target_identity_changed",
                error_detail={
                    "reconciliationRequired": True,
                    "expectedTargetIdentity": execution_record.target_identity,
                    "currentTargetIdentity": recovery.target_identity,
                },
                recover_expired=not automatic,
            ).execution
        transaction_status = recovery.status
        if transaction_status == "aborted":
            return self._transition_execution(
                execution_record,
                status="failed",
                commit_outcome="rolled_back",
                error_code="migration_transaction_aborted",
                recover_expired=not automatic,
            ).execution
        if transaction_status == "in progress":
            return self._transition_execution(
                execution_record,
                status="applying" if automatic else "reconciliation_required",
                commit_outcome="uncertain",
                error_code="migration_transaction_in_progress",
                recover_expired=not automatic,
            ).execution
        committed = self._transition_execution(
            execution_record,
            status="succeeded",
            commit_outcome="committed",
            sync_status="pending",
            error_code=None,
            error_detail=None,
            recover_expired=not automatic,
        )
        return self._sync_committed(owner_id, plan, committed)

    def _sync_committed(
        self,
        owner_id: str,
        record: PlanRecord,
        execution_record: ExecutionRecord,
    ) -> MigrationExecution:
        execution = execution_record.execution
        intended = execution_record.intended_result
        if not intended:
            return self._transition_execution(
                execution_record,
                status="reconciliation_required",
                commit_outcome="committed",
                sync_status="failed",
                error_code="migration_result_evidence_missing",
            ).execution
        merged = SchemiiDesignContent.model_validate(intended["content"])
        catalog = PostgresCatalog.model_validate_json(
            json.dumps(intended["catalog"], ensure_ascii=False)
        )
        design = self._design(owner_id, record.plan.workspace_id)
        design_conflict = False
        if design.fingerprint != content_fingerprint(merged):
            if (
                design.revision != record.plan.design_revision
                or design.fingerprint != record.plan.design_fingerprint
            ):
                design_conflict = True
            else:
                try:
                    design = self._designs.replace(
                        owner_id,
                        record.plan.workspace_id,
                        SchemiiDesignReplace(
                            expected_design_revision=design.revision,
                            content=merged,
                        ),
                        operation_kind="checkpoint",
                    )
                except DesignConflictError:
                    design_conflict = True
        baseline = self._repository.current_baseline(owner_id, record.plan.workspace_id)
        if baseline is None or baseline.catalog.fingerprint != catalog.fingerprint:
            try:
                self._repository.create_baseline(
                    owner_id=owner_id,
                    workspace_id=record.plan.workspace_id,
                    connection_id=record.authority.connection_id,
                    connection_revision=record.authority.connection_revision,
                    database=record.authority.database,
                    namespace=record.authority.namespace,
                    design_revision=(
                        record.plan.design_revision
                        if design_conflict
                        else design.revision
                    ),
                    content=merged,
                    catalog=catalog,
                    complete=True,
                    issues=[],
                    source="migration",
                    expected_predecessor_id=baseline.id if baseline else None,
                    source_execution_id=execution.id,
                )
            except MigrationConflictError:
                # A concurrent synchronizer may already have recorded the same
                # committed catalog. Only a genuinely different new head is a
                # conflict with this execution's target evidence.
                baseline = self._repository.current_baseline(
                    owner_id, record.plan.workspace_id
                )
                if baseline is None or baseline.catalog.fingerprint != catalog.fingerprint:
                    return self._transition_execution(
                        execution_record,
                        status="succeeded",
                        commit_outcome="committed",
                        sync_status="conflict",
                        error_code="post_commit_baseline_changed",
                    ).execution
        latest_design = self._design(owner_id, record.plan.workspace_id)
        if latest_design.fingerprint != content_fingerprint(merged):
            design_conflict = True
        if design_conflict:
            return self._transition_execution(
                execution_record,
                status="succeeded",
                commit_outcome="committed",
                sync_status="conflict",
                error_code="post_commit_design_changed",
                error_detail={
                    "preservedDesignRevision": latest_design.revision,
                    "baselineAdvanced": True,
                },
            ).execution
        return self._transition_execution(
            execution_record,
            status="succeeded",
            commit_outcome="committed",
            sync_status="succeeded",
            error_code=None,
            error_detail=None,
        ).execution

    def _record_processing_failure(
        self,
        work: ExecutionWork,
        *,
        error_code: str,
        error_detail: dict[str, Any] | None = None,
    ) -> MigrationExecution:
        try:
            current = self._repository.get_execution(
                work.record.owner_id, work.record.execution.id
            )
            if current.lease_owner != work.record.lease_owner:
                return current.execution
            if current.execution.status == "reserved":
                return self._transition_execution(
                    current,
                    status="failed",
                    commit_outcome="rolled_back",
                    error_code=error_code,
                    error_detail={
                        "beforeTargetMutation": True,
                        **(error_detail or {}),
                    },
                ).execution
            if current.execution.status == "applying":
                return self._transition_execution(
                    current,
                    status="reconciliation_required",
                    commit_outcome="uncertain",
                    error_code=error_code,
                    error_detail={
                        "reconciliationRequired": True,
                        **(error_detail or {}),
                    },
                ).execution
            if (
                current.execution.status == "succeeded"
                and current.execution.commit_outcome == "committed"
            ):
                return self._transition_execution(
                    current,
                    status="succeeded",
                    sync_status="failed",
                    error_code=error_code,
                    error_detail=error_detail,
                ).execution
            return current.execution
        except Exception:
            LOGGER.exception(
                "Could not persist migration worker failure",
                extra={"execution_id": work.record.execution.id},
            )
            return work.record.execution

    def _latest_execution(self, record: ExecutionRecord) -> MigrationExecution:
        try:
            return self._repository.get_execution(
                record.owner_id, record.execution.id
            ).execution
        except Exception:
            return record.execution

    def _transition_execution(
        self,
        record: ExecutionRecord,
        *,
        status: MigrationExecutionStatus,
        recover_expired: bool = False,
        **changes: Any,
    ) -> ExecutionRecord:
        effective_sync_status = changes.get(
            "sync_status", record.execution.sync_status
        )
        retains_lease = status in {"reserved", "applying"} or (
            status == "succeeded"
            and effective_sync_status in {"pending", "failed"}
        )
        lease_owner = record.lease_owner
        if retains_lease and lease_owner is None:
            # Manual reconciliation also needs a fence while it records the
            # committed target in metadata. A crash simply lets this lease age
            # out and the normal sync-only worker path resumes the operation.
            lease_owner = f"mls_{secrets.token_hex(16)}"
        try:
            return self._repository.transition_execution(
                record.owner_id,
                record.execution.id,
                expected_revision=record.execution.revision,
                allowed_from={record.execution.status},
                status=status,
                lease_owner=lease_owner,
                lease_expires_at=(
                    self._lease_deadline()
                    if retains_lease
                    else None
                ),
                recover_expired_before=self._clock() if recover_expired else None,
                **changes,
            )
        except MigrationConflictError as error:
            raise migration_repository_conflict(error) from error
        except MigrationStorageUnavailableError as error:
            raise migration_storage_error(error) from error

    def _fail_before_apply(
        self,
        execution_record: ExecutionRecord,
        code: str,
        message: str,
        *,
        status: int = 409,
        details: dict[str, Any] | None = None,
    ) -> MigrationExecution:
        failed = self._transition_execution(
            execution_record,
            status="failed",
            commit_outcome="rolled_back",
            error_code=code,
            error_detail={"beforeTargetMutation": True, **(details or {})},
        )
        # The worker consumes this error and leaves the durable receipt readable.
        raise MigrationServiceError(
            status,
            code,
            message,
            details={"executionId": failed.execution.id, **(details or {})},
        )

    def _workspace(self, owner_id: str, workspace_id: str) -> Any:
        try:
            return self._workspaces.get(owner_id, workspace_id)
        except WorkspaceNotFoundError as error:
            raise MigrationServiceError(404, "workspace_not_found", str(error)) from error

    def _design(self, owner_id: str, workspace_id: str) -> Any:
        try:
            return self._designs.get(owner_id, workspace_id)
        except DesignWorkspaceNotFoundError as error:
            raise MigrationServiceError(404, "workspace_not_found", str(error)) from error

    def _plan(self, owner_id: str, plan_id: str) -> PlanRecord:
        try:
            return self._repository.get_plan(owner_id, plan_id)
        except MigrationNotFoundError as error:
            raise MigrationServiceError(404, "migration_plan_not_found", str(error)) from error

    def _execution(self, owner_id: str, execution_id: str) -> ExecutionRecord:
        try:
            return self._repository.get_execution(owner_id, execution_id)
        except MigrationNotFoundError as error:
            raise MigrationServiceError(
                404, "migration_execution_not_found", str(error)
            ) from error

    def _lease_deadline(self) -> datetime:
        return self._clock() + self._lease_ttl
