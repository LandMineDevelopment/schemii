from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import Barrier
from types import SimpleNamespace
from typing import Any, Iterator

import pytest

from schemii.common.postgres.errors import PostgresCommitUncertainError
from schemii.common.postgres.models import build_postgres_catalog
from schemii.schemii.designs.models import SchemiiDesignContent
from schemii.schemii.designs.store import design_fingerprint
from schemii.schemii.migrations.models import (
    MigrationExecutionCreate,
    MigrationPlan,
)
from schemii.schemii.migrations.repository import (
    InMemoryMigrationRepository,
    MigrationConflictError,
    PlanAuthority,
    PlanRecord,
)
from schemii.schemii.migrations.service import MigrationService, MigrationServiceError


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
OWNER_ID = "user-test"
WORKSPACE_ID = "ws_" + "1" * 32
CONNECTION_ID = "pg_" + "2" * 32
PLAN_ID = "mpl_" + "3" * 32
REVIEW_DIGEST = "4" * 64


def _plan_record(
    *,
    destructive: bool = False,
    external_acknowledgement: bool = False,
) -> PlanRecord:
    content = SchemiiDesignContent()
    fingerprint = design_fingerprint(content)
    catalog = build_postgres_catalog(
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
    plan = MigrationPlan(
        id=PLAN_ID,
        workspace_id=WORKSPACE_ID,
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


def _claimed_repository() -> tuple[InMemoryMigrationRepository, Any]:
    repository = InMemoryMigrationRepository(SimpleNamespace())
    repository.create_plan(_plan_record())
    claim = repository.claim_execution(
        OWNER_ID,
        PLAN_ID,
        REVIEW_DIGEST,
        False,
        False,
        claimed_at=NOW,
        lease_owner="mls_" + "6" * 32,
        lease_expires_at=NOW + timedelta(minutes=30),
    )
    assert claim.claimed_now is True
    return repository, claim.record


class _WorkspaceRepository:
    def get(self, owner_id: str, workspace_id: str) -> Any:
        assert (owner_id, workspace_id) == (OWNER_ID, WORKSPACE_ID)
        return SimpleNamespace(
            revision=1,
            connection_id=CONNECTION_ID,
            database="analytics",
            namespace="public",
        )


class _DesignRepository:
    def __init__(self, fingerprint: str) -> None:
        self._fingerprint = fingerprint

    def get(self, owner_id: str, workspace_id: str) -> Any:
        assert (owner_id, workspace_id) == (OWNER_ID, WORKSPACE_ID)
        return SimpleNamespace(revision=1, fingerprint=self._fingerprint)


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


def test_duplicate_execution_returns_the_same_receipt_without_target_io() -> None:
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
    request = MigrationExecutionCreate(
        review_digest=REVIEW_DIGEST,
        confirm_destructive=True,
        confirm_external_changes=True,
    )

    first = service.create_execution(OWNER_ID, PLAN_ID, request)
    duplicate = service.create_execution(OWNER_ID, PLAN_ID, request)

    assert first.status == "uncertain"
    assert duplicate == first
    assert gateway.execution_calls == 1

    with pytest.raises(MigrationServiceError) as missing_confirmation:
        service.create_execution(
            OWNER_ID,
            PLAN_ID,
            request.model_copy(update={"confirm_destructive": False}),
        )
    assert missing_confirmation.value.code == "destructive_confirmation_required"
    assert gateway.execution_calls == 1


def test_execution_transition_is_revision_cas_and_rejects_illegal_edges() -> None:
    repository, claimed = _claimed_repository()
    ready = Barrier(2)

    def finish() -> str:
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
        outcomes = list(pool.map(lambda _: finish(), range(2)))

    assert sorted(outcomes) == ["migration_execution_changed", "transitioned"]

    repository, claimed = _claimed_repository()
    applying = repository.transition_execution(
        OWNER_ID,
        claimed.execution.id,
        expected_revision=claimed.execution.revision,
        allowed_from={"reserved"},
        status="applying",
        lease_owner=claimed.lease_owner,
        lease_expires_at=NOW + timedelta(minutes=30),
    )
    with pytest.raises(MigrationConflictError) as illegal:
        repository.transition_execution(
            OWNER_ID,
            applying.execution.id,
            expected_revision=applying.execution.revision,
            allowed_from={"applying"},
            status="reserved",
            lease_owner=applying.lease_owner,
            lease_expires_at=NOW + timedelta(minutes=30),
        )
    assert illegal.value.code == "migration_execution_transition_invalid"


def test_transition_can_explicitly_clear_evidence_fields() -> None:
    repository, claimed = _claimed_repository()
    applying = repository.transition_execution(
        OWNER_ID,
        claimed.execution.id,
        expected_revision=claimed.execution.revision,
        allowed_from={"reserved"},
        status="applying",
        lease_owner=claimed.lease_owner,
        lease_expires_at=NOW + timedelta(minutes=30),
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


def test_expired_execution_lease_is_discoverable_and_recoverable() -> None:
    repository, claimed = _claimed_repository()
    expiry = claimed.lease_expires_at
    assert expiry is not None
    assert repository.list_recoverable_executions(expiry - timedelta(seconds=1), 10) == []
    assert repository.list_recoverable_executions(expiry, 10) == [claimed]

    with pytest.raises(MigrationConflictError) as active:
        repository.transition_execution(
            OWNER_ID,
            claimed.execution.id,
            expected_revision=claimed.execution.revision,
            allowed_from={"reserved"},
            status="failed",
            lease_owner="mls_" + "7" * 32,
        )
    assert active.value.code == "migration_execution_lease_active"

    recovered = repository.transition_execution(
        OWNER_ID,
        claimed.execution.id,
        expected_revision=claimed.execution.revision,
        allowed_from={"reserved"},
        status="failed",
        lease_owner="mls_" + "7" * 32,
        recover_expired_before=expiry,
        commit_outcome="rolled_back",
        error_code="migration_never_started",
    )
    assert recovered.execution.status == "failed"
    assert recovered.lease_owner is None
    assert recovered.lease_expires_at is None
    assert repository.list_recoverable_executions(expiry, 10) == []
