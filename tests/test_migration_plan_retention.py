from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from schemii.common.postgres.models import build_postgres_catalog
from schemii.schemii.designs.models import SchemiiDesignContent
from schemii.schemii.designs.store import design_fingerprint
from schemii.schemii.migrations.models import MigrationPlan, MigrationPlanStatus
from schemii.schemii.migrations.repository import (
    InMemoryMigrationRepository,
    MigrationNotFoundError,
    PlanAuthority,
    PlanRecord,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
OWNER_ID = "retention-owner"
OTHER_OWNER_ID = "another-retention-owner"
WORKSPACE_ID = "ws_" + "1" * 32
CONNECTION_ID = "pg_" + "2" * 32
BASELINE_ID = "mbl_" + "3" * 32


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
    marker: str,
    *,
    owner_id: str = OWNER_ID,
    status: MigrationPlanStatus = "reviewable",
    expired: bool = True,
) -> PlanRecord:
    content = SchemiiDesignContent()
    fingerprint = design_fingerprint(content)
    catalog = _catalog()
    created_at = NOW - timedelta(hours=2) if expired else NOW
    expires_at = NOW - timedelta(hours=1) if expired else NOW + timedelta(hours=1)
    return PlanRecord(
        owner_id=owner_id,
        baseline_id=BASELINE_ID,
        plan=MigrationPlan(
            id="mpl_" + marker * 32,
            workspace_id=WORKSPACE_ID,
            status=status,
            workspace_revision=1,
            design_revision=1,
            design_fingerprint=fingerprint,
            baseline_revision=1,
            catalog_fingerprint=catalog.fingerprint,
            merged_design_fingerprint=fingerprint,
            review_digest=marker * 64,
            drift_status="none",
            complete=True,
            apply_capable=True,
            destructive=False,
            requires_external_change_acknowledgement=False,
            steps=[],
            created_at=created_at,
            expires_at=expires_at,
        ),
        authority=PlanAuthority(
            baseline_content=content,
            desired_content=content,
            merged_content=content,
            live_content=content,
            live_catalog=catalog,
            allow_destructive=False,
            connection_id=CONNECTION_ID,
            connection_revision=1,
            database="analytics",
            namespace="public",
        ),
    )


def _assert_missing(
    repository: InMemoryMigrationRepository,
    owner_id: str,
    plan_id: str,
) -> None:
    with pytest.raises(MigrationNotFoundError):
        repository.get_plan(owner_id, plan_id)


def test_new_plan_prunes_only_expired_unclaimed_owner_reviews() -> None:
    repository = InMemoryMigrationRepository(designs=object())  # type: ignore[arg-type]
    reviewable = _plan_record("1")
    blocked = _plan_record("2", status="blocked")
    explicitly_expired = _plan_record("3", status="expired")
    claimed = _plan_record("4")
    resolved = _plan_record("5", status="resolved")
    another_owner = _plan_record("6", owner_id=OTHER_OWNER_ID)

    for record in (
        reviewable,
        blocked,
        explicitly_expired,
        claimed,
        resolved,
        another_owner,
    ):
        repository.create_plan(record)
    reservation = repository.reserve_execution(
        OWNER_ID,
        claimed.plan.id,
        claimed.plan.review_digest,
        False,
        False,
        reserved_at=NOW - timedelta(minutes=90),
    )
    assert reservation.reserved_now is True
    repository.transition_execution(
        OWNER_ID,
        reservation.record.execution.id,
        expected_revision=reservation.record.execution.revision,
        allowed_from={"reserved"},
        status="failed",
        commit_outcome="rolled_back",
        error_code="test_execution_settled",
    )

    current = _plan_record("7", expired=False)
    repository.create_plan(current)

    for abandoned in (reviewable, blocked, explicitly_expired):
        _assert_missing(repository, OWNER_ID, abandoned.plan.id)
    assert repository.get_plan(OWNER_ID, claimed.plan.id).plan.status == "claimed"
    assert repository.get_plan(OWNER_ID, resolved.plan.id).plan.status == "resolved"
    assert repository.get_plan(OTHER_OWNER_ID, another_owner.plan.id).plan == another_owner.plan
    assert repository.get_plan(OWNER_ID, current.plan.id).plan == current.plan


def test_new_plan_does_not_prune_an_unexpired_prior_review() -> None:
    repository = InMemoryMigrationRepository(designs=object())  # type: ignore[arg-type]
    existing = _plan_record("8", expired=False)
    repository.create_plan(existing)

    repository.create_plan(_plan_record("9", expired=False))

    assert repository.get_plan(OWNER_ID, existing.plan.id).plan == existing.plan
