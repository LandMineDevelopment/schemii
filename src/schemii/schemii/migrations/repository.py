"""Durable metadata authority for Schemii migration state."""

from __future__ import annotations

import json
import secrets
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Protocol, runtime_checkable

from schemii.common.postgres.models import PostgresCatalog
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

from .models import (
    MigrationDriftResolution,
    MigrationExecution,
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


@runtime_checkable
class MigrationRepository(Protocol):
    def current_baseline(self, owner_id: str, workspace_id: str) -> BaselineRecord | None: ...

    def has_active_execution(self, owner_id: str, workspace_id: str) -> bool: ...

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

    def create_plan(self, record: PlanRecord) -> MigrationPlan: ...
    def get_plan(self, owner_id: str, plan_id: str) -> PlanRecord: ...
    def claim_execution(
        self,
        owner_id: str,
        plan_id: str,
        review_digest: str,
        confirm_destructive: bool,
        confirm_external_changes: bool,
    ) -> ExecutionRecord: ...
    def get_execution(self, owner_id: str, execution_id: str) -> ExecutionRecord: ...
    def list_executions(self, owner_id: str, workspace_id: str, limit: int) -> list[MigrationExecution]: ...
    def update_execution(
        self,
        owner_id: str,
        execution_id: str,
        *,
        status: str,
        completed_step_count: int | None = None,
        transaction_id: str | None = None,
        target_identity: dict[str, Any] | None = None,
        intended_result: dict[str, Any] | None = None,
        commit_outcome: str | None = None,
        sync_status: str | None = None,
        error_code: str | None = None,
        error_detail: dict[str, Any] | None = None,
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
        self._lock = threading.RLock()

    def current_baseline(self, owner_id: str, workspace_id: str) -> BaselineRecord | None:
        with self._lock:
            rows = self._baselines.get((owner_id, workspace_id), [])
            return rows[-1] if rows else None

    def has_active_execution(self, owner_id: str, workspace_id: str) -> bool:
        with self._lock:
            return any(
                candidate_owner == owner_id
                and record.execution.workspace_id == workspace_id
                and record.execution.status
                in {"reserved", "applying", "uncertain", "reconciliation_required"}
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

    def create_plan(self, record: PlanRecord) -> MigrationPlan:
        with self._lock:
            self._plans[(record.owner_id, record.plan.id)] = record
            return record.plan.model_copy(deep=True)

    def get_plan(self, owner_id: str, plan_id: str) -> PlanRecord:
        with self._lock:
            try:
                return self._plans[(owner_id, plan_id)]
            except KeyError as error:
                raise MigrationNotFoundError("Migration plan was not found") from error

    def claim_execution(
        self,
        owner_id: str,
        plan_id: str,
        review_digest: str,
        confirm_destructive: bool,
        confirm_external_changes: bool,
    ) -> ExecutionRecord:
        with self._lock:
            existing_id = self._execution_by_plan.get((owner_id, plan_id))
            if existing_id:
                return self._executions[(owner_id, existing_id)]
            record = self.get_plan(owner_id, plan_id)
            plan = record.plan
            if plan.status != "reviewable" or not plan.apply_capable:
                raise MigrationConflictError("migration_plan_not_executable", "Migration plan cannot be executed")
            if review_digest != plan.review_digest:
                raise MigrationConflictError("migration_review_changed", "Migration review digest does not match")
            if plan.destructive and not confirm_destructive:
                raise MigrationConflictError("destructive_confirmation_required", "Destructive changes require confirmation")
            if plan.requires_external_change_acknowledgement and not confirm_external_changes:
                raise MigrationConflictError("external_changes_confirmation_required", "Compatible external changes require acknowledgement")
            now = _now()
            execution = MigrationExecution(
                id=f"mex_{secrets.token_hex(16)}",
                plan_id=plan_id,
                workspace_id=plan.workspace_id,
                revision=1,
                status="reserved",
                completed_step_count=0,
                created_at=now,
                updated_at=now,
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
            return execution_record

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

    def update_execution(self, owner_id: str, execution_id: str, **changes: Any) -> ExecutionRecord:
        with self._lock:
            current = self.get_execution(owner_id, execution_id)
            update = {
                "revision": current.execution.revision + 1,
                "status": changes["status"],
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
                if changes.get(source) is not None:
                    update[target] = changes[source]
            update["reconcile_required"] = changes["status"] in {"uncertain", "reconciliation_required"}
            revised = current.execution.model_copy(update=update)
            record = ExecutionRecord(
                owner_id=owner_id,
                execution=revised,
                confirmed_review_digest=current.confirmed_review_digest,
                destructive_confirmed=current.destructive_confirmed,
                external_changes_confirmed=current.external_changes_confirmed,
                target_identity=changes.get("target_identity") or current.target_identity,
                intended_result=changes.get("intended_result") or current.intended_result,
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
        with self._lock:
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

    def create_baseline(self, **values: Any) -> BaselineRecord:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                return self._insert_baseline(cursor, **values)

    def create_plan(self, record: PlanRecord) -> MigrationPlan:
        review = record.plan.model_dump(mode="json", by_alias=False)
        authority = self._authority_document(record.authority)
        with self._transaction() as connection:
            with connection.cursor() as cursor:
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

    def claim_execution(
        self,
        owner_id: str,
        plan_id: str,
        review_digest: str,
        confirm_destructive: bool,
        confirm_external_changes: bool,
    ) -> ExecutionRecord:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM schemii.migration_plans WHERE owner_id = %s AND id = %s FOR UPDATE",
                    (owner_id, plan_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise MigrationNotFoundError("Migration plan was not found")
                cursor.execute(
                    "SELECT * FROM schemii.migration_executions WHERE owner_id = %s AND plan_id = %s",
                    (owner_id, plan_id),
                )
                existing = cursor.fetchone()
                if existing:
                    return self._execution(cursor, existing)
                plan = self._plan(row).plan
                if plan.status != "reviewable" or not plan.apply_capable:
                    raise MigrationConflictError("migration_plan_not_executable", "Migration plan cannot be executed")
                if review_digest != plan.review_digest:
                    raise MigrationConflictError("migration_review_changed", "Migration review digest does not match")
                if plan.destructive and not confirm_destructive:
                    raise MigrationConflictError("destructive_confirmation_required", "Destructive changes require confirmation")
                if plan.requires_external_change_acknowledgement and not confirm_external_changes:
                    raise MigrationConflictError("external_changes_confirmation_required", "Compatible external changes require acknowledgement")
                execution_id = f"mex_{secrets.token_hex(16)}"
                cursor.execute(
                    """
                    INSERT INTO schemii.migration_executions (
                        id, plan_id, workspace_id, owner_id, status,
                        confirmed_review_digest, destructive_confirmed,
                        external_changes_confirmed
                    ) VALUES (%s, %s, %s, %s, 'reserved', %s, %s, %s)
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
                return self._execution(cursor, execution_row)

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

    def update_execution(self, owner_id: str, execution_id: str, **changes: Any) -> ExecutionRecord:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM schemii.migration_executions WHERE owner_id = %s AND id = %s FOR UPDATE",
                    (owner_id, execution_id),
                )
                current = cursor.fetchone()
                if current is None:
                    raise MigrationNotFoundError("Migration execution was not found")
                cursor.execute(
                    """
                    UPDATE schemii.migration_executions
                    SET revision = revision + 1,
                        status = %s,
                        completed_step_count = COALESCE(%s, completed_step_count),
                        target_xid = COALESCE(%s, target_xid),
                        target_identity = COALESCE(%s::jsonb, target_identity),
                        intended_result = COALESCE(%s::jsonb, intended_result),
                        commit_outcome = COALESCE(%s, commit_outcome),
                        error_code = COALESCE(%s, error_code),
                        error_detail = COALESCE(%s::jsonb, error_detail),
                        updated_at = clock_timestamp()
                    WHERE owner_id = %s AND id = %s
                    RETURNING *
                    """,
                    (
                        changes["status"],
                        changes.get("completed_step_count"),
                        changes.get("transaction_id"),
                        self._json_dump(changes.get("target_identity")) if changes.get("target_identity") is not None else None,
                        self._json_dump(changes.get("intended_result")) if changes.get("intended_result") is not None else None,
                        changes.get("commit_outcome"),
                        changes.get("error_code"),
                        self._json_dump(changes.get("error_detail")) if changes.get("error_detail") is not None else None,
                        owner_id,
                        execution_id,
                    ),
                )
                row = cursor.fetchone()
                cursor.execute(
                    """
                    INSERT INTO schemii.migration_execution_transitions
                        (execution_id, from_status, to_status, evidence)
                    VALUES (%s, %s, %s, %s::jsonb)
                    """,
                    (
                        execution_id,
                        current["status"],
                        changes["status"],
                        self._json_dump(changes.get("error_detail")) if changes.get("error_detail") is not None else None,
                    ),
                )
                if changes.get("sync_status") is not None:
                    cursor.execute(
                        """
                        INSERT INTO schemii.migration_syncs (execution_id, status)
                        VALUES (%s, %s)
                        ON CONFLICT (execution_id) DO UPDATE
                        SET status = EXCLUDED.status, updated_at = clock_timestamp()
                        """,
                        (execution_id, changes["sync_status"]),
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
