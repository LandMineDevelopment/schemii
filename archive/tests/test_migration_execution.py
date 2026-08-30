import copy
import sys
import unittest
import uuid
from contextlib import nullcontext
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.migration_execution import DurableMigrationCoordinator
from schemii.migration_contract import full_schema_completeness_proof
from schemii.metadata import MetadataStoreError
from schemii.postgres_common import PostgresServiceError, canonical_fingerprint
from schemii.postgres_common import narrow_statement_timeout
from schemii.schema_store import SchemaStoreError


IDENTITY = {"database": "demo", "databaseOid": "42", "serverVersionNum": "160000", "serverAddress": None, "serverPort": None}


class MemoryMetadata:
    def __init__(self, plan, *, fail_finalize=False, fail_sync=False):
        self.plan = copy.deepcopy(plan)
        self.execution = None
        self.events = []
        self.fail_finalize = fail_finalize
        self.fail_sync = fail_sync

    def create_migration_execution(self, plan_id, digest, confirmed):
        self.events.append("confirmation")
        if self.execution:
            return {"executionId": self.execution["executionId"], "state": self.execution["state"], "executionOwner": False}
        self.execution = {"executionId": str(uuid.uuid4()), "planId": plan_id, "state": "ready", "confirmedReviewDigest": digest,
                          "destructiveConfirmed": confirmed, "targetXid": None, "targetIdentity": None, "intendedResult": None,
                          "commitOutcome": None, "reconciliationStatus": "not_required", "reconciliationEvidence": None, "sync": None}
        return {"executionId": self.execution["executionId"], "state": "ready", "executionOwner": True}

    def get_migration_plan(self, plan_id, include_private=False):
        result = copy.deepcopy(self.plan)
        if not include_private:
            result.pop("privatePayload", None)
        return result

    def get_migration_status(self, plan_id):
        return {"plan": self.get_migration_plan(plan_id), "execution": copy.deepcopy(self.execution)}

    def get_migration_execution_context(self, execution_id):
        return {"plan": self.get_migration_plan(self.plan["planId"], include_private=True), "execution": copy.deepcopy(self.execution)}

    def get_migration_execution(self, execution_id):
        return copy.deepcopy(self.execution)

    def begin_migration_execution(self, execution_id, xid, identity):
        self.events.append("xid")
        self.execution.update(state="applying", targetXid=xid, targetIdentity=copy.deepcopy(identity))
        return {"transitionOwner": True}

    def record_migration_intended_result(self, execution_id, intended):
        self.events.append("intended")
        self.execution["intendedResult"] = copy.deepcopy(intended)
        return {"recordOwner": True}

    def finish_migration_execution(self, execution_id, state, outcome, evidence=None):
        self.events.append(state)
        if self.fail_finalize:
            self.fail_finalize = False
            raise MetadataStoreError("metadata_unavailable", "metadata unavailable", status=503)
        self.execution.update(state=state, commitOutcome=outcome, reconciliationStatus="required" if state == "uncertain" else "not_required")
        return {"transitionOwner": True}

    def prepare_migration_reconciliation(self, execution_id, evidence):
        manual = not self.execution.get("targetXid") or self.execution.get("targetIdentity") is None
        self.execution.update(state="uncertain", commitOutcome="uncertain",
                              reconciliationStatus="failed" if manual else "required",
                              reconciliationEvidence=copy.deepcopy(evidence))
        return {"state": "uncertain", "manualRequired": manual}

    def require_manual_migration_reconciliation(self, execution_id, evidence):
        self.execution.update(state="uncertain", commitOutcome="uncertain", reconciliationStatus="failed",
                              reconciliationEvidence=copy.deepcopy(evidence))
        return {"state": "uncertain", "manualRequired": True}

    def fail_migration_execution_before_mutation(self, execution_id, evidence):
        self.execution.update(state="failed", commitOutcome="rolled_back")

    def reconcile_migration_execution(self, execution_id, outcome, evidence):
        self.execution.update(state="succeeded" if outcome == "committed" else "failed", commitOutcome=outcome,
                              reconciliationStatus="reconciled", reconciliationEvidence=copy.deepcopy(evidence))

    def record_migration_sync(self, execution_id, state, receipt=None):
        if self.fail_sync:
            raise MetadataStoreError("metadata_unavailable", "metadata unavailable", status=503)
        self.events.append(f"sync:{state}")
        self.execution["sync"] = {"syncId": str(uuid.uuid4()), "state": state, "receipt": copy.deepcopy(receipt)}
        return self.execution["sync"]


class Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.rowcount = 2

    def execute(self, sql, params=()):
        self.connection.events.append((sql, params))
        if self.connection.fail_on and self.connection.fail_on in sql:
            raise RuntimeError("untrusted database failure")

    def close(self):
        pass


class Connection:
    def __init__(self, fail_commit=False, fail_on=None):
        self.events = []
        self.fail_commit = fail_commit
        self.rollbacks = 0
        self.commits = 0
        self.fail_on = fail_on

    def cursor(self):
        return Cursor(self)

    def commit(self):
        self.commits += 1
        if self.fail_commit:
            raise RuntimeError("lost acknowledgement")

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


class FakeService:
    _plan_ttl = 900

    def __init__(self, connection, *, transaction_status="committed"):
        self.connection = connection
        self.connects = 0
        self.mutations = 0
        self.catalog = {"postgres": {"fingerprint": "a" * 64, "database": "demo"}}
        self.transaction_status = transaction_status
        self.assessment_token = None
        self.admissions = []

    def admission_target(self, profile_id):
        return "target-" + profile_id

    def execution(self, execution_class, target=None):
        self.admissions.append((execution_class, target))
        return nullcontext()

    def _profile(self, profile_id):
        return {"dbname": "demo"}

    def _profile_fingerprint(self, profile):
        return "b" * 64

    def _connect_profile(self, profile):
        self.connects += 1
        return self.connection

    def _close(self, connection):
        connection.close()

    def _acquire_namespace_mutation_lock(self, cursor, namespace, database):
        cursor.execute("LOCK NAMESPACE")

    def _introspect_connection(self, connection, profile_id, namespace):
        return copy.deepcopy(self.catalog)

    def _migration_safety_assessment(self, profile_id, namespace, live, desired, *, connection=None):
        return {"status": "available", "relations": {}, "opaque": self.assessment_token}

    def _migration_fingerprint(self, live, assessment):
        if assessment.get("opaque") is None:
            return live["postgres"]["fingerprint"]
        return canonical_fingerprint({
            "catalog": live["postgres"]["fingerprint"], "assessment": assessment,
            "timezoneInput": live["postgres"].get("timeZone"),
        })

    def introspect(self, profile_id, namespace):
        return copy.deepcopy(self.catalog)

    def _execute_rows(self, connection, sql, params=()):
        if "pg_xact_status" in sql:
            return [{"status": self.transaction_status}]
        if "pg_current_xact_id" in sql:
            return [{"xid": "77"}]
        if "current_database" in sql:
            return [{"database": "demo", "database_oid": "42", "server_version_num": "160000", "server_address": None, "server_port": None}]
        return []


class FakeSchemas:
    def __init__(self, conflict=False):
        self.conflict = conflict

    def require_migration_binding(self, *args):
        return {"schema": {}}

    def sync_full_migration_result(self, *args):
        if self.conflict:
            raise SchemaStoreError(409, "schema_conflict", "changed")
        return {"status": "saved", "revision": 2, "layoutToken": "c" * 64}


