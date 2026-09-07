from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from schemii.common.postgres.models import PostgresColumn, PostgresTable, build_postgres_catalog
from schemii.schemii.designs.importer import import_postgres_catalog
from schemii.schemii.designs.store import design_fingerprint
from schemii.schemii.migrations.models import MigrationDriftResolutionRequest, MigrationPlan
from schemii.schemii.migrations.planner import reconcile_designs
from schemii.schemii.migrations.repository import PlanAuthority, PlanRecord
from schemii.schemii.migrations.service import MigrationService, MigrationServiceError


NOW = datetime(2026, 9, 5, tzinfo=timezone.utc)
OWNER = "drift-test"
WORKSPACE = "ws_" + "1" * 32
CONNECTION = "pg_" + "2" * 32
PLAN = "mpl_" + "3" * 32
DIGEST = "4" * 64


def _catalog(data_type):
    return build_postgres_catalog(
        database="demo", namespace="public", server_version="17.2",
        server_version_num=170002, server_timezone="UTC", captured_at=NOW,
        tables=(PostgresTable(
            namespace="public", name="customers", kind="table", is_partition=False,
            columns=tuple(PostgresColumn(name=name, ordinal=index, data_type=data_type, nullable=True)
                          for index, name in enumerate(("name", "region"), 1)),
        ),),
        relationships=(), functions=(), views=(), materialized_views=(),
    )


class _Repository:
    def __init__(self, record):
        self.record = record
        self.saved = []
        self.receipt = object()

    def get_plan(self, owner_id, plan_id):
        assert (owner_id, plan_id) == (OWNER, PLAN)
        return self.record

    def reconcile_drift(self, **kwargs):
        self.saved.append(kwargs)
        return self.receipt


class _Gateway:
    """Only catalog inspection is available: resolving choices must not run DDL."""

    def __init__(self, catalog):
        self.catalog = catalog
        self.inspections = 0

    def introspect(self, connection, namespace):
        assert (connection.revision, namespace) == (1, "public")
        self.inspections += 1
        return self.catalog


class _Connections:
    @contextmanager
    def use(self, owner_id, connection_id):
        assert (owner_id, connection_id) == (OWNER, CONNECTION)
        yield SimpleNamespace(revision=1)


class _Workspaces:
    def get(self, owner_id, workspace_id):
        assert (owner_id, workspace_id) == (OWNER, WORKSPACE)
        return SimpleNamespace(connection_id=CONNECTION, database="demo", namespace="public")


@pytest.fixture
def drift():
    baseline = import_postgres_catalog(_catalog("text")).content
    desired = baseline.model_copy(deep=True)
    for column in desired.tables[0].columns:
        column.data_type = "character varying(80)"
    live = _catalog("character varying(40)")
    reconciliation = reconcile_designs(baseline, desired, live)
    assert len(reconciliation.conflicts) == 2
    plan = MigrationPlan(
        id=PLAN, workspace_id=WORKSPACE, status="blocked", workspace_revision=1,
        design_revision=7, design_fingerprint=design_fingerprint(desired),
        baseline_revision=2, catalog_fingerprint=live.fingerprint,
        merged_design_fingerprint=design_fingerprint(desired), review_digest=DIGEST,
        drift_status="conflicting", complete=True, apply_capable=False, destructive=False,
        requires_external_change_acknowledgement=False, steps=[],
        conflicts=reconciliation.conflicts, created_at=NOW, expires_at=NOW + timedelta(minutes=15),
    )
    authority = PlanAuthority(
        baseline_content=baseline, desired_content=desired, merged_content=desired,
        live_content=reconciliation.live, live_catalog=live, allow_destructive=False,
        connection_id=CONNECTION, connection_revision=1, database="demo", namespace="public",
    )
    repository = _Repository(PlanRecord(owner_id=OWNER, baseline_id="mbl_" + "5" * 32,
                                      plan=plan, authority=authority))
    gateway = _Gateway(live)
    service = MigrationService(repository=repository, connections=_Connections(), postgres=gateway,
                               workspaces=_Workspaces(), designs=object(), clock=lambda: NOW)
    request = MigrationDriftResolutionRequest(
        expected_design_revision=7, review_digest=DIGEST,
        resolutions=[{"conflict_id": item.id, "resolution": "pull_live"}
                     for item in plan.conflicts],
    )
    return SimpleNamespace(service=service, repository=repository, gateway=gateway, request=request)


