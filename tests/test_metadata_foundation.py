import json
import sys
import tempfile
import unittest
import uuid
from importlib import resources
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.metadata import MetadataConfig, MetadataConnectionFactory, MetadataStore, MetadataStoreError
from schemii.metadata.migrator import MetadataMigrator, packaged_migrations, validate_applied_migrations
from schemii.metadata.validation import bounded_json


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.executions = []
        self.closed = False

    def execute(self, sql, params=None):
        self.executions.append((sql, params))

    def fetchone(self):
        return self.rows.pop(0)

    def fetchall(self):
        return self.rows.pop(0) if self.rows else []

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, rows=None):
        self.cursor_value = FakeCursor(rows)
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class MetadataConfigTests(unittest.TestCase):
    def test_explicit_and_environment_configuration(self):
        config = MetadataConfig.from_env({
            "SCHEMII_METADATA_DSN": "postgresql://metadata@db/schemii_metadata",
            "SCHEMII_METADATA_APPLICATION_NAME": "schemer-metadata",
            "SCHEMII_METADATA_CONNECT_TIMEOUT": "9",
            "SCHEMII_METADATA_MAX_JSON_BYTES": "4096",
            "SCHEMII_METADATA_EXPECTED_ROLE": "schemii_metadata_schemer",
            "SCHEMII_METADATA_EXPECTED_OWNER": "schemii_metadata_owner",
        })
        self.assertEqual(config.application_name, "schemer-metadata")
        self.assertEqual(config.connect_timeout, 9)
        self.assertEqual(config.max_json_bytes, 4096)
        self.assertEqual(config.expected_role, "schemii_metadata_schemer")
        self.assertEqual(config.expected_owner, "schemii_metadata_owner")
        with self.assertRaises(ValueError):
            MetadataConfig.from_env({})
        with self.assertRaises(ValueError):
            MetadataConfig("sqlite:///metadata.db")
        with self.assertRaises(ValueError):
            MetadataConfig("postgresql://metadata@db/schemii_metadata", max_json_bytes=1024 * 1024 + 1)

    def test_runtime_configuration_cannot_disable_or_replace_native_identity_checks(self):
        config = MetadataConfig.from_runtime_env("schemer", {
            "SCHEMII_METADATA_DSN": "postgresql://wrong-role@db/schemii_metadata",
            "SCHEMII_METADATA_APPLICATION_NAME": "operator-override",
            "SCHEMII_METADATA_EXPECTED_APPLICATION": "schemii",
            "SCHEMII_METADATA_EXPECTED_ROLE": "schemii_metadata_schemii",
            "SCHEMII_METADATA_EXPECTED_OWNER": "postgres",
            "SCHEMII_METADATA_EXPECTED_ADMIN_OWNER": "postgres",
        })
        self.assertEqual(config.application_name, "schemer")
        self.assertEqual(config.expected_application, "schemer")
        self.assertEqual(config.expected_role, "schemii_metadata_schemer")
        self.assertEqual(config.expected_owner, "schemii_metadata_owner")
        self.assertEqual(config.expected_admin_owner, "schemii_metadata_bootstrap")

        missing_overrides = MetadataConfig.from_runtime_env("schemii", {
            "SCHEMII_METADATA_DSN": "host=db dbname=schemii_metadata user=runtime",
        })
        self.assertEqual(missing_overrides.expected_role, "schemii_metadata_schemii")
        self.assertEqual(missing_overrides.expected_owner, "schemii_metadata_owner")

    def test_native_server_startup_fails_closed_on_missing_or_wrong_runtime_role(self):
        from schemii import schemer_server, server

        applied = [
            {"version": item.version, "name": item.name, "checksum": item.checksum}
            for item in packaged_migrations()
        ]
        for module, application, store_name in (
            (server, "schemii", "SchemaStore"),
            (schemer_server, "schemer", "DashboardStore"),
        ):
            expected_role = f"schemii_metadata_{application}"
            wrong_role = "schemii_metadata_schemer" if application == "schemii" else "schemii_metadata_schemii"
            wrong_identity = {
                **MetadataStoreTests.healthy_identity(),
                "application_id": application,
                "current_user": wrong_role,
                "session_user": wrong_role,
            }
            config = MetadataConfig(
                "postgresql://runtime@db/schemii_metadata",
                application_name=application,
                expected_application=application,
                expected_role=expected_role,
                expected_owner="schemii_metadata_owner",
                expected_admin_owner="schemii_metadata_bootstrap",
            )
            for identity in (None, wrong_identity):
                with self.subTest(application=application, identity="missing" if identity is None else "wrong"):
                    connection = FakeConnection(rows=[applied, identity])
                    with (
                        patch.object(module, "_paths", return_value=(Path("web"), Path("config"), Path("data"))),
                        patch.object(module, "validate_static_directory"),
                        patch.object(module, "PostgresService"),
                        patch.object(module, store_name),
                        patch.object(module.MetadataConfig, "from_runtime_env", return_value=config) as runtime_config,
                        patch.object(module, "MetadataConnectionFactory", return_value=lambda: connection),
                        patch.object(module, "run_server") as run_server,
                    ):
                        with self.assertRaisesRegex(SystemExit, "metadata readiness failed"):
                            module.main()
                    runtime_config.assert_called_once_with(application)
                    run_server.assert_not_called()

    def test_connection_factory_passes_only_validated_connection_settings(self):
        calls = []
        expected = object()
        factory = MetadataConnectionFactory(
            MetadataConfig("postgresql://metadata@db/schemii_metadata", connect_timeout=7),
            lambda *args, **kwargs: calls.append((args, kwargs)) or expected,
        )
        self.assertIs(factory(), expected)
        self.assertEqual(calls[0][0], ("postgresql://metadata@db/schemii_metadata",))
        self.assertEqual(calls[0][1], {"connect_timeout": 7, "application_name": "schemii-metadata"})

    def test_connection_factory_reads_password_file_without_changing_dsn(self):
        with tempfile.TemporaryDirectory() as directory:
            password_file = Path(directory) / "password"
            password_file.write_bytes(b"file-only-secret\n")
            calls = []
            factory = MetadataConnectionFactory(
                MetadataConfig("host=db dbname=schemii_metadata user=runtime", password_file=str(password_file)),
                lambda *args, **kwargs: calls.append((args, kwargs)) or object(),
            )
            factory()
        self.assertEqual(calls[0][0], ("host=db dbname=schemii_metadata user=runtime",))
        self.assertEqual(calls[0][1]["password"], "file-only-secret")
        self.assertNotIn("file-only-secret", calls[0][0][0])

    def test_connection_factory_rejects_invalid_password_file_format(self):
        for value in (
            "short\n",
            "valid-credential-value\r\n",
            "valid-credential-value\r",
            "valid-credential-value\nsecond-line\n",
            "invalid credential value\n",
        ):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                password_file = Path(directory) / "password"
                password_file.write_bytes(value.encode("utf-8"))
                factory = MetadataConnectionFactory(
                    MetadataConfig(
                        "host=db dbname=schemii_metadata user=runtime",
                        password_file=str(password_file),
                    ),
                    lambda *args, **kwargs: object(),
                )
                with self.assertRaises(MetadataStoreError) as caught:
                    factory()
                self.assertEqual(caught.exception.code, "metadata_unavailable")

    def test_connection_errors_are_structured_and_do_not_leak_dsn(self):
        dsn = "postgresql://metadata:do-not-leak@db/schemii_metadata"
        factory = MetadataConnectionFactory(MetadataConfig(dsn), lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(dsn)))
        with self.assertRaises(MetadataStoreError) as caught:
            factory()
        self.assertEqual(caught.exception.code, "metadata_unavailable")
        self.assertNotIn("do-not-leak", str(caught.exception.to_dict()))