def full_plan():
    plan_id = str(uuid.uuid4())
    proof = full_schema_completeness_proof("a" * 64, "d" * 64)
    review = {"adapterKind": "full_schema", "target": {"profileId": "local", "database": "demo", "namespace": "public"},
              "steps": [{"sql": "CREATE TABLE events(id integer);", "destructive": False}], "warnings": [], "destructive": False,
              "complete": True, "applyCapable": True, "blockingDifferences": [], "completenessProof": proof}
    return {"planId": plan_id, "applicationId": "schemii", "resourceKind": "schema", "resourceId": "schema_one",
            "resourceRevision": 1, "layoutToken": "c" * 64, "adapterKind": "full_schema", "sourceKind": "normal",
            "target": {"profileId": "local", "databaseName": "demo", "namespaceName": "public", "profileFingerprint": "b" * 64,
                       "connectedTargetFingerprint": canonical_fingerprint(IDENTITY)},
            "liveFingerprint": "a" * 64, "desiredFingerprint": "d" * 64, "reviewPayload": review,
            "reviewDigest": "e" * 64, "destructive": False, "state": "ready",
             "privatePayload": {"schemaBinding": {"schemaId": "schema_one", "revision": 1, "layoutToken": "c" * 64},
                                 "desiredSchema": {}, "steps": review["steps"], "completenessProof": proof}}


def insert_plan():
    plan = full_plan()
    plan["adapterKind"] = "insert_rows"
    tree = [
        {"relation_oid": "7", "namespace": "public", "name": "events", "row_security": False, "force_row_security": False},
        {"relation_oid": "9", "namespace": "archive", "name": "events_old", "row_security": False, "force_row_security": False},
        {"relation_oid": "8", "namespace": "public", "name": "events_new", "row_security": False, "force_row_security": False},
    ]
    catalog = {"catalogKind": "p", "relationOid": "7", "tree": tree, "columns": [], "constraints": [], "triggers": [], "policies": [], "rules": [], "types": [], "dependencies": []}
    effects, effects_digest = DurableMigrationCoordinator._insert_effects(catalog)
    review = {"adapterKind": "insert_rows", "target": {"profileId": "local", "database": "demo", "namespace": "public"},
              "kind": "insert_rows", "steps": [{"sql": 'INSERT INTO "public"."events" ("name") VALUES (%s)', "destructive": False}],
              "warnings": [], "destructive": False, "rowCount": 2, "submittedRowCount": 2,
              "effects": effects, "effectsDigest": effects_digest, "secondaryWritesCounted": False}
    plan.update(liveFingerprint=canonical_fingerprint(catalog), desiredFingerprint="d" * 64, reviewPayload=review, reviewDigest="e" * 64)
    plan["privatePayload"] = {"schemaBinding": plan["privatePayload"]["schemaBinding"], "relation": "events",
                              "expectation": {"kind": "partitioned_table", "fingerprint": plan["liveFingerprint"], "catalog": catalog},
                              "columns": ["name"], "encodedRows": '[{"name":"a"},{"name":"b"}]', "rowCount": 2,
                              "steps": review["steps"], "effectsDigest": effects_digest}
    return plan


