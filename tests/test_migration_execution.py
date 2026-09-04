from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event
from types import SimpleNamespace
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from schemii.common.connections.store import InMemoryConnectionRepository
from schemii.common.metadata.factory import MetadataRepositories
from schemii.common.metadata.models import Principal, get_current_principal
from schemii.common.postgres.errors import (
    PostgresCommitUncertainError,
    PostgresMigrationPreconditionError,
)
from schemii.common.postgres.gateway import (
    PostgresMigrationResult,
    PostgresTransactionRecovery,
)
from schemii.common.postgres.models import (
    PostgresColumn,
    PostgresTable,
    build_postgres_catalog,
)
from schemii.main import ApplicationServices, create_app
from schemii.schemii.designs.importer import import_postgres_catalog
from schemii.schemii.designs.models import DesignColumn, SchemiiDesignContent
from schemii.schemii.designs.store import design_fingerprint
from schemii.schemii.migrations.models import (
    MigrationExecutionCreate,
    MigrationPlan,
    MigrationPlanCreate,
)
from schemii.schemii.migrations.repository import (
    ExecutionWork,
    InMemoryMigrationRepository,
    MigrationConflictError,
    MigrationNotFoundError,
    PlanAuthority,
    PlanRecord,
)
from schemii.schemii.migrations.service import MigrationService, MigrationServiceError
from schemii.schemii.migrations.worker import MigrationExecutionWorker
from schemii.schemii.workspaces.models import (
    WorkspaceCreateRecord,
    SchemiiWorkspaceLayoutUpdate,
)
from schemii.schemii.workspaces.store import (
    InMemoryWorkspaceRepository,
    WorkspaceMutationBlockedError,
    WorkspaceNotFoundError,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
OWNER_ID = "user-test"
WORKSPACE_ID = "ws_" + "1" * 32
CONNECTION_ID = "pg_" + "2" * 32
PLAN_ID = "mpl_" + "3" * 32
REVIEW_DIGEST = "4" * 64
WORKER_ID = "mls_" + "6" * 32
TARGET_IDENTITY = {
    "database": "analytics",
    "database_oid": "16384",
    "server_version_num": "170002",
    "server_address": "10.0.0.12",
    "server_port": 5432,
}


def _catalog(*, server_version: str = "17.2", server_version_num: int = 170002) -> Any:
    return build_postgres_catalog(
        database="analytics",
        namespace="public",
        server_version=server_version,
        server_version_num=server_version_num,
        server_timezone="UTC",
        tables=(),
        relationships=(),
        functions=(),
        views=(),
        materialized_views=(),
        captured_at=NOW,
    )


def _single_table_catalog() -> Any:
    return build_postgres_catalog(
        database="analytics",
        namespace="public",
        server_version="17.2",
        server_version_num=170002,
        server_timezone="UTC",
        tables=(
            PostgresTable(
                namespace="public",
                name="events",
                kind="table",
                is_partition=False,
                columns=(
                    PostgresColumn(
                        name="id",
                        ordinal=1,
                        data_type="bigint",
                        nullable=False,
                    ),
                ),
            ),
        ),
        relationships=(),
        functions=(),
        views=(),
        materialized_views=(),
        captured_at=NOW,
    )


def _plan_record(
    *,
    destructive: bool = False,
    external_acknowledgement: bool = False,
    plan_id: str = PLAN_ID,
    workspace_id: str = WORKSPACE_ID,
    baseline_id: str = "mbl_" + "5" * 32,
    baseline_revision: int = 1,
    workspace_revision: int = 1,
    catalog: Any | None = None,
    required_empty_tables: tuple[str, ...] = (),
) -> PlanRecord:
    content = SchemiiDesignContent()
    fingerprint = design_fingerprint(content)
    catalog = catalog or _catalog()
    plan = MigrationPlan(
        id=plan_id,
        workspace_id=workspace_id,
        status="reviewable",
        workspace_revision=workspace_revision,
        design_revision=1,
        design_fingerprint=fingerprint,
        baseline_revision=baseline_revision,
        catalog_fingerprint=catalog.fingerprint,
        merged_design_fingerprint=fingerprint,
        review_digest=REVIEW_DIGEST,
        drift_status="compatible" if external_acknowledgement else "none",
        complete=True,
        apply_capable=True,
        destructive=destructive,
        requires_external_change_acknowledgement=external_acknowledgement,
        steps=[],
        external_changes=[],
        conflicts=[],
        warnings=[],
        blocking_differences=[],
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    return PlanRecord(
        owner_id=OWNER_ID,
        baseline_id=baseline_id,
        plan=plan,
        authority=PlanAuthority(
            baseline_content=content,
            desired_content=content,
            merged_content=content,
            live_content=content,
            live_catalog=catalog,
            allow_destructive=destructive,
            connection_id=CONNECTION_ID,
            connection_revision=1,
            database="analytics",
            namespace="public",
            required_empty_tables=required_empty_tables,
        ),
    )


class _WorkspaceRepository:
    def __init__(
        self,
        *,
        revision: int = 1,
        connection_id: str = CONNECTION_ID,
        database: str = "analytics",
        namespace: str = "public",
    ) -> None:
        self.revision = revision
        self.connection_id = connection_id
        self.database = database
        self.namespace = namespace

    def get(self, owner_id: str, workspace_id: str) -> Any:
        assert (owner_id, workspace_id) == (OWNER_ID, WORKSPACE_ID)
        return SimpleNamespace(
            revision=self.revision,
            connection_id=self.connection_id,
            database=self.database,
            namespace=self.namespace,
            import_summary=SimpleNamespace(complete=True),
        )


class _MissingWorkspaceRepository:
    def get(self, owner_id: str, workspace_id: str) -> Any:
        del owner_id, workspace_id
        raise WorkspaceNotFoundError("Workspace was not found")


class _DesignRepository:
    def __init__(
        self,
        fingerprint: str,
        *,
        workspace_id: str = WORKSPACE_ID,
        revision: int = 1,
        content: SchemiiDesignContent | None = None,
    ) -> None:
        self._fingerprint = fingerprint
        self._workspace_id = workspace_id
        self._revision = revision
        self._content = content or SchemiiDesignContent()
        self.replace_calls = 0

    def get(self, owner_id: str, workspace_id: str) -> Any:
        assert (owner_id, workspace_id) == (OWNER_ID, self._workspace_id)
        return SimpleNamespace(
            revision=self._revision,
            fingerprint=self._fingerprint,
            content=self._content,
        )

    def replace(self, *_args: Any, **_kwargs: Any) -> Any:
        self.replace_calls += 1
        self._revision += 1
        self._fingerprint = design_fingerprint(self._content)
        return self.get(OWNER_ID, self._workspace_id)


class _ConcurrentDesignRepository:
    """Expose a newer desired design and reject any attempt to overwrite it."""

    def __init__(self) -> None:
        self.replace_calls = 0

    def get(self, owner_id: str, workspace_id: str) -> Any:
        assert (owner_id, workspace_id) == (OWNER_ID, WORKSPACE_ID)
        return SimpleNamespace(revision=2, fingerprint="f" * 64)

    def replace(self, *_args: Any, **_kwargs: Any) -> Any:
        self.replace_calls += 1
        raise AssertionError("a concurrent desired design must be preserved")


class _Connections:
    @contextmanager
    def use(self, owner_id: str, connection_id: str) -> Iterator[Any]:
        assert (owner_id, connection_id) == (OWNER_ID, CONNECTION_ID)
        yield SimpleNamespace(revision=1, database="analytics")


class _UncertainGateway:
    def __init__(self) -> None:
        self.execution_calls = 0

    def execute_migration(
        self,
        connection: Any,
        namespace: str,
        expected_catalog_fingerprint: str,
        statements: list[str],
        *,
        on_started: Any,
        on_intended: Any,
    ) -> Any:
        del connection, expected_catalog_fingerprint, statements, on_intended
        assert namespace == "public"
        self.execution_calls += 1
        on_started("42", TARGET_IDENTITY)
        raise PostgresCommitUncertainError()


class _SuccessfulGateway:
    def __init__(self) -> None:
        self.execution_calls = 0
        self.status_calls = 0
        self.introspection_calls = 0

    def introspect(self, connection: Any, namespace: str) -> Any:
        del connection
        assert namespace == "public"
        self.introspection_calls += 1
        return _catalog()

    def execute_migration(
        self,
        connection: Any,
        namespace: str,
        expected_catalog_fingerprint: str,
        statements: list[str],
        *,
        on_started: Any,
        on_intended: Any,
    ) -> PostgresMigrationResult:
        del connection, expected_catalog_fingerprint, statements
        assert namespace == "public"
        self.execution_calls += 1
        catalog = _catalog()
        identity = TARGET_IDENTITY
        on_started("42", identity)
        on_intended(catalog)
        return PostgresMigrationResult(
            catalog=catalog,
            transaction_id="42",
            target_identity=identity,
            completed_step_count=0,
        )

    def transaction_status(
        self,
        connection: Any,
        transaction_id: str,
    ) -> PostgresTransactionRecovery:
        del connection
        assert transaction_id == "42"
        self.status_calls += 1
        return PostgresTransactionRecovery(
            status="committed",
            target_identity=TARGET_IDENTITY,
        )


class _PreconditionGateway(_SuccessfulGateway):
    def execute_migration(self, *args: Any, **kwargs: Any) -> PostgresMigrationResult:
        self.execution_calls += 1
        assert kwargs["required_empty_tables"] == ("events",)
        raise PostgresMigrationPreconditionError(("events",))


class _PlanningEmptinessGateway:
    def __init__(self, catalog: Any, *, is_empty: bool) -> None:
        self.catalog = catalog
        self.is_empty = is_empty
        self.emptiness_calls: list[tuple[str, ...]] = []

    def introspect(self, connection: Any, namespace: str) -> Any:
        del connection
        assert namespace == "public"
        return self.catalog

    def table_emptiness(
        self,
        connection: Any,
        namespace: str,
        table_names: tuple[str, ...],
    ) -> dict[str, bool]:
        del connection
        assert namespace == "public"
        self.emptiness_calls.append(table_names)
        return {name: self.is_empty for name in table_names}

    def table_column_rebuild_blockers(
        self,
        connection: Any,
        namespace: str,
        table_names: tuple[str, ...],
    ) -> dict[str, tuple[str, ...]]:
        del connection
        assert namespace == "public"
        return {name: () for name in table_names}


class _BlockingGateway(_SuccessfulGateway):
    def __init__(self) -> None:
        super().__init__()
        self.started = Event()
        self.release = Event()

    def execute_migration(self, *args: Any, **kwargs: Any) -> PostgresMigrationResult:
        self.started.set()
        assert self.release.wait(timeout=5), "test did not release the target gateway"
        return super().execute_migration(*args, **kwargs)


class _WrongTargetGateway(_SuccessfulGateway):
    def transaction_status(
        self,
        connection: Any,
        transaction_id: str,
    ) -> PostgresTransactionRecovery:
        del connection
        assert transaction_id == "42"
        self.status_calls += 1
        return PostgresTransactionRecovery(
            status="committed",
            target_identity={
                **TARGET_IDENTITY,
                "database_oid": "24576",
                "server_address": "10.0.0.99",
            },
        )


class _MutableClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def _seed_baseline(
    repository: InMemoryMigrationRepository,
    *,
    workspace_id: str = WORKSPACE_ID,
    catalog: Any | None = None,
    content: SchemiiDesignContent | None = None,
) -> Any:
    current = repository.current_baseline(OWNER_ID, workspace_id)
    if current is not None:
        return current
    return repository.create_baseline(
        owner_id=OWNER_ID,
        workspace_id=workspace_id,
        connection_id=CONNECTION_ID,
        connection_revision=1,
        database="analytics",
        namespace="public",
        design_revision=1,
        content=content or SchemiiDesignContent(),
        catalog=catalog or _catalog(),
        complete=True,
        issues=[],
        source="import",
        expected_predecessor_id=None,
    )


def _store_plan(
    repository: InMemoryMigrationRepository,
    *,
    baseline_catalog: Any | None = None,
    **plan_values: Any,
) -> PlanRecord:
    workspace_id = plan_values.get("workspace_id", WORKSPACE_ID)
    baseline = _seed_baseline(
        repository,
        workspace_id=workspace_id,
        catalog=baseline_catalog,
    )
    record = _plan_record(
        baseline_id=baseline.id,
        baseline_revision=baseline.revision,
        **plan_values,
    )
    repository.create_plan(record)
    return record


def _service(
    gateway: Any,
    *,
    repository: InMemoryMigrationRepository | None = None,
    clock: Any = None,
    lease_ttl: timedelta = timedelta(minutes=2),
) -> tuple[MigrationService, InMemoryMigrationRepository]:
    plan = _plan_record()
    designs = _DesignRepository(plan.plan.design_fingerprint)
    active_repository = repository or InMemoryMigrationRepository(designs)
    try:
        active_repository.get_plan(OWNER_ID, PLAN_ID)
    except MigrationNotFoundError:
        _store_plan(active_repository)
    service = MigrationService(
        repository=active_repository,
        connections=_Connections(),
        postgres=gateway,
        workspaces=_WorkspaceRepository(),
        designs=designs,
        execution_lease_ttl=lease_ttl,
        clock=clock or (lambda: NOW),
    )
    return service, active_repository


def _request(**changes: Any) -> MigrationExecutionCreate:
    values = {
        "review_digest": REVIEW_DIGEST,
        "confirm_destructive": False,
        "confirm_external_changes": False,
    }
    values.update(changes)
    return MigrationExecutionCreate(**values)


def _reserve_and_claim(
    repository: InMemoryMigrationRepository,
    *,
    lease_expires_at: datetime = NOW + timedelta(minutes=2),
) -> Any:
    reservation = repository.reserve_execution(
        OWNER_ID,
        PLAN_ID,
        REVIEW_DIGEST,
        False,
        False,
        reserved_at=NOW,
    )
    assert reservation.reserved_now is True
    work = repository.claim_next_execution(
        claimed_at=NOW,
        lease_owner=WORKER_ID,
        lease_expires_at=lease_expires_at,
    )
    assert work is not None
    assert work.kind == "execute"
    return work.record


def _intended_result() -> dict[str, Any]:
    catalog = _catalog()
    content = SchemiiDesignContent()
    return {
        "catalog": catalog.model_dump(mode="json"),
        "content": content.model_dump(mode="json"),
        "catalogFingerprint": catalog.fingerprint,
        "designFingerprint": design_fingerprint(content),
    }


def test_submission_only_reserves_and_duplicate_authorization_is_idempotent() -> None:
    plan_record = _plan_record(destructive=True, external_acknowledgement=True)
    designs = _DesignRepository(plan_record.plan.design_fingerprint)
    repository = InMemoryMigrationRepository(designs)
    plan_record = _store_plan(
        repository,
        destructive=True,
        external_acknowledgement=True,
    )
    gateway = _UncertainGateway()
    service = MigrationService(
        repository=repository,
        connections=_Connections(),
        postgres=gateway,
        workspaces=_WorkspaceRepository(),
        designs=designs,
        clock=lambda: NOW,
    )
    request = _request(
        confirm_destructive=True,
        confirm_external_changes=True,
    )

    first = service.create_execution(OWNER_ID, PLAN_ID, request)
    duplicate = service.create_execution(OWNER_ID, PLAN_ID, request)

    assert first.status == "reserved"
    assert duplicate == first
    assert gateway.execution_calls == 0

    with pytest.raises(MigrationServiceError) as missing_confirmation:
        service.create_execution(
            OWNER_ID,
            PLAN_ID,
            request.model_copy(update={"confirm_destructive": False}),
        )
    assert missing_confirmation.value.code == "destructive_confirmation_required"
    assert gateway.execution_calls == 0

    work = service.execution_coordinator.claim_next(WORKER_ID)
    assert work is not None
    terminal = service.execution_coordinator.process(work)
    assert terminal.status == "uncertain"
    assert gateway.execution_calls == 1
    assert service.execution_coordinator.claim_next(WORKER_ID) is None


def test_concurrent_duplicate_submissions_create_one_target_attempt() -> None:
    gateway = _UncertainGateway()
    service, repository = _service(gateway)
    ready = Barrier(8)

    def submit(_: int) -> str:
        ready.wait()
        return service.create_execution(OWNER_ID, PLAN_ID, _request()).id

    with ThreadPoolExecutor(max_workers=8) as pool:
        execution_ids = list(pool.map(submit, range(8)))

    assert len(set(execution_ids)) == 1
    work = service.execution_coordinator.claim_next(WORKER_ID)
    assert work is not None
    service.execution_coordinator.process(work)
    assert gateway.execution_calls == 1
    assert repository.get_execution(OWNER_ID, execution_ids[0]).execution.status == "uncertain"


def test_worker_service_error_before_target_start_is_durably_terminal() -> None:
    plan = _plan_record()
    designs = _DesignRepository(plan.plan.design_fingerprint)
    repository = InMemoryMigrationRepository(designs)
    _store_plan(repository)
    gateway = _UncertainGateway()
    service = MigrationService(
        repository=repository,
        connections=_Connections(),
        postgres=gateway,
        workspaces=_MissingWorkspaceRepository(),
        designs=designs,
        clock=lambda: NOW,
    )
    receipt = service.create_execution(OWNER_ID, PLAN_ID, _request())
    work = service.execution_coordinator.claim_next(WORKER_ID)
    assert work is not None

    terminal = service.execution_coordinator.process(work)

    assert terminal.id == receipt.id
    assert terminal.status == "failed"
    assert terminal.commit_outcome == "rolled_back"
    assert terminal.error_code == "workspace_not_found"
    assert gateway.execution_calls == 0
    assert service.list_recoverable_executions() == []


def test_required_empty_table_is_rechecked_and_reported_before_ddl() -> None:
    plan = _plan_record(required_empty_tables=("events",))
    designs = _DesignRepository(plan.plan.design_fingerprint)
    repository = InMemoryMigrationRepository(designs)
    _store_plan(repository, required_empty_tables=("events",))
    gateway = _PreconditionGateway()
    service = MigrationService(
        repository=repository,
        connections=_Connections(),
        postgres=gateway,
        workspaces=_WorkspaceRepository(),
        designs=designs,
        clock=lambda: NOW,
    )
    receipt = service.create_execution(OWNER_ID, PLAN_ID, _request())
    work = service.execution_coordinator.claim_next(WORKER_ID)
    assert work is not None

    terminal = service.execution_coordinator.process(work)

    assert terminal.id == receipt.id
    assert terminal.status == "failed"
    assert terminal.commit_outcome == "rolled_back"
    assert terminal.error_code == "postgres_migration_precondition_changed"
    assert repository.get_execution(OWNER_ID, terminal.id).error_detail == {
        "tables": ["events"]
    }
    assert gateway.execution_calls == 1


def test_execution_transition_is_revision_cas_and_rejects_illegal_edges() -> None:
    service, repository = _service(_SuccessfulGateway())
    del service
    claimed = _reserve_and_claim(repository)
    ready = Barrier(2)

    def finish(_: int) -> str:
        ready.wait()
        try:
            repository.transition_execution(
                OWNER_ID,
                claimed.execution.id,
                expected_revision=claimed.execution.revision,
                allowed_from={"reserved"},
                status="failed",
                lease_owner=claimed.lease_owner,
                commit_outcome="rolled_back",
            )
        except MigrationConflictError as error:
            return error.code
        return "transitioned"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(finish, range(2)))

    assert sorted(outcomes) == ["migration_execution_changed", "transitioned"]

    service, repository = _service(_SuccessfulGateway())
    del service
    claimed = _reserve_and_claim(repository)
    applying = repository.transition_execution(
        OWNER_ID,
        claimed.execution.id,
        expected_revision=claimed.execution.revision,
        allowed_from={"reserved"},
        status="applying",
        lease_owner=claimed.lease_owner,
        lease_expires_at=NOW + timedelta(minutes=2),
    )
    with pytest.raises(MigrationConflictError) as illegal:
        repository.transition_execution(
            OWNER_ID,
            applying.execution.id,
            expected_revision=applying.execution.revision,
            allowed_from={"applying"},
            status="reserved",
            lease_owner=applying.lease_owner,
            lease_expires_at=NOW + timedelta(minutes=2),
        )
    assert illegal.value.code == "migration_execution_transition_invalid"


def test_transition_can_explicitly_clear_evidence_fields() -> None:
    service, repository = _service(_SuccessfulGateway())
    del service
    claimed = _reserve_and_claim(repository)
    applying = repository.transition_execution(
        OWNER_ID,
        claimed.execution.id,
        expected_revision=claimed.execution.revision,
        allowed_from={"reserved"},
        status="applying",
        lease_owner=claimed.lease_owner,
        lease_expires_at=NOW + timedelta(minutes=2),
        transaction_id="42",
        target_identity=TARGET_IDENTITY,
        error_code="old_error",
        error_detail={"old": True},
    )

    succeeded = repository.transition_execution(
        OWNER_ID,
        applying.execution.id,
        expected_revision=applying.execution.revision,
        allowed_from={"applying"},
        status="succeeded",
        lease_owner=applying.lease_owner,
        transaction_id=None,
        target_identity=None,
        error_code=None,
        error_detail=None,
    )

    assert succeeded.execution.transaction_id is None
    assert succeeded.execution.error_code is None
    assert succeeded.target_identity is None
    assert succeeded.error_detail is None
    assert succeeded.lease_owner is None
    assert succeeded.execution.recovery_available_at is None


def test_lease_renewal_prevents_a_second_worker_from_claiming_active_work() -> None:
    clock = _MutableClock()
    service, repository = _service(
        _SuccessfulGateway(),
        clock=clock,
        lease_ttl=timedelta(seconds=30),
    )
    service.create_execution(OWNER_ID, PLAN_ID, _request())
    work = service.execution_coordinator.claim_next(WORKER_ID)
    assert work is not None
    original_expiry = work.record.lease_expires_at
    assert original_expiry == NOW + timedelta(seconds=30)

    clock.value = NOW + timedelta(seconds=20)
    assert service.execution_coordinator.renew(work) is True
    clock.value = NOW + timedelta(seconds=31)
    assert service.execution_coordinator.claim_next("mls_" + "7" * 32) is None

    renewed = repository.get_execution(OWNER_ID, work.record.execution.id)
    assert renewed.lease_expires_at == NOW + timedelta(seconds=50)


def test_expired_reserved_work_is_recovered_after_restart() -> None:
    clock = _MutableClock()
    first_service, repository = _service(
        _SuccessfulGateway(),
        clock=clock,
        lease_ttl=timedelta(seconds=30),
    )
    receipt = first_service.create_execution(OWNER_ID, PLAN_ID, _request())
    assert receipt.status == "reserved"
    assert repository.get_execution(OWNER_ID, receipt.id).lease_owner is None

    gateway = _SuccessfulGateway()
    restarted_service, _ = _service(
        gateway,
        repository=repository,
        clock=clock,
        lease_ttl=timedelta(seconds=30),
    )
    worker = MigrationExecutionWorker(restarted_service.execution_coordinator)
    assert asyncio.run(worker.run_once()) is True

    terminal = repository.get_execution(OWNER_ID, receipt.id).execution
    assert terminal.status == "succeeded"
    assert terminal.commit_outcome == "committed"
    assert terminal.sync_status == "succeeded"
    assert gateway.execution_calls == 1


def test_expired_applying_work_reconciles_and_never_replays_ddl() -> None:
    clock = _MutableClock()
    gateway = _SuccessfulGateway()
    service, repository = _service(
        gateway,
        clock=clock,
        lease_ttl=timedelta(seconds=30),
    )
    claimed = _reserve_and_claim(
        repository,
        lease_expires_at=NOW + timedelta(seconds=30),
    )
    repository.transition_execution(
        OWNER_ID,
        claimed.execution.id,
        expected_revision=claimed.execution.revision,
        allowed_from={"reserved"},
        status="applying",
        lease_owner=claimed.lease_owner,
        lease_expires_at=NOW + timedelta(seconds=30),
        transaction_id="42",
        target_identity=TARGET_IDENTITY,
        intended_result=_intended_result(),
        completed_step_count=0,
    )

    clock.value = NOW + timedelta(seconds=31)
    work = service.execution_coordinator.claim_next("mls_" + "7" * 32)
    assert work is not None
    assert work.kind == "reconcile"
    terminal = service.execution_coordinator.process(work)

    assert terminal.status == "succeeded"
    assert terminal.sync_status == "succeeded"
    assert gateway.status_calls == 1
    assert gateway.execution_calls == 0


def test_committed_target_sync_is_retried_without_replaying_ddl() -> None:
    clock = _MutableClock()
    gateway = _SuccessfulGateway()
    service, repository = _service(
        gateway,
        clock=clock,
        lease_ttl=timedelta(seconds=30),
    )
    claimed = _reserve_and_claim(repository)
    applying = repository.transition_execution(
        OWNER_ID,
        claimed.execution.id,
        expected_revision=claimed.execution.revision,
        allowed_from={"reserved"},
        status="applying",
        lease_owner=claimed.lease_owner,
        lease_expires_at=NOW + timedelta(minutes=2),
        transaction_id="42",
        target_identity=TARGET_IDENTITY,
        intended_result=_intended_result(),
    )
    committed = repository.transition_execution(
        OWNER_ID,
        applying.execution.id,
        expected_revision=applying.execution.revision,
        allowed_from={"applying"},
        status="succeeded",
        lease_owner=applying.lease_owner,
        lease_expires_at=NOW + timedelta(seconds=30),
        commit_outcome="committed",
        sync_status="pending",
    )

    assert committed.lease_owner == WORKER_ID
    assert committed.execution.recovery_available_at == NOW + timedelta(seconds=30)
    assert service.execution_coordinator.claim_next("mls_" + "7" * 32) is None

    clock.value = NOW + timedelta(seconds=31)
    work = service.execution_coordinator.claim_next(WORKER_ID)
    assert work is not None
    assert work.kind == "sync"
    terminal = service.execution_coordinator.process(work)

    assert terminal.status == "succeeded"
    assert terminal.sync_status == "succeeded"
    assert terminal.revision > committed.execution.revision
    assert gateway.execution_calls == 0
    assert gateway.status_calls == 0


def test_committed_sync_advances_baseline_without_overwriting_newer_design() -> None:
    designs = _ConcurrentDesignRepository()
    repository = InMemoryMigrationRepository(designs)
    _store_plan(
        repository,
        baseline_catalog=_catalog(
            server_version="17.1",
            server_version_num=170001,
        ),
    )
    service = MigrationService(
        repository=repository,
        connections=_Connections(),
        postgres=_SuccessfulGateway(),
        workspaces=_WorkspaceRepository(),
        designs=designs,
        clock=lambda: NOW,
    )
    claimed = _reserve_and_claim(repository)
    applying = repository.transition_execution(
        OWNER_ID,
        claimed.execution.id,
        expected_revision=claimed.execution.revision,
        allowed_from={"reserved"},
        status="applying",
        lease_owner=claimed.lease_owner,
        lease_expires_at=NOW + timedelta(minutes=2),
        transaction_id="42",
        target_identity=TARGET_IDENTITY,
        intended_result=_intended_result(),
    )
    committed = repository.transition_execution(
        OWNER_ID,
        applying.execution.id,
        expected_revision=applying.execution.revision,
        allowed_from={"applying"},
        status="succeeded",
        lease_owner=applying.lease_owner,
        lease_expires_at=NOW + timedelta(minutes=2),
        commit_outcome="committed",
        sync_status="pending",
    )

    terminal = service.execution_coordinator.process(
        ExecutionWork(kind="sync", record=committed)
    )

    assert terminal.status == "succeeded"
    assert terminal.sync_status == "conflict"
    assert terminal.error_code == "post_commit_design_changed"
    assert designs.replace_calls == 0
    baseline = repository.current_baseline(OWNER_ID, WORKSPACE_ID)
    assert baseline is not None
    assert baseline.catalog.fingerprint == _catalog().fingerprint
    assert baseline.content == SchemiiDesignContent()
    assert baseline.source_execution_id == committed.execution.id
    assert baseline.design_revision == 1
    assert repository.blocks_workspace_lifecycle(OWNER_ID, WORKSPACE_ID) is False


def test_workspace_lifecycle_stays_blocked_until_committed_sync_is_resolved() -> None:
    workspaces = InMemoryWorkspaceRepository()
    workspace = workspaces.create(
        OWNER_ID,
        WorkspaceCreateRecord(name="Lifecycle guard"),
    )
    designs = _DesignRepository(
        _plan_record().plan.design_fingerprint,
        workspace_id=workspace.id,
    )
    repository = InMemoryMigrationRepository(designs)
    plan = _store_plan(repository, workspace_id=workspace.id)
    MigrationService(
        repository=repository,
        connections=_Connections(),
        postgres=_SuccessfulGateway(),
        workspaces=workspaces,
        designs=designs,
        clock=lambda: NOW,
    )
    reservation = repository.reserve_execution(
        OWNER_ID,
        PLAN_ID,
        REVIEW_DIGEST,
        False,
        False,
        reserved_at=NOW,
    )
    claimed_work = repository.claim_next_execution(
        claimed_at=NOW,
        lease_owner=WORKER_ID,
        lease_expires_at=NOW + timedelta(minutes=2),
    )
    assert claimed_work is not None
    applying = repository.transition_execution(
        OWNER_ID,
        reservation.record.execution.id,
        expected_revision=claimed_work.record.execution.revision,
        allowed_from={"reserved"},
        status="applying",
        lease_owner=WORKER_ID,
        lease_expires_at=NOW + timedelta(minutes=2),
    )
    committed = repository.transition_execution(
        OWNER_ID,
        applying.execution.id,
        expected_revision=applying.execution.revision,
        allowed_from={"applying"},
        status="succeeded",
        lease_owner=WORKER_ID,
        lease_expires_at=NOW + timedelta(minutes=2),
        commit_outcome="committed",
        sync_status="pending",
    )

    assert repository.has_active_execution(OWNER_ID, workspace.id) is False
    assert repository.blocks_workspace_lifecycle(OWNER_ID, workspace.id) is True
    next_plan_id = "mpl_" + "8" * 32
    with pytest.raises(MigrationConflictError) as blocked_plan:
        repository.create_plan(
            _plan_record(
                plan_id=next_plan_id,
                workspace_id=workspace.id,
                baseline_id=plan.baseline_id,
                baseline_revision=plan.plan.baseline_revision,
            )
        )
    assert blocked_plan.value.code == "migration_execution_active"
    with pytest.raises(WorkspaceMutationBlockedError):
        workspaces.delete(OWNER_ID, workspace.id, workspace.revision)

    settled = repository.transition_execution(
        OWNER_ID,
        committed.execution.id,
        expected_revision=committed.execution.revision,
        allowed_from={"succeeded"},
        status="succeeded",
        lease_owner=WORKER_ID,
        sync_status="conflict",
    )
    assert settled.lease_owner is None
    assert repository.blocks_workspace_lifecycle(OWNER_ID, workspace.id) is False
    workspaces.delete(OWNER_ID, workspace.id, workspace.revision)


def test_plan_creation_during_unsettled_sync_is_rejected_without_target_io() -> None:
    gateway = _SuccessfulGateway()
    service, repository = _service(gateway)
    claimed = _reserve_and_claim(repository)
    applying = repository.transition_execution(
        OWNER_ID,
        claimed.execution.id,
        expected_revision=claimed.execution.revision,
        allowed_from={"reserved"},
        status="applying",
        lease_owner=claimed.lease_owner,
        lease_expires_at=NOW + timedelta(minutes=2),
        transaction_id="42",
        target_identity=TARGET_IDENTITY,
        intended_result=_intended_result(),
    )
    repository.transition_execution(
        OWNER_ID,
        applying.execution.id,
        expected_revision=applying.execution.revision,
        allowed_from={"applying"},
        status="succeeded",
        lease_owner=applying.lease_owner,
        lease_expires_at=NOW + timedelta(minutes=2),
        commit_outcome="committed",
        sync_status="pending",
    )

    with pytest.raises(MigrationServiceError) as blocked:
        service.create_plan(
            OWNER_ID,
            WORKSPACE_ID,
            MigrationPlanCreate(
                expected_workspace_revision=1,
                expected_design_revision=1,
            ),
        )

    assert blocked.value.code == "migration_execution_active"
    assert gateway.introspection_calls == 0


def test_execution_rejects_a_stale_baseline_before_target_io() -> None:
    gateway = _SuccessfulGateway()
    service, repository = _service(gateway)
    current = repository.current_baseline(OWNER_ID, WORKSPACE_ID)
    assert current is not None
    service.create_execution(OWNER_ID, PLAN_ID, _request())
    work = service.execution_coordinator.claim_next(WORKER_ID)
    assert work is not None
    advanced = repository.create_baseline(
        owner_id=OWNER_ID,
        workspace_id=WORKSPACE_ID,
        connection_id=CONNECTION_ID,
        connection_revision=1,
        database="analytics",
        namespace="public",
        design_revision=1,
        content=SchemiiDesignContent(),
        catalog=_catalog(),
        complete=True,
        issues=[],
        source="migration",
        expected_predecessor_id=current.id,
    )

    terminal = service.execution_coordinator.process(work)

    assert terminal.status == "failed"
    assert terminal.commit_outcome == "rolled_back"
    assert terminal.error_code == "baseline_changed"
    assert gateway.execution_calls == 0
    error_detail = repository.get_execution(OWNER_ID, terminal.id).error_detail
    assert error_detail is not None
    assert error_detail["reviewedBaselineId"] == current.id
    assert error_detail["currentBaselineId"] == advanced.id


def test_layout_save_after_reservation_does_not_invalidate_target_authority() -> None:
    workspaces = InMemoryWorkspaceRepository()
    workspace = workspaces.create(
        OWNER_ID,
        WorkspaceCreateRecord(
            name="Layout-safe execution",
            connection_id=CONNECTION_ID,
            database="analytics",
            namespace="public",
        ),
    )
    fingerprint = design_fingerprint(SchemiiDesignContent())
    designs = _DesignRepository(fingerprint, workspace_id=workspace.id)
    repository = InMemoryMigrationRepository(designs)
    _store_plan(repository, workspace_id=workspace.id)
    gateway = _SuccessfulGateway()
    service = MigrationService(
        repository=repository,
        connections=_Connections(),
        postgres=gateway,
        workspaces=workspaces,
        designs=designs,
        clock=lambda: NOW,
    )
    service.create_execution(OWNER_ID, PLAN_ID, _request())

    saved = workspaces.update_layout(
        OWNER_ID,
        workspace.id,
        SchemiiWorkspaceLayoutUpdate(
            expected_revision=workspace.revision,
            expected_connection_revision=1,
            tables=[],
            column_orders=[],
        ),
    )
    assert saved.revision == workspace.revision + 1
    work = service.execution_coordinator.claim_next(WORKER_ID)
    assert work is not None

    terminal = service.execution_coordinator.process(work)

    assert terminal.status == "succeeded"
    assert terminal.sync_status == "succeeded"
    assert gateway.execution_calls == 1


def test_target_change_after_reservation_fails_before_target_io() -> None:
    gateway = _SuccessfulGateway()
    workspaces = _WorkspaceRepository()
    plan = _plan_record()
    designs = _DesignRepository(plan.plan.design_fingerprint)
    repository = InMemoryMigrationRepository(designs)
    _store_plan(repository)
    service = MigrationService(
        repository=repository,
        connections=_Connections(),
        postgres=gateway,
        workspaces=workspaces,
        designs=designs,
        clock=lambda: NOW,
    )
    service.create_execution(OWNER_ID, PLAN_ID, _request())
    workspaces.database = "other_database"
    work = service.execution_coordinator.claim_next(WORKER_ID)
    assert work is not None

    terminal = service.execution_coordinator.process(work)

    assert terminal.status == "failed"
    assert terminal.error_code == "workspace_changed"
    assert gateway.execution_calls == 0


def test_plan_creation_ignores_presentation_only_workspace_revision() -> None:
    gateway = _SuccessfulGateway()
    design = SchemiiDesignContent()
    designs = _DesignRepository(design_fingerprint(design), content=design)
    repository = InMemoryMigrationRepository(designs)
    _seed_baseline(repository)
    service = MigrationService(
        repository=repository,
        connections=_Connections(),
        postgres=gateway,
        workspaces=_WorkspaceRepository(revision=4),
        designs=designs,
        clock=lambda: NOW,
    )

    plan = service.create_plan(
        OWNER_ID,
        WORKSPACE_ID,
        MigrationPlanCreate(
            expected_workspace_revision=1,
            expected_design_revision=1,
        ),
    )

    assert plan.workspace_revision == 4
    assert plan.apply_capable is True
    assert gateway.introspection_calls == 1


def test_plan_creation_rejects_an_imported_workspace_without_its_atomic_baseline() -> None:
    gateway = _SuccessfulGateway()
    design = SchemiiDesignContent()
    designs = _DesignRepository(design_fingerprint(design), content=design)
    service = MigrationService(
        repository=InMemoryMigrationRepository(designs),
        connections=_Connections(),
        postgres=gateway,
        workspaces=_WorkspaceRepository(),
        designs=designs,
        clock=lambda: NOW,
    )

    with pytest.raises(MigrationServiceError) as missing:
        service.create_plan(
            OWNER_ID,
            WORKSPACE_ID,
            MigrationPlanCreate(
                expected_workspace_revision=1,
                expected_design_revision=1,
            ),
        )

    assert missing.value.code == "migration_baseline_missing"
    assert gateway.introspection_calls == 0


@pytest.mark.parametrize(
    ("is_empty", "apply_capable", "blocker_codes", "expected_preconditions"),
    [
        (True, True, [], ("events",)),
        (False, False, ["required_column_population_required"], ()),
    ],
)
def test_plan_creation_uses_exact_table_emptiness_for_required_columns(
    is_empty: bool,
    apply_capable: bool,
    blocker_codes: list[str],
    expected_preconditions: tuple[str, ...],
) -> None:
    catalog = _single_table_catalog()
    desired = import_postgres_catalog(catalog).content
    baseline_content = desired.model_copy(deep=True)
    desired.tables[0].columns.append(
        DesignColumn(
            id="column_" + "7" * 32,
            name="tenant_id",
            data_type="bigint",
            nullable=False,
        )
    )
    designs = _DesignRepository(
        design_fingerprint(desired),
        content=desired,
    )
    repository = InMemoryMigrationRepository(designs)
    _seed_baseline(repository, catalog=catalog, content=baseline_content)
    gateway = _PlanningEmptinessGateway(catalog, is_empty=is_empty)
    service = MigrationService(
        repository=repository,
        connections=_Connections(),
        postgres=gateway,
        workspaces=_WorkspaceRepository(),
        designs=designs,
        clock=lambda: NOW,
    )

    plan = service.create_plan(
        OWNER_ID,
        WORKSPACE_ID,
        MigrationPlanCreate(
            expected_workspace_revision=1,
            expected_design_revision=1,
        ),
    )

    assert plan.apply_capable is apply_capable
    assert [item.code for item in plan.blocking_differences] == blocker_codes
    assert gateway.emptiness_calls == [("events",)]
    stored = repository.get_plan(OWNER_ID, plan.id)
    assert stored.authority.required_empty_tables == expected_preconditions
    assert [step.operation for step in plan.steps] == (["add"] if is_empty else [])


@pytest.mark.parametrize(
    ("is_empty", "requested", "selected", "step_count_positive", "destructive"),
    [
        (True, None, True, True, False),
        (True, [], False, False, False),
        (False, None, False, False, False),
        (False, ["selected"], True, True, True),
    ],
)
def test_physical_column_reorder_is_optional_and_only_defaults_for_empty_tables(
    is_empty: bool,
    requested: list[str] | None,
    selected: bool,
    step_count_positive: bool,
    destructive: bool,
) -> None:
    catalog = build_postgres_catalog(
        database="analytics",
        namespace="public",
        server_version="17.2",
        server_version_num=170002,
        server_timezone="UTC",
        tables=(PostgresTable(
            namespace="public",
            name="events",
            kind="table",
            is_partition=False,
            columns=(
                PostgresColumn(name="id", ordinal=1, data_type="bigint", nullable=False),
                PostgresColumn(name="label", ordinal=2, data_type="text", nullable=True),
            ),
        ),),
        relationships=(),
        functions=(),
        views=(),
        materialized_views=(),
        captured_at=NOW,
    )
    baseline_content = import_postgres_catalog(catalog).content
    desired = baseline_content.model_copy(deep=True)
    desired.tables[0].columns.reverse()
    table_id = desired.tables[0].id
    request_ids = (
        None
        if requested is None
        else [table_id] if requested == ["selected"] else []
    )
    designs = _DesignRepository(design_fingerprint(desired), content=desired)
    repository = InMemoryMigrationRepository(designs)
    _seed_baseline(repository, catalog=catalog, content=baseline_content)
    service = MigrationService(
        repository=repository,
        connections=_Connections(),
        postgres=_PlanningEmptinessGateway(catalog, is_empty=is_empty),
        workspaces=_WorkspaceRepository(),
        designs=designs,
        clock=lambda: NOW,
    )

    plan = service.create_plan(
        OWNER_ID,
        WORKSPACE_ID,
        MigrationPlanCreate(
            expected_workspace_revision=1,
            expected_design_revision=1,
            allow_destructive=destructive,
            rebuild_table_ids=request_ids,
        ),
    )

    assert len(plan.column_order_rebuilds) == 1
    assert plan.column_order_rebuilds[0].selected is selected
    assert bool(plan.steps) is step_count_positive
    assert plan.destructive is destructive
    stored = repository.get_plan(OWNER_ID, plan.id)
    assert stored.authority.rebuild_table_ids == ((table_id,) if selected else ())
    assert stored.authority.required_empty_tables == (
        ("events",) if selected and is_empty else ()
    )


@pytest.mark.parametrize("execution_status", ["reserved", "applying"])
def test_drift_reconciliation_is_blocked_by_unsettled_execution(
    execution_status: str,
) -> None:
    workspaces = InMemoryWorkspaceRepository()
    workspace = workspaces.create(OWNER_ID, WorkspaceCreateRecord(name="Drift guard"))
    designs = _DesignRepository(
        design_fingerprint(SchemiiDesignContent()),
        workspace_id=workspace.id,
    )
    repository = InMemoryMigrationRepository(designs)
    executing = _store_plan(repository, workspace_id=workspace.id)
    drift_plan = _plan_record(
        plan_id="mpl_" + "9" * 32,
        workspace_id=workspace.id,
        baseline_id=executing.baseline_id,
        baseline_revision=executing.plan.baseline_revision,
    )
    repository.create_plan(drift_plan)
    MigrationService(
        repository=repository,
        connections=_Connections(),
        postgres=_SuccessfulGateway(),
        workspaces=workspaces,
        designs=designs,
        clock=lambda: NOW,
    )
    repository.reserve_execution(
        OWNER_ID,
        PLAN_ID,
        REVIEW_DIGEST,
        False,
        False,
        reserved_at=NOW,
    )
    claimed = repository.claim_next_execution(
        claimed_at=NOW,
        lease_owner=WORKER_ID,
        lease_expires_at=NOW + timedelta(minutes=2),
    )
    assert claimed is not None
    if execution_status == "applying":
        repository.transition_execution(
            OWNER_ID,
            claimed.record.execution.id,
            expected_revision=claimed.record.execution.revision,
            allowed_from={"reserved"},
            status="applying",
            lease_owner=claimed.record.lease_owner,
            lease_expires_at=NOW + timedelta(minutes=2),
            transaction_id="42",
            target_identity=TARGET_IDENTITY,
        )
    baseline = repository.current_baseline(OWNER_ID, workspace.id)
    assert baseline is not None

    with pytest.raises(MigrationConflictError) as blocked:
        repository.reconcile_drift(
            record=drift_plan,
            expected_design_revision=1,
            resolved_content=SchemiiDesignContent(),
            live_content=SchemiiDesignContent(),
            resolutions=[],
        )

    assert blocked.value.code == "migration_execution_active"
    assert designs.replace_calls == 0
    assert repository.current_baseline(OWNER_ID, workspace.id) == baseline


def test_recovery_refuses_transaction_status_from_a_different_target() -> None:
    clock = _MutableClock()
    gateway = _WrongTargetGateway()
    service, repository = _service(
        gateway,
        clock=clock,
        lease_ttl=timedelta(seconds=30),
    )
    claimed = _reserve_and_claim(
        repository,
        lease_expires_at=NOW + timedelta(seconds=30),
    )
    applying = repository.transition_execution(
        OWNER_ID,
        claimed.execution.id,
        expected_revision=claimed.execution.revision,
        allowed_from={"reserved"},
        status="applying",
        lease_owner=claimed.lease_owner,
        lease_expires_at=NOW + timedelta(seconds=30),
        transaction_id="42",
        target_identity=TARGET_IDENTITY,
        intended_result=_intended_result(),
    )
    clock.value = NOW + timedelta(seconds=31)
    work = service.execution_coordinator.claim_next("mls_" + "7" * 32)
    assert work is not None
    assert work.kind == "reconcile"

    terminal = service.execution_coordinator.process(work)

    assert terminal.status == "reconciliation_required"
    assert terminal.commit_outcome == "uncertain"
    assert terminal.error_code == "migration_target_identity_changed"
    assert gateway.status_calls == 1
    assert gateway.execution_calls == 0
    stored = repository.get_execution(OWNER_ID, applying.execution.id)
    assert stored.error_detail is not None
    assert stored.error_detail["expectedTargetIdentity"] == TARGET_IDENTITY
    assert stored.error_detail["currentTargetIdentity"]["database_oid"] == "24576"


def test_http_submission_returns_202_while_target_io_is_blocked() -> None:
    gateway = _BlockingGateway()
    service, repository = _service(gateway)
    application = create_app(
        ApplicationServices(
            metadata=MetadataRepositories(connections=InMemoryConnectionRepository()),
            connections=_Connections(),
            postgres=gateway,
            workspaces=_WorkspaceRepository(),
            designs=service._designs,
            migrations=service,
        )
    )
    application.dependency_overrides[get_current_principal] = lambda: Principal(
        user_id=OWNER_ID,
        authentication_source="local_prototype",
    )

    try:
        with TestClient(application, base_url="http://localhost") as api:
            response = api.post(
                f"/api/v1/schemii/migration-plans/{PLAN_ID}/executions",
                json={"reviewDigest": REVIEW_DIGEST},
            )
            assert response.status_code == 202
            assert response.json()["status"] == "reserved"
            execution_id = response.json()["id"]
            assert gateway.started.wait(timeout=2)

            duplicate = api.post(
                f"/api/v1/schemii/migration-plans/{PLAN_ID}/executions",
                json={"reviewDigest": REVIEW_DIGEST},
            )
            assert duplicate.status_code == 202
            assert duplicate.json()["id"] == execution_id
            assert gateway.execution_calls == 0
            gateway.release.set()
    finally:
        gateway.release.set()

    terminal = repository.get_execution(OWNER_ID, execution_id).execution
    assert terminal.status == "succeeded"
    assert terminal.sync_status == "succeeded"
    assert gateway.execution_calls == 1


def test_durable_composition_never_silently_uses_memory_migrations() -> None:
    plan = _plan_record()
    designs = _DesignRepository(plan.plan.design_fingerprint)
    services = ApplicationServices(
        metadata=MetadataRepositories(
            connections=InMemoryConnectionRepository(),
            storage="postgresql",
            durable=True,
        ),
        connections=_Connections(),
        postgres=_SuccessfulGateway(),
        workspaces=_WorkspaceRepository(),
        designs=designs,
        migrations=None,
    )

    with pytest.raises(
        RuntimeError,
        match="Durable application services require an explicit durable migration repository",
    ):
        create_app(services)