class MetadataValidationTests(unittest.TestCase):
    def test_json_is_bounded_and_credentials_are_rejected_recursively(self):
        self.assertEqual(bounded_json({"rows": [[1]]}, "payload", 1024), {"rows": [[1]]})
        for payload in ({"password": "x"}, {"nested": [{"accessToken": "x"}]}):
            with self.assertRaises(MetadataStoreError) as caught:
                bounded_json(payload, "payload", 1024)
            self.assertEqual(caught.exception.code, "credentials_forbidden")
        with self.assertRaises(MetadataStoreError):
            bounded_json({"value": "x" * 2000}, "payload", 1024)


class MetadataMigrationTests(unittest.TestCase):
    def test_packaged_migrations_are_ordered_and_checksummed(self):
        migrations = packaged_migrations()
        self.assertGreaterEqual(len(migrations), 1)
        self.assertEqual([item.version for item in migrations], list(range(1, len(migrations) + 1)))
        self.assertTrue(all(len(item.checksum) == 64 for item in migrations))

    def test_migrator_locks_checks_and_records_each_migration(self):
        connection = FakeConnection(rows=[[]])
        migration = packaged_migrations()[0]
        migrator = MetadataMigrator(lambda: connection, (migration,))
        self.assertEqual(migrator.migrate(), 1)
        sql = "\n".join(item[0] for item in connection.cursor_value.executions)
        self.assertIn("pg_advisory_lock", sql)
        self.assertIn("metadata_schema_migrations", sql)
        self.assertIn("INSERT INTO metadata_schema_migrations", sql)
        self.assertIn("pg_advisory_unlock", sql)
        self.assertGreaterEqual(connection.commits, 3)
        self.assertTrue(connection.closed)

    def test_migrator_rejects_changed_applied_checksum(self):
        migration = packaged_migrations()[0]
        connection = FakeConnection(rows=[[{
            "version": migration.version,
            "name": migration.name,
            "checksum": "0" * 64,
        }]])
        with self.assertRaises(MetadataStoreError) as caught:
            MetadataMigrator(lambda: connection, (migration,)).migrate()
        self.assertEqual(caught.exception.code, "metadata_migration_checksum")
        self.assertEqual(connection.rollbacks, 1)

    def test_migration_history_must_be_an_exact_contiguous_prefix(self):
        migrations = packaged_migrations()
        with self.assertRaises(MetadataStoreError) as caught:
            validate_applied_migrations([{"version": 2, "name": migrations[0].name, "checksum": migrations[0].checksum}], migrations)
        self.assertEqual(caught.exception.code, "metadata_migration_history_invalid")


class MetadataSqlContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = resources.files("schemii.metadata.migrations").joinpath("0001_initial.sql").read_text(encoding="utf-8")

    def test_schema_contains_all_normalized_foundations(self):
        tables = {
            "metadata_applications", "metadata_chats", "metadata_targets", "metadata_policy_versions",
            "metadata_capabilities", "metadata_grants", "metadata_proposals", "metadata_operations",
            "metadata_operation_approvals", "metadata_operation_attempts", "metadata_operation_outcomes",
            "metadata_query_result_references", "metadata_query_result_payloads", "metadata_query_result_deliveries",
            "metadata_authority_transitions", "metadata_migration_plans", "metadata_migration_executions",
            "metadata_migration_syncs", "metadata_migration_transitions",
        }
        for table in tables:
            self.assertIn(f"CREATE TABLE {table}", self.sql)

    def test_schema_enforces_ownership_states_uniqueness_and_json_bounds(self):
        self.assertIn("proposal_id uuid UNIQUE", self.sql)
        self.assertIn("plan_id uuid NOT NULL UNIQUE", self.sql)
        self.assertIn("metadata_attempts_one_running", self.sql)
        self.assertIn("metadata_deliveries_one_open", self.sql)
        self.assertIn("pg_column_size(payload) <= 1048576", self.sql)
        self.assertIn("CHECK (state IN ('ready', 'applying', 'succeeded', 'failed', 'uncertain'))", self.sql)
        self.assertNotIn("password", self.sql.lower())
        self.assertNotIn("credential", self.sql.lower())

    def test_schema_enforces_runtime_application_isolation(self):
        self.assertIn("CREATE FUNCTION metadata_current_application()", self.sql)
        self.assertIn("ALTER TABLE metadata_chats ENABLE ROW LEVEL SECURITY", self.sql)
        self.assertIn("metadata_migration_plans_isolation", self.sql)

    def test_current_migration_forces_rls_without_blocking_owner_maintenance(self):
        sql = resources.files("schemii.metadata.migrations").joinpath(
            "0012_force_rls_and_catalog_guards.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("CREATE POLICY metadata_owner_maintenance", sql)
        self.assertIn("TO schemii_metadata_owner", sql)
        self.assertIn("FORCE ROW LEVEL SECURITY", sql)

    def test_chat_cleanup_ownership_preserves_audit_and_shared_policy_evidence(self):
        sql = resources.files("schemii.metadata.migrations").joinpath(
            "0013_chat_cleanup_ownership.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("REFERENCES metadata_operations(operation_id)", sql)
        self.assertIn("ON DELETE CASCADE", sql)
        self.assertIn("metadata_authority_transitions", sql)
        self.assertNotIn("DELETE FROM metadata_authority_transitions", sql)
        self.assertNotIn("DELETE FROM metadata_agent_policy_revisions", sql)


class MetadataStoreTests(unittest.TestCase):
    @staticmethod
    def healthy_identity():
        owner = "schemii_metadata_owner"
        role_defaults = {
            "inherit": True, "superuser": False, "createRole": False,
            "createDatabase": False, "replication": False, "bypassRls": False,
        }
        return {
            "current_user": "schemii_metadata_schemer", "session_user": "schemii_metadata_schemer",
            "database_owner": owner, "schema_owner": owner,
            "application_id": "schemer", "admin_schema_owner": "schemii_metadata_bootstrap",
            "object_owners": [
                {"name": "metadata_applications", "kind": "r", "owner": owner},
                {"name": "metadata_schema_migrations", "kind": "r", "owner": owner},
                {"name": "metadata_operation_events_event_id_seq", "kind": "S", "owner": owner},
            ],
            "function_owners": [{"name": "metadata_current_application", "owner": owner}],
            "rls_tables": [{"name": "metadata_applications", "enabled": True, "forced": True}],
            "metadata_roles": [
                {"name": "schemii_metadata_bootstrap", "login": False, "memberOfOwner": False,
                 "inherit": True, "superuser": True, "createRole": True,
                 "createDatabase": True, "replication": True, "bypassRls": True},
                {"name": owner, "login": False, "memberOfOwner": False, **role_defaults},
                {"name": "schemii_metadata_migration", "login": True, "memberOfOwner": True, **role_defaults},
                {"name": "schemii_metadata_schemii", "login": True, "memberOfOwner": False, **role_defaults},
                {"name": "schemii_metadata_schemer", "login": True, "memberOfOwner": False, **role_defaults},
            ],
        }

    def test_health_requires_exact_packaged_version(self):
        migrations = packaged_migrations()
        connection = FakeConnection(rows=[[
            {"version": migration.version, "name": migration.name, "checksum": migration.checksum}
            for migration in migrations
        ]])
        store = MetadataStore(lambda: connection)
        expected_version = len(migrations)
        self.assertEqual(store.health(), {"ok": True, "version": expected_version, "expectedVersion": expected_version})
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertTrue(connection.closed)

    def test_health_validates_runtime_role_and_metadata_owners(self):
        migrations = packaged_migrations()
        applied = [{"version": item.version, "name": item.name, "checksum": item.checksum} for item in migrations]
        identity = self.healthy_identity()
        store = MetadataStore(
            lambda: FakeConnection(rows=[applied, identity]),
            expected_application="schemer", expected_role="schemii_metadata_schemer",
            expected_owner="schemii_metadata_owner", expected_admin_owner="schemii_metadata_bootstrap",
        )
        self.assertEqual(store.health()["role"], "schemii_metadata_schemer")

        mismatched = MetadataStore(
            lambda: FakeConnection(rows=[applied, {**identity, "current_user": "schemii_metadata_schemii", "session_user": "schemii_metadata_schemii"}]),
            expected_application="schemer", expected_role="schemii_metadata_schemer",
            expected_owner="schemii_metadata_owner", expected_admin_owner="schemii_metadata_bootstrap",
        )
        with self.assertRaises(MetadataStoreError) as caught:
            mismatched.health()
        self.assertEqual(caught.exception.code, "metadata_identity_mismatch")

    def test_health_rejects_catalog_ownership_rls_and_role_drift(self):
        migrations = packaged_migrations()
        applied = [{"version": item.version, "name": item.name, "checksum": item.checksum} for item in migrations]
        cases = (
            ("object_owners", [{"name": "metadata_applications", "kind": "r", "owner": "postgres"}], "ownershipDrift"),
            ("rls_tables", [{"name": "metadata_applications", "enabled": True, "forced": False}], "rowSecurityDrift"),
            ("metadata_roles", [], "roleDrift"),
        )
        for field, value, detail in cases:
            with self.subTest(field=field):
                identity = {**self.healthy_identity(), field: value}
                store = MetadataStore(
                    lambda: FakeConnection(rows=[applied, identity]),
                    expected_application="schemer", expected_role="schemii_metadata_schemer",
                    expected_owner="schemii_metadata_owner", expected_admin_owner="schemii_metadata_bootstrap",
                )
                with self.assertRaises(MetadataStoreError) as caught:
                    store.health()
                self.assertEqual(caught.exception.code, "metadata_catalog_mismatch")
                self.assertTrue(caught.exception.details[detail])

    def test_health_fails_closed_when_identity_or_catalog_rows_are_unavailable(self):
        migrations = packaged_migrations()
        applied = [{"version": item.version, "name": item.name, "checksum": item.checksum} for item in migrations]
        missing_identity = MetadataStore(
            lambda: FakeConnection(rows=[applied, None]),
            expected_application="schemer", expected_role="schemii_metadata_schemer",
            expected_owner="schemii_metadata_owner", expected_admin_owner="schemii_metadata_bootstrap",
        )
        with self.assertRaises(MetadataStoreError) as caught:
            missing_identity.health()
        self.assertEqual(caught.exception.code, "metadata_identity_mismatch")

        malformed = {**self.healthy_identity(), "object_owners": "not-json"}
        malformed_catalog = MetadataStore(
            lambda: FakeConnection(rows=[applied, malformed]),
            expected_application="schemer", expected_role="schemii_metadata_schemer",
            expected_owner="schemii_metadata_owner", expected_admin_owner="schemii_metadata_bootstrap",
        )
        with self.assertRaises(MetadataStoreError) as caught:
            malformed_catalog.health()
        self.assertEqual(caught.exception.code, "metadata_catalog_mismatch")

    def test_chat_conversation_title_initialization_and_rename_are_durable(self):
        chat_id = str(uuid.uuid4())
        connection = FakeConnection(rows=[
            {"application_id": "schemii", "state": "active"},
            {"conversation_title": "Build varied slate assignments"},
        ])
        store = MetadataStore(lambda: connection)

        result = store.set_chat_conversation_title(chat_id, "Build varied slate assignments")

        self.assertEqual(result["conversationTitle"], "Build varied slate assignments")
        update = next(item for item in connection.cursor_value.executions if "SET conversation_title" in item[0])
        self.assertEqual(update[1][2], False)

        with self.assertRaises(MetadataStoreError):
            store.set_chat_conversation_title(chat_id, "Invalid\ntitle", overwrite=True)

    def test_policy_update_is_transactional_and_increments_locked_revision(self):
        connection = FakeConnection(rows=[{"state": "active"}, {"revision": 2}, {"application_id": "schemii"}])
        store = MetadataStore(lambda: connection)
        result = store.update_policy(str(uuid.uuid4()), 2, {"version": 1}, {"write": "approval"})
        self.assertEqual(result["revision"], 3)
        sql = "\n".join(item[0] for item in connection.cursor_value.executions)
        self.assertIn("FOR UPDATE", sql)
        self.assertIn("metadata_policy_versions", sql)
        self.assertIn("metadata_capabilities", sql)
        self.assertIn("metadata_grants SET state = 'revoked'", sql)

    def test_claim_operation_creates_hashed_single_attempt(self):
        operation_id = uuid.uuid4()
        connection = FakeConnection(rows=[
            {"cancellation_requested_at": None},
            {"state": "ready", "chat_id": uuid.uuid4()},
            {"application_id": "schemii"},
        ])
        result = MetadataStore(lambda: connection).claim_operation(str(operation_id), "worker-1")
        self.assertEqual(result["state"], "running")
        insert = next(item for item in connection.cursor_value.executions if "INSERT INTO metadata_operation_attempts" in item[0])
        self.assertEqual(len(insert[1][3]), 64)
        self.assertNotEqual(insert[1][3], result["claimToken"])

    def test_authorize_once_per_chat_atomically_creates_grant_approval_and_operation(self):
        chat_id = uuid.uuid4()
        connection = FakeConnection(rows=[{
            "chat_id": chat_id,
            "capability": "write",
            "policy_revision": 4,
            "state": "ready",
            "binding": {"policyBinding": {"effectiveMode": "once_per_chat"}},
            "current": True,
        }, {"state": "active"}, {
            "grant_mode": "once_per_chat",
            "current_revision": 4,
            "grant_id": None,
        }, {"application_id": "schemii"}, {"application_id": "schemii"}])
        result = MetadataStore(lambda: connection).authorize_and_create_operation(
            str(uuid.uuid4()), expected_policy_revision=4, approved=True,
        )
        self.assertTrue(result["executionOwner"])
        sql = "\n".join(item[0] for item in connection.cursor_value.executions)
        self.assertIn("FROM metadata_proposals WHERE proposal_id = %s FOR UPDATE", sql)
        self.assertIn("INSERT INTO metadata_grants", sql)
        self.assertIn("INSERT INTO metadata_operations", sql)
        self.assertIn("INSERT INTO metadata_operation_approvals", sql)
        self.assertEqual(connection.commits, 1)

    def test_authorize_rejects_proposal_bound_to_noncurrent_policy(self):
        connection = FakeConnection(rows=[{
            "chat_id": uuid.uuid4(),
            "capability": "write",
            "policy_revision": 4,
            "state": "ready",
            "binding": {"policyBinding": {"effectiveMode": "automatic"}},
            "current": True,
        }, {"state": "active"}, {
            "grant_mode": "automatic",
            "current_revision": 5,
            "grant_id": None,
        }])
        with self.assertRaises(MetadataStoreError) as caught:
            MetadataStore(lambda: connection).authorize_and_create_operation(
                str(uuid.uuid4()), expected_policy_revision=4,
            )
        self.assertEqual(caught.exception.code, "policy_changed")
        self.assertEqual(connection.rollbacks, 1)

    def test_settings_linked_proposal_cannot_be_created_after_agent_revision_changes(self):
        chat_id = uuid.uuid4()
        revision_id = uuid.uuid4()
        snapshot = {
            "version": 2, "application": "schemii", "agentId": "default",
            "agentPolicyRevision": 3, "agentPolicyRevisionId": str(revision_id),
            "agentPolicySchemaVersion": 1, "policyDigest": "a" * 64,
            "capabilities": {"schema": {"configuredMode": "every_action", "effectiveMode": "every_action", "safetyFloorReason": None}},
            "bounds": {"rowsDisclosed": None, "rowsWritten": None, "pagesInspected": None, "rawStatements": None, "operationTimeoutMs": None, "agentConcurrency": 1},
            "disclosureClass": "schema", "targetVerified": False,
        }
        policy_binding = {
            "application": "schemii", "agentId": "default", "agentPolicyRevision": 3,
            "agentPolicyRevisionId": str(revision_id), "agentPolicySchemaVersion": 1,
            "chatPolicyRevision": 1, "policyRevision": 1, "canonicalCapability": "schema", "capability": "schema",
            "configuredMode": "every_action", "effectiveMode": "every_action", "safetyFloorReason": None,
            "snapshot": snapshot, "disclosureClass": "schema", "origin": "model",
            "resource": {"kind": "schema", "id": "schema_one", "revision": 4, "layoutToken": "b" * 64},
            "target": {},
        }
        connection = FakeConnection(rows=[
            {"application_id": "schemii", "state": "active"}, {"exists": 1},
            {"application_id": "schemii", "resource_kind": "schema", "resource_id": "schema_one", "policy": snapshot,
             "agent_policy_revision_id": revision_id, "agent_policy_schema_version": 1, "profile_id": None,
             "database_name": None, "namespace_name": None, "profile_fingerprint": None},
            {"current_revision": 4},
        ])
        with self.assertRaises(MetadataStoreError) as caught:
            MetadataStore(lambda: connection).create_proposal(
                str(chat_id), "schema", 1,
                {"policyBinding": policy_binding, "authorizationTarget": {}, "schemaConcurrency": {"revision": 4, "layoutToken": "b" * 64}},
                {"type": "add_table"},
            )
        self.assertEqual(caught.exception.code, "agent_policy_changed")

    def test_agent_concurrency_is_serialized_in_metadata_before_operation_creation(self):
        chat_id = uuid.uuid4()
        revision_id = uuid.uuid4()
        snapshot = {
            "version": 2, "application": "schemii", "agentId": "default",
            "agentPolicyRevision": 3, "agentPolicyRevisionId": str(revision_id),
            "agentPolicySchemaVersion": 1, "policyDigest": "a" * 64,
            "capabilities": {"structured_read": {"configuredMode": "automatic", "effectiveMode": "automatic", "safetyFloorReason": None}},
            "bounds": {"rowsDisclosed": None, "rowsWritten": None, "pagesInspected": None, "rawStatements": None, "operationTimeoutMs": None, "agentConcurrency": 1},
            "disclosureClass": "data", "targetVerified": True,
        }
        target = {"profileId": "local", "database": "demo", "namespace": "public", "profileFingerprint": "c" * 64}
        policy_binding = {
            "application": "schemii", "agentId": "default", "agentPolicyRevision": 3,
            "agentPolicyRevisionId": str(revision_id), "agentPolicySchemaVersion": 1,
            "chatPolicyRevision": 1, "policyRevision": 1, "canonicalCapability": "structured_read", "capability": "structured_read",
            "configuredMode": "automatic", "effectiveMode": "automatic", "safetyFloorReason": None,
            "snapshot": snapshot, "disclosureClass": "data", "origin": "model",
            "resource": {"kind": "schema", "id": "schema_one", "revision": 4, "layoutToken": "b" * 64},
            "target": target,
        }
        binding = {"policyBinding": policy_binding, "authorizationTarget": target, "schemaConcurrency": {"revision": 4, "layoutToken": "b" * 64}}
        durable = {
            "application_id": "schemii", "resource_kind": "schema", "resource_id": "schema_one", "policy": snapshot,
            "agent_policy_revision_id": revision_id, "agent_policy_schema_version": 1, "profile_id": "local",
            "database_name": "demo", "namespace_name": "public", "profile_fingerprint": "c" * 64,
        }
        connection = FakeConnection(rows=[
            {"chat_id": chat_id, "capability": "structured_read", "policy_revision": 1, "state": "ready", "binding": binding, "current": True},
            {"state": "active"}, {"grant_mode": "automatic", "current_revision": 1, "grant_id": None},
            durable, {"current_revision": 3}, {"current_revision": 3}, {"active_count": 1},
        ])
        with self.assertRaises(MetadataStoreError) as caught:
            MetadataStore(lambda: connection).authorize_and_create_operation(
                str(uuid.uuid4()), expected_policy_revision=1,
            )
        self.assertEqual(caught.exception.code, "agent_concurrency_exhausted")
        statements = [sql for sql, _ in connection.cursor_value.executions]
        lock_index = next(index for index, sql in enumerate(statements) if "metadata_agent_settings" in sql and "FOR UPDATE" in sql)
        count_index = next(index for index, sql in enumerate(statements) if "active_count" in sql)
        self.assertLess(lock_index, count_index)
        self.assertFalse(any("INSERT INTO metadata_operations" in sql for sql in statements))

    def test_result_release_is_only_pre_dispatch_and_uncertain_scrubs_payload(self):
        delivery_id = uuid.uuid4()
        result_ref_id = uuid.uuid4()
        token = "reservation"
        token_hash = __import__("hashlib").sha256(token.encode()).hexdigest()
        release_connection = FakeConnection(rows=[{
            "result_ref_id": result_ref_id, "state": "reserved", "reservation_token_hash": token_hash,
        }, {"application_id": "schemii"}])
        released = MetadataStore(lambda: release_connection).release_result(str(delivery_id), token)
        self.assertEqual(released["state"], "released")
        self.assertTrue(any("state = %s" in sql for sql, _ in release_connection.cursor_value.executions))

        uncertain_connection = FakeConnection(rows=[{
            "result_ref_id": result_ref_id, "state": "delivering", "reservation_token_hash": token_hash,
        }, {"application_id": "schemii"}])
        uncertain = MetadataStore(lambda: uncertain_connection).mark_result_uncertain(str(delivery_id), token)
        self.assertEqual(uncertain["state"], "uncertain")
        sql = "\n".join(item[0] for item in uncertain_connection.cursor_value.executions)
        self.assertIn("payload = '{}'::jsonb", sql)

    def test_structured_errors_roll_back_transactions(self):
        connection = FakeConnection(rows=[None])
        with self.assertRaises(MetadataStoreError) as caught:
            MetadataStore(lambda: connection).claim_operation(str(uuid.uuid4()), "worker")
        self.assertEqual(caught.exception.code, "operation_not_found")
        self.assertEqual(connection.rollbacks, 1)
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
