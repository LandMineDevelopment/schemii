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
from schemii.common.postgres.errors import PostgresCommitUncertainError
from schemii.common.postgres.gateway import PostgresMigrationResult
from schemii.common.postgres.models import build_postgres_catalog
from schemii.main import ApplicationServices, create_app
from schemii.schemii.designs.models import SchemiiDesignContent
from schemii.schemii.designs.store import design_fingerprint
from schemii.schemii.migrations.models import (
    MigrationExecutionCreate,
    MigrationPlan,
)
from schemii.schemii.migrations.repository import (
    ExecutionWork,
    InMemoryMigrationRepository,
    MigrationConflictError,
    PlanAuthority,
    PlanRecord,
)
from schemii.schemii.migrations.service import MigrationService, MigrationServiceError
from schemii.schemii.migrations.worker import MigrationExecutionWorker
from schemii.schemii.workspaces.models import SchemiiWorkspaceCreate
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


def _catalog() -> Any:
    return build_postgres_catalog(
        database="analytics",
        namespace="public",
        server_version="17.2",
        server_version_num=170002,
        server_timezone="UTC",
        tables=(),
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
) -> PlanRecord:
    content = SchemiiDesignContent()
    fingerprint = design_fingerprint(content)
    catalog = _catalog()
    plan = MigrationPlan(
        id=plan_id,
        workspace_id=workspace_id,
        status="reviewable",
        workspace_revision=1,
        design_revision=1,
        design_fingerprint=fingerprint,
        baseline_revision=1,
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
        baseline_id="mbl_" + "5" * 32,
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
        ),
    )


class _WorkspaceRepository:
    def get(self, owner_id: str, workspace_id: str) -> Any:
        assert (owner_id, workspace_id) == (OWNER_ID, WORKSPACE_ID)
        return SimpleNamespace(
            revision=1,
            connection_id=CONNECTION_ID,
            database="analytics",
            namespace="public",
        )


class _MissingWorkspaceRepository:
    def get(self, owner_id: str, workspace_id: str) -> Any:
        del owner_id, workspace_id
        raise WorkspaceNotFoundError("Workspace was not found")


class _DesignRepository:
    def __init__(self, fingerprint: str) -> None:
        self._fingerprint = fingerprint

    def get(self, owner_id: str, workspace_id: str) -> Any:
        assert (owner_id, workspace_id) == (OWNER_ID, WORKSPACE_ID)
        return SimpleNamespace(revision=1, fingerprint=self._fingerprint)


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
        yield SimpleNamespace(revision=1)


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
        on_started("42", {"database": "analytics"})
        raise PostgresCommitUncertainError()


class _SuccessfulGateway:
    def __init__(self) -> None:
        self.execution_calls = 0
        self.status_calls = 0

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
        identity = {"database": "analytics"}
        on_started("42", identity)
        on_intended(catalog)
        return PostgresMigrationResult(
            catalog=catalog,
            transaction_id="42",
            target_identity=identity,
            completed_step_count=0,
        )

    def transaction_status(self, connection: Any, transaction_id: str) -> str:
        del connection
        assert transaction_id == "42"
        self.status_calls += 1
        return "committed"


class _BlockingGateway(_SuccessfulGateway):
    def __init__(self) -> None:
        super().__init__()
        self.started = Event()
        self.release = Event()

    def execute_migration(self, *args: Any, **kwargs: Any) -> PostgresMigrationResult:
        self.started.set()
        assert self.release.wait(timeout=5), "test did not release the target gateway"
        return super().execute_migration(*args, **kwargs)


class _MutableClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


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
    except Exception:
        active_repository.create_plan(plan)
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
    repository.create_plan(plan_record)
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
    repository.create_plan(plan)
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
        target_identity={"database": "analytics"},
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
        target_identity={"database": "analytics"},
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
        target_identity={"database": "analytics"},
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
    repository.create_plan(_plan_record())
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
        target_identity={"database": "analytics"},
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
    designs = _DesignRepository(_plan_record().plan.design_fingerprint)
    workspaces = InMemoryWorkspaceRepository()
    workspace = workspaces.create(
        OWNER_ID,
        SchemiiWorkspaceCreate(name="Lifecycle guard"),
    )
    repository = InMemoryMigrationRepository(designs)
    plan = _plan_record(workspace_id=workspace.id)
    repository.create_plan(plan)
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
    repository.create_plan(
        _plan_record(plan_id=next_plan_id, workspace_id=workspace.id)
    )
    with pytest.raises(MigrationConflictError) as blocked_execution:
        repository.reserve_execution(
            OWNER_ID,
            next_plan_id,
            REVIEW_DIGEST,
            False,
            False,
            reserved_at=NOW,
        )
    assert blocked_execution.value.code == "migration_execution_active"
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
