import hashlib
import json
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from importlib import resources
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.metadata import MetadataStore, MetadataStoreError, canonical_review_digest
from schemii.metadata.migrator import packaged_migrations
from schemii.metadata.store import _chat_record, _execution_record, _operation_record, _plan_record, _proposal_record


class FakeCursor:
    def __init__(self, rows=None, rowcounts=None):
        self.rows = list(rows or [])
        self.rowcounts = list(rowcounts or [])
        self.rowcount = 1
        self.executions = []

    def execute(self, sql, params=None):
        self.executions.append((sql, params))
        self.rowcount = self.rowcounts.pop(0) if self.rowcounts else 1

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        return self.rows.pop(0) if self.rows else []

    def close(self):
        pass


class FakeConnection:
    def __init__(self, rows=None, rowcounts=None):
        self.cursor_value = FakeCursor(rows, rowcounts)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


TARGET = {
    "profileId": "profile-1",
    "databaseName": "app",
    "namespaceName": "public",
    "profileFingerprint": "a" * 64,
    "connectedTargetFingerprint": "b" * 64,
}


class MetadataRepositoryMigrationTests(unittest.TestCase):
    def test_0002_is_additive_and_adds_repository_evidence(self):
        migrations = packaged_migrations()
        self.assertEqual([migration.version for migration in migrations], [1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        sql = resources.files("schemii.metadata.migrations").joinpath("0002_authority_repository.sql").read_text()
        self.assertIn("ADD COLUMN binding jsonb", sql)
        self.assertIn("lease_expires_at", sql)
        self.assertIn("reconciliation_status", sql)
        self.assertNotIn("FORCE ROW LEVEL SECURITY", sql)
        self.assertIn("metadata_proposal_snapshot_guard", sql)
        self.assertIn("metadata_migration_plan_snapshot_guard", sql)
        self.assertNotIn("DROP TABLE", sql)
        durable = resources.files("schemii.metadata.migrations").joinpath("0004_durable_migration_execution.sql").read_text()
        self.assertIn("adapter_kind", durable)
        self.assertIn("private_payload_redacted_at", durable)
        self.assertNotIn("DROP TABLE", durable)
        console = resources.files("schemii.metadata.migrations").joinpath("0005_console_execution_receipts.sql").read_text()
        self.assertIn("metadata_console_execution_receipts", console)
        self.assertIn("metadata_console_execution_receipts_isolation", console)
        self.assertNotIn("sql_text", console)
        self.assertNotIn("DROP TABLE", console)
        agent_policy = resources.files("schemii.metadata.migrations").joinpath("0006_versioned_ai_agent_policy.sql").read_text()
        self.assertIn("metadata_agent_policy_revisions", agent_policy)
        self.assertIn("metadata_ai_operation_usage", agent_policy)
        self.assertIn("metadata_agent_settings_isolation", agent_policy)
        self.assertIn("agent_policy_revision_id IS NULL", agent_policy)
        self.assertNotIn("DROP TABLE", agent_policy)
        reservations = resources.files("schemii.metadata.migrations").joinpath("0007_console_execution_reservations.sql").read_text()
        self.assertIn("'reserved', 'running'", reservations)
        self.assertNotIn("DROP TABLE", reservations)
        cancellation = resources.files("schemii.metadata.migrations").joinpath("0008_ai_query_cancellation.sql").read_text()
        self.assertIn("cancellation_requested_at", cancellation)
        self.assertIn("'cancelled'", cancellation)
        self.assertIn("metadata_query_results_operation", cancellation)
        self.assertNotIn("DROP TABLE", cancellation)
        console_limit = resources.files("schemii.metadata.migrations").joinpath("0009_console_statement_limit.sql").read_text()
        self.assertIn("statement_limit BETWEEN 1 AND 100", console_limit)
        self.assertNotIn("UPDATE metadata_console_settings", console_limit)
        self.assertNotIn("DROP TABLE", console_limit)
        chat_titles = resources.files("schemii.metadata.migrations").joinpath("0010_chat_conversation_titles.sql").read_text()
        self.assertIn("ADD COLUMN conversation_title", chat_titles)
        self.assertIn("octet_length(conversation_title) <= 80", chat_titles)
        self.assertNotIn("DROP TABLE", chat_titles)


class MetadataRepositoryContractTests(unittest.TestCase):
    def test_console_reservation_uses_global_execution_key_without_owner_readback(self):
        execution, console = uuid.uuid4(), uuid.uuid4()
        connection = FakeConnection(rowcounts=[0])
        receipt = {
            "executionId": str(execution), "applicationId": "schemer", "sessionBinding": "new-session",
            "serverId": "new-server", "profileId": "other-profile", "profileFingerprint": "a" * 64,
            "database": "app", "namespace": "public", "consoleId": str(console), "mode": "managed",
            "settingsRevision": 1, "state": "reserved", "outcome": "not_started",
            "completedStatementIndexes": [], "errorCode": None, "postgresEvidence": None,
            "reconciliationEvidence": None,
        }
        with self.assertRaises(MetadataStoreError) as caught:
            MetadataStore(lambda: connection).put_console_execution_receipt(receipt)
        self.assertEqual(caught.exception.code, "execution_conflict")
        self.assertEqual(len(connection.cursor_value.executions), 1)
        sql, _ = connection.cursor_value.executions[0]
        self.assertIn("ON CONFLICT (execution_id) DO NOTHING", sql)
        self.assertNotIn("SELECT", sql)

    def test_exact_claim_abandonment_is_uncertain_and_never_requeues(self):
        attempt, operation, chat = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        token = "exact-token"
        from schemii.metadata.store import _token_hash
        connection = FakeConnection(rows=[
            {"operation_id": operation, "state": "running", "claim_token_hash": _token_hash(token)},
            {"chat_id": chat}, {"application_id": "schemii"},
        ])
        result = MetadataStore(lambda: connection).abandon_operation_attempt(str(attempt), token)
        self.assertEqual(result["state"], "uncertain")
        statements = [sql for sql, _ in connection.cursor_value.executions]
        self.assertTrue(any("state = 'abandoned'" in sql for sql in statements))
        self.assertTrue(any("'uncertain'" in sql and "metadata_operation_outcomes" in sql for sql in statements))
        self.assertTrue(any("scrubbed_at = clock_timestamp()" in sql for sql in statements))
        self.assertFalse(any("UPDATE metadata_operations SET state = 'ready'" in sql for sql in statements))

    def test_cleanup_bounds_private_payload_redaction_as_well_as_deletes(self):
        connection = FakeConnection(rowcounts=[3, 0, 0, 0])
        result = MetadataStore(lambda: connection).cleanup(before=datetime.now(timezone.utc), limit=3)
        sql, params = connection.cursor_value.executions[0]
        self.assertIn("LIMIT %s", sql)
        self.assertIn("SKIP LOCKED", sql)
        self.assertEqual(params[-1], 3)
        self.assertEqual(result["planPayloadsRedacted"], 3)

    def test_terminal_operation_readback_uses_durable_outcome_after_restart(self):
        operation, proposal, chat = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        timestamp = datetime.now(timezone.utc)
        row = {
            "operation_id": operation, "proposal_id": proposal, "chat_id": chat,
            "capability": "schema", "state": "succeeded", "created_at": timestamp,
            "updated_at": timestamp, "attempt_id": None, "worker_id": None,
            "lease_expires_at": None, "outcome_state": "succeeded",
            "result": {"kind": "durable_receipt"}, "error": None,
        }
        restarted_store = MetadataStore(lambda: FakeConnection(rows=[row]))
        result = restarted_store.get_operation(str(operation))
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["outcome"]["result"], {"kind": "durable_receipt"})
        self.assertIsNone(result["attempt"])

    def test_console_receipt_stores_only_operational_evidence_and_enforces_owner(self):
        execution, console = uuid.uuid4(), uuid.uuid4()
        timestamp = datetime.now(timezone.utc)
        binding_hash = hashlib.sha256(b"browser-session").hexdigest()
        row = {"application_id": "schemii", "session_binding_hash": binding_hash, "server_id": "server",
               "profile_id": "profile", "profile_fingerprint": "a" * 64, "database_name": "app",
               "namespace_name": "public", "console_id": console, "mode": "managed", "settings_revision": 7, "state": "uncertain",
               "outcome": "uncertain", "completed_statement_indexes": [0], "error_code": "execution_outcome_unknown",
               "postgres_evidence": {"sqlstate": "08006"}, "reconciliation_evidence": {"commitAttempted": True},
               "created_at": timestamp, "updated_at": timestamp}
        inserted = FakeConnection()
        connections = [inserted, FakeConnection(rows=[row])]
        store = MetadataStore(lambda: connections.pop(0))
        receipt = {"executionId": str(execution), "applicationId": "schemii", "sessionBinding": "browser-session",
                   "serverId": "server", "profileId": "profile", "profileFingerprint": "a" * 64,
                    "database": "app", "namespace": "public", "consoleId": str(console), "mode": "managed", "settingsRevision": 7,
                   "state": "uncertain", "outcome": "uncertain", "completedStatementIndexes": [0],
                   "errorCode": "execution_outcome_unknown", "postgresEvidence": {"sqlstate": "08006"},
                   "reconciliationEvidence": {"commitAttempted": True}}
        result = store.put_console_execution_receipt(receipt)
        self.assertEqual(result["outcome"], "uncertain")
        sql, params = inserted.cursor_value.executions[0]
        self.assertIn("metadata_console_execution_receipts", sql)
        self.assertNotIn("browser-session", params)
        encoded = json.dumps(receipt)
        self.assertNotIn("password", encoded)
        self.assertNotIn("SELECT", encoded)

        wrong = FakeConnection(rows=[row])
        with self.assertRaises(MetadataStoreError) as caught:
            MetadataStore(lambda: wrong).get_console_execution_receipt(
                str(execution), "schemer", "browser-session", "server", "profile", "a" * 64,
                "app", "public", str(console),
            )
        self.assertEqual(caught.exception.code, "execution_not_found")

    def test_chat_listing_casts_optional_filters_for_postgresql(self):
        connection = FakeConnection(rows=[[]])
        self.assertEqual(MetadataStore(lambda: connection).list_chats(resource_kind="schema", resource_id="schema_one", states=["active"]), [])
        sql, params = connection.cursor_value.executions[0]
        self.assertIn("%s::text IS NULL", sql)
        self.assertIn("cardinality(%s::text[])", sql)
        self.assertEqual(params[:4], ("schema", "schema", "schema_one", "schema_one"))

    def test_public_migration_records_serialize_postgresql_timestamps(self):
        timestamp = datetime(2026, 8, 14, 3, tzinfo=timezone(timedelta(hours=3)))
        plan = _plan_record({
            "plan_id": uuid.uuid4(), "application_id": "schemii", "resource_kind": "schema",
            "resource_id": "schema_one", "resource_revision": 1, "layout_token": "l" * 64,
            "profile_id": "profile", "database_name": "db", "namespace_name": "public",
            "profile_fingerprint": "a" * 64, "connected_target_fingerprint": "b" * 64,
            "live_fingerprint": "c" * 64, "desired_fingerprint": "d" * 64,
            "private_payload": {}, "review_payload": {}, "review_digest": "e" * 64,
            "destructive": False, "state": "ready", "created_at": timestamp, "expires_at": timestamp,
            "adapter_kind": "full_schema", "source_kind": "normal", "retain_until": timestamp,
            "private_payload_redacted_at": None,
        }, include_private=False)
        execution = _execution_record({
            "execution_id": uuid.uuid4(), "plan_id": uuid.uuid4(), "state": "succeeded",
            "confirmed_review_digest": "e" * 64, "destructive_confirmed": False, "target_xid": "1",
            "target_identity": {}, "intended_result": {}, "commit_outcome": "committed",
            "created_at": timestamp, "updated_at": timestamp, "reconciliation_status": "not_required",
            "reconciliation_evidence": None, "sync_id": None, "sync_state": None, "sync_receipt": None,
        })
        self.assertEqual(plan["createdAt"], "2026-08-14T00:00:00Z")
        self.assertEqual(execution["updatedAt"], "2026-08-14T00:00:00Z")
        self.assertIsNone(plan["privatePayloadRedactedAt"])

    def test_public_authority_records_serialize_nullable_and_non_utc_timestamps(self):
        timestamp = datetime(2026, 8, 13, 20, tzinfo=timezone(timedelta(hours=-4)))
        chat_id = uuid.uuid4()
        proposal_id = uuid.uuid4()
        operation_id = uuid.uuid4()
        records = [
            _chat_record({
                "chat_id": chat_id, "application_id": "schemii", "resource_kind": "schema",
                "resource_id": "schema_one", "external_session_id": None, "state": "active",
                "created_at": timestamp, "updated_at": timestamp, "deleted_at": None,
                "target_id": None, "profile_id": None, "database_name": None, "namespace_name": None,
                "profile_fingerprint": None, "connected_target_fingerprint": None, "display_title": "Chat",
            }),
            _proposal_record({
                "proposal_id": proposal_id, "chat_id": chat_id, "capability": "schema.read",
                "policy_revision": 1, "binding": {}, "action": {}, "state": "ready",
                "created_at": timestamp, "expires_at": timestamp,
            }),
            _operation_record({
                "operation_id": operation_id, "proposal_id": proposal_id, "chat_id": chat_id,
                "capability": "schema.read", "state": "running", "created_at": timestamp,
                "updated_at": timestamp, "attempt_id": uuid.uuid4(), "worker_id": "worker",
                "lease_expires_at": timestamp, "outcome_state": None, "result": None, "error": None,
            }),
        ]
        policy = MetadataStore(lambda: FakeConnection(rows=[{
            "policy_version_id": uuid.uuid4(), "revision": 1, "policy": {}, "created_at": timestamp,
        }, []])).get_current_policy(str(chat_id))
        grants = MetadataStore(lambda: FakeConnection(rows=[[
            {"grant_id": uuid.uuid4(), "capability": "schema.read", "policy_revision": 1,
             "state": "active", "expires_at": None, "created_at": timestamp, "revoked_at": None},
        ]])).list_grants(str(chat_id))
        transitions = MetadataStore(lambda: FakeConnection(rows=[[
            {"transition_id": 1, "from_state": None, "to_state": "active", "reason": "created",
             "created_at": timestamp},
        ]])).list_transitions("chat", str(chat_id))

        self.assertEqual(records[0]["createdAt"], "2026-08-14T00:00:00Z")
        self.assertIsNone(records[0]["deletedAt"])
        self.assertEqual(records[1]["expiresAt"], "2026-08-14T00:00:00Z")
        self.assertEqual(records[2]["attempt"]["leaseExpiresAt"], "2026-08-14T00:00:00Z")
        self.assertEqual(policy["createdAt"], "2026-08-14T00:00:00Z")
        self.assertEqual(grants[0]["createdAt"], "2026-08-14T00:00:00Z")
        self.assertIsNone(grants[0]["expiresAt"])
        self.assertEqual(transitions[0]["createdAt"], "2026-08-14T00:00:00Z")
        json.dumps([*records, policy, grants, transitions])

    def test_canonical_review_digest_is_order_independent(self):
        first = canonical_review_digest({"steps": [{"sql": "SELECT 1"}], "destructive": False})
        second = canonical_review_digest({"destructive": False, "steps": [{"sql": "SELECT 1"}]})
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_plan_rejects_noncanonical_digest_before_database_access(self):
        called = False

        def connection_factory():
            nonlocal called
            called = True
            return FakeConnection()

        with self.assertRaises(MetadataStoreError) as caught:
            MetadataStore(connection_factory).create_migration_plan(
                "schemii", "schema", "schema-1", 7, "layout-7", TARGET,
                "c" * 64, "d" * 64, {"desired": {}}, {"steps": []}, "0" * 64, False,
                adapter_kind="full_schema", source_kind="normal",
            )
        self.assertEqual(caught.exception.code, "review_digest_mismatch")
        self.assertFalse(called)

    def test_provisioning_retry_returns_same_saga_without_new_provider_owner(self):
        chat = uuid.uuid4()
        connection = FakeConnection(rows=[{
            "chat_id": chat, "resource_kind": "schema", "resource_id": "schema-1", "state": "provisioning",
        }])
        result = MetadataStore(lambda: connection).provision_chat(
            "schemii", "schema", "schema-1", external_session_id="session-1",
        )
        self.assertEqual(result, {"chatId": str(chat), "state": "provisioning", "provisioningOwner": False})
        self.assertFalse(any("INSERT INTO metadata_chats" in sql for sql, _ in connection.cursor_value.executions))

    def test_active_chat_rejects_activation_against_different_target(self):
        connection = FakeConnection(rows=[
            {"application_id": "schemii", "state": "active"},
            {"target_id": uuid.uuid4(), "profile_id": "other", "database_name": "app",
             "namespace_name": "public", "profile_fingerprint": "a" * 64,
             "connected_target_fingerprint": "b" * 64},
        ])
        with self.assertRaises(MetadataStoreError) as caught:
            MetadataStore(lambda: connection).activate_chat(str(uuid.uuid4()), TARGET)
        self.assertEqual(caught.exception.code, "target_conflict")

    def test_proposal_creation_binds_current_policy_and_immutable_snapshots(self):
        connection = FakeConnection(rows=[
            {"application_id": "schemii", "state": "active"}, {"exists": 1},
        ])
        result = MetadataStore(lambda: connection).create_proposal(
            str(uuid.uuid4()), "schema.write", 3, {"resourceRevision": 8}, {"kind": "apply"},
        )
        self.assertEqual(result["policyRevision"], 3)
        insert = next(item for item in connection.cursor_value.executions if "INSERT INTO metadata_proposals" in item[0])
        self.assertIn('"resourceRevision":8', insert[1][4])
        self.assertIn('"kind":"apply"', insert[1][5])

    def test_create_plan_persists_exact_binding_and_canonical_digest(self):
        connection = FakeConnection()
        proof = {
            "version": 1, "complete": True,
            "liveFingerprint": "c" * 64, "desiredFingerprint": "d" * 64,
        }
        review = {
            "steps": [], "warnings": [], "complete": True, "applyCapable": True,
            "blockingDifferences": [], "completenessProof": proof,
        }
        digest = canonical_review_digest(review)
        result = MetadataStore(lambda: connection).create_migration_plan(
            "schemii", "schema", "schema-1", 7, "layout-7", TARGET,
            "c" * 64, "d" * 64, {"desired": {}, "completenessProof": proof}, review, digest, True,
            adapter_kind="full_schema", source_kind="normal",
        )
        self.assertEqual(result["reviewDigest"], digest)
        insert = next(item for item in connection.cursor_value.executions if "INSERT INTO metadata_migration_plans" in item[0])
        self.assertEqual(insert[1][2:6], ("schema", "schema-1", 7, "layout-7"))
        self.assertEqual(insert[1][6:11], ("profile-1", "app", "public", "a" * 64, "b" * 64))

    def test_execution_authorization_is_atomic_and_idempotent(self):
        execution = uuid.uuid4()
        plan = uuid.uuid4()
        existing = FakeConnection(rows=[{
            "review_payload": {"steps": []}, "review_digest": canonical_review_digest({"steps": []}),
            "destructive": False, "state": "ready", "current": True, "adapter_kind": "insert_rows",
        }, {"execution_id": execution, "state": "applying"}])
        result = MetadataStore(lambda: existing).create_migration_execution(
            str(plan), canonical_review_digest({"steps": []}), False,
        )
        self.assertFalse(result["executionOwner"])
        self.assertEqual(result["executionId"], str(execution))

    def test_full_schema_execution_rejects_legacy_plan_without_completeness_proof(self):
        review = {"steps": []}
        connection = FakeConnection(rows=[{
            "review_payload": review, "review_digest": canonical_review_digest(review),
            "destructive": False, "state": "ready", "current": True, "adapter_kind": "full_schema",
            "private_payload": {}, "live_fingerprint": "a" * 64, "desired_fingerprint": "b" * 64,
        }])
        with self.assertRaises(MetadataStoreError) as caught:
            MetadataStore(lambda: connection).create_migration_execution(
                str(uuid.uuid4()), canonical_review_digest(review), False,
            )
        self.assertEqual(caught.exception.code, "migration_plan_incomplete")

    def test_begin_execution_records_target_evidence_before_applying_transition(self):
        execution = uuid.uuid4()
        connection = FakeConnection(rows=[{
            "state": "ready", "target_xid": None, "target_identity": None,
            "intended_result": None, "commit_outcome": None,
            "reconciliation_status": "not_required", "reconciliation_evidence": None,
        }])
        result = MetadataStore(lambda: connection).begin_migration_execution(str(execution), "8123", {"databaseOid": 42})
        self.assertEqual(result["state"], "applying")
        sql = "\n".join(item[0] for item in connection.cursor_value.executions)
        self.assertIn("target_xid = %s", sql)
        self.assertIn("INSERT INTO metadata_migration_transitions", sql)

    def test_committed_execution_requires_intended_result(self):
        connection = FakeConnection(rows=[{
            "state": "applying", "target_xid": "1", "target_identity": {},
            "intended_result": None, "commit_outcome": None,
            "reconciliation_status": "not_required", "reconciliation_evidence": None,
        }])
        with self.assertRaises(MetadataStoreError) as caught:
            MetadataStore(lambda: connection).finish_migration_execution(
                str(uuid.uuid4()), "succeeded", "committed",
            )
        self.assertEqual(caught.exception.code, "intended_result_required")

    def test_interrupted_applying_execution_is_durably_promoted_to_reconcile_only(self):
        execution = uuid.uuid4()
        connection = FakeConnection(rows=[{
            "state": "applying", "target_xid": "77", "target_identity": {"databaseOid": "4"},
            "intended_result": {"resultFingerprint": "f"}, "commit_outcome": None,
            "reconciliation_status": "not_required", "reconciliation_evidence": None,
        }])
        result = MetadataStore(lambda: connection).prepare_migration_reconciliation(
            str(execution), {"code": "worker_restarted", "reconcileOnly": True},
        )
        self.assertEqual(result["state"], "uncertain")
        self.assertFalse(result["manualRequired"])
        sql = "\n".join(item[0] for item in connection.cursor_value.executions)
        self.assertIn("reconciliation_status = %s", sql)
        self.assertIn("INSERT INTO metadata_migration_transitions", sql)

    def test_interrupted_applying_execution_without_xid_requires_manual_recovery(self):
        connection = FakeConnection(rows=[{
            "state": "applying", "target_xid": None, "target_identity": None,
            "intended_result": None, "commit_outcome": None,
            "reconciliation_status": "not_required", "reconciliation_evidence": None,
        }])
        result = MetadataStore(lambda: connection).prepare_migration_reconciliation(
            str(uuid.uuid4()), {"code": "worker_restarted"},
        )
        self.assertTrue(result["manualRequired"])
        update = next(params for sql, params in connection.cursor_value.executions if "reconciliation_status = %s" in sql)
        self.assertEqual(update[0], "failed")

    def test_committed_without_intended_result_stays_uncertain_manual(self):
        connection = FakeConnection(rows=[{
            "state": "uncertain", "target_xid": "77", "target_identity": {"databaseOid": "4"},
            "intended_result": None, "commit_outcome": "uncertain",
            "reconciliation_status": "required", "reconciliation_evidence": None,
        }])
        result = MetadataStore(lambda: connection).require_manual_migration_reconciliation(
            str(uuid.uuid4()), {"code": "committed_without_intended_result"},
        )
        self.assertEqual(result, {"executionId": result["executionId"], "state": "uncertain", "manualRequired": True, "transitionOwner": True})
        self.assertTrue(any("reconciliation_status = 'failed'" in sql for sql, _ in connection.cursor_value.executions))

    def test_uncertain_execution_reconciliation_records_proven_commit_without_replay(self):
        execution = uuid.uuid4()
        connection = FakeConnection(rows=[{
            "state": "uncertain", "target_xid": "7", "target_identity": {"databaseOid": 4},
            "intended_result": {"fingerprint": "f"}, "commit_outcome": "uncertain",
            "reconciliation_status": "required", "reconciliation_evidence": None,
        }])
        result = MetadataStore(lambda: connection).reconcile_migration_execution(
            str(execution), "committed", {"xidStatus": "committed"},
        )
        self.assertEqual(result["state"], "succeeded")
        sql = "\n".join(item[0] for item in connection.cursor_value.executions)
        self.assertIn("reconciliation_status = 'reconciled'", sql)
        self.assertNotIn("private_payload", sql)

    def test_sync_outcome_is_separate_from_committed_execution(self):
        sync = uuid.uuid4()
        connection = FakeConnection(rows=[{
            "state": "succeeded", "target_xid": "7", "target_identity": {},
            "intended_result": {}, "commit_outcome": "committed",
            "reconciliation_status": "not_required", "reconciliation_evidence": None,
        }, {"sync_id": sync, "state": "pending", "receipt": None}])
        result = MetadataStore(lambda: connection).record_migration_sync(
            str(uuid.uuid4()), "conflict", receipt={"code": "layout_conflict"},
        )
        self.assertEqual(result["state"], "conflict")
        self.assertTrue(any("UPDATE metadata_migration_syncs" in sql for sql, _ in connection.cursor_value.executions))

    def test_stale_operation_is_abandoned_to_uncertain_not_ready(self):
        operation = uuid.uuid4()
        connection = FakeConnection(rows=[[
            {"attempt_id": uuid.uuid4(), "operation_id": operation, "chat_id": uuid.uuid4()},
        ], {"application_id": "schemii"}])
        result = MetadataStore(lambda: connection).abandon_stale_operations(
            stale_before=datetime.now(timezone.utc),
        )
        self.assertEqual(result, [str(operation)])
        sql = "\n".join(item[0] for item in connection.cursor_value.executions)
        self.assertIn("state = 'abandoned'", sql)
        self.assertIn("state = 'uncertain'", sql)
        self.assertNotIn("UPDATE metadata_operations SET state = 'ready'", sql)

    def test_heartbeat_renews_live_lease_and_rejects_expired_update(self):
        token = "claim"
        row = {"operation_id": uuid.uuid4(), "state": "running",
               "claim_token_hash": hashlib.sha256(token.encode()).hexdigest(), "lease_expires_at": datetime.now(timezone.utc)}
        connection = FakeConnection(rows=[row], rowcounts=[1, 0])
        with self.assertRaises(MetadataStoreError) as caught:
            MetadataStore(lambda: connection).heartbeat_operation(str(uuid.uuid4()), token)
        self.assertEqual(caught.exception.code, "operation_lease_expired")

    def test_identical_terminal_operation_retry_is_idempotent(self):
        token = "claim"
        operation = uuid.uuid4()
        attempt = uuid.uuid4()
        result_payload = {"receipt": "ok"}
        connection = FakeConnection(rows=[{
            "operation_id": operation, "state": "succeeded",
            "claim_token_hash": hashlib.sha256(token.encode()).hexdigest(), "lease_expires_at": datetime.now(timezone.utc),
        }, {"cancellation_requested_at": None}, {"state": "succeeded", "result": result_payload, "error": None}])
        result = MetadataStore(lambda: connection).finish_operation(
            str(attempt), token, "succeeded", result=result_payload,
        )
        self.assertFalse(result["resolutionOwner"])

    def test_running_query_cancellation_is_durable_and_terminal_success_is_suppressed(self):
        chat = uuid.uuid4()
        proposal = uuid.uuid4()
        operation = uuid.uuid4()
        cancellation_connection = FakeConnection(rows=[
            {"chat_id": chat, "state": "authorized", "cancellation_requested_at": None, "application_id": "schemii", "resource_kind": "schema"},
            {"operation_id": operation, "state": "running"},
            {"application_id": "schemii"},
        ])

        cancellation = MetadataStore(lambda: cancellation_connection).request_proposal_cancellation(
            str(proposal), str(chat), expected_application="schemii", expected_resource_kind="schema",
        )

        self.assertEqual(cancellation, {
            "requested": True, "proposalState": "authorized",
            "operationId": str(operation), "operationState": "running",
        })
        cancellation_sql = "\n".join(sql for sql, _ in cancellation_connection.cursor_value.executions)
        self.assertIn("cancellation_requested_at = COALESCE", cancellation_sql)

        token = "claim"
        attempt = uuid.uuid4()
        finished_connection = FakeConnection(rows=[
            {"operation_id": operation, "state": "running", "claim_token_hash": hashlib.sha256(token.encode()).hexdigest(), "lease_expires_at": datetime.now(timezone.utc)},
            {"cancellation_requested_at": datetime.now(timezone.utc)},
            {"chat_id": chat},
            {"application_id": "schemii"},
        ])
        finished = MetadataStore(lambda: finished_connection).finish_operation(
            str(attempt), token, "succeeded", result={"kind": "sql_result"},
        )

        self.assertEqual((finished["state"], finished["result"], finished["error"]["code"]),
                         ("cancelled", None, "execution_cancelled"))
        terminal_sql = "\n".join(sql for sql, _ in finished_connection.cursor_value.executions)
        self.assertIn("metadata_operation_outcomes", terminal_sql)
        self.assertIn("UPDATE metadata_operations SET state", terminal_sql)
        self.assertIn("revocation_reason = %s", terminal_sql)
        self.assertTrue(any(params and params[0] == "operation_cancelled" for _, params in finished_connection.cursor_value.executions))
        self.assertIn("scrubbed_at = clock_timestamp()", terminal_sql)

    def test_query_cancellation_before_operation_creation_prevents_dispatch(self):
        chat = uuid.uuid4()
        proposal = uuid.uuid4()
        connection = FakeConnection(rows=[
            {"chat_id": chat, "state": "ready", "cancellation_requested_at": None, "application_id": "schemii", "resource_kind": "schema"},
            None,
            {"application_id": "schemii"},
        ])

        cancellation = MetadataStore(lambda: connection).request_proposal_cancellation(
            str(proposal), str(chat), expected_application="schemii", expected_resource_kind="schema",
        )

        self.assertEqual(cancellation["proposalState"], "cancelled")
        self.assertIsNone(cancellation["operationId"])
        sql = "\n".join(statement for statement, _ in connection.cursor_value.executions)
        self.assertIn("state = 'cancelled', cancellation_requested_at = clock_timestamp()", sql)

    def test_query_cancellation_enforces_application_resource_ownership(self):
        chat = uuid.uuid4()
        connection = FakeConnection(rows=[{
            "chat_id": chat, "state": "ready", "cancellation_requested_at": None,
            "application_id": "schemer", "resource_kind": "dashboard",
        }])

        with self.assertRaises(MetadataStoreError) as caught:
            MetadataStore(lambda: connection).request_proposal_cancellation(
                str(uuid.uuid4()), str(chat),
                expected_application="schemii", expected_resource_kind="schema",
            )

        self.assertEqual(caught.exception.code, "authority_binding_mismatch")

    def test_cancelled_ready_operation_cannot_be_claimed(self):
        operation = uuid.uuid4()
        connection = FakeConnection(rows=[
            {"cancellation_requested_at": datetime.now(timezone.utc)},
            {"state": "ready", "chat_id": uuid.uuid4()},
        ])

        with self.assertRaises(MetadataStoreError) as caught:
            MetadataStore(lambda: connection).claim_operation(str(operation), "worker")

        self.assertEqual(caught.exception.code, "proposal_cancelled")

    def test_operation_bound_result_remains_consumable_without_browser_operation_authority(self):
        chat = uuid.uuid4()
        binding = {"resource": "schema_one", "revision": 4, "access": "data"}
        stored_binding = {"operationId": str(uuid.uuid4()), "policyBinding": {"policyRevision": 2}, **binding}
        connection = FakeConnection(rows=[
            {"chat_id": chat, "binding": stored_binding, "state": "ready", "current": True, "payload": {"rows": [[1]]}},
            {"application_id": "schemii"},
        ])

        reserved = MetadataStore(lambda: connection).reserve_result(str(uuid.uuid4()), str(chat), binding)

        self.assertEqual(reserved["payload"], {"rows": [[1]]})
        self.assertEqual(reserved["state"], "reserved")

    def test_abandoned_operation_cannot_create_a_result_after_cleanup(self):
        chat = uuid.uuid4()
        operation = uuid.uuid4()
        connection = FakeConnection(rows=[
            {"chat_id": chat, "cancellation_requested_at": None},
            {"state": "uncertain", "chat_id": chat, "active_claim": False},
        ])

        with self.assertRaises(MetadataStoreError) as caught:
            MetadataStore(lambda: connection).create_result(
                str(chat), {"operationId": str(operation), "resource": "schema_one"}, {"rows": [[1]]},
            )

        self.assertEqual(caught.exception.code, "operation_not_running")
        self.assertFalse(any("INSERT INTO metadata_query_result_references" in sql for sql, _ in connection.cursor_value.executions))

    def test_stale_delivery_recovery_scrubs_post_dispatch_payload_and_audits(self):
        delivery = uuid.uuid4()
        result_ref = uuid.uuid4()
        connection = FakeConnection(rows=[[
            {"delivery_id": delivery, "result_ref_id": result_ref, "state": "delivering"},
        ], {"application_id": "schemii"}])
        now = datetime.now(timezone.utc)
        recovered = MetadataStore(lambda: connection).recover_stale_results(
            reserved_before=now, delivering_before=now,
        )
        self.assertEqual(recovered, {"released": [], "uncertain": [str(delivery)]})
        sql = "\n".join(item[0] for item in connection.cursor_value.executions)
        self.assertIn("scrubbed_at = clock_timestamp()", sql)
        self.assertIn("metadata_authority_transitions", sql)

    def test_cleanup_is_bounded_and_only_targets_terminal_or_unowned_rows(self):
        connection = FakeConnection(rowcounts=[2, 3, 4])
        result = MetadataStore(lambda: connection).cleanup(before=datetime.now(timezone.utc), limit=25)
        self.assertEqual(result, {"planPayloadsRedacted": 2, "results": 3, "plans": 4, "chats": 1})
        self.assertTrue(all(len(params) == 1 or params[1] == 25 for _, params in connection.cursor_value.executions))
        sql = "\n".join(item[0] for item in connection.cursor_value.executions)
        self.assertIn("state IN ('consumed', 'uncertain', 'expired', 'revoked')", sql)
        self.assertIn("e.execution_id IS NULL", sql)
        self.assertIn("state = 'deleted'", sql)


if __name__ == "__main__":
    unittest.main()
