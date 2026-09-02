from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import SecretStr

from schemii.common.connections.models import PostgresConnectionCreate
from schemii.common.postgres.models import build_postgres_catalog
from schemii.schemii.designs.models import SchemiiDesignContent
from schemii.schemii.designs.store import design_fingerprint
from schemii.schemii.migrations.models import MigrationPlan, MigrationPlanStatus
from schemii.schemii.migrations.repository import (
    MigrationNotFoundError,
    PlanAuthority,
    PlanRecord,
    PostgresMigrationRepository,
)
from schemii.schemii.workspaces.models import SchemiiWorkspaceCreate
from schemii.schemii.workspaces.postgres_store import PostgresWorkspaceRepository
from tests.integration.postgres_fixture import PostgresMetadataHarness


REFERENCE_TIME = datetime(2030, 1, 1, tzinfo=timezone.utc)


def _catalog() -> Any:
    return build_postgres_catalog(
        database="application_data",
        namespace="public",
        server_version="17.2",
        server_version_num=170002,
        server_timezone="UTC",
        tables=(),
        relationships=(),
        functions=(),
        views=(),
        materialized_views=(),
        captured_at=REFERENCE_TIME,
    )


def _plan_record(
    marker: str,
    *,
    owner_id: str,
    workspace_id: str,
    connection_id: str,
    baseline_id: str,
    status: MigrationPlanStatus = "reviewable",
    expired: bool = True,
) -> PlanRecord:
    content = SchemiiDesignContent()
    fingerprint = design_fingerprint(content)
    catalog = _catalog()
    created_at = (
        REFERENCE_TIME - timedelta(hours=2) if expired else REFERENCE_TIME
    )
    expires_at = (
        REFERENCE_TIME - timedelta(hours=1)
        if expired
        else REFERENCE_TIME + timedelta(hours=1)
    )
    return PlanRecord(
        owner_id=owner_id,
        baseline_id=baseline_id,
        plan=MigrationPlan(
            id="mpl_" + marker * 32,
            workspace_id=workspace_id,
            status=status,
            workspace_revision=1,
            design_revision=0,
            design_fingerprint=fingerprint,
            baseline_revision=1,
            catalog_fingerprint=catalog.fingerprint,
            merged_design_fingerprint=fingerprint,
            review_digest=marker * 64,
            drift_status="conflicting" if status == "blocked" else "none",
            complete=True,
            apply_capable=status == "reviewable",
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
            connection_id=connection_id,
            connection_revision=1,
            database="application_data",
            namespace="public",
        ),
    )


def test_postgres_plan_retention_preserves_execution_and_reconciliation_records(
    postgres_metadata: PostgresMetadataHarness,
) -> None:
    owner_id = postgres_metadata.owner_id
    target = postgres_metadata.repositories.connections.create(
        owner_id,
        PostgresConnectionCreate(
            name="Migration retention target",
            host="application-postgres",
            database="application_data",
            username="application_user",
            password=SecretStr("target-only-secret"),
        ),
    )
    workspace = PostgresWorkspaceRepository(
        postgres_metadata.connection_factory
    ).create(
        owner_id,
        SchemiiWorkspaceCreate(
            name="Migration plan retention",
            connection_id=target.id,
            database=target.database,
            namespace="public",
        ),
    )
    repository = PostgresMigrationRepository(postgres_metadata.connection_factory)
    baseline = repository.create_baseline(
        owner_id=owner_id,
        workspace_id=workspace.id,
        connection_id=target.id,
        connection_revision=target.revision,
        database=target.database,
        namespace="public",
        design_revision=0,
        content=SchemiiDesignContent(),
        catalog=_catalog(),
        complete=True,
        issues=[],
        source="target_attach",
        expected_predecessor_id=None,
    )
    abandoned = _plan_record(
        "1",
        owner_id=owner_id,
        workspace_id=workspace.id,
        connection_id=target.id,
        baseline_id=baseline.id,
    )
    execution_record = _plan_record(
        "2",
        owner_id=owner_id,
        workspace_id=workspace.id,
        connection_id=target.id,
        baseline_id=baseline.id,
    )
    reconciliation_record = _plan_record(
        "3",
        owner_id=owner_id,
        workspace_id=workspace.id,
        connection_id=target.id,
        baseline_id=baseline.id,
        status="blocked",
    )
    for record in (abandoned, execution_record, reconciliation_record):
        repository.create_plan(record)

    # Reads are side-effect free: retention runs only when a replacement review
    # is created, never while resolving the current baseline.
    assert repository.current_baseline(owner_id, workspace.id) == baseline
    assert repository.get_plan(owner_id, abandoned.plan.id).plan == abandoned.plan

    reservation = repository.reserve_execution(
        owner_id,
        execution_record.plan.id,
        execution_record.plan.review_digest,
        False,
        False,
        reserved_at=REFERENCE_TIME - timedelta(minutes=90),
    )
    assert reservation.reserved_now is True
    with postgres_metadata.connection_factory() as connection:
        with connection.cursor() as cursor:
            # Keep the review status eligible for cleanup so the execution FK,
            # rather than the status alone, proves recovery evidence is retained.
            cursor.execute(
                "UPDATE schemii.migration_plans SET status = 'reviewable' WHERE id = %s",
                (execution_record.plan.id,),
            )
            cursor.execute(
                """
                INSERT INTO schemii.drift_reconciliations (
                    id, plan_id, workspace_id, owner_id, review_digest,
                    previous_design_revision, design_revision,
                    previous_baseline_revision, baseline_revision,
                    catalog_fingerprint, resolutions, external_changes
                ) VALUES (%s, %s, %s, %s, %s, 0, 1, 1, 1, %s, '[]'::jsonb, '[]'::jsonb)
                """,
                (
                    "mdr_" + "4" * 32,
                    reconciliation_record.plan.id,
                    workspace.id,
                    owner_id,
                    reconciliation_record.plan.review_digest,
                    reconciliation_record.plan.catalog_fingerprint,
                ),
            )
        connection.commit()

    current = _plan_record(
        "5",
        owner_id=owner_id,
        workspace_id=workspace.id,
        connection_id=target.id,
        baseline_id=baseline.id,
        expired=False,
    )
    repository.create_plan(current)

    with pytest.raises(MigrationNotFoundError):
        repository.get_plan(owner_id, abandoned.plan.id)
    assert repository.get_plan(owner_id, execution_record.plan.id).plan.id == (
        execution_record.plan.id
    )
    assert repository.get_plan(owner_id, reconciliation_record.plan.id).plan.id == (
        reconciliation_record.plan.id
    )
    assert repository.get_plan(owner_id, current.plan.id).plan == current.plan

    with postgres_metadata.connection_factory() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id
                FROM schemii.migration_plans
                WHERE owner_id = %s
                ORDER BY id
                """,
                (owner_id,),
            )
            assert [row["id"] for row in cursor.fetchall()] == [
                execution_record.plan.id,
                reconciliation_record.plan.id,
                current.plan.id,
            ]
