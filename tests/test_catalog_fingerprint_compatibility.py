"""Historical wire fixtures frozen before versioned catalog evidence existed."""

import json
import pytest
from schemii.common.postgres.models import PostgresCatalog, compute_catalog_fingerprint

LEGACY_JSON = '{"database":"analytics","namespace":"public","server_version":"17.2","server_version_num":170002,"server_timezone":"UTC","types":[{"namespace":"public","name":"customer_state","kind":"enum","definition":"CREATE TYPE customer_state AS ENUM (\'active\', \'paused\')"}],"tables":[{"namespace":"public","name":"customers","kind":"table","is_partition":false,"partition_key":null,"columns":[{"name":"id","ordinal":1,"data_type":"bigint","nullable":false,"default_expression":null,"identity":null,"generated":null,"collation_schema":null,"collation_name":null},{"name":"name","ordinal":2,"data_type":"text","nullable":false,"default_expression":null,"identity":null,"generated":null,"collation_schema":null,"collation_name":null},{"name":"normalized_name","ordinal":3,"data_type":"text","nullable":true,"default_expression":"lower(name)","identity":null,"generated":"stored","collation_schema":null,"collation_name":null}],"primary_key":{"name":"customers_pkey","table":"customers","columns":["id"],"definition":"PRIMARY KEY (id)","validated":true,"deferrable":false,"initially_deferred":false},"unique_constraints":[{"name":"customers_name_key","table":"customers","columns":["name"],"definition":"UNIQUE (name)","validated":true,"deferrable":false,"initially_deferred":false}],"checks":[{"name":"customers_name_check","table":"customers","columns":["name"],"definition":"CHECK (char_length(name) > 0)","validated":true}],"not_null_constraints":[],"exclusion_constraints":[],"indexes":[{"name":"customers_lower_name_idx","table":"customers","definition":"CREATE INDEX customers_lower_name_idx ON public.customers USING btree (lower(name)) WHERE name IS NOT NULL","method":"btree","unique":false,"valid":true,"predicate":"name IS NOT NULL"},{"name":"customers_name_covering_idx","table":"customers","definition":"CREATE INDEX customers_name_covering_idx ON public.customers USING btree (name) INCLUDE (id, normalized_name)","method":"btree","unique":false,"valid":true,"predicate":null}],"triggers":[{"name":"customers_touch","table":"customers","definition":"CREATE TRIGGER customers_touch BEFORE UPDATE OF name ON public.customers FOR EACH ROW EXECUTE FUNCTION public.touch_customer()","enabled":"origin"}]},{"namespace":"public","name":"orders","kind":"table","is_partition":false,"partition_key":null,"columns":[{"name":"id","ordinal":1,"data_type":"bigint","nullable":false,"default_expression":null,"identity":null,"generated":null,"collation_schema":null,"collation_name":null},{"name":"customer_id","ordinal":2,"data_type":"bigint","nullable":false,"default_expression":null,"identity":null,"generated":null,"collation_schema":null,"collation_name":null}],"primary_key":{"name":"orders_pkey","table":"orders","columns":["id"],"definition":"PRIMARY KEY (id)","validated":true,"deferrable":false,"initially_deferred":false},"unique_constraints":[],"checks":[],"not_null_constraints":[],"exclusion_constraints":[],"indexes":[],"triggers":[]}],"relationships":[{"name":"orders_customer_id_fkey","source_namespace":"public","source_table":"orders","source_columns":["customer_id"],"target_namespace":"public","target_table":"customers","target_columns":["id"],"definition":"FOREIGN KEY (customer_id) REFERENCES customers(id)","on_update":"NO ACTION","on_delete":"CASCADE","match_type":"SIMPLE","validated":true,"deferrable":false,"initially_deferred":false}],"functions":[{"namespace":"public","name":"touch_customer","kind":"function","identity_arguments":"","arguments":"","return_type":"trigger","language":"plpgsql","definition":"CREATE FUNCTION public.touch_customer() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$"}],"views":[{"namespace":"public","name":"customer_names","columns":[{"name":"id","ordinal":1,"data_type":"bigint","nullable":true,"default_expression":null,"identity":null,"generated":null,"collation_schema":null,"collation_name":null},{"name":"name","ordinal":2,"data_type":"text","nullable":true,"default_expression":null,"identity":null,"generated":null,"collation_schema":null,"collation_name":null}],"query_definition":"SELECT id, name FROM public.customers"}],"materialized_views":[],"captured_at":"2026-11-12T03:13:44.190467Z","fingerprint":"1b6e61a588c6f72cb455c13b9614b4422b4c172af9681a9266541da17d03a7c1"}'
LEGACY_DIGEST = "1b6e61a588c6f72cb455c13b9614b4422b4c172af9681a9266541da17d03a7c1"