class DurableMigrationExecutionTests(unittest.TestCase):
    @staticmethod
    def interrupted_execution(metadata, *, state, intended=False):
        metadata.create_migration_execution(metadata.plan["planId"], metadata.plan["reviewDigest"], False)
        if state == "applying":
            metadata.begin_migration_execution(metadata.execution["executionId"], "77", IDENTITY)
            if intended:
                metadata.record_migration_intended_result(metadata.execution["executionId"], {
                    "kind": "migration_applied", "resultFingerprint": "a" * 64,
                })
        return metadata.execution["executionId"]

    def test_confirmation_xid_intended_and_commit_are_ordered_and_apply_is_single_owner(self):
        connection = Connection()
        metadata = MemoryMetadata(full_plan())
        coordinator = DurableMigrationCoordinator(FakeService(connection), metadata, FakeSchemas())
        result = coordinator.apply(metadata.plan["planId"], metadata.plan["reviewDigest"], False)
        duplicate = coordinator.apply(metadata.plan["planId"], metadata.plan["reviewDigest"], False)
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(duplicate["state"], "succeeded")
        self.assertEqual(connection.commits, 1)
        self.assertFalse(any("statement_timeout" in sql for sql, _ in connection.events))
        self.assertLess(metadata.events.index("confirmation"), metadata.events.index("xid"))
        self.assertLess(metadata.events.index("xid"), metadata.events.index("intended"))
        mutation_index = next(index for index, event in enumerate(connection.events) if event[0].startswith("CREATE TABLE"))
        xid_event_index = metadata.events.index("xid")
        self.assertGreater(mutation_index, 0)
        self.assertGreater(xid_event_index, metadata.events.index("confirmation"))

    def test_ai_apply_installs_bound_timeout_before_target_locks_and_null_inherits(self):
        plan = full_plan()
        plan["sourceKind"] = "ai"
        plan["privatePayload"]["operationTimeoutMs"] = 2500
        connection = Connection()
        metadata = MemoryMetadata(plan)
        DurableMigrationCoordinator(FakeService(connection), metadata, FakeSchemas()).apply(
            plan["planId"], plan["reviewDigest"], False, operation_timeout_ms=2500,
        )
        timeout_index = next(index for index, (sql, _) in enumerate(connection.events) if "statement_timeout" in sql)
        lock_index = next(index for index, (sql, _) in enumerate(connection.events) if sql == "LOCK NAMESPACE")
        self.assertLess(timeout_index, lock_index)
        self.assertEqual(connection.events[timeout_index][1], (2500, 2500, True))

        plan = full_plan()
        plan["sourceKind"] = "ai"
        plan["privatePayload"]["operationTimeoutMs"] = None
        connection = Connection()
        DurableMigrationCoordinator(FakeService(connection), MemoryMetadata(plan), FakeSchemas()).apply(
            plan["planId"], plan["reviewDigest"], False, operation_timeout_ms=None,
        )
        self.assertFalse(any("statement_timeout" in sql for sql, _ in connection.events))

    def test_ai_apply_rejects_tampered_timeout_before_target_connection(self):
        plan = full_plan()
        plan["sourceKind"] = "ai"
        plan["privatePayload"]["operationTimeoutMs"] = 2500
        connection = Connection()
        service = FakeService(connection)
        metadata = MemoryMetadata(plan)
        with self.assertRaises(PostgresServiceError) as caught:
            DurableMigrationCoordinator(service, metadata, FakeSchemas()).apply(
                plan["planId"], plan["reviewDigest"], False, operation_timeout_ms=5000,
            )
        self.assertEqual(caught.exception.code, "authority_binding_mismatch")
        self.assertEqual(service.connects, 0)
        self.assertEqual(metadata.execution["state"], "failed")

    def test_timeout_helper_preserves_stricter_postgresql_policy(self):
        class RecordingCursor:
            def __init__(self): self.calls = []
            def execute(self, sql, params): self.calls.append((sql, params))

        cursor = RecordingCursor()
        narrow_statement_timeout(cursor, None)
        self.assertEqual(cursor.calls, [])
        narrow_statement_timeout(cursor, 4000)
        sql, params = cursor.calls[0]
        self.assertIn("current_setting('statement_timeout')", sql)
        self.assertIn("ELSE pg_catalog.current_setting('statement_timeout')", sql)
        self.assertEqual(params, (4000, 4000, True))

    def test_incomplete_full_preview_is_returned_without_creating_durable_plan(self):
        class PreviewService:
            def _profile(self, profile_id):
                return {"dbname": "demo"}

            def preview(self, *args, **kwargs):
                return {
                    "id": None, "previewOnly": True, "steps": [{"sql": "CREATE TABLE safe_part();"}],
                    "warnings": [], "blockingDifferences": [{"code": "unsupported", "message": "blocked"}],
                    "complete": False, "applyCapable": False, "liveFingerprint": "a" * 64,
                }

        class BoundSchemas:
            def require_migration_binding(self, *args):
                return {"schema": {"tables": []}}

        class NoPlans:
            def create_migration_plan(self, *args, **kwargs):
                raise AssertionError("incomplete previews must not persist a plan")

        result = DurableMigrationCoordinator(PreviewService(), NoPlans(), BoundSchemas()).preview_full(
            "local", "public", "schema_one", 1, "c" * 64, False,
        )

        self.assertIsNone(result["id"])
        self.assertFalse(result["complete"])
        self.assertFalse(result["applyCapable"])
        self.assertEqual(len(result["steps"]), 1)

    def test_legacy_full_schema_plan_without_completeness_proof_fails_before_execution(self):
        plan = full_plan()
        plan["privatePayload"].pop("completenessProof")
        metadata = MemoryMetadata(plan)
        coordinator = DurableMigrationCoordinator(FakeService(Connection()), metadata, FakeSchemas())

        with self.assertRaises(PostgresServiceError) as caught:
            coordinator.apply(plan["planId"], plan["reviewDigest"], False)

        self.assertEqual(caught.exception.code, "migration_plan_incomplete")
        self.assertIsNone(metadata.execution)

    def test_commit_exception_is_uncertain_and_never_claims_rollback(self):
        connection = Connection(fail_commit=True)
        metadata = MemoryMetadata(full_plan())
        coordinator = DurableMigrationCoordinator(FakeService(connection), metadata, FakeSchemas())
        with self.assertRaises(PostgresServiceError) as caught:
            coordinator.apply(metadata.plan["planId"], metadata.plan["reviewDigest"], False)
        self.assertEqual(caught.exception.code, "execution_outcome_unknown")
        self.assertEqual(metadata.execution["state"], "uncertain")
        self.assertEqual(connection.rollbacks, 0)

    def test_reconcile_uses_xact_status_without_replaying_sql_and_sync_conflict_does_not_change_commit(self):
        connection = Connection(fail_commit=True)
        metadata = MemoryMetadata(full_plan())
        service = FakeService(connection)
        coordinator = DurableMigrationCoordinator(service, metadata, FakeSchemas(conflict=True))
        with self.assertRaises(PostgresServiceError):
            coordinator.apply(metadata.plan["planId"], metadata.plan["reviewDigest"], False)
        mutations_before = len([event for event in connection.events if event[0].startswith("CREATE TABLE")])
        connection.fail_commit = False
        result = coordinator.reconcile(metadata.execution["executionId"])
        mutations_after = len([event for event in connection.events if event[0].startswith("CREATE TABLE")])
        self.assertEqual((mutations_before, mutations_after), (1, 1))
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["execution"]["commitOutcome"], "committed")
        self.assertEqual(result["execution"]["sync"]["state"], "conflict")

    def test_restart_uses_same_metadata_plan_without_process_memory(self):
        metadata = MemoryMetadata(full_plan())
        first = DurableMigrationCoordinator(FakeService(Connection()), metadata, FakeSchemas())
        plan_id = metadata.plan["planId"]
        self.assertEqual(first.status(plan_id)["state"], "ready")
        restarted = DurableMigrationCoordinator(FakeService(Connection()), metadata, FakeSchemas())
        self.assertEqual(restarted.apply(plan_id, metadata.plan["reviewDigest"], False)["state"], "succeeded")

    def test_crash_after_confirmation_before_xid_closes_as_not_started_without_connecting(self):
        metadata = MemoryMetadata(full_plan())
        execution_id = self.interrupted_execution(metadata, state="ready")
        service = FakeService(Connection())
        result = DurableMigrationCoordinator(service, metadata, FakeSchemas()).reconcile(execution_id)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["execution"]["commitOutcome"], "rolled_back")
        self.assertEqual(service.connects, 0)

    def test_crash_after_xid_before_intended_aborted_is_failed_without_replay(self):
        metadata = MemoryMetadata(full_plan())
        execution_id = self.interrupted_execution(metadata, state="applying")
        connection = Connection()
        result = DurableMigrationCoordinator(
            FakeService(connection, transaction_status="aborted"), metadata, FakeSchemas(),
        ).reconcile(execution_id)
        self.assertEqual(result["state"], "failed")
        self.assertFalse(any(sql.startswith("CREATE TABLE") for sql, _ in connection.events))

    def test_crash_after_xid_before_intended_committed_requires_manual_recovery(self):
        metadata = MemoryMetadata(full_plan())
        execution_id = self.interrupted_execution(metadata, state="applying")
        connection = Connection()
        result = DurableMigrationCoordinator(FakeService(connection), metadata, FakeSchemas()).reconcile(execution_id)
        self.assertEqual(result["state"], "uncertain")
        self.assertEqual(result["recovery"]["status"], "manual_required")
        self.assertEqual(result["execution"]["reconciliationStatus"], "failed")
        self.assertFalse(any(sql.startswith("CREATE TABLE") for sql, _ in connection.events))

    def test_crash_after_intended_before_commit_reconciles_aborted_without_replay(self):
        metadata = MemoryMetadata(full_plan())
        execution_id = self.interrupted_execution(metadata, state="applying", intended=True)
        connection = Connection()
        result = DurableMigrationCoordinator(
            FakeService(connection, transaction_status="aborted"), metadata, FakeSchemas(),
        ).reconcile(execution_id)
        self.assertEqual(result["state"], "failed")
        self.assertFalse(any(sql.startswith("CREATE TABLE") for sql, _ in connection.events))

    def test_crash_after_intended_and_commit_reconciles_success_and_sync_without_replay(self):
        metadata = MemoryMetadata(full_plan())
        execution_id = self.interrupted_execution(metadata, state="applying", intended=True)
        connection = Connection()
        result = DurableMigrationCoordinator(FakeService(connection), metadata, FakeSchemas()).reconcile(execution_id)
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["execution"]["sync"]["state"], "succeeded")
        self.assertFalse(any(sql.startswith("CREATE TABLE") for sql, _ in connection.events))

    def test_metadata_finalize_failure_after_successful_commit_is_bounded_and_recoverable(self):
        connection = Connection()
        metadata = MemoryMetadata(full_plan(), fail_finalize=True)
        service = FakeService(connection)
        coordinator = DurableMigrationCoordinator(service, metadata, FakeSchemas())
        with self.assertRaises(PostgresServiceError) as caught:
            coordinator.apply(metadata.plan["planId"], metadata.plan["reviewDigest"], False)
        self.assertEqual(caught.exception.code, "execution_outcome_unknown")
        self.assertEqual(caught.exception.details, {"executionId": metadata.execution["executionId"], "reconcileRequired": True})
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        mutations = len([event for event in connection.events if event[0].startswith("CREATE TABLE")])
        restarted = DurableMigrationCoordinator(FakeService(connection), metadata, FakeSchemas())
        result = restarted.reconcile(metadata.execution["executionId"])
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(len([event for event in connection.events if event[0].startswith("CREATE TABLE")]), mutations)

    def test_stale_catalog_fails_before_xid_and_never_executes_mutation(self):
        connection = Connection()
        metadata = MemoryMetadata(full_plan())
        service = FakeService(connection)
        service.catalog["postgres"]["fingerprint"] = "9" * 64
        coordinator = DurableMigrationCoordinator(service, metadata, FakeSchemas())
        with self.assertRaises(PostgresServiceError) as caught:
            coordinator.apply(metadata.plan["planId"], metadata.plan["reviewDigest"], False)
        self.assertEqual(caught.exception.code, "stale_plan")
        self.assertEqual(metadata.execution["state"], "failed")
        self.assertEqual(connection.rollbacks, 1)
        self.assertFalse(any(sql.startswith("CREATE TABLE") for sql, _ in connection.events))

    def test_stale_opaque_migration_assessment_fails_before_mutation(self):
        connection = Connection()
        plan = full_plan()
        metadata = MemoryMetadata(plan)
        service = FakeService(connection)
        service.assessment_token = "preview"
        plan["liveFingerprint"] = service._migration_fingerprint(
            service.catalog, service._migration_safety_assessment("local", "public", service.catalog, {}),
        )
        plan["privatePayload"]["completenessProof"] = full_schema_completeness_proof(
            plan["liveFingerprint"], plan["desiredFingerprint"],
        )
        plan["reviewPayload"]["completenessProof"] = copy.deepcopy(plan["privatePayload"]["completenessProof"])
        metadata.plan = copy.deepcopy(plan)
        service.assessment_token = "changed"

        with self.assertRaises(PostgresServiceError) as caught:
            DurableMigrationCoordinator(service, metadata, FakeSchemas()).apply(
                plan["planId"], plan["reviewDigest"], False,
            )

        self.assertEqual(caught.exception.code, "stale_plan")
        self.assertFalse(any(sql.startswith("CREATE TABLE") for sql, _ in connection.events))

    def test_mutation_failure_after_xid_is_proven_rolled_back_and_sanitized(self):
        connection = Connection(fail_on="CREATE TABLE")
        metadata = MemoryMetadata(full_plan())
        coordinator = DurableMigrationCoordinator(FakeService(connection), metadata, FakeSchemas())
        with self.assertRaises(PostgresServiceError) as caught:
            coordinator.apply(metadata.plan["planId"], metadata.plan["reviewDigest"], False)
        self.assertEqual(caught.exception.code, "apply_failed")
        self.assertEqual(caught.exception.details["rollback"], {"proven": True, "state": "rolled_back"})
        self.assertNotIn("untrusted", caught.exception.message)
        self.assertEqual(metadata.execution["state"], "failed")
        self.assertEqual(metadata.execution["commitOutcome"], "rolled_back")
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.commits, 0)

    def test_postgresql_diagnostic_is_preserved_with_proven_rollback(self):
        class Diagnostic:
            message_primary = "cannot insert a non-DEFAULT value into identity column"
            message_detail = "Column id is an identity column defined as GENERATED ALWAYS."
            message_hint = "Use OVERRIDING SYSTEM VALUE to override."
            statement_position = "15"

        class DatabaseFailure(Exception):
            sqlstate = "428C9"
            diag = Diagnostic()

        class DiagnosticCursor(Cursor):
            def execute(self, sql, params=()):
                if "CREATE TABLE" in sql:
                    raise DatabaseFailure("driver text")
                super().execute(sql, params)

        class DiagnosticConnection(Connection):
            def cursor(self):
                return DiagnosticCursor(self)

        connection = DiagnosticConnection()
        metadata = MemoryMetadata(full_plan())
        with self.assertRaises(PostgresServiceError) as caught:
            DurableMigrationCoordinator(FakeService(connection), metadata, FakeSchemas()).apply(metadata.plan["planId"], metadata.plan["reviewDigest"], False)
        self.assertEqual(caught.exception.details["postgres"], {
            "sqlstate": "428C9", "message": Diagnostic.message_primary,
            "detail": Diagnostic.message_detail, "hint": Diagnostic.message_hint, "position": 15,
        })
        self.assertEqual(caught.exception.details["rollback"]["state"], "rolled_back")

    def test_partition_tree_locks_are_oid_ordered_before_reinspection_and_xid(self):
        class InsertService(FakeService):
            def _inspect_ai_insert_target(self, connection, database, namespace, relation, columns):
                connection.events.append(("REINSPECT INSERT TARGET", ()))
                return copy.deepcopy(plan["privatePayload"]["expectation"])

            def _execute_rows(self, connection, sql, params=()):
                if "pg_current_xact_id" in sql:
                    connection.events.append(("OBTAIN XID", ()))
                return super()._execute_rows(connection, sql, params)

        plan = insert_plan()
        connection = Connection()
        metadata = MemoryMetadata(plan)
        result = DurableMigrationCoordinator(InsertService(connection), metadata, FakeSchemas()).apply(plan["planId"], plan["reviewDigest"], False)
        statements = [sql for sql, _ in connection.events]
        root = statements.index('LOCK TABLE "public"."events" IN SHARE UPDATE EXCLUSIVE MODE')
        leaf_eight = statements.index('LOCK TABLE "public"."events_new" IN ROW EXCLUSIVE MODE')
        leaf_nine = statements.index('LOCK TABLE "archive"."events_old" IN ROW EXCLUSIVE MODE')
        reinspect = statements.index("REINSPECT INSERT TARGET")
        xid = statements.index("OBTAIN XID")
        insert = next(index for index, sql in enumerate(statements) if sql.startswith("INSERT INTO"))
        self.assertLess(root, leaf_eight)
        self.assertLess(leaf_eight, leaf_nine)
        self.assertLess(leaf_nine, reinspect)
        self.assertLess(reinspect, xid)
        self.assertLess(xid, insert)
        intended = result["execution"]["intendedResult"]
        self.assertEqual((intended["submittedRowCount"], intended["commandRowCount"]), (2, 2))
        self.assertFalse(intended["secondaryWritesCounted"])

    def test_effect_digest_covers_bounded_secondary_effect_review(self):
        catalog = insert_plan()["privatePayload"]["expectation"]["catalog"]
        catalog["triggers"] = [{"oid": str(index), "name": f"trigger_{index}"} for index in range(55)]
        catalog["dependencies"] = [{"kind": "function", "namespace": "public", "name": "external_call"}]
        effects, digest = DurableMigrationCoordinator._insert_effects(catalog)
        trigger_effect = next(item for item in effects if item["kind"] == "triggers")
        external_effect = next(item for item in effects if item["kind"] == "unknown_external_function_effects")
        self.assertEqual((trigger_effect["objectCount"], len(trigger_effect["details"]), trigger_effect["complete"], trigger_effect["truncated"]), (55, 50, False, True))
        self.assertEqual(external_effect["certainty"], "unknown")
        self.assertEqual(digest, canonical_fingerprint({"effects": effects}))

    def test_tampered_insert_effect_digest_fails_before_execution(self):
        plan = insert_plan()
        plan["reviewPayload"]["effectsDigest"] = "0" * 64
        metadata = MemoryMetadata(plan)
        service = FakeService(Connection())
        with self.assertRaises(PostgresServiceError) as caught:
            DurableMigrationCoordinator(service, metadata, FakeSchemas()).apply(plan["planId"], plan["reviewDigest"], False)
        self.assertEqual(caught.exception.code, "insert_effects_incomplete")
        self.assertEqual(service.connects, 0)

    def test_post_commit_sync_metadata_failure_returns_recoverable_committed_status(self):
        connection = Connection()
        metadata = MemoryMetadata(full_plan(), fail_sync=True)
        coordinator = DurableMigrationCoordinator(FakeService(connection), metadata, FakeSchemas())
        result = coordinator.apply(metadata.plan["planId"], metadata.plan["reviewDigest"], False)
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["execution"]["commitOutcome"], "committed")
        self.assertEqual(result["recovery"]["status"], "sync_pending")
        self.assertEqual(connection.rollbacks, 0)
        metadata.fail_sync = False
        recovered = coordinator.reconcile(metadata.execution["executionId"])
        self.assertEqual(recovered["execution"]["sync"]["state"], "succeeded")


if __name__ == "__main__":
    unittest.main()
