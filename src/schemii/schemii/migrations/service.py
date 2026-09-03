"""Server-owned migration planning, drift reconciliation, and execution orchestration."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from schemii.common.connections.service import ConnectionService
from schemii.common.connections.store import ConnectionNotFoundError
from schemii.common.postgres import PostgresGateway
from schemii.common.postgres.errors import PostgresGatewayError
from schemii.common.postgres.models import PostgresCatalog
from schemii.schemii.designs.importer import ImportedDesign
from schemii.schemii.designs.history import design_change_summary
from schemii.schemii.designs.models import (
    DesignBaselineResetPreview,
    DesignBaselineResetRequest,
    DesignHistoryBaseline,
    DesignHistoryMutation,
    DesignHistoryState,
    DesignHistoryTransitionRequest,
    DesignWorkspaceSnapshot,
    SchemiiDesignContent,
    SchemiiDesignReplace,
)
from schemii.schemii.designs.store import (
    DesignConflictError,
    DesignHistoryBoundaryError,
    DesignMutationBlockedError,
    DesignRepository,
    DesignValidationError,
    DesignWorkspaceNotFoundError,
    design_fingerprint,
)
from schemii.schemii.workspaces.store import (
    WorkspaceDesignBootstrap,
    WorkspaceImportBaseline,
    WorkspaceImportTargetChangedError,
    WorkspaceNotFoundError,
    WorkspaceRepository,
)
from schemii.schemii.workspaces.models import SchemiiWorkspace, SchemiiWorkspaceCreate

from .errors import MigrationServiceError, migration_storage_error
from .execution import MigrationExecutionCoordinator
from .models import (
    MigrationDriftResolution,
    MigrationDriftResolutionRequest,
    MigrationExecution,
    MigrationExecutionCreate,
    MigrationPlan,
    MigrationPlanCreate,
    MigrationReconciliationRequest,
    MigrationWarning,
)
from .planner import (
    compile_migration_steps,
    content_fingerprint,
    new_baseline_content,
    random_plan_id,
    reconcile_designs,
    tables_requiring_empty_for_required_columns,
)
from .repository import (
    ExecutionRecord,
    MigrationConflictError,
    MigrationNotFoundError,
    MigrationRepository,
    MigrationStorageUnavailableError,
    PlanAuthority,
    PlanRecord,
)


def _install_workspace_execution_guards(
    workspaces: WorkspaceRepository,
    repository: MigrationRepository,
) -> None:
    workspace_guard = getattr(workspaces, "set_mutation_guard", None)
    if callable(workspace_guard):
        workspace_guard(repository.blocks_workspace_lifecycle)
    claim_guard = getattr(workspaces, "execution_claim_guard", None)
    install_claim_guard = getattr(repository, "set_workspace_claim_guard", None)
    if callable(claim_guard) and callable(install_claim_guard):
        install_claim_guard(claim_guard)


def _create_import_workspace(
    workspaces: WorkspaceRepository,
    baselines: MigrationRepository,
    owner_id: str,
    request: SchemiiWorkspaceCreate,
    imported: ImportedDesign,
    connection_revision: int,
    catalog: PostgresCatalog,
) -> SchemiiWorkspace:
    """Build and persist one imported design through its atomic repository boundary."""

    baseline_content, warnings, complete = new_baseline_content(
        catalog,
        imported.content,
    )
    try:
        return workspaces.create_import(
            owner_id,
            request,
            bootstrap=WorkspaceDesignBootstrap(
                content=imported.content,
                layout=imported.layout,
                import_summary=imported.summary,
            ),
            baseline=WorkspaceImportBaseline(
                connection_revision=connection_revision,
                content=baseline_content,
                catalog=catalog,
                complete=complete,
                issues=[
                    warning.model_dump(mode="json", by_alias=True)
                    for warning in warnings
                ],
            ),
            baselines=baselines,
        )
    except WorkspaceImportTargetChangedError as error:
        raise MigrationServiceError(
            409,
            "connection_changed_during_import",
            str(error),
            details={
                "expectedRevision": error.expected_revision,
                "currentRevision": error.current_revision,
                "expectedDatabase": error.expected_database,
                "currentDatabase": error.current_database,
            },
        ) from error
    except MigrationStorageUnavailableError as error:
        raise migration_storage_error(error) from error


class MigrationService:
    """Own every validation decision from live inspection through reconciliation."""

    def __init__(
        self,
        *,
        repository: MigrationRepository,
        connections: ConnectionService,
        postgres: PostgresGateway,
        workspaces: WorkspaceRepository,
        designs: DesignRepository,
        plan_ttl: timedelta = timedelta(minutes=15),
        execution_lease_ttl: timedelta = timedelta(minutes=2),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if plan_ttl <= timedelta(0):
            raise ValueError("migration plan TTL must be positive")
        if execution_lease_ttl <= timedelta(0):
            raise ValueError("migration execution lease TTL must be positive")
        self._repository = repository
        self._connections = connections
        self._postgres = postgres
        self._workspaces = workspaces
        self._designs = designs
        self._plan_ttl = plan_ttl
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._execution_waker: Callable[[], None] | None = None
        self._execution_coordinator = MigrationExecutionCoordinator(
            repository=repository,
            connections=connections,
            postgres=postgres,
            workspaces=workspaces,
            designs=designs,
            lease_ttl=execution_lease_ttl,
            clock=self._clock,
        )
        _install_workspace_execution_guards(self._workspaces, self._repository)

    @property
    def execution_coordinator(self) -> MigrationExecutionCoordinator:
        return self._execution_coordinator

    def set_execution_waker(self, waker: Callable[[], None] | None) -> None:
        self._execution_waker = waker

    def create_import_workspace(
        self,
        owner_id: str,
        request: SchemiiWorkspaceCreate,
        imported: ImportedDesign,
        connection_revision: int,
        catalog: PostgresCatalog,
    ) -> SchemiiWorkspace:
        """Atomically persist one inspected catalog as a targeted design baseline."""

        return _create_import_workspace(
            self._workspaces,
            self._repository,
            owner_id,
            request,
            imported,
            connection_revision,
            catalog,
        )

    def design_history_state(self, owner_id: str, workspace_id: str) -> DesignHistoryState:
        """Return the durable cursor relative to the server-owned sync point."""

        self._workspace(owner_id, workspace_id)
        baseline, _ = self._design_baseline(owner_id, workspace_id)
        return self._designs.history_state(owner_id, workspace_id, baseline)

    def design_snapshot(
        self,
        owner_id: str,
        workspace_id: str,
    ) -> DesignWorkspaceSnapshot:
        """Return all detached-design client state from one repository snapshot."""

        self._workspace(owner_id, workspace_id)
        baseline, _ = self._design_baseline(owner_id, workspace_id)
        return self._designs.snapshot(owner_id, workspace_id, baseline)

    def undo_design(
        self,
        owner_id: str,
        workspace_id: str,
        request: DesignHistoryTransitionRequest,
    ) -> DesignHistoryMutation:
        return self._move_design_history(owner_id, workspace_id, request, "undo")

    def redo_design(
        self,
        owner_id: str,
        workspace_id: str,
        request: DesignHistoryTransitionRequest,
    ) -> DesignHistoryMutation:
        return self._move_design_history(owner_id, workspace_id, request, "redo")

    def preview_baseline_reset(
        self, owner_id: str, workspace_id: str
    ) -> DesignBaselineResetPreview:
        """Derive the exact metadata-only reset without mutating either database."""

        self._workspace(owner_id, workspace_id)
        design = self._design(owner_id, workspace_id)
        baseline, target = self._design_baseline(owner_id, workspace_id)
        if not baseline.complete:
            raise MigrationServiceError(
                409,
                "baseline_reset_incomplete",
                "The PostgreSQL baseline is incomplete and cannot be restored losslessly",
            )
        summary = design_change_summary(design.content, target)
        if summary.change_count == 0:
            raise MigrationServiceError(
                409,
                "design_already_at_baseline",
                "The desired design already matches its baseline",
            )
        digest = self._baseline_reset_digest(
            workspace_id, design.revision, design.fingerprint, baseline, summary
        )
        return DesignBaselineResetPreview(
            design_revision=design.revision,
            baseline=baseline,
            summary=summary,
            review_digest=digest,
        )

    def reset_design_to_baseline(
        self,
        owner_id: str,
        workspace_id: str,
        request: DesignBaselineResetRequest,
    ) -> DesignHistoryMutation:
        """Apply exactly the reviewed reset to desired metadata; PostgreSQL is untouched."""

        preview = self.preview_baseline_reset(owner_id, workspace_id)
        if (
            request.expected_design_revision != preview.design_revision
            or request.baseline_id != preview.baseline.id
            or request.baseline_revision != preview.baseline.revision
            or request.review_digest != preview.review_digest
        ):
            raise MigrationServiceError(
                409,
                "baseline_reset_review_changed",
                "The design or baseline changed after the reset was reviewed",
                details={
                    "currentDesignRevision": preview.design_revision,
                    "currentBaselineId": preview.baseline.id,
                    "currentBaselineRevision": preview.baseline.revision,
                },
            )
        _, target = self._design_baseline(owner_id, workspace_id)
        try:
            self._designs.replace(
                owner_id,
                workspace_id,
                SchemiiDesignReplace(
                    expected_design_revision=request.expected_design_revision,
                    content=target,
                ),
                operation_kind="baseline_reset",
                expected_baseline_id=(
                    preview.baseline.id if preview.baseline.kind == "postgresql" else None
                ),
            )
        except DesignConflictError as error:
            raise MigrationServiceError(
                409,
                "design_changed",
                "The desired design changed after the reset was reviewed",
                details={"currentDesignRevision": error.current_revision},
            ) from error
        except DesignMutationBlockedError as error:
            raise MigrationServiceError(409, "design_mutation_blocked", str(error)) from error
        except DesignValidationError as error:
            code = "baseline_changed" if error.details.get("reason") == "baseline_changed" else "invalid_design"
            raise MigrationServiceError(409, code, str(error), details=error.details) from error
        return self._history_mutation(owner_id, workspace_id)

    def create_plan(
        self,
        owner_id: str,
        workspace_id: str,
        request: MigrationPlanCreate,
    ) -> MigrationPlan:
        workspace = self._workspace(owner_id, workspace_id)
        if workspace.mode != "design":
            raise MigrationServiceError(
                409,
                "editable_design_required",
                "Migration planning requires an editable targeted design workspace",
            )
        if workspace.connection_id is None or workspace.database is None or workspace.namespace is None:
            raise MigrationServiceError(
                409,
                "workspace_target_required",
                "Attach a PostgreSQL target before planning a migration",
            )
        try:
            execution_unsettled = self._repository.blocks_workspace_lifecycle(
                owner_id, workspace_id
            )
        except MigrationStorageUnavailableError as error:
            raise self._storage_error(error) from error
        if execution_unsettled:
            raise MigrationServiceError(
                409,
                "migration_execution_active",
                "Finish or reconcile the current migration execution before creating another review",
            )
        design = self._design(owner_id, workspace_id)
        if design.revision != request.expected_design_revision:
            raise MigrationServiceError(
                409,
                "design_changed",
                "The desired design changed after it was opened",
                details={"currentDesignRevision": design.revision},
            )

        try:
            with self._connections.use(owner_id, workspace.connection_id) as connection:
                if connection.database != workspace.database:
                    raise MigrationServiceError(
                        409,
                        "workspace_target_changed",
                        "The connection no longer targets the workspace database",
                    )
                catalog = self._postgres.introspect(connection, workspace.namespace)
                connection_revision = connection.revision
        except ConnectionNotFoundError as error:
            raise MigrationServiceError(404, "connection_not_found", str(error)) from error
        except PostgresGatewayError as error:
            raise MigrationServiceError(502, "postgres_inspection_failed", str(error), retryable=True) from error

        if (
            request.expected_catalog_fingerprint is not None
            and request.expected_catalog_fingerprint != catalog.fingerprint
        ):
            raise MigrationServiceError(
                409,
                "catalog_changed",
                "PostgreSQL changed after the catalog shown in the browser",
                details={"currentCatalogFingerprint": catalog.fingerprint},
            )

        baseline = self._repository.current_baseline(owner_id, workspace_id)
        if baseline is None:
            baseline_content, baseline_warnings, baseline_complete = new_baseline_content(
                catalog,
                design.content,
            )
            try:
                baseline = self._repository.create_baseline(
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    connection_id=workspace.connection_id,
                    connection_revision=connection_revision,
                    database=workspace.database,
                    namespace=workspace.namespace,
                    design_revision=design.revision,
                    content=baseline_content,
                    catalog=catalog,
                    complete=baseline_complete,
                    issues=[warning.model_dump(mode="json") for warning in baseline_warnings],
                    source="import" if workspace.import_summary is not None else "target_attach",
                    expected_predecessor_id=None,
                )
            except MigrationStorageUnavailableError as error:
                raise self._storage_error(error) from error
        else:
            self._validate_baseline_target(
                baseline.connection_id,
                baseline.database,
                baseline.namespace,
                workspace.connection_id,
                workspace.database,
                workspace.namespace,
            )

        reconciliation = reconcile_designs(
            baseline.content,
            design.content,
            catalog,
        )
        merged = reconciliation.merged or design.content
        required_empty_tables = tables_requiring_empty_for_required_columns(
            reconciliation.live,
            merged,
        )
        empty_tables: frozenset[str] = frozenset()
        if required_empty_tables:
            try:
                with self._connections.use(owner_id, workspace.connection_id) as connection:
                    if connection.revision != connection_revision:
                        raise MigrationServiceError(
                            409,
                            "connection_changed",
                            "The PostgreSQL connection changed during migration review",
                        )
                    emptiness = self._postgres.table_emptiness(
                        connection,
                        workspace.namespace,
                        tuple(required_empty_tables),
                    )
                    empty_tables = frozenset(
                        name for name, is_empty in emptiness.items() if is_empty
                    )
            except ConnectionNotFoundError as error:
                raise MigrationServiceError(404, "connection_not_found", str(error)) from error
            except PostgresGatewayError as error:
                raise MigrationServiceError(
                    502,
                    "postgres_emptiness_check_failed",
                    str(error),
                    retryable=True,
                ) from error
        steps, compiler_blockers = compile_migration_steps(
            workspace.namespace,
            reconciliation.live,
            merged,
            empty_tables=empty_tables,
        )
        warnings = list(reconciliation.warnings)
        if reconciliation.external_changes:
            warnings.insert(
                0,
                MigrationWarning(
                    code="compatible_external_changes",
                    message=(
                        f"PostgreSQL contains {len(reconciliation.external_changes)} compatible "
                        "external change(s). They will be preserved and incorporated into the workspace."
                    ),
                    object_path="schema",
                ),
            )
        blocking = list(compiler_blockers)
        if any(step.destructive for step in steps) and not request.allow_destructive:
            blocking.append(
                MigrationWarning(
                    code="destructive_review_required",
                    message="Enable destructive changes to review and authorize this complete migration",
                    object_path="schema",
                )
            )
        if reconciliation.conflicts:
            blocking.append(
                MigrationWarning(
                    code="external_change_conflict",
                    message=(
                        f"Resolve {len(reconciliation.conflicts)} conflict(s) between the "
                        "workspace and PostgreSQL before applying"
                    ),
                    object_path="schema",
                )
            )

        drift_status = (
            "conflicting"
            if reconciliation.conflicts
            else "compatible"
            if reconciliation.external_changes
            else "none"
        )
        complete = not reconciliation.conflicts and not compiler_blockers
        destructive = any(step.destructive for step in steps)
        apply_capable = complete and (request.allow_destructive or not destructive)
        now = self._clock()
        plan_id = random_plan_id()
        digest_document = {
            "version": 1,
            "planId": plan_id,
            "workspaceId": workspace_id,
            "workspaceRevision": workspace.revision,
            "designRevision": design.revision,
            "designFingerprint": design.fingerprint,
            "baselineId": baseline.id,
            "baselineRevision": baseline.revision,
            "catalogFingerprint": catalog.fingerprint,
            "mergedDesignFingerprint": content_fingerprint(merged),
            "connectionId": workspace.connection_id,
            "connectionRevision": connection_revision,
            "database": workspace.database,
            "namespace": workspace.namespace,
            "allowDestructive": request.allow_destructive,
            "requiredEmptyTables": sorted(empty_tables),
            "steps": [step.model_dump(mode="json", by_alias=True) for step in steps],
            "externalChanges": [
                item.model_dump(mode="json", by_alias=True)
                for item in reconciliation.external_changes
            ],
            "conflicts": [
                item.model_dump(mode="json", by_alias=True)
                for item in reconciliation.conflicts
            ],
            "warnings": [item.model_dump(mode="json", by_alias=True) for item in warnings],
            "blockingDifferences": [
                item.model_dump(mode="json", by_alias=True) for item in blocking
            ],
        }
        review_digest = hashlib.sha256(
            json.dumps(
                digest_document,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        plan = MigrationPlan(
            id=plan_id,
            workspace_id=workspace_id,
            status="reviewable" if apply_capable else "blocked",
            workspace_revision=workspace.revision,
            design_revision=design.revision,
            design_fingerprint=design.fingerprint,
            baseline_revision=baseline.revision,
            catalog_fingerprint=catalog.fingerprint,
            merged_design_fingerprint=content_fingerprint(merged),
            review_digest=review_digest,
            drift_status=drift_status,
            complete=complete,
            apply_capable=apply_capable,
            destructive=destructive,
            requires_external_change_acknowledgement=bool(reconciliation.external_changes),
            steps=steps,
            external_changes=reconciliation.external_changes,
            conflicts=reconciliation.conflicts,
            warnings=warnings,
            blocking_differences=blocking,
            created_at=now,
            expires_at=now + self._plan_ttl,
        )
        try:
            return self._repository.create_plan(
                PlanRecord(
                    owner_id=owner_id,
                    baseline_id=baseline.id,
                    plan=plan,
                    authority=PlanAuthority(
                        baseline_content=baseline.content,
                        desired_content=design.content,
                        merged_content=merged,
                        live_content=reconciliation.live,
                        live_catalog=catalog,
                        allow_destructive=request.allow_destructive,
                        connection_id=workspace.connection_id,
                        connection_revision=connection_revision,
                        database=workspace.database,
                        namespace=workspace.namespace,
                        required_empty_tables=tuple(sorted(empty_tables)),
                    ),
                )
            )
        except MigrationConflictError as error:
            raise self._repository_conflict(error) from error
        except MigrationStorageUnavailableError as error:
            raise self._storage_error(error) from error

    def get_plan(self, owner_id: str, plan_id: str) -> MigrationPlan:
        record = self._plan(owner_id, plan_id)
        if record.plan.status in {"reviewable", "blocked"} and record.plan.expires_at <= self._clock():
            return record.plan.model_copy(update={"status": "expired", "apply_capable": False})
        return record.plan

    def resolve_drift(
        self,
        owner_id: str,
        plan_id: str,
        request: MigrationDriftResolutionRequest,
    ) -> MigrationDriftResolution:
        record = self._plan(owner_id, plan_id)
        plan = record.plan
        if plan.status != "blocked" or not plan.conflicts:
            raise MigrationServiceError(
                409,
                "drift_plan_not_resolvable",
                "This migration plan is not awaiting drift resolution",
            )
        if plan.expires_at <= self._clock():
            raise MigrationServiceError(409, "migration_plan_expired", "Refresh the migration review")
        if request.review_digest != plan.review_digest:
            raise MigrationServiceError(409, "migration_review_changed", "Migration review digest does not match")
        if request.expected_design_revision != plan.design_revision:
            raise MigrationServiceError(
                409,
                "design_changed",
                "The requested design revision does not match the drift review",
                details={"reviewedDesignRevision": plan.design_revision},
            )
        conflict_by_id = {conflict.id: conflict for conflict in plan.conflicts}
        submitted = {item.conflict_id: item.resolution for item in request.resolutions}
        if set(submitted) != set(conflict_by_id):
            raise MigrationServiceError(
                422,
                "incomplete_drift_resolutions",
                "Every server-generated conflict must be resolved exactly once",
                details={
                    "requiredConflictIds": sorted(conflict_by_id),
                    "submittedConflictIds": sorted(submitted),
                },
            )
        for identifier, resolution in submitted.items():
            if resolution not in conflict_by_id[identifier].allowed_resolutions:
                raise MigrationServiceError(
                    422,
                    "drift_resolution_not_allowed",
                    "The selected resolution is not valid for this conflict",
                    details={"conflictId": identifier, "resolution": resolution},
                )

        workspace = self._workspace(owner_id, plan.workspace_id)
        if (
            workspace.connection_id != record.authority.connection_id
            or workspace.database != record.authority.database
            or workspace.namespace != record.authority.namespace
        ):
            raise MigrationServiceError(
                409,
                "workspace_target_changed",
                "Workspace target changed after review",
            )
        try:
            with self._connections.use(owner_id, record.authority.connection_id) as connection:
                if connection.revision != record.authority.connection_revision:
                    raise MigrationServiceError(409, "connection_changed", "Connection changed after review")
                current_catalog = self._postgres.introspect(connection, record.authority.namespace)
        except ConnectionNotFoundError as error:
            raise MigrationServiceError(404, "connection_not_found", str(error)) from error
        except PostgresGatewayError as error:
            raise MigrationServiceError(502, "postgres_inspection_failed", str(error), retryable=True) from error
        if current_catalog.fingerprint != record.authority.live_catalog.fingerprint:
            raise MigrationServiceError(
                409,
                "catalog_changed",
                "PostgreSQL changed again after the drift review; refresh before resolving",
                details={"currentCatalogFingerprint": current_catalog.fingerprint},
            )
        reconciliation = reconcile_designs(
            record.authority.baseline_content,
            record.authority.desired_content,
            current_catalog,
            resolutions=submitted,
        )
        unresolved = [item for item in reconciliation.conflicts if item.id not in submitted]
        if unresolved or reconciliation.merged is None:
            raise MigrationServiceError(
                409,
                "drift_resolution_incomplete",
                "The selected changes do not produce a valid lossless design",
                details={"conflicts": [item.model_dump(mode="json", by_alias=True) for item in unresolved]},
            )
        try:
            return self._repository.reconcile_drift(
                record=record,
                expected_design_revision=request.expected_design_revision,
                resolved_content=reconciliation.merged,
                live_content=reconciliation.live,
                resolutions=[item.model_dump(mode="json", by_alias=True) for item in request.resolutions],
            )
        except DesignConflictError as error:
            raise MigrationServiceError(
                409,
                "design_changed",
                "The workspace design changed during reconciliation",
                details={"currentDesignRevision": error.current_revision},
            ) from error
        except MigrationConflictError as error:
            raise self._repository_conflict(error) from error
        except MigrationStorageUnavailableError as error:
            raise self._storage_error(error) from error

    def create_execution(
        self,
        owner_id: str,
        plan_id: str,
        request: MigrationExecutionCreate,
    ) -> MigrationExecution:
        """Durably reserve one immutable plan and wake the execution worker."""
        record = self._plan(owner_id, plan_id)
        reservation = self._execution_coordinator.reserve(owner_id, record, request)
        if self._execution_waker is not None:
            self._execution_waker()
        return reservation.record.execution

    def get_execution(self, owner_id: str, execution_id: str) -> MigrationExecution:
        try:
            return self._repository.get_execution(owner_id, execution_id).execution
        except MigrationNotFoundError as error:
            raise MigrationServiceError(404, "migration_execution_not_found", str(error)) from error

    def list_executions(self, owner_id: str, workspace_id: str, limit: int) -> list[MigrationExecution]:
        self._workspace(owner_id, workspace_id)
        return self._repository.list_executions(owner_id, workspace_id, limit)

    def list_recoverable_executions(self, limit: int = 100) -> list[ExecutionRecord]:
        """Return queued or abandoned target attempts visible to recovery."""

        return self._execution_coordinator.list_recoverable(limit)

    def reconcile_execution(
        self,
        owner_id: str,
        execution_id: str,
        request: MigrationReconciliationRequest,
    ) -> MigrationExecution:
        return self._execution_coordinator.reconcile(
            owner_id,
            execution_id,
            request,
        )

    def _move_design_history(
        self,
        owner_id: str,
        workspace_id: str,
        request: DesignHistoryTransitionRequest,
        action: str,
    ) -> DesignHistoryMutation:
        self._workspace(owner_id, workspace_id)
        try:
            mover = self._designs.undo if action == "undo" else self._designs.redo
            mover(owner_id, workspace_id, request.expected_design_revision)
        except DesignConflictError as error:
            raise MigrationServiceError(
                409,
                "design_changed",
                "The desired design changed before the history action completed",
                details={"currentDesignRevision": error.current_revision},
            ) from error
        except DesignHistoryBoundaryError as error:
            raise MigrationServiceError(409, f"nothing_to_{action}", str(error)) from error
        except DesignMutationBlockedError as error:
            raise MigrationServiceError(409, "design_mutation_blocked", str(error)) from error
        return self._history_mutation(owner_id, workspace_id)

    def _history_mutation(
        self,
        owner_id: str,
        workspace_id: str,
    ) -> DesignHistoryMutation:
        snapshot = self.design_snapshot(owner_id, workspace_id)
        return DesignHistoryMutation.model_validate(
            snapshot.model_dump(mode="python")
        )

    def _design_baseline(
        self, owner_id: str, workspace_id: str
    ) -> tuple[DesignHistoryBaseline, SchemiiDesignContent]:
        try:
            target = self._repository.current_baseline(owner_id, workspace_id)
        except MigrationStorageUnavailableError as error:
            raise self._storage_error(error) from error
        if target is not None:
            return (
                DesignHistoryBaseline(
                    kind="postgresql",
                    id=target.id,
                    revision=target.revision,
                    design_revision=target.design_revision,
                    label=f"PostgreSQL baseline · revision {target.revision}",
                    fingerprint=design_fingerprint(target.content),
                    complete=target.complete,
                ),
                target.content.model_copy(deep=True),
            )
        initial_revision, initial = self._designs.initial_content(owner_id, workspace_id)
        return (
            DesignHistoryBaseline(
                kind="workspace_start",
                id="workspace_start",
                revision=0,
                design_revision=initial_revision,
                label="Workspace starting point",
                fingerprint=design_fingerprint(initial),
                complete=True,
            ),
            initial,
        )

    @staticmethod
    def _baseline_reset_digest(
        workspace_id: str,
        design_revision: int,
        design_fingerprint_value: str,
        baseline: DesignHistoryBaseline,
        summary: Any,
    ) -> str:
        document = {
            "workspaceId": workspace_id,
            "designRevision": design_revision,
            "designFingerprint": design_fingerprint_value,
            "baseline": baseline.model_dump(mode="json", by_alias=True),
            "summary": summary.model_dump(mode="json", by_alias=True),
        }
        return hashlib.sha256(
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def _workspace(self, owner_id: str, workspace_id: str):
        try:
            return self._workspaces.get(owner_id, workspace_id)
        except WorkspaceNotFoundError as error:
            raise MigrationServiceError(404, "workspace_not_found", str(error)) from error

    def _design(self, owner_id: str, workspace_id: str):
        try:
            return self._designs.get(owner_id, workspace_id)
        except DesignWorkspaceNotFoundError as error:
            raise MigrationServiceError(404, "workspace_not_found", str(error)) from error

    def _plan(self, owner_id: str, plan_id: str) -> PlanRecord:
        try:
            return self._repository.get_plan(owner_id, plan_id)
        except MigrationNotFoundError as error:
            raise MigrationServiceError(404, "migration_plan_not_found", str(error)) from error

    @staticmethod
    def _validate_baseline_target(
        baseline_connection: str,
        baseline_database: str,
        baseline_namespace: str,
        connection: str,
        database: str,
        namespace: str,
    ) -> None:
        if (baseline_connection, baseline_database, baseline_namespace) != (
            connection,
            database,
            namespace,
        ):
            raise MigrationServiceError(
                409,
                "workspace_target_changed",
                "The workspace target differs from its synchronization baseline",
            )

    @staticmethod
    def _repository_conflict(error: MigrationConflictError) -> MigrationServiceError:
        return MigrationServiceError(409, error.code, str(error), details=error.details)

    @staticmethod
    def _storage_error(error: MigrationStorageUnavailableError) -> MigrationServiceError:
        return migration_storage_error(error)
