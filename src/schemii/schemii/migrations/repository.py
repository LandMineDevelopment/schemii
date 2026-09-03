"""Durable metadata authority for Schemii migration state."""

from __future__ import annotations

import json
import secrets
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import (
    Any,
    Callable,
    Collection,
    ContextManager,
    Iterator,
    Literal,
    Protocol,
    runtime_checkable,
)

from schemii.common.postgres.models import PostgresCatalog
from schemii.schemii.designs.history_retention import prune_postgres_history
from schemii.schemii.designs.models import (
    SchemiiDesignContent,
    SchemiiDesignReplace,
)
from schemii.schemii.designs.store import (
    DesignConflictError,
    DesignRepository,
    authored_content_document,
    design_fingerprint,
    design_object_ids,
)
from schemii.schemii.workspaces.store import WorkspaceImportBaseline

from .models import (
    MigrationDriftResolution,
    MigrationExecution,
    MigrationExecutionStatus,
    MigrationExternalChange,
    MigrationPlan,
)


class MigrationRepositoryError(RuntimeError):
    """Base error for migration metadata operations."""


class MigrationNotFoundError(MigrationRepositoryError):
    pass


class MigrationConflictError(MigrationRepositoryError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(message)


class MigrationStorageUnavailableError(MigrationRepositoryError):
    def __init__(
        self,
        *,
        sqlstate: str | None = None,
        constraint: str | None = None,
        error_type: str | None = None,
    ) -> None:
        self.sqlstate = sqlstate
        self.constraint = constraint
        self.error_type = error_type
        super().__init__("Migration metadata is temporarily unavailable")


@dataclass(frozen=True, slots=True)
class BaselineRecord:
    id: str
    owner_id: str
    workspace_id: str
    revision: int
    predecessor_id: str | None
    connection_id: str
    connection_revision: int
    database: str
    namespace: str
    design_revision: int
    content: SchemiiDesignContent
    catalog: PostgresCatalog
    complete: bool
    issues: list[dict[str, Any]]
    source: str
    source_execution_id: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PlanAuthority:
    baseline_content: SchemiiDesignContent
    desired_content: SchemiiDesignContent
    merged_content: SchemiiDesignContent
    live_content: SchemiiDesignContent
    live_catalog: PostgresCatalog
    allow_destructive: bool
    connection_id: str
    connection_revision: int
    database: str
    namespace: str
    required_empty_tables: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PlanRecord:
    owner_id: str
    baseline_id: str
    plan: MigrationPlan
    authority: PlanAuthority


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    owner_id: str
    execution: MigrationExecution
    confirmed_review_digest: str
    destructive_confirmed: bool
    external_changes_confirmed: bool
    target_identity: dict[str, Any] | None = None
    intended_result: dict[str, Any] | None = None
    error_detail: dict[str, Any] | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ExecutionReservation:
    """Atomic reservation result distinguishing new work from an idempotent read."""

    record: ExecutionRecord
    reserved_now: bool


ExecutionWorkKind = Literal["execute", "reconcile", "sync"]


@dataclass(frozen=True, slots=True)
class ExecutionWork:
    """One durably leased unit of migration work."""

    kind: ExecutionWorkKind
    record: ExecutionRecord


class _Unset:
    __slots__ = ()


UNSET = _Unset()


_LEGAL_EXECUTION_TRANSITIONS: dict[
    MigrationExecutionStatus,
    frozenset[MigrationExecutionStatus],
] = {
    "reserved": frozenset({"applying", "failed"}),
    "applying": frozenset(
        {"applying", "succeeded", "failed", "uncertain", "reconciliation_required"}
    ),
    "uncertain": frozenset({"succeeded", "failed", "reconciliation_required"}),
    "reconciliation_required": frozenset(
        {"succeeded", "failed", "reconciliation_required"}
    ),
    "succeeded": frozenset({"succeeded", "reconciliation_required"}),
    "failed": frozenset(),
}

_ACTIVE_EXECUTION_STATUSES: frozenset[MigrationExecutionStatus] = frozenset(
    {"reserved", "applying", "uncertain", "reconciliation_required"}
)
_ABANDONED_PLAN_STATUSES = frozenset({"reviewable", "blocked", "expired"})


def _blocks_workspace_lifecycle(record: ExecutionRecord) -> bool:
    """Return whether removing/retargeting this workspace could lose authority."""

    execution = record.execution
    return execution.status in _ACTIVE_EXECUTION_STATUSES or (
        execution.status == "succeeded"
        and execution.commit_outcome == "committed"
        and execution.sync_status in {"pending", "failed"}
    )


def _holds_execution_lease(
    status: MigrationExecutionStatus,
    sync_status: str | None,
) -> bool:
    """Keep committed work fenced until its metadata synchronization is settled."""

    return status in {"reserved", "applying"} or (
        status == "succeeded" and sync_status in {"pending", "failed"}
    )


def _validate_execution_transition(
    current: MigrationExecution,
    *,
    expected_revision: int,
    allowed_from: Collection[MigrationExecutionStatus],
    status: MigrationExecutionStatus,
) -> None:
    if current.revision != expected_revision:
        raise MigrationConflictError(
            "migration_execution_changed",
            "The migration execution changed in another request",
            {"currentExecutionRevision": current.revision},
        )
    if (
        current.status not in allowed_from
        or status not in _LEGAL_EXECUTION_TRANSITIONS[current.status]
    ):
        raise MigrationConflictError(
            "migration_execution_transition_invalid",
            f"Migration execution cannot transition from {current.status} to {status}",
            {"currentStatus": current.status, "requestedStatus": status},
        )


def _validate_execution_authorization(
    plan: MigrationPlan,
    *,
    review_digest: str,
    confirm_destructive: bool,
    confirm_external_changes: bool,
) -> None:
    if review_digest != plan.review_digest:
        raise MigrationConflictError(
            "migration_review_changed",
            "Migration review digest does not match",
        )
    if plan.destructive and not confirm_destructive:
        raise MigrationConflictError(
            "destructive_confirmation_required",
            "Destructive changes require confirmation",
        )
    if plan.requires_external_change_acknowledgement and not confirm_external_changes:
        raise MigrationConflictError(
            "external_changes_confirmation_required",
            "Compatible external changes require acknowledgement",
        )


@runtime_checkable
class MigrationRepository(Protocol):
    def current_baseline(self, owner_id: str, workspace_id: str) -> BaselineRecord | None: ...

    def has_active_execution(self, owner_id: str, workspace_id: str) -> bool: ...

    def blocks_workspace_lifecycle(self, owner_id: str, workspace_id: str) -> bool: ...

    def create_baseline(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        connection_id: str,
        connection_revision: int,
        database: str,
        namespace: str,
        design_revision: int,
        content: SchemiiDesignContent,
        catalog: PostgresCatalog,
        complete: bool,
        issues: list[dict[str, Any]],
        source: str,
        expected_predecessor_id: str | None = None,
        source_execution_id: str | None = None,
    ) -> BaselineRecord: ...

    def create_import_baseline(
        self,
        *,
        metadata_cursor: Any | None,
        owner_id: str,
        workspace_id: str,
        connection_id: str,
        database: str,
        namespace: str,
        baseline: WorkspaceImportBaseline,
    ) -> BaselineRecord: ...

    def create_plan(self, record: PlanRecord) -> MigrationPlan: ...
    def get_plan(self, owner_id: str, plan_id: str) -> PlanRecord: ...
    def reserve_execution(
        self,
        owner_id: str,
        plan_id: str,
        review_digest: str,
        confirm_destructive: bool,
        confirm_external_changes: bool,
        *,
        reserved_at: datetime,
    ) -> ExecutionReservation: ...
    def claim_next_execution(
        self,
        *,
        claimed_at: datetime,
        lease_owner: str,
        lease_expires_at: datetime,
    ) -> ExecutionWork | None: ...
    def renew_execution_lease(
        self,
        owner_id: str,
        execution_id: str,
        *,
        lease_owner: str,
        lease_expires_at: datetime,
    ) -> bool: ...
    def get_execution(self, owner_id: str, execution_id: str) -> ExecutionRecord: ...
    def list_executions(self, owner_id: str, workspace_id: str, limit: int) -> list[MigrationExecution]: ...
    def list_recoverable_executions(
        self,
        stale_at: datetime,
        limit: int,
    ) -> list[ExecutionRecord]: ...
    def transition_execution(
        self,
        owner_id: str,
        execution_id: str,
        *,
        expected_revision: int,
        allowed_from: Collection[MigrationExecutionStatus],
        status: MigrationExecutionStatus,
        lease_owner: str | None = None,
        lease_expires_at: datetime | None = None,
        recover_expired_before: datetime | None = None,
        completed_step_count: int | _Unset = UNSET,
        transaction_id: str | None | _Unset = UNSET,
        target_identity: dict[str, Any] | None | _Unset = UNSET,
        intended_result: dict[str, Any] | None | _Unset = UNSET,
        commit_outcome: str | None | _Unset = UNSET,
        sync_status: str | None | _Unset = UNSET,
        error_code: str | None | _Unset = UNSET,
        error_detail: dict[str, Any] | None | _Unset = UNSET,
    ) -> ExecutionRecord: ...
    def reconcile_drift(
        self,
        *,
        record: PlanRecord,
        expected_design_revision: int,
        resolved_content: SchemiiDesignContent,
        live_content: SchemiiDesignContent,
        resolutions: list[dict[str, str]],
    ) -> MigrationDriftResolution: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryMigrationRepository:
    """Process-local implementation used by isolated API tests."""

    def __init__(self, designs: DesignRepository) -> None:
        self._designs = designs
        self._baselines: dict[tuple[str, str], list[BaselineRecord]] = {}
        self._plans: dict[tuple[str, str], PlanRecord] = {}
        self._executions: dict[tuple[str, str], ExecutionRecord] = {}
        self._execution_by_plan: dict[tuple[str, str], str] = {}
        self._workspace_claim_guard: (
            Callable[[str, str], ContextManager[None]] | None
        ) = None
        self._lock = threading.RLock()

    def set_workspace_claim_guard(
        self,
        guard: Callable[[str, str], ContextManager[None]],
    ) -> None:
        self._workspace_claim_guard = guard

    def current_baseline(self, owner_id: str, workspace_id: str) -> BaselineRecord | None:
        with self._lock:
            rows = self._baselines.get((owner_id, workspace_id), [])
            return rows[-1] if rows else None

    def has_active_execution(self, owner_id: str, workspace_id: str) -> bool:
        with self._lock:
            return any(
                candidate_owner == owner_id
                and record.execution.workspace_id == workspace_id
                and record.execution.status in _ACTIVE_EXECUTION_STATUSES
                for (candidate_owner, _), record in self._executions.items()
            )

    def blocks_workspace_lifecycle(self, owner_id: str, workspace_id: str) -> bool:
        with self._lock:
            return any(
                candidate_owner == owner_id
                and record.execution.workspace_id == workspace_id
                and _blocks_workspace_lifecycle(record)
                for (candidate_owner, _), record in self._executions.items()
            )

    def create_baseline(self, **values: Any) -> BaselineRecord:
        with self._lock:
            key = (values["owner_id"], values["workspace_id"])
            rows = self._baselines.setdefault(key, [])
            predecessor = rows[-1] if rows else None
            expected = values.get("expected_predecessor_id")
            if expected != (predecessor.id if predecessor else None):
                raise MigrationConflictError(
                    "baseline_changed",
                    "The workspace synchronization baseline changed",
                    {"currentBaselineId": predecessor.id if predecessor else None},
                )
            record = BaselineRecord(
                id=f"mbl_{secrets.token_hex(16)}",
                owner_id=values["owner_id"],
                workspace_id=values["workspace_id"],
                revision=len(rows) + 1,
                predecessor_id=predecessor.id if predecessor else None,
                connection_id=values["connection_id"],
                connection_revision=values["connection_revision"],
                database=values["database"],
                namespace=values["namespace"],
                design_revision=values["design_revision"],
                content=values["content"].model_copy(deep=True),
                catalog=values["catalog"].model_copy(deep=True),
                complete=values["complete"],
                issues=list(values["issues"]),
                source=values["source"],
                source_execution_id=values.get("source_execution_id"),
                created_at=_now(),
            )
            rows.append(record)
            return record

    def create_import_baseline(
        self,
        *,
        metadata_cursor: Any | None,
        owner_id: str,
        workspace_id: str,
        connection_id: str,
        database: str,
        namespace: str,
        baseline: WorkspaceImportBaseline,
    ) -> BaselineRecord:
        if metadata_cursor is not None:
            raise ValueError("In-memory imports cannot join a PostgreSQL transaction")
        return self.create_baseline(
            owner_id=owner_id,
            workspace_id=workspace_id,
            connection_id=connection_id,
            connection_revision=baseline.connection_revision,
            database=database,
            namespace=namespace,
            design_revision=1,
            content=baseline.content,
            catalog=baseline.catalog,
            complete=baseline.complete,
            issues=baseline.issues,
            source="import",
            expected_predecessor_id=None,
        )

    def create_plan(self, record: PlanRecord) -> MigrationPlan:
        if self._workspace_claim_guard is None:
            return self._create_plan_locked(record)
        with self._workspace_claim_guard(record.owner_id, record.plan.workspace_id):
            return self._create_plan_locked(record)

    def _create_plan_locked(self, record: PlanRecord) -> MigrationPlan:
        with self._lock:
            if any(
                candidate_owner == record.owner_id
                and candidate.execution.workspace_id == record.plan.workspace_id
                and _blocks_workspace_lifecycle(candidate)
                for (candidate_owner, _), candidate in self._executions.items()
            ):
                raise MigrationConflictError(
                    "migration_execution_active",
                    "Finish or reconcile the current migration execution before creating another review",
                )
            expired = [
                key
                for key, candidate in self._plans.items()
                if candidate.owner_id == record.owner_id
                and candidate.plan.status in _ABANDONED_PLAN_STATUSES
                and candidate.plan.expires_at <= record.plan.created_at
                and key not in self._execution_by_plan
            ]
            for key in expired:
                del self._plans[key]
            self._plans[(record.owner_id, record.plan.id)] = record
            return record.plan.model_copy(deep=True)

    def get_plan(self, owner_id: str, plan_id: str) -> PlanRecord:
        with self._lock:
            try:
                return self._plans[(owner_id, plan_id)]
            except KeyError as error:
                raise MigrationNotFoundError("Migration plan was not found") from error

    def reserve_execution(
        self,
        owner_id: str,
        plan_id: str,
        review_digest: str,
        confirm_destructive: bool,
        confirm_external_changes: bool,
        *,
        reserved_at: datetime,
    ) -> ExecutionReservation:
        with self._lock:
            record = self.get_plan(owner_id, plan_id)
            workspace_id = record.plan.workspace_id
        if self._workspace_claim_guard is None:
            return self._reserve_execution_locked(
                owner_id,
                plan_id,
                review_digest,
                confirm_destructive,
                confirm_external_changes,
                reserved_at=reserved_at,
            )
        with self._workspace_claim_guard(owner_id, workspace_id):
            return self._reserve_execution_locked(
                owner_id,
                plan_id,
                review_digest,
                confirm_destructive,
                confirm_external_changes,
                reserved_at=reserved_at,
            )

    def _reserve_execution_locked(
        self,
        owner_id: str,
        plan_id: str,
        review_digest: str,
        confirm_destructive: bool,
        confirm_external_changes: bool,
        *,
        reserved_at: datetime,
    ) -> ExecutionReservation:
        with self._lock:
            record = self.get_plan(owner_id, plan_id)
            plan = record.plan
            _validate_execution_authorization(
                plan,
                review_digest=review_digest,
                confirm_destructive=confirm_destructive,
                confirm_external_changes=confirm_external_changes,
            )
            existing_id = self._execution_by_plan.get((owner_id, plan_id))
            if existing_id:
                return ExecutionReservation(
                    record=self._executions[(owner_id, existing_id)],
                    reserved_now=False,
                )
            if any(
                candidate_owner == owner_id
                and candidate.execution.workspace_id == plan.workspace_id
                and _blocks_workspace_lifecycle(candidate)
                for (candidate_owner, _), candidate in self._executions.items()
            ):
                raise MigrationConflictError(
                    "migration_execution_active",
                    "Finish or reconcile the current migration execution before starting another",
                )
            if plan.expires_at <= reserved_at:
                raise MigrationConflictError(
                    "migration_plan_expired",
                    "Refresh the migration review",
                )
            if plan.status != "reviewable" or not plan.apply_capable:
                raise MigrationConflictError("migration_plan_not_executable", "Migration plan cannot be executed")
            execution = MigrationExecution(
                id=f"mex_{secrets.token_hex(16)}",
                plan_id=plan_id,
                workspace_id=plan.workspace_id,
                revision=1,
                status="reserved",
                completed_step_count=0,
                created_at=reserved_at,
                updated_at=reserved_at,
            )
            execution_record = ExecutionRecord(
                owner_id=owner_id,
                execution=execution,
                confirmed_review_digest=review_digest,
                destructive_confirmed=confirm_destructive,
                external_changes_confirmed=confirm_external_changes,
            )
            self._execution_by_plan[(owner_id, plan_id)] = execution.id
            self._executions[(owner_id, execution.id)] = execution_record
            self._plans[(owner_id, plan_id)] = PlanRecord(
                owner_id=record.owner_id,
                baseline_id=record.baseline_id,
                plan=record.plan.model_copy(update={"status": "claimed"}),
                authority=record.authority,
            )
            return ExecutionReservation(record=execution_record, reserved_now=True)

    def claim_next_execution(
        self,
        *,
        claimed_at: datetime,
        lease_owner: str,
        lease_expires_at: datetime,
    ) -> ExecutionWork | None:
        with self._lock:
            eligible: list[tuple[int, datetime, str, ExecutionRecord, ExecutionWorkKind]] = []
            for record in self._executions.values():
                execution = record.execution
                lease_available = (
                    record.lease_owner is None
                    or (
                        record.lease_expires_at is not None
                        and record.lease_expires_at <= claimed_at
                    )
                )
                if not lease_available:
                    continue
                kind: ExecutionWorkKind | None = None
                priority = 0
                if execution.status == "applying":
                    kind = "reconcile"
                elif execution.status == "reserved":
                    kind = "execute"
                    priority = 1
                elif (
                    execution.status == "succeeded"
                    and execution.commit_outcome == "committed"
                    and execution.sync_status in {"pending", "failed"}
                ):
                    kind = "sync"
                    priority = 2
                if kind is not None:
                    eligible.append(
                        (priority, execution.created_at, execution.id, record, kind)
                    )
            if not eligible:
                return None
            _, _, execution_id, current, kind = min(eligible)
            execution = current.execution.model_copy(
                update={
                    "revision": current.execution.revision + 1,
                    "recovery_available_at": lease_expires_at,
                    "updated_at": claimed_at,
                }
            )
            claimed = ExecutionRecord(
                owner_id=current.owner_id,
                execution=execution,
                confirmed_review_digest=current.confirmed_review_digest,
                destructive_confirmed=current.destructive_confirmed,
                external_changes_confirmed=current.external_changes_confirmed,
                target_identity=current.target_identity,
                intended_result=current.intended_result,
                error_detail=current.error_detail,
                lease_owner=lease_owner,
                lease_expires_at=lease_expires_at,
            )
            self._executions[(current.owner_id, execution_id)] = claimed
            return ExecutionWork(kind=kind, record=claimed)

    def renew_execution_lease(
        self,
        owner_id: str,
        execution_id: str,
        *,
        lease_owner: str,
        lease_expires_at: datetime,
    ) -> bool:
        with self._lock:
            current = self._executions.get((owner_id, execution_id))
            if current is None or current.lease_owner != lease_owner:
                return False
            if current.execution.status not in {"reserved", "applying", "succeeded"}:
                return False
            self._executions[(owner_id, execution_id)] = ExecutionRecord(
                owner_id=current.owner_id,
                execution=current.execution.model_copy(
                    update={"recovery_available_at": lease_expires_at}
                ),
                confirmed_review_digest=current.confirmed_review_digest,
                destructive_confirmed=current.destructive_confirmed,
                external_changes_confirmed=current.external_changes_confirmed,
                target_identity=current.target_identity,
                intended_result=current.intended_result,
                error_detail=current.error_detail,
                lease_owner=lease_owner,
                lease_expires_at=lease_expires_at,
            )
            return True

    def get_execution(self, owner_id: str, execution_id: str) -> ExecutionRecord:
        with self._lock:
            try:
                return self._executions[(owner_id, execution_id)]
            except KeyError as error:
                raise MigrationNotFoundError("Migration execution was not found") from error

    def list_executions(self, owner_id: str, workspace_id: str, limit: int) -> list[MigrationExecution]:
        with self._lock:
            values = [
                item.execution.model_copy(deep=True)
                for (candidate_owner, _), item in self._executions.items()
                if candidate_owner == owner_id and item.execution.workspace_id == workspace_id
            ]
        return sorted(values, key=lambda item: item.created_at, reverse=True)[:limit]

    def list_recoverable_executions(
        self,
        stale_at: datetime,
        limit: int,
    ) -> list[ExecutionRecord]:
        with self._lock:
            values = [
                record
                for record in self._executions.values()
                if (
                    record.execution.status == "reserved"
                    and record.lease_owner is None
                )
                or (
                    record.execution.status in {"reserved", "applying"}
                    and record.lease_expires_at is not None
                    and record.lease_expires_at <= stale_at
                )
                or (
                    record.execution.status == "succeeded"
                    and record.execution.commit_outcome == "committed"
                    and record.execution.sync_status in {"pending", "failed"}
                    and (
                        record.lease_owner is None
                        or (
                            record.lease_expires_at is not None
                            and record.lease_expires_at <= stale_at
                        )
                    )
                )
            ]
        return sorted(
            values,
            key=lambda item: (
                item.lease_expires_at or datetime.min.replace(tzinfo=timezone.utc),
                item.execution.id,
            ),
        )[:limit]

    def transition_execution(
        self,
        owner_id: str,
        execution_id: str,
        *,
        expected_revision: int,
        allowed_from: Collection[MigrationExecutionStatus],
        status: MigrationExecutionStatus,
        lease_owner: str | None = None,
        lease_expires_at: datetime | None = None,
        recover_expired_before: datetime | None = None,
        completed_step_count: int | _Unset = UNSET,
        transaction_id: str | None | _Unset = UNSET,
        target_identity: dict[str, Any] | None | _Unset = UNSET,
        intended_result: dict[str, Any] | None | _Unset = UNSET,
        commit_outcome: str | None | _Unset = UNSET,
        sync_status: str | None | _Unset = UNSET,
        error_code: str | None | _Unset = UNSET,
        error_detail: dict[str, Any] | None | _Unset = UNSET,
    ) -> ExecutionRecord:
        with self._lock:
            current = self.get_execution(owner_id, execution_id)
            _validate_execution_transition(
                current.execution,
                expected_revision=expected_revision,
                allowed_from=allowed_from,
                status=status,
            )
            if current.lease_owner is not None:
                lease_matches = lease_owner is not None and lease_owner == current.lease_owner
                lease_is_stale = (
                    recover_expired_before is not None
                    and current.lease_expires_at is not None
                    and current.lease_expires_at <= recover_expired_before
                )
                if not lease_matches and not lease_is_stale:
                    raise MigrationConflictError(
                        "migration_execution_lease_active",
                        "The migration execution is owned by an active worker lease",
                        {"recoveryAvailableAt": current.lease_expires_at},
                    )
            effective_sync_status = (
                current.execution.sync_status
                if sync_status is UNSET
                else sync_status
            )
            holds_lease = _holds_execution_lease(status, effective_sync_status)
            if holds_lease and (
                lease_owner is None or lease_expires_at is None
            ):
                raise ValueError(
                    "leased migration execution transitions require an owner and expiry"
                )
            next_lease_owner = lease_owner if holds_lease else None
            next_lease_expires_at = lease_expires_at if holds_lease else None
            update = {
                "revision": current.execution.revision + 1,
                "status": status,
                "recovery_available_at": next_lease_expires_at,
                "updated_at": _now(),
            }
            mapping = {
                "completed_step_count": "completed_step_count",
                "transaction_id": "transaction_id",
                "commit_outcome": "commit_outcome",
                "sync_status": "sync_status",
                "error_code": "error_code",
            }
            for source, target in mapping.items():
                value = locals()[source]
                if value is not UNSET:
                    update[target] = value
            update["reconcile_required"] = status in {"uncertain", "reconciliation_required"}
            revised = current.execution.model_copy(update=update)
            record = ExecutionRecord(
                owner_id=owner_id,
                execution=revised,
                confirmed_review_digest=current.confirmed_review_digest,
                destructive_confirmed=current.destructive_confirmed,
                external_changes_confirmed=current.external_changes_confirmed,
                target_identity=(
                    current.target_identity if target_identity is UNSET else target_identity
                ),
                intended_result=(
                    current.intended_result if intended_result is UNSET else intended_result
                ),
                error_detail=(
                    current.error_detail if error_detail is UNSET else error_detail
                ),
                lease_owner=next_lease_owner,
                lease_expires_at=next_lease_expires_at,
            )
            self._executions[(owner_id, execution_id)] = record
            return record

    def reconcile_drift(
        self,
        *,
        record: PlanRecord,
        expected_design_revision: int,
        resolved_content: SchemiiDesignContent,
        live_content: SchemiiDesignContent,
        resolutions: list[dict[str, str]],
    ) -> MigrationDriftResolution:
        if self._workspace_claim_guard is None:
            return self._reconcile_drift_locked(
                record=record,
                expected_design_revision=expected_design_revision,
                resolved_content=resolved_content,
                live_content=live_content,
                resolutions=resolutions,
            )
        with self._workspace_claim_guard(
            record.owner_id, record.plan.workspace_id
        ):
            return self._reconcile_drift_locked(
                record=record,
                expected_design_revision=expected_design_revision,
                resolved_content=resolved_content,
                live_content=live_content,
                resolutions=resolutions,
            )

    def _reconcile_drift_locked(
        self,
        *,
        record: PlanRecord,
        expected_design_revision: int,
        resolved_content: SchemiiDesignContent,
        live_content: SchemiiDesignContent,
        resolutions: list[dict[str, str]],
    ) -> MigrationDriftResolution:
        with self._lock:
            if any(
                candidate_owner == record.owner_id
                and candidate.execution.workspace_id == record.plan.workspace_id
                and _blocks_workspace_lifecycle(candidate)
                for (candidate_owner, _), candidate in self._executions.items()
            ):
                raise MigrationConflictError(
                    "migration_execution_active",
                    "Finish or reconcile the current migration execution before resolving drift",
                )
            design = self._designs.replace(
                record.owner_id,
                record.plan.workspace_id,
                SchemiiDesignReplace(
                    expected_design_revision=expected_design_revision,
                    content=resolved_content,
                ),
                operation_kind="checkpoint",
            )
            baseline = self.create_baseline(
                owner_id=record.owner_id,
                workspace_id=record.plan.workspace_id,
                connection_id=record.authority.connection_id,
                connection_revision=record.authority.connection_revision,
                database=record.authority.database,
                namespace=record.authority.namespace,
                design_revision=design.revision,
                content=live_content,
                catalog=record.authority.live_catalog,
                complete=True,
                issues=[],
                source="drift_reconciliation",
                expected_predecessor_id=record.baseline_id,
            )
            self._plans[(record.owner_id, record.plan.id)] = PlanRecord(
                owner_id=record.owner_id,
                baseline_id=record.baseline_id,
                plan=record.plan.model_copy(update={"status": "resolved"}),
                authority=record.authority,
            )
            return MigrationDriftResolution(
                id=f"mdr_{secrets.token_hex(16)}",
                plan_id=record.plan.id,
                workspace_id=record.plan.workspace_id,
                design_revision=design.revision,
                design_fingerprint=design.fingerprint,
                baseline_revision=baseline.revision,
                catalog_fingerprint=record.authority.live_catalog.fingerprint,
                incorporated_external_changes=record.plan.external_changes,
                created_at=_now(),
            )


class PostgresMigrationRepository:
    """Metadata PostgreSQL implementation with immutable plans and CAS baselines."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def current_baseline(self, owner_id: str, workspace_id: str) -> BaselineRecord | None:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT baseline.*
                    FROM schemii.workspace_schema_baseline_heads AS head
                    JOIN schemii.workspace_schema_baselines AS baseline
                      ON baseline.id = head.baseline_id
                    WHERE head.owner_id = %s AND head.workspace_id = %s
                    """,
                    (owner_id, workspace_id),
                )
                row = cursor.fetchone()
                return self._baseline(row) if row else None

    def has_active_execution(self, owner_id: str, workspace_id: str) -> bool:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM schemii.migration_executions
                        WHERE owner_id = %s AND workspace_id = %s
                          AND status IN (
                              'reserved', 'applying', 'uncertain',
                              'reconciliation_required'
                          )
                    ) AS active
                    """,
                    (owner_id, workspace_id),
                )
                return bool(cursor.fetchone()["active"])

    def blocks_workspace_lifecycle(self, owner_id: str, workspace_id: str) -> bool:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                return self._workspace_lifecycle_blocked(
                    cursor, owner_id, workspace_id
                )

    def create_baseline(self, **values: Any) -> BaselineRecord:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                return self._insert_baseline(cursor, **values)

    def create_import_baseline(
        self,
        *,
        metadata_cursor: Any | None,
        owner_id: str,
        workspace_id: str,
        connection_id: str,
        database: str,
        namespace: str,
        baseline: WorkspaceImportBaseline,
    ) -> BaselineRecord:
        values = {
            "owner_id": owner_id,
            "workspace_id": workspace_id,
            "connection_id": connection_id,
            "connection_revision": baseline.connection_revision,
            "database": database,
            "namespace": namespace,
            "design_revision": 1,
            "content": baseline.content,
            "catalog": baseline.catalog,
            "complete": baseline.complete,
            "issues": baseline.issues,
            "source": "import",
            "expected_predecessor_id": None,
        }
        if metadata_cursor is not None:
            return self._insert_baseline(metadata_cursor, **values)
        return self.create_baseline(**values)

    def create_plan(self, record: PlanRecord) -> MigrationPlan:
        review = record.plan.model_dump(mode="json", by_alias=False)
        authority = self._authority_document(record.authority)
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id
                    FROM schemii.workspaces
                    WHERE owner_id = %s AND id = %s
                    FOR UPDATE
                    """,
                    (record.owner_id, record.plan.workspace_id),
                )
                if cursor.fetchone() is None:
                    raise MigrationNotFoundError("Workspace was not found")
                if self._workspace_lifecycle_blocked(
                    cursor, record.owner_id, record.plan.workspace_id
                ):
                    raise MigrationConflictError(
                        "migration_execution_active",
                        "Finish or reconcile the current migration execution before creating another review",
                    )
                cursor.execute(
                    """
                    DELETE FROM schemii.migration_plans AS plan
                    WHERE plan.owner_id = %s
                      AND plan.expires_at <= %s
                      AND plan.status IN ('reviewable', 'blocked', 'expired')
                      AND NOT EXISTS (
                          SELECT 1
                          FROM schemii.migration_executions AS execution
                          WHERE execution.plan_id = plan.id
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM schemii.drift_reconciliations AS reconciliation
                          WHERE reconciliation.plan_id = plan.id
                      )
                    """,
                    (record.owner_id, record.plan.created_at),
                )
                cursor.execute(
                    """
                    INSERT INTO schemii.migration_plans (
                        id, workspace_id, owner_id, baseline_id, baseline_revision,
                        workspace_revision, design_revision, design_fingerprint,
                        merged_design_fingerprint, connection_id, connection_revision,
                        database_name, namespace, catalog_fingerprint,
                        review_document, authority_document, review_digest, status,
                        complete, apply_capable, destructive, drift_status,
                        created_at, expires_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        record.plan.id,
                        record.plan.workspace_id,
                        record.owner_id,
                        record.baseline_id,
                        record.plan.baseline_revision,
                        record.plan.workspace_revision,
                        record.plan.design_revision,
                        record.plan.design_fingerprint,
                        record.plan.merged_design_fingerprint,
                        record.authority.connection_id,
                        record.authority.connection_revision,
                        record.authority.database,
                        record.authority.namespace,
                        record.plan.catalog_fingerprint,
                        self._json_dump(review),
                        self._json_dump(authority),
                        record.plan.review_digest,
                        record.plan.status,
                        record.plan.complete,
                        record.plan.apply_capable,
                        record.plan.destructive,
                        record.plan.drift_status,
                        record.plan.created_at,
                        record.plan.expires_at,
                    ),
                )
        return record.plan

    def get_plan(self, owner_id: str, plan_id: str) -> PlanRecord:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM schemii.migration_plans WHERE owner_id = %s AND id = %s",
                    (owner_id, plan_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise MigrationNotFoundError("Migration plan was not found")
                return self._plan(row)

    def reserve_execution(
        self,
        owner_id: str,
        plan_id: str,
        review_digest: str,
        confirm_destructive: bool,
        confirm_external_changes: bool,
        *,
        reserved_at: datetime,
    ) -> ExecutionReservation:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT workspace_id, connection_id FROM schemii.migration_plans WHERE owner_id = %s AND id = %s",
                    (owner_id, plan_id),
                )
                authority_row = cursor.fetchone()
                if authority_row is None:
                    raise MigrationNotFoundError("Migration plan was not found")
                cursor.execute(
                    "SELECT id FROM metadata.postgres_connections WHERE owner_id = %s AND id = %s FOR UPDATE",
                    (owner_id, authority_row["connection_id"]),
                )
                if cursor.fetchone() is None:
                    raise MigrationConflictError(
                        "workspace_target_changed",
                        "The migration target connection no longer exists",
                    )
                cursor.execute(
                    "SELECT id FROM schemii.workspaces WHERE owner_id = %s AND id = %s FOR UPDATE",
                    (owner_id, authority_row["workspace_id"]),
                )
                if cursor.fetchone() is None:
                    raise MigrationNotFoundError("Migration plan was not found")
                cursor.execute(
                    "SELECT * FROM schemii.migration_plans WHERE owner_id = %s AND id = %s FOR UPDATE",
                    (owner_id, plan_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise MigrationNotFoundError("Migration plan was not found")
                plan = self._plan(row).plan
                _validate_execution_authorization(
                    plan,
                    review_digest=review_digest,
                    confirm_destructive=confirm_destructive,
                    confirm_external_changes=confirm_external_changes,
                )
                cursor.execute(
                    "SELECT * FROM schemii.migration_executions WHERE owner_id = %s AND plan_id = %s",
                    (owner_id, plan_id),
                )
                existing = cursor.fetchone()
                if existing:
                    return ExecutionReservation(
                        record=self._execution(cursor, existing),
                        reserved_now=False,
                    )
                cursor.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM schemii.migration_executions AS execution
                        LEFT JOIN schemii.migration_syncs AS sync
                          ON sync.execution_id = execution.id
                        WHERE execution.owner_id = %s
                          AND execution.workspace_id = %s
                          AND (
                              execution.status IN (
                                  'reserved', 'applying', 'uncertain',
                                  'reconciliation_required'
                              )
                              OR (
                                  execution.status = 'succeeded'
                                  AND execution.commit_outcome = 'committed'
                                  AND sync.status IN ('pending', 'failed')
                              )
                          )
                    ) AS active
                    """,
                    (owner_id, plan.workspace_id),
                )
                if cursor.fetchone()["active"]:
                    raise MigrationConflictError(
                        "migration_execution_active",
                        "Finish or reconcile the current migration execution before starting another",
                    )
                if plan.expires_at <= reserved_at:
                    raise MigrationConflictError(
                        "migration_plan_expired",
                        "Refresh the migration review",
                    )
                if plan.status != "reviewable" or not plan.apply_capable:
                    raise MigrationConflictError("migration_plan_not_executable", "Migration plan cannot be executed")
                execution_id = f"mex_{secrets.token_hex(16)}"
                cursor.execute(
                    """
                    INSERT INTO schemii.migration_executions (
                        id, plan_id, workspace_id, owner_id, status,
                        confirmed_review_digest, destructive_confirmed,
                        external_changes_confirmed, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, 'reserved', %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        execution_id,
                        plan.id,
                        plan.workspace_id,
                        owner_id,
                        review_digest,
                        confirm_destructive,
                        confirm_external_changes,
                        reserved_at,
                        reserved_at,
                    ),
                )
                execution_row = cursor.fetchone()
                cursor.execute("UPDATE schemii.migration_plans SET status = 'claimed' WHERE id = %s", (plan_id,))
                cursor.execute(
                    """
                    INSERT INTO schemii.migration_execution_transitions
                        (execution_id, from_status, to_status)
                    VALUES (%s, NULL, 'reserved')
                    """,
                    (execution_id,),
                )
                return ExecutionReservation(
                    record=self._execution(cursor, execution_row),
                    reserved_now=True,
                )

    def claim_next_execution(
        self,
        *,
        claimed_at: datetime,
        lease_owner: str,
        lease_expires_at: datetime,
    ) -> ExecutionWork | None:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT execution.*,
                           sync.status AS sync_status,
                           CASE
                               WHEN execution.status = 'applying' THEN 'reconcile'
                               WHEN execution.status = 'reserved' THEN 'execute'
                               ELSE 'sync'
                           END AS work_kind
                    FROM schemii.migration_executions AS execution
                    LEFT JOIN schemii.migration_syncs AS sync
                      ON sync.execution_id = execution.id
                    WHERE (
                            execution.status = 'applying'
                            AND execution.lease_expires_at <= %s
                          )
                       OR (
                            execution.status = 'reserved'
                            AND (
                                execution.lease_owner IS NULL
                                OR execution.lease_expires_at <= %s
                            )
                          )
                       OR (
                            execution.status = 'succeeded'
                            AND execution.commit_outcome = 'committed'
                            AND sync.status IN ('pending', 'failed')
                            AND (
                                execution.lease_owner IS NULL
                                OR execution.lease_expires_at <= %s
                            )
                          )
                    ORDER BY
                        CASE execution.status
                            WHEN 'applying' THEN 0
                            WHEN 'reserved' THEN 1
                            ELSE 2
                        END,
                        execution.created_at,
                        execution.id
                    FOR UPDATE OF execution SKIP LOCKED
                    LIMIT 1
                    """,
                    (claimed_at, claimed_at, claimed_at),
                )
                candidate = cursor.fetchone()
                if candidate is None:
                    return None
                work_kind: ExecutionWorkKind = candidate.pop("work_kind")
                candidate.pop("sync_status", None)
                cursor.execute(
                    """
                    UPDATE schemii.migration_executions
                    SET revision = revision + 1,
                        lease_owner = %s,
                        lease_expires_at = %s,
                        updated_at = %s
                    WHERE id = %s AND revision = %s
                    RETURNING *
                    """,
                    (
                        lease_owner,
                        lease_expires_at,
                        claimed_at,
                        candidate["id"],
                        candidate["revision"],
                    ),
                )
                claimed = cursor.fetchone()
                if claimed is None:
                    return None
                return ExecutionWork(
                    kind=work_kind,
                    record=self._execution(cursor, claimed),
                )

    def renew_execution_lease(
        self,
        owner_id: str,
        execution_id: str,
        *,
        lease_owner: str,
        lease_expires_at: datetime,
    ) -> bool:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE schemii.migration_executions
                    SET lease_expires_at = %s
                    WHERE owner_id = %s
                      AND id = %s
                      AND lease_owner = %s
                      AND status IN ('reserved', 'applying', 'succeeded')
                    """,
                    (lease_expires_at, owner_id, execution_id, lease_owner),
                )
                return cursor.rowcount == 1

    def get_execution(self, owner_id: str, execution_id: str) -> ExecutionRecord:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM schemii.migration_executions WHERE owner_id = %s AND id = %s",
                    (owner_id, execution_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise MigrationNotFoundError("Migration execution was not found")
                return self._execution(cursor, row)

    def list_executions(self, owner_id: str, workspace_id: str, limit: int) -> list[MigrationExecution]:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT execution.*, sync.status AS sync_status
                    FROM schemii.migration_executions AS execution
                    LEFT JOIN schemii.migration_syncs AS sync ON sync.execution_id = execution.id
                    WHERE execution.owner_id = %s AND execution.workspace_id = %s
                    ORDER BY execution.created_at DESC, execution.id DESC
                    LIMIT %s
                    """,
                    (owner_id, workspace_id, limit),
                )
                return [self._public_execution(row) for row in cursor.fetchall()]

    def list_recoverable_executions(
        self,
        stale_at: datetime,
        limit: int,
    ) -> list[ExecutionRecord]:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT execution.*
                    FROM schemii.migration_executions AS execution
                    LEFT JOIN schemii.migration_syncs AS sync
                      ON sync.execution_id = execution.id
                    WHERE (
                            execution.status = 'reserved'
                            AND execution.lease_owner IS NULL
                          )
                       OR (
                            execution.status IN ('reserved', 'applying')
                            AND execution.lease_expires_at <= %s
                          )
                       OR (
                            execution.status = 'succeeded'
                            AND execution.commit_outcome = 'committed'
                            AND sync.status IN ('pending', 'failed')
                            AND (
                                execution.lease_owner IS NULL
                                OR execution.lease_expires_at <= %s
                            )
                          )
                    ORDER BY execution.lease_expires_at NULLS FIRST, execution.id
                    LIMIT %s
                    """,
                    (stale_at, stale_at, limit),
                )
                return [self._execution(cursor, row) for row in cursor.fetchall()]

    def transition_execution(
        self,
        owner_id: str,
        execution_id: str,
        *,
        expected_revision: int,
        allowed_from: Collection[MigrationExecutionStatus],
        status: MigrationExecutionStatus,
        lease_owner: str | None = None,
        lease_expires_at: datetime | None = None,
        recover_expired_before: datetime | None = None,
        completed_step_count: int | _Unset = UNSET,
        transaction_id: str | None | _Unset = UNSET,
        target_identity: dict[str, Any] | None | _Unset = UNSET,
        intended_result: dict[str, Any] | None | _Unset = UNSET,
        commit_outcome: str | None | _Unset = UNSET,
        sync_status: str | None | _Unset = UNSET,
        error_code: str | None | _Unset = UNSET,
        error_detail: dict[str, Any] | None | _Unset = UNSET,
    ) -> ExecutionRecord:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM schemii.migration_executions WHERE owner_id = %s AND id = %s FOR UPDATE",
                    (owner_id, execution_id),
                )
                current = cursor.fetchone()
                if current is None:
                    raise MigrationNotFoundError("Migration execution was not found")
                current_record = self._execution(cursor, current)
                _validate_execution_transition(
                    current_record.execution,
                    expected_revision=expected_revision,
                    allowed_from=allowed_from,
                    status=status,
                )
                if current_record.lease_owner is not None:
                    lease_matches = (
                        lease_owner is not None
                        and lease_owner == current_record.lease_owner
                    )
                    lease_is_stale = (
                        recover_expired_before is not None
                        and current_record.lease_expires_at is not None
                        and current_record.lease_expires_at <= recover_expired_before
                    )
                    if not lease_matches and not lease_is_stale:
                        raise MigrationConflictError(
                            "migration_execution_lease_active",
                            "The migration execution is owned by an active worker lease",
                            {"recoveryAvailableAt": current_record.lease_expires_at},
                        )
                effective_sync_status = (
                    current_record.execution.sync_status
                    if sync_status is UNSET
                    else sync_status
                )
                holds_lease = _holds_execution_lease(status, effective_sync_status)
                if holds_lease and (
                    lease_owner is None or lease_expires_at is None
                ):
                    raise ValueError(
                        "leased migration execution transitions require an owner and expiry"
                    )
                next_lease_owner = lease_owner if holds_lease else None
                next_lease_expires_at = lease_expires_at if holds_lease else None
                assignments = [
                    "revision = revision + 1",
                    "status = %s",
                    "lease_owner = %s",
                    "lease_expires_at = %s",
                    "updated_at = clock_timestamp()",
                ]
                parameters: list[Any] = [
                    status,
                    next_lease_owner,
                    next_lease_expires_at,
                ]
                scalar_updates = (
                    ("completed_step_count", completed_step_count),
                    ("target_xid", transaction_id),
                    ("commit_outcome", commit_outcome),
                    ("error_code", error_code),
                )
                for column, value in scalar_updates:
                    if value is UNSET:
                        continue
                    assignments.append(f"{column} = %s")
                    parameters.append(value)
                json_updates = (
                    ("target_identity", target_identity),
                    ("intended_result", intended_result),
                    ("error_detail", error_detail),
                )
                for column, value in json_updates:
                    if value is UNSET:
                        continue
                    assignments.append(f"{column} = %s::jsonb")
                    parameters.append(
                        self._json_dump(value) if value is not None else None
                    )
                parameters.extend((owner_id, execution_id, expected_revision))
                cursor.execute(
                    f"""
                    UPDATE schemii.migration_executions
                    SET {", ".join(assignments)}
                    WHERE owner_id = %s AND id = %s AND revision = %s
                    RETURNING *
                    """,
                    parameters,
                )
                row = cursor.fetchone()
                if row is None:
                    raise MigrationConflictError(
                        "migration_execution_changed",
                        "The migration execution changed in another request",
                    )
                cursor.execute(
                    """
                    INSERT INTO schemii.migration_execution_transitions
                        (execution_id, from_status, to_status, evidence)
                    VALUES (%s, %s, %s, %s::jsonb)
                    """,
                    (
                        execution_id,
                        current["status"],
                        status,
                        (
                            self._json_dump(error_detail)
                            if error_detail is not UNSET and error_detail is not None
                            else None
                        ),
                    ),
                )
                if sync_status is not UNSET and sync_status is None:
                    cursor.execute(
                        "DELETE FROM schemii.migration_syncs WHERE execution_id = %s",
                        (execution_id,),
                    )
                elif sync_status is not UNSET:
                    cursor.execute(
                        """
                        INSERT INTO schemii.migration_syncs (execution_id, status)
                        VALUES (%s, %s)
                        ON CONFLICT (execution_id) DO UPDATE
                        SET status = EXCLUDED.status, updated_at = clock_timestamp()
                        """,
                        (execution_id, sync_status),
                    )
                return self._execution(cursor, row)

    def reconcile_drift(
        self,
        *,
        record: PlanRecord,
        expected_design_revision: int,
        resolved_content: SchemiiDesignContent,
        live_content: SchemiiDesignContent,
        resolutions: list[dict[str, str]],
    ) -> MigrationDriftResolution:
        serialized = self._json_dump(authored_content_document(resolved_content))
        fingerprint = design_fingerprint(resolved_content)
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                # Match execution reservation's lock order so target changes,
                # workspace lifecycle changes, and drift resolution cannot
                # cross after their respective authority checks.
                cursor.execute(
                    """
                    SELECT id
                    FROM metadata.postgres_connections
                    WHERE owner_id = %s AND id = %s
                    FOR UPDATE
                    """,
                    (record.owner_id, record.authority.connection_id),
                )
                if cursor.fetchone() is None:
                    raise MigrationConflictError(
                        "workspace_target_changed",
                        "The migration target connection no longer exists",
                    )
                cursor.execute(
                    """
                    SELECT id
                    FROM schemii.workspaces
                    WHERE owner_id = %s AND id = %s
                    FOR UPDATE
                    """,
                    (record.owner_id, record.plan.workspace_id),
                )
                if cursor.fetchone() is None:
                    raise MigrationNotFoundError("Workspace was not found")
                if self._workspace_lifecycle_blocked(
                    cursor, record.owner_id, record.plan.workspace_id
                ):
                    raise MigrationConflictError(
                        "migration_execution_active",
                        "Finish or reconcile the current migration execution before resolving drift",
                    )
                cursor.execute(
                    "SELECT status, review_digest FROM schemii.migration_plans WHERE owner_id = %s AND id = %s FOR UPDATE",
                    (record.owner_id, record.plan.id),
                )
                plan_row = cursor.fetchone()
                if plan_row is None:
                    raise MigrationNotFoundError("Migration plan was not found")
                if plan_row["status"] != "blocked":
                    raise MigrationConflictError("drift_plan_not_resolvable", "Migration plan is not awaiting drift resolution")
                cursor.execute(
                    "SELECT revision, content FROM schemii.workspace_designs WHERE owner_id = %s AND workspace_id = %s FOR UPDATE",
                    (record.owner_id, record.plan.workspace_id),
                )
                design_row = cursor.fetchone()
                if design_row is None:
                    raise MigrationNotFoundError("Workspace design was not found")
                if design_row["revision"] != expected_design_revision:
                    raise MigrationConflictError(
                        "design_changed",
                        "The workspace design changed after the drift review",
                        {"currentDesignRevision": design_row["revision"]},
                    )
                cursor.execute(
                    """
                    SELECT cursor_id FROM schemii.workspace_design_history_state
                    WHERE owner_id = %s AND workspace_id = %s FOR UPDATE
                    """,
                    (record.owner_id, record.plan.workspace_id),
                )
                history_state = cursor.fetchone()
                if history_state is None:
                    current_content = SchemiiDesignContent.model_validate(
                        self._json_load(design_row["content"])
                    )
                    cursor.execute(
                        """
                        INSERT INTO schemii.workspace_design_history_entries (
                            workspace_id, owner_id, parent_id, source_design_revision,
                            operation_kind, content, fingerprint
                        ) VALUES (%s, %s, NULL, %s, 'initial', %s::jsonb, %s)
                        RETURNING id
                        """,
                        (
                            record.plan.workspace_id,
                            record.owner_id,
                            expected_design_revision,
                            self._json_dump(authored_content_document(current_content)),
                            design_fingerprint(current_content),
                        ),
                    )
                    history_cursor_id = int(cursor.fetchone()["id"])
                    cursor.execute(
                        """
                        INSERT INTO schemii.workspace_design_history_state (
                            workspace_id, owner_id, cursor_id, active_tip_id
                        ) VALUES (%s, %s, %s, %s)
                        """,
                        (
                            record.plan.workspace_id,
                            record.owner_id,
                            history_cursor_id,
                            history_cursor_id,
                        ),
                    )
                else:
                    history_cursor_id = int(history_state["cursor_id"])
                next_design_revision = expected_design_revision + 1
                cursor.execute(
                    """
                    UPDATE schemii.workspace_designs
                    SET revision = %s, content = %s::jsonb, fingerprint = %s,
                        updated_at = clock_timestamp()
                    WHERE owner_id = %s AND workspace_id = %s
                    """,
                    (next_design_revision, serialized, fingerprint, record.owner_id, record.plan.workspace_id),
                )
                cursor.execute(
                    """
                    INSERT INTO schemii.workspace_design_history_entries (
                        workspace_id, owner_id, parent_id, source_design_revision,
                        operation_kind, content, fingerprint
                    ) VALUES (%s, %s, %s, %s, 'checkpoint', %s::jsonb, %s)
                    RETURNING id
                    """,
                    (
                        record.plan.workspace_id,
                        record.owner_id,
                        history_cursor_id,
                        next_design_revision,
                        serialized,
                        fingerprint,
                    ),
                )
                checkpoint_id = int(cursor.fetchone()["id"])
                cursor.execute(
                    """
                    UPDATE schemii.workspace_design_history_state
                    SET cursor_id = %s, active_tip_id = %s,
                        updated_at = clock_timestamp()
                    WHERE owner_id = %s AND workspace_id = %s
                    """,
                    (
                        checkpoint_id,
                        checkpoint_id,
                        record.owner_id,
                        record.plan.workspace_id,
                    ),
                )
                prune_postgres_history(
                    cursor,
                    record.owner_id,
                    record.plan.workspace_id,
                )
                cursor.execute(
                    "SELECT revision, objects FROM schemii.workspace_design_layouts WHERE owner_id = %s AND workspace_id = %s FOR UPDATE",
                    (record.owner_id, record.plan.workspace_id),
                )
                layout = cursor.fetchone()
                objects = self._json_load(layout["objects"])
                for item in objects:
                    object_id = item.get("objectId", item.get("object_id"))
                    if not object_id:
                        continue
                    cursor.execute(
                        """
                        INSERT INTO schemii.workspace_design_position_memory (
                            workspace_id, owner_id, object_id, layer, x, y
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (owner_id, workspace_id, object_id) DO UPDATE
                        SET layer = EXCLUDED.layer, x = EXCLUDED.x, y = EXCLUDED.y,
                            updated_at = clock_timestamp()
                        """,
                        (
                            record.plan.workspace_id,
                            record.owner_id,
                            object_id,
                            item["layer"],
                            item["x"],
                            item["y"],
                        ),
                    )
                allowed = design_object_ids(resolved_content)
                retained = [item for item in objects if allowed.get(item.get("objectId", item.get("object_id"))) == item.get("layer")]
                retained_ids = {
                    item.get("objectId", item.get("object_id")) for item in retained
                }
                cursor.execute(
                    """
                    SELECT object_id, layer, x, y
                    FROM schemii.workspace_design_position_memory
                    WHERE owner_id = %s AND workspace_id = %s
                    ORDER BY updated_at, object_id
                    """,
                    (record.owner_id, record.plan.workspace_id),
                )
                for memory in cursor.fetchall():
                    object_id = memory["object_id"]
                    if object_id not in retained_ids and allowed.get(object_id) == memory["layer"]:
                        retained.append(
                            {
                                "object_id": object_id,
                                "layer": memory["layer"],
                                "x": memory["x"],
                                "y": memory["y"],
                            }
                        )
                cursor.execute(
                    """
                    UPDATE schemii.workspace_design_layouts
                    SET revision = revision + 1, design_revision = %s,
                        objects = %s::jsonb, updated_at = clock_timestamp()
                    WHERE owner_id = %s AND workspace_id = %s
                    """,
                    (next_design_revision, self._json_dump(retained), record.owner_id, record.plan.workspace_id),
                )
                baseline = self._insert_baseline(
                    cursor,
                    owner_id=record.owner_id,
                    workspace_id=record.plan.workspace_id,
                    connection_id=record.authority.connection_id,
                    connection_revision=record.authority.connection_revision,
                    database=record.authority.database,
                    namespace=record.authority.namespace,
                    design_revision=next_design_revision,
                    content=live_content,
                    catalog=record.authority.live_catalog,
                    complete=True,
                    issues=[],
                    source="drift_reconciliation",
                    expected_predecessor_id=record.baseline_id,
                )
                reconciliation_id = f"mdr_{secrets.token_hex(16)}"
                cursor.execute(
                    """
                    INSERT INTO schemii.drift_reconciliations (
                        id, plan_id, workspace_id, owner_id, review_digest,
                        previous_design_revision, design_revision,
                        previous_baseline_revision, baseline_revision,
                        catalog_fingerprint, resolutions, external_changes
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                    """,
                    (
                        reconciliation_id,
                        record.plan.id,
                        record.plan.workspace_id,
                        record.owner_id,
                        record.plan.review_digest,
                        expected_design_revision,
                        next_design_revision,
                        record.plan.baseline_revision,
                        baseline.revision,
                        record.authority.live_catalog.fingerprint,
                        self._json_dump(resolutions),
                        self._json_dump([item.model_dump(mode="json") for item in record.plan.external_changes]),
                    ),
                )
                cursor.execute("UPDATE schemii.migration_plans SET status = 'resolved' WHERE id = %s", (record.plan.id,))
                return MigrationDriftResolution(
                    id=reconciliation_id,
                    plan_id=record.plan.id,
                    workspace_id=record.plan.workspace_id,
                    design_revision=next_design_revision,
                    design_fingerprint=fingerprint,
                    baseline_revision=baseline.revision,
                    catalog_fingerprint=record.authority.live_catalog.fingerprint,
                    incorporated_external_changes=record.plan.external_changes,
                    created_at=_now(),
                )

    @staticmethod
    def _workspace_lifecycle_blocked(
        cursor: Any,
        owner_id: str,
        workspace_id: str,
    ) -> bool:
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM schemii.migration_executions AS execution
                LEFT JOIN schemii.migration_syncs AS sync
                  ON sync.execution_id = execution.id
                WHERE execution.owner_id = %s
                  AND execution.workspace_id = %s
                  AND (
                      execution.status IN (
                          'reserved', 'applying', 'uncertain',
                          'reconciliation_required'
                      )
                      OR (
                          execution.status = 'succeeded'
                          AND execution.commit_outcome = 'committed'
                          AND sync.status IN ('pending', 'failed')
                      )
                  )
            ) AS active
            """,
            (owner_id, workspace_id),
        )
        return bool(cursor.fetchone()["active"])

    def _insert_baseline(self, cursor: Any, **values: Any) -> BaselineRecord:
        cursor.execute(
            "SELECT baseline_id, baseline_revision FROM schemii.workspace_schema_baseline_heads WHERE owner_id = %s AND workspace_id = %s FOR UPDATE",
            (values["owner_id"], values["workspace_id"]),
        )
        head = cursor.fetchone()
        current_id = head["baseline_id"] if head else None
        if values.get("expected_predecessor_id") != current_id:
            raise MigrationConflictError(
                "baseline_changed",
                "The workspace synchronization baseline changed",
                {"currentBaselineId": current_id},
            )
        revision = int(head["baseline_revision"]) + 1 if head else 1
        baseline_id = f"mbl_{secrets.token_hex(16)}"
        cursor.execute(
            """
            INSERT INTO schemii.workspace_schema_baselines (
                id, workspace_id, owner_id, revision, predecessor_id,
                connection_id, connection_revision, database_name, namespace,
                design_revision, design_fingerprint, design_content,
                catalog_fingerprint, catalog, complete, issues, source,
                source_execution_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                      %s, %s::jsonb, %s, %s::jsonb, %s, %s)
            RETURNING *
            """,
            (
                baseline_id,
                values["workspace_id"],
                values["owner_id"],
                revision,
                current_id,
                values["connection_id"],
                values["connection_revision"],
                values["database"],
                values["namespace"],
                values["design_revision"],
                design_fingerprint(values["content"]),
                self._json_dump(authored_content_document(values["content"])),
                values["catalog"].fingerprint,
                self._json_dump(values["catalog"].model_dump(mode="json")),
                values["complete"],
                self._json_dump(values["issues"]),
                values["source"],
                values.get("source_execution_id"),
            ),
        )
        row = cursor.fetchone()
        cursor.execute(
            """
            INSERT INTO schemii.workspace_schema_baseline_heads
                (workspace_id, owner_id, baseline_id, baseline_revision)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (workspace_id) DO UPDATE
            SET baseline_id = EXCLUDED.baseline_id,
                baseline_revision = EXCLUDED.baseline_revision,
                updated_at = clock_timestamp()
            """,
            (values["workspace_id"], values["owner_id"], baseline_id, revision),
        )
        return self._baseline(row)

    @staticmethod
    def _authority_document(authority: PlanAuthority) -> dict[str, Any]:
        return {
            "baselineContent": authored_content_document(authority.baseline_content),
            "desiredContent": authored_content_document(authority.desired_content),
            "mergedContent": authored_content_document(authority.merged_content),
            "liveContent": authored_content_document(authority.live_content),
            "liveCatalog": authority.live_catalog.model_dump(mode="json"),
            "allowDestructive": authority.allow_destructive,
            "connectionId": authority.connection_id,
            "connectionRevision": authority.connection_revision,
            "database": authority.database,
            "namespace": authority.namespace,
            "requiredEmptyTables": list(authority.required_empty_tables),
        }

    def _baseline(self, row: dict[str, Any]) -> BaselineRecord:
        return BaselineRecord(
            id=row["id"], owner_id=row["owner_id"], workspace_id=row["workspace_id"],
            revision=row["revision"], predecessor_id=row["predecessor_id"],
            connection_id=row["connection_id"], connection_revision=row["connection_revision"],
            database=row["database_name"], namespace=row["namespace"],
            design_revision=row["design_revision"],
            content=SchemiiDesignContent.model_validate(self._json_load(row["design_content"])),
            catalog=PostgresCatalog.model_validate_json(
                self._json_dump(self._json_load(row["catalog"]))
            ),
            complete=row["complete"], issues=self._json_load(row["issues"]), source=row["source"],
            source_execution_id=row["source_execution_id"], created_at=row["created_at"],
        )

    def _plan(self, row: dict[str, Any]) -> PlanRecord:
        review = self._json_load(row["review_document"])
        review["status"] = row["status"]
        plan = MigrationPlan.model_validate(review)
        authority = self._json_load(row["authority_document"])
        return PlanRecord(
            owner_id=row["owner_id"], baseline_id=row["baseline_id"], plan=plan,
            authority=PlanAuthority(
                baseline_content=SchemiiDesignContent.model_validate(authority["baselineContent"]),
                desired_content=SchemiiDesignContent.model_validate(authority["desiredContent"]),
                merged_content=SchemiiDesignContent.model_validate(authority["mergedContent"]),
                live_content=SchemiiDesignContent.model_validate(authority["liveContent"]),
                live_catalog=PostgresCatalog.model_validate_json(
                    self._json_dump(authority["liveCatalog"])
                ),
                allow_destructive=authority["allowDestructive"],
                connection_id=authority["connectionId"],
                connection_revision=authority["connectionRevision"],
                database=authority["database"], namespace=authority["namespace"],
                required_empty_tables=tuple(authority.get("requiredEmptyTables", ())),
            ),
        )

    def _execution(self, cursor: Any, row: dict[str, Any]) -> ExecutionRecord:
        cursor.execute("SELECT status FROM schemii.migration_syncs WHERE execution_id = %s", (row["id"],))
        sync = cursor.fetchone()
        copied = dict(row)
        copied["sync_status"] = sync["status"] if sync else None
        return ExecutionRecord(
            owner_id=row["owner_id"], execution=self._public_execution(copied),
            confirmed_review_digest=row["confirmed_review_digest"],
            destructive_confirmed=row["destructive_confirmed"],
            external_changes_confirmed=row["external_changes_confirmed"],
            target_identity=self._json_load(row["target_identity"]) if row["target_identity"] is not None else None,
            intended_result=self._json_load(row["intended_result"]) if row["intended_result"] is not None else None,
            error_detail=(
                self._json_load(row["error_detail"])
                if row["error_detail"] is not None
                else None
            ),
            lease_owner=row["lease_owner"],
            lease_expires_at=row["lease_expires_at"],
        )

    @staticmethod
    def _public_execution(row: dict[str, Any]) -> MigrationExecution:
        return MigrationExecution(
            id=row["id"], plan_id=row["plan_id"], workspace_id=row["workspace_id"],
            revision=row["revision"], status=row["status"],
            completed_step_count=row["completed_step_count"], transaction_id=row["target_xid"],
            commit_outcome=row["commit_outcome"], sync_status=row.get("sync_status"),
            error_code=row["error_code"],
            reconcile_required=row["status"] in {"uncertain", "reconciliation_required"},
            recovery_available_at=(
                row.get("lease_expires_at")
                if row["status"] in {"reserved", "applying"}
                or (
                    row["status"] == "succeeded"
                    and row.get("sync_status") in {"pending", "failed"}
                )
                else None
            ),
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _json_dump(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _json_load(value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) else value

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        try:
            connection = self._connection_factory()
        except Exception as error:
            raise MigrationStorageUnavailableError() from error
        try:
            yield connection
            connection.commit()
        except MigrationRepositoryError:
            connection.rollback()
            raise
        except Exception as error:
            connection.rollback()
            diagnostic = getattr(error, "diag", None)
            raise MigrationStorageUnavailableError(
                sqlstate=getattr(error, "sqlstate", None),
                constraint=getattr(diagnostic, "constraint_name", None),
                error_type=type(error).__name__,
            ) from error
        finally:
            connection.close()
