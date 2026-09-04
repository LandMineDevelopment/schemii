"""Server-owned migration planning, drift reconciliation, and execution orchestration."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from schemii.common.connections.service import ConnectionService
from schemii.common.connections.store import ConnectionNotFoundError
from schemii.common.metadata.limit_events import LimitEventRecorder
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
from schemii.schemii.workspaces.models import SchemiiWorkspace, WorkspaceCreateRecord

from .errors import MigrationServiceError, migration_storage_error
from .execution import MigrationExecutionCoordinator
from .models import (
    ColumnTypeAnalysis,
    ColumnTypeAnalysisRequest,
    ColumnTypeConversion,
    MigrationColumnOrderRebuild,
    MigrationDriftResolution,
    MigrationDriftResolutionRequest,
    MigrationExecution,
    MigrationExecutionCreate,
    MigrationPlan,
    MigrationPlanCreate,
    MigrationReconciliationRequest,
    MigrationWarning,
)
from .conversions import compile_conversion, conversion_candidates, quote
from .type_changes import classify_type_change
from .planner import (
    column_order_differences,
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
    request: WorkspaceCreateRecord,
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
    """Own every validation decision from target inspection through reconciliation."""

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
        limit_events: LimitEventRecorder | None = None,
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
            limit_events=limit_events,
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
        request: WorkspaceCreateRecord,
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
        """Return all editable-design client state from one repository snapshot."""

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
        if (
            workspace.connection_id is None
            or workspace.database is None
            or workspace.namespace is None
        ):
            raise MigrationServiceError(
                409,
                "database_design_required",
                "Migrations are available only in editable designs created from PostgreSQL",
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
            baseline = self._repository.current_baseline(owner_id, workspace_id)
        except MigrationStorageUnavailableError as error:
            raise self._storage_error(error) from error
        if baseline is None:
            raise MigrationServiceError(
                409,
                "migration_baseline_missing",
                "The database-derived workspace is missing its original PostgreSQL baseline",
                details={"workspaceId": workspace_id},
            )
        self._validate_baseline_target(
            baseline.connection_id,
            baseline.database,
            baseline.namespace,
            workspace.connection_id,
            workspace.database,
            workspace.namespace,
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

        reconciliation = reconcile_designs(
            baseline.content,
            design.content,
            catalog,
        )
        merged = reconciliation.merged or design.content
        order_differences = column_order_differences(
            workspace.namespace,
            reconciliation.live,
            merged,
        )
        required_empty_tables = tables_requiring_empty_for_required_columns(
            reconciliation.live,
            merged,
        )
        empty_tables: frozenset[str] = frozenset()
        inspected_table_names = frozenset({
            *required_empty_tables,
            *(item.table_name for item in order_differences),
        })
        rebuild_blockers: dict[str, tuple[str, ...]] = {}
        if inspected_table_names:
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
                        tuple(inspected_table_names),
                    )
                    empty_tables = frozenset(
                        name for name, is_empty in emptiness.items() if is_empty
                    )
                    rebuild_safety_names = tuple(
                        item.table_name
                        for item in order_differences
                        if not item.blocking_reasons
                    )
                    if rebuild_safety_names:
                        rebuild_blockers = self._postgres.table_column_rebuild_blockers(
                            connection,
                            workspace.namespace,
                            rebuild_safety_names,
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
        requested_rebuild_ids = (
            set(request.rebuild_table_ids)
            if request.rebuild_table_ids is not None
            else {
                item.table_id
                for item in order_differences
                if item.table_name in empty_tables
                and not item.blocking_reasons
                and not rebuild_blockers.get(item.table_name)
            }
        )
        known_rebuild_ids = {item.table_id for item in order_differences}
        invalid_rebuild_ids = sorted(requested_rebuild_ids - known_rebuild_ids)
        order_rebuilds: list[MigrationColumnOrderRebuild] = []
        eligible_rebuild_ids: set[str] = set()
        populated_rebuild_ids: set[str] = set()
        selected_rebuild_blockers: list[MigrationWarning] = []
        for difference in order_differences:
            reasons = [
                *difference.blocking_reasons,
                *rebuild_blockers.get(difference.table_name, ()),
            ]
            selected = difference.table_id in requested_rebuild_ids
            eligible = not reasons
            contains_data = difference.table_name not in empty_tables
            order_rebuilds.append(MigrationColumnOrderRebuild(
                table_id=difference.table_id,
                table_name=difference.table_name,
                current_order=list(difference.current_order),
                desired_order=list(difference.desired_order),
                selected=selected,
                eligible=eligible,
                contains_data=contains_data,
                blocking_reasons=list(reasons),
            ))
            if selected and eligible:
                eligible_rebuild_ids.add(difference.table_id)
                if contains_data:
                    populated_rebuild_ids.add(difference.table_id)
            elif selected:
                selected_rebuild_blockers.append(MigrationWarning(
                    code="physical_column_order_rebuild_blocked",
                    message=(
                        f"PostgreSQL column order for {difference.table_name} cannot be rebuilt: "
                        + " ".join(reasons)
                    ),
                    object_path=f"tables.{difference.table_name}.column_order",
                ))
        for table_id in invalid_rebuild_ids:
            selected_rebuild_blockers.append(MigrationWarning(
                code="physical_column_order_not_available",
                message="The requested table no longer has a physical column-order difference.",
                object_path=f"tables.{table_id}.column_order",
            ))
        selected_empty_rebuild_names = {
            item.table_name
            for item in order_rebuilds
            if item.selected and item.eligible and not item.contains_data
        }
        required_empty_preconditions = frozenset({
            *(required_empty_tables & empty_tables),
            *selected_empty_rebuild_names,
        })
        conversion_reviews = []
        conversions = {}
        conversion_blockers = []
        choices = {choice.column_id: choice for choice in request.column_type_conversions}
        for before, after, old, new in conversion_candidates(reconciliation.live, merged):
            choice = choices.pop(new.id, None)
            review = ColumnTypeConversion(
                column_id=new.id, table_id=after.id, table_name=after.name,
                column_name=new.name, source_type=old.data_type, target_type=new.data_type,
                reason=classify_type_change(old.data_type, new.data_type).reason,
                default_expression=f"{quote(new.name)}::{new.data_type}",
                strategy=choice.strategy if choice else None,
                expression=choice.expression if choice else None,
            )
            if choice:
                try:
                    compiled = compile_conversion(workspace.namespace, before, after, old, new, choice)
                    review = compiled.review
                    with self._connections.use(owner_id, workspace.connection_id) as connection:
                        review.validation_error = self._postgres.validate_column_conversion(connection, compiled.validation_sql)
                    if not review.validation_error:
                        conversions[new.id] = compiled
                except ValueError as error:
                    review.validation_error = str(error)
                except (PostgresGatewayError, ConnectionNotFoundError) as error:
                    raise MigrationServiceError(502, "conversion_validation_unavailable", str(error), retryable=True) from error
                if review.validation_error:
                    conversion_blockers.append(MigrationWarning(
                        code="column_type_conversion_invalid", message=review.validation_error,
                        object_path=f"tables.{after.name}.columns.{new.name}",
                    ))
            conversion_reviews.append(review)
        if choices:
            raise MigrationServiceError(409, "column_conversion_changed", "A selected column no longer requires conversion. Refresh the review.")
        steps, compiler_blockers = compile_migration_steps(
            workspace.namespace,
            reconciliation.live,
            merged,
            empty_tables=empty_tables,
            rebuild_table_ids=eligible_rebuild_ids,
            populated_rebuild_table_ids=populated_rebuild_ids,
            column_type_conversions=conversions,
        )
        compiler_blockers.extend(conversion_blockers)
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
        blocking = [*compiler_blockers, *selected_rebuild_blockers]
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
        complete = (
            not reconciliation.conflicts
            and not compiler_blockers
            and not selected_rebuild_blockers
        )
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
            "columnTypeConversions": [item.model_dump(mode="json", by_alias=True) for item in conversion_reviews],
            "rebuildTableIds": sorted(eligible_rebuild_ids),
            "requiredEmptyTables": sorted(required_empty_preconditions),
            "columnOrderRebuilds": [
                item.model_dump(mode="json", by_alias=True) for item in order_rebuilds
            ],
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
            column_type_conversions=conversion_reviews,
            requires_external_change_acknowledgement=bool(reconciliation.external_changes),
            column_order_rebuilds=order_rebuilds,
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
                        required_empty_tables=tuple(sorted(required_empty_preconditions)),
                        rebuild_table_ids=tuple(sorted(eligible_rebuild_ids)),
                    ),
                )
            )
        except MigrationConflictError as error:
            raise self._repository_conflict(error) from error
        except MigrationStorageUnavailableError as error:
            raise self._storage_error(error) from error

    def analyze_column_type(self, owner_id: str, workspace_id: str, request: ColumnTypeAnalysisRequest) -> ColumnTypeAnalysis:
        workspace = self._workspace(owner_id, workspace_id)
        try:
            baseline = self._repository.current_baseline(owner_id, workspace_id) if workspace.connection_id else None
        except MigrationStorageUnavailableError as error:
            raise self._storage_error(error) from error
        source = next((column for table in baseline.content.tables for column in table.columns if column.id == request.column_id), None) if baseline else None
        if source is None:
            return ColumnTypeAnalysis(requires_conversion=False, source_type=None, target_type=request.target_type, reason="This column has no live baseline to convert.")
        decision = classify_type_change(source.data_type, request.target_type)
        return ColumnTypeAnalysis(requires_conversion=decision.disposition == "blocked", source_type=source.data_type, target_type=request.target_type, reason=decision.reason)

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