def test_frozen_historical_catalog_digest_and_wire_read_stay_valid():
    value = PostgresCatalog.model_validate_json(LEGACY_JSON)
    assert value.fingerprint == LEGACY_DIGEST
    assert compute_catalog_fingerprint(value) == LEGACY_DIGEST


def test_historical_tampering_is_not_silently_rehashed():
    value = json.loads(LEGACY_JSON)
    value["tables"][0]["columns"][0]["data_type"] = "text"
    with pytest.raises(ValueError, match="fingerprint"):
        PostgresCatalog.model_validate_json(json.dumps(value))


def test_unknown_versions_and_attached_historical_safety_evidence_are_rejected():
    value = json.loads(LEGACY_JSON)
    for version in (0, 3):
        with pytest.raises(ValueError):
            PostgresCatalog.model_validate_json(
                json.dumps({**value, "fingerprint_version": version})
            )
    evidence = {
        "version": 1,
        "dependencies": [],
        "preservation": [],
        "dependency_complete": True,
        "unknown_reasons": [],
    }
    with pytest.raises(ValueError, match="historical"):
        PostgresCatalog.model_validate_json(json.dumps({**value, "safety": evidence}))


def test_versioned_evidence_is_included_in_full_drift_digest_and_cannot_be_tampered():
    from schemii.common.postgres.models import (
        PostgresSafetyEvidence,
        build_postgres_catalog,
    )

    legacy = PostgresCatalog.model_validate_json(LEGACY_JSON)
    evidence = PostgresSafetyEvidence(
        dependencies=(),
        preservation=(),
        dependency_complete=False,
        unknown_reasons=("fixture unproven",),
    )
    current = build_postgres_catalog(
        **legacy.model_dump(
            exclude={"fingerprint", "captured_at", "safety", "fingerprint_version"}
        ),
        captured_at=legacy.captured_at,
        safety=evidence,
    )
    assert current.fingerprint_version == 2 and current.fingerprint != LEGACY_DIGEST
    value = json.loads(current.model_dump_json())
    value["safety"]["unknown_reasons"] = ["tampered"]
    value["safety"]["dependency_complete"] = False
    with pytest.raises(ValueError, match="fingerprint"):
        PostgresCatalog.model_validate_json(json.dumps(value))


def test_frozen_catalog_loads_through_persisted_baseline_and_plan_authority_readers():
    from datetime import datetime, timezone
    from schemii.schemii.migrations.repository import PostgresMigrationRepository

    repository = PostgresMigrationRepository(
        lambda: (_ for _ in ()).throw(AssertionError("No database access expected"))
    )
    empty = {
        "types": [],
        "tables": [],
        "relationships": [],
        "functions": [],
        "views": [],
        "triggers": [],
    }
    row = {
        "id": "mbl_" + "5" * 32,
        "owner_id": "owner",
        "workspace_id": "ws_" + "1" * 32,
        "revision": 1,
        "predecessor_id": None,
        "connection_id": "pg_" + "2" * 32,
        "connection_revision": 1,
        "database_name": "analytics",
        "namespace": "public",
        "design_revision": 1,
        "design_content": empty,
        "catalog": LEGACY_JSON,
        "complete": False,
        "issues": [{"reason": "historical"}],
        "source": "import",
        "source_execution_id": None,
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    baseline = repository._baseline(row)
    assert (
        baseline.catalog.fingerprint == LEGACY_DIGEST
        and baseline.catalog.safety is None
    )
    assert baseline.complete is False
    review = {
        "id": "mpl_" + "3" * 32,
        "workspaceId": row["workspace_id"],
        "status": "reviewable",
        "workspaceRevision": 1,
        "designRevision": 1,
        "designFingerprint": "0" * 64,
        "baselineRevision": 1,
        "catalogFingerprint": LEGACY_DIGEST,
        "mergedDesignFingerprint": "0" * 64,
        "reviewDigest": "4" * 64,
        "driftStatus": "none",
        "complete": True,
        "applyCapable": True,
        "destructive": False,
        "requiresExternalChangeAcknowledgement": False,
        "steps": [],
        "createdAt": "2026-01-01T00:00:00Z",
        "expiresAt": "2026-01-01T01:00:00Z",
    }
    authority = {
        "baselineContent": empty,
        "desiredContent": empty,
        "mergedContent": empty,
        "liveContent": empty,
        "liveCatalog": json.loads(LEGACY_JSON),
        "allowDestructive": False,
        "connectionId": row["connection_id"],
        "connectionRevision": 1,
        "database": "analytics",
        "namespace": "public",
    }
    plan = repository._plan(
        {
            "owner_id": "owner",
            "baseline_id": row["id"],
            "status": "reviewable",
            "review_document": json.dumps(review),
            "authority_document": json.dumps(authority),
        }
    )
    assert plan.plan.catalog_fingerprint == LEGACY_DIGEST
    assert plan.plan.review_digest == "4" * 64
    assert (
        plan.plan.compatibility is None and plan.authority.live_catalog.safety is None
    )