@pytest.mark.parametrize(("updates", "error_code"), [
    ({"review_digest": "f" * 64}, "migration_review_changed"),
    ({"expected_design_revision": 6}, "design_changed"),
])
def test_resolution_requires_exact_review_and_design_revision(drift, updates, error_code):
    with pytest.raises(MigrationServiceError) as raised:
        drift.service.resolve_drift(OWNER, PLAN, drift.request.model_copy(update=updates))
    assert raised.value.code == error_code
    assert drift.gateway.inspections == 0
    assert not drift.repository.saved


@pytest.mark.parametrize("unknown", [False, True])
def test_resolution_requires_every_server_conflict_and_no_unknown_conflicts(drift, unknown):
    choices = list(drift.request.resolutions[:1])
    if unknown:
        choices.append(choices[0].model_copy(update={"conflict_id": "mcf_" + "f" * 32}))
    with pytest.raises(MigrationServiceError) as raised:
        drift.service.resolve_drift(OWNER, PLAN, drift.request.model_copy(update={"resolutions": choices}))
    assert raised.value.code == "incomplete_drift_resolutions"
    assert drift.gateway.inspections == 0
    assert not drift.repository.saved


def test_duplicate_conflict_choices_are_rejected_by_request_contract(drift):
    body = drift.request.model_dump()
    body["resolutions"] = [body["resolutions"][0]] * 2
    with pytest.raises(ValidationError, match="each migration conflict may be resolved once"):
        MigrationDriftResolutionRequest.model_validate(body)


def test_resolution_obeys_allowed_choices_for_each_conflict(drift):
    record = drift.repository.record
    conflicts = [item.model_copy(update={"allowed_resolutions": ["pull_live"]})
                 for item in record.plan.conflicts]
    drift.repository.record = replace(record, plan=record.plan.model_copy(update={"conflicts": conflicts}))
    choices = [item.model_copy(update={"resolution": "keep_design"}) for item in drift.request.resolutions]
    with pytest.raises(MigrationServiceError) as raised:
        drift.service.resolve_drift(OWNER, PLAN, drift.request.model_copy(update={"resolutions": choices}))
    assert raised.value.code == "drift_resolution_not_allowed"
    assert drift.gateway.inspections == 0
    assert not drift.repository.saved


def test_resolution_refuses_catalog_changed_since_review(drift):
    drift.gateway.catalog = _catalog("character varying(20)")
    with pytest.raises(MigrationServiceError) as raised:
        drift.service.resolve_drift(OWNER, PLAN, drift.request)
    assert raised.value.code == "catalog_changed"
    assert drift.gateway.inspections == 1
    assert not drift.repository.saved


def test_mixed_resolution_bundle_reconciles_once_without_executing_sql(drift):
    choices = list(drift.request.resolutions)
    choices[0] = choices[0].model_copy(update={"resolution": "keep_design"})
    request = drift.request.model_copy(update={"resolutions": choices})
    result = drift.service.resolve_drift(OWNER, PLAN, request)
    assert result is drift.repository.receipt
    assert drift.gateway.inspections == 1
    assert len(drift.repository.saved) == 1
    saved = drift.repository.saved[0]
    assert saved["expected_design_revision"] == 7
    assert saved["resolutions"] == [item.model_dump(mode="json", by_alias=True) for item in choices]
    resolved_types = [column.data_type for column in saved["resolved_content"].tables[0].columns]
    assert sorted(resolved_types) == ["character varying(40)", "character varying(80)"]
    assert {column.data_type for column in saved["live_content"].tables[0].columns} == {"character varying(40)"}
