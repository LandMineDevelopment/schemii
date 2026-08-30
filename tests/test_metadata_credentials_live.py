import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.metadata.migrator import packaged_migrations


@unittest.skipUnless(os.environ.get("SCHEMII_RUN_DOCKER_INTEGRATION") == "1", "Docker credential integration is opt-in")
class MetadataCredentialIntegrationTests(unittest.TestCase):
    def run_command(self, command, env, check=True, input_text=None):
        result = subprocess.run(
            command, cwd=ROOT, env=env, capture_output=True, text=True,
            timeout=300, input=input_text,
        )
        if check and result.returncode:
            self.fail(f"{command!r} failed ({result.returncode})\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        return result

    @staticmethod
    def cleanup_graph_ids(application):
        application_number = 1 if application == "schemii" else 2
        names = (
            "chat", "target", "policy", "capability", "grant", "proposal", "operation",
            "approval", "attempt", "outcome", "result", "delivery", "agent_policy",
        )
        return {
            name: f"10000000-0000-0000-{application_number:04d}-{index:012d}"
            for index, name in enumerate(names, start=1)
        }

    def owner_query(self, psql, env, sql):
        prefix = psql[:-1] if psql[-1] == "--command" else psql
        return self.run_command(
            prefix + ["--tuples-only", "--no-align", "--command", f"SET ROLE schemii_metadata_owner;\n{sql}"], env,
        ).stdout.strip()

    def owner_input(self, psql, env, sql):
        command = psql[:-1] if psql[-1] == "--command" else psql
        return self.run_command(
            command + ["--set", "ON_ERROR_STOP=1"], env,
            input_text=f"SET ROLE schemii_metadata_owner;\n{sql}",
        )

    def seed_cleanup_graphs(self, psql, env):
        statements = ["BEGIN;"]
        for application in ("schemii", "schemer"):
            ids = self.cleanup_graph_ids(application)
            resource_kind = "schema" if application == "schemii" else "dashboard"
            statements.append(f"""
INSERT INTO metadata_agent_settings (application_id, agent_id, current_revision)
VALUES ('{application}', 'cleanup-regression', 1);
INSERT INTO metadata_agent_policy_revisions (
  agent_policy_revision_id, application_id, agent_id, revision, schema_version, policy, policy_digest
) VALUES (
  '{ids['agent_policy']}', '{application}', 'cleanup-regression', 1, 1, '{{}}'::jsonb, repeat('a', 64)
);
INSERT INTO metadata_agent_policy_capabilities (
  agent_policy_revision_id, capability, configured_mode, effective_mode, safety_floor
) VALUES ('{ids['agent_policy']}', 'structured_read', 'automatic', 'automatic', 'every_action');
INSERT INTO metadata_agent_policy_bounds (agent_policy_revision_id, rows_disclosed)
VALUES ('{ids['agent_policy']}', 100);
INSERT INTO metadata_chats (
  chat_id, application_id, resource_kind, resource_id, external_session_id, state,
  created_at, updated_at, deleted_at, display_title, conversation_title
) VALUES (
  '{ids['chat']}', '{application}', '{resource_kind}', 'cleanup-regression',
  'cleanup-regression', 'deleted', clock_timestamp() - interval '9 days',
  clock_timestamp() - interval '8 days', clock_timestamp() - interval '8 days',
  'Cleanup regression', 'Cleanup regression'
);
INSERT INTO metadata_targets (
  target_id, chat_id, profile_id, database_name, namespace_name,
  profile_fingerprint, connected_target_fingerprint
) VALUES (
  '{ids['target']}', '{ids['chat']}', 'cleanup-profile', 'cleanup_db', 'public',
  repeat('b', 64), repeat('c', 64)
);
INSERT INTO metadata_policy_versions (
  policy_version_id, chat_id, revision, policy, agent_policy_revision_id, agent_policy_schema_version
) VALUES ('{ids['policy']}', '{ids['chat']}', 1, '{{}}'::jsonb, '{ids['agent_policy']}', 1);
INSERT INTO metadata_capabilities (capability_id, policy_version_id, capability, grant_mode)
VALUES ('{ids['capability']}', '{ids['policy']}', 'structured_read', 'automatic');
INSERT INTO metadata_grants (grant_id, chat_id, capability, policy_revision, state)
VALUES ('{ids['grant']}', '{ids['chat']}', 'structured_read', 1, 'consumed');
INSERT INTO metadata_proposals (
  proposal_id, chat_id, capability, policy_revision, binding, action, state, created_at, expires_at
) VALUES (
  '{ids['proposal']}', '{ids['chat']}', 'structured_read', 1, '{{}}'::jsonb,
  '{{"type":"cleanup_regression"}}'::jsonb, 'authorized',
  clock_timestamp() - interval '9 days', clock_timestamp() + interval '1 day'
);
INSERT INTO metadata_operations (
  operation_id, proposal_id, chat_id, capability, state, created_at, updated_at
) VALUES (
  '{ids['operation']}', '{ids['proposal']}', '{ids['chat']}', 'structured_read', 'succeeded',
  clock_timestamp() - interval '9 days', clock_timestamp() - interval '9 days'
);
INSERT INTO metadata_operation_approvals (approval_id, operation_id, policy_revision, decision)
VALUES ('{ids['approval']}', '{ids['operation']}', 1, 'automatic');
INSERT INTO metadata_operation_attempts (
  attempt_id, operation_id, worker_id, claim_token_hash, state, claimed_at,
  heartbeat_at, finished_at, lease_expires_at
) VALUES (
  '{ids['attempt']}', '{ids['operation']}', 'cleanup-worker', repeat('d', 64), 'succeeded',
  clock_timestamp() - interval '9 days', clock_timestamp() - interval '9 days',
  clock_timestamp() - interval '9 days' + interval '2 minutes',
  clock_timestamp() - interval '9 days' + interval '1 minute'
);
INSERT INTO metadata_operation_outcomes (outcome_id, operation_id, state, result)
VALUES (
  '{ids['outcome']}', '{ids['operation']}', 'succeeded',
  '{{"receipt":"retained-until-chat-cleanup"}}'::jsonb
);
INSERT INTO metadata_ai_operation_usage (operation_id, bound_name, used, evidence)
VALUES ('{ids['operation']}', 'rowsDisclosed', 3, '[{{"used":3}}]'::jsonb);
INSERT INTO metadata_query_result_references (
  result_ref_id, chat_id, binding, state, created_at, expires_at
) VALUES (
  '{ids['result']}', '{ids['chat']}',
  '{{"operationId":"{ids['operation']}"}}'::jsonb, 'consumed',
  clock_timestamp() - interval '9 days', clock_timestamp() + interval '1 day'
);
INSERT INTO metadata_query_result_payloads (result_ref_id, payload, byte_count)
VALUES ('{ids['result']}', '{{"rows":[]}}'::jsonb, 11);
INSERT INTO metadata_query_result_deliveries (
  delivery_id, result_ref_id, reservation_token_hash, state, reserved_at,
  dispatch_started_at, finished_at
) VALUES (
  '{ids['delivery']}', '{ids['result']}', repeat('e', 64), 'consumed',
  clock_timestamp() - interval '9 days', clock_timestamp() - interval '9 days',
  clock_timestamp() - interval '9 days'
);
INSERT INTO metadata_authority_transitions (
  application_id, aggregate_kind, aggregate_id, from_state, to_state, reason
) VALUES
  ('{application}', 'chat', '{ids['chat']}', 'deleting', 'deleted', 'cleanup_regression'),
  ('{application}', 'proposal', '{ids['proposal']}', 'ready', 'authorized', 'cleanup_regression'),
  ('{application}', 'operation', '{ids['operation']}', 'running', 'succeeded', 'cleanup_regression'),
  ('{application}', 'result', '{ids['result']}', 'delivering', 'consumed', 'cleanup_regression');
""")
        statements.append("COMMIT;")
        statements.append("SELECT current_user, session_user, count(*) FROM metadata_chats GROUP BY current_user, session_user;")
        seeded = self.owner_input(psql, env, "\n".join(statements))
        self.assertIn("schemii_metadata_owner", seeded.stdout, seeded.stderr)

    def assert_cleanup_graph_state(self, psql, env, *, owned_count, retained_count):
        ids = [self.cleanup_graph_ids(application) for application in ("schemii", "schemer")]
        values = {name: ",".join(f"'{item[name]}'" for item in ids) for name in ids[0]}
        owned = self.owner_query(psql, env, f"""
SELECT concat_ws(',',
  (SELECT count(*) FROM metadata_chats WHERE chat_id IN ({values['chat']})),
  (SELECT count(*) FROM metadata_targets WHERE target_id IN ({values['target']})),
  (SELECT count(*) FROM metadata_policy_versions WHERE policy_version_id IN ({values['policy']})),
  (SELECT count(*) FROM metadata_capabilities WHERE capability_id IN ({values['capability']})),
  (SELECT count(*) FROM metadata_grants WHERE grant_id IN ({values['grant']})),
  (SELECT count(*) FROM metadata_proposals WHERE proposal_id IN ({values['proposal']})),
  (SELECT count(*) FROM metadata_operations WHERE operation_id IN ({values['operation']})),
  (SELECT count(*) FROM metadata_operation_approvals WHERE approval_id IN ({values['approval']})),
  (SELECT count(*) FROM metadata_operation_attempts WHERE attempt_id IN ({values['attempt']})),
  (SELECT count(*) FROM metadata_operation_outcomes WHERE outcome_id IN ({values['outcome']})),
  (SELECT count(*) FROM metadata_ai_operation_usage WHERE operation_id IN ({values['operation']})),
  (SELECT count(*) FROM metadata_query_result_references WHERE result_ref_id IN ({values['result']})),
  (SELECT count(*) FROM metadata_query_result_payloads WHERE result_ref_id IN ({values['result']})),
  (SELECT count(*) FROM metadata_query_result_deliveries WHERE delivery_id IN ({values['delivery']}))
);
""")
        self.assertEqual(
            owned, ",".join([str(owned_count)] * 14),
            self.owner_query(psql, env, """
SELECT current_user || ':' || session_user || ':' || count(*)
FROM metadata_chats WHERE external_session_id = 'cleanup-regression'
GROUP BY current_user, session_user;
"""),
        )
        retained = self.owner_query(psql, env, f"""
SELECT concat_ws(',',
  (SELECT count(*) FROM metadata_agent_settings WHERE agent_id = 'cleanup-regression'),
  (SELECT count(*) FROM metadata_agent_policy_revisions WHERE agent_policy_revision_id IN ({values['agent_policy']})),
  (SELECT count(*) FROM metadata_agent_policy_capabilities WHERE agent_policy_revision_id IN ({values['agent_policy']})),
  (SELECT count(*) FROM metadata_agent_policy_bounds WHERE agent_policy_revision_id IN ({values['agent_policy']})),
  (SELECT count(*) FROM metadata_authority_transitions WHERE aggregate_id IN (
    {values['chat']}, {values['proposal']}, {values['operation']}, {values['result']}
  ))
);
""")
        self.assertEqual(retained, f"{retained_count},{retained_count},{retained_count},{retained_count},{retained_count * 4}")

    def run_cleanup_maintenance(self, compose, env, application):
        own_ids = self.cleanup_graph_ids(application)
        other_application = "schemer" if application == "schemii" else "schemii"
        other_ids = self.cleanup_graph_ids(other_application)
        script = f"""
import json
import time
from schemii.ai_operation_maintenance import AiOperationMaintenance, AiOperationMaintenanceConfig
from schemii.metadata import MetadataConfig, MetadataConnectionFactory, MetadataStore

config = MetadataConfig.from_runtime_env('{application}')
store = MetadataStore(
    MetadataConnectionFactory(config), max_json_bytes=config.max_json_bytes,
    expected_application=config.expected_application, expected_role=config.expected_role,
    expected_owner=config.expected_owner, expected_admin_owner=config.expected_admin_owner,
)
maintenance = AiOperationMaintenance(store, AiOperationMaintenanceConfig(
    interval_seconds=60, heartbeat_seconds=1, lease_seconds=3,
    cleanup_retention_seconds=604800,
))
maintenance.start()
deadline = time.monotonic() + 10
while maintenance.health()['lastSuccessAt'] is None and time.monotonic() < deadline:
    time.sleep(0.05)
health = maintenance.health()
own_audit = store.list_transitions('chat', '{own_ids['chat']}')
foreign_audit = store.list_transitions('chat', '{other_ids['chat']}')
metadata = store.health()
maintenance.close()
if health['status'] != 'available' or health['lastCounts'].get('chats') != 1:
    raise RuntimeError(health)
if len(own_audit) != 1 or foreign_audit:
    raise RuntimeError({{'ownAudit': own_audit, 'foreignAudit': foreign_audit}})
print(json.dumps({{'maintenance': health, 'metadata': metadata}}, sort_keys=True))
"""
        run_options = []
        if application == "schemer":
            credential_file = Path(env["SCHEMII_CREDENTIAL_DIR"]) / "metadata_schemer_password"
            run_options = [
                "--env", "SCHEMII_METADATA_DSN=host=metadata-postgres port=5432 dbname=schemii_metadata user=schemii_metadata_schemer",
                "--env", "SCHEMII_METADATA_PASSWORD_FILE=/run/secrets/metadata_schemer_password",
                "--volume", f"{credential_file}:/run/secrets/metadata_schemer_password:ro",
            ]
        result = self.run_command(
            compose + ["run", "--rm", "--no-deps", *run_options, "schemii", "python", "-c", script], env,
        )
        payload = json.loads(result.stdout.strip())
        self.assertEqual(payload["maintenance"]["status"], "available")
        self.assertEqual(payload["metadata"]["application"], application)
        self.assertEqual(payload["metadata"]["version"], 13)

    def run_cleanup_regression(self, compose, psql, env):
        self.seed_cleanup_graphs(psql, env)
        self.assert_cleanup_graph_state(psql, env, owned_count=2, retained_count=2)
        self.run_cleanup_maintenance(compose, env, "schemii")
        self.run_cleanup_maintenance(compose, env, "schemer")
        self.assert_cleanup_graph_state(psql, env, owned_count=0, retained_count=2)
        action = self.owner_query(psql, env, """
SELECT constraint_record.confdeltype
FROM pg_catalog.pg_constraint AS constraint_record
WHERE constraint_record.conname = 'metadata_ai_operation_usage_operation_id_fkey';
""")
        self.assertEqual(action, "c")

    def assert_v12_cleanup_fails_atomically(self, compose, psql, env):
        self.seed_cleanup_graphs(psql, env)
        script = """
from datetime import datetime, timedelta, timezone
from schemii.metadata import MetadataConfig, MetadataConnectionFactory, MetadataStore, MetadataStoreError

config = MetadataConfig.from_runtime_env('schemii')
store = MetadataStore(MetadataConnectionFactory(config), max_json_bytes=config.max_json_bytes)
try:
    store.cleanup(before=datetime.now(timezone.utc) - timedelta(days=7), limit=100)
except MetadataStoreError as error:
    if error.code != 'metadata_store_failed':
        raise
else:
    raise RuntimeError('v12 cleanup unexpectedly succeeded')
"""
        self.run_command(
            compose + ["run", "--rm", "--no-deps", "schemii", "python", "-c", script], env,
        )
        self.assert_cleanup_graph_state(psql, env, owned_count=2, retained_count=2)

    def test_fresh_metadata_cleanup_catalog_privileges_and_live_rotate_restore(self):
        if shutil.which("docker") is None:
            self.skipTest("Docker is unavailable")
        project = f"schemii-credential-test-{secrets.token_hex(4)}"
        with tempfile.TemporaryDirectory() as test_directory:
            test_path = Path(test_directory)
            test_path.chmod(0o700)
            credential_path = test_path / "credentials"
            credential_path.mkdir(mode=0o700)
            credential_dir = str(credential_path)
            backup_path = test_path / "backup"
            backup_path.mkdir(mode=0o700)
            backup_dir = str(backup_path)
            (credential_path / "instance").write_text(f"{project}\n", encoding="utf-8")
            for name in (
                "metadata_bootstrap_password", "metadata_migration_password",
                "metadata_schemii_password", "metadata_schemer_password", "opencode_password",
            ):
                path = credential_path / name
                path.write_text(f"{secrets.token_hex(32)}\n", encoding="utf-8")
                path.chmod(0o600)
            env = {
                **os.environ,
                "SCHEMII_CREDENTIAL_DIR": credential_dir,
                "SCHEMII_INSTANCE": project,
                "SCHEMII_NO_OPEN": "1",
            }
            compose = ["docker", "compose", "--project-name", project, "-f", "compose.yaml"]
            recovery_compose = compose + ["-f", "compose.recovery.yaml"]
            try:
                self.run_command(compose + ["up", "--no-build", "-d", "--wait", "metadata-postgres"], env)
                container = self.run_command(compose + ["ps", "-q", "metadata-postgres"], env).stdout.strip()
                psql = [
                    "docker", "exec", "-i", "-u", "postgres", "-e",
                    "PGPASSFILE=/tmp/schemii-metadata-secrets/metadata_migration_password.pgpass",
                    container, "psql", "--quiet", "--tuples-only", "--no-align",
                    "--host", "127.0.0.1", "--username", "schemii_metadata_migration",
                    "--dbname", "schemii_metadata", "--command",
                ]
                self.run_command(compose + ["run", "--rm", "metadata-migrate"], env)
                self.run_command(recovery_compose + ["run", "--rm", "--no-deps", "metadata-recovery", "verify-metadata"], env)
                health_script = (
                    "import json; "
                    "from schemii.metadata import MetadataConfig, MetadataConnectionFactory, MetadataStore; "
                    "config = MetadataConfig.from_runtime_env('schemii'); "
                    "store = MetadataStore(MetadataConnectionFactory(config), "
                    "max_json_bytes=config.max_json_bytes, expected_application=config.expected_application, "
                    "expected_role=config.expected_role, expected_owner=config.expected_owner, "
                    "expected_admin_owner=config.expected_admin_owner); "
                    "print(json.dumps(store.health(), sort_keys=True))"
                )
                health = self.run_command(
                    compose + ["run", "--rm", "--no-deps", "schemii", "python", "-c", health_script], env,
                )
                self.assertEqual(
                    health.stdout.strip(),
                    '{"application": "schemii", "expectedVersion": 13, "ok": true, "role": "schemii_metadata_schemii", "version": 13}',
                )
                self.run_cleanup_regression(compose, psql, env)
                self.run_command(recovery_compose + ["run", "--rm", "--no-deps", "metadata-recovery", "verify-metadata"], env)
                catalog_sql = r"""
SELECT rolname || ':' || rolcreaterole || ':' || rolcanlogin
FROM pg_roles WHERE rolname IN ('schemii_metadata_bootstrap', 'schemii_metadata_migration') ORDER BY rolname;
SELECT count(*) FROM pg_auth_members m JOIN pg_roles member ON member.oid=m.member
WHERE member.rolname='schemii_metadata_migration' AND m.admin_option;
SELECT p.prosecdef || ':' || r.rolname || ':' || p.proconfig[1]
FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_roles r ON r.oid=p.proowner
WHERE n.nspname='schemii_admin' AND p.proname='rotate_metadata_passwords';
SELECT EXISTS (SELECT 1 FROM aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) acl
               WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE') || ':' ||
       has_function_privilege('schemii_metadata_migration', p.oid, 'EXECUTE')
FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
WHERE n.nspname='schemii_admin' AND p.proname='rotate_metadata_passwords';
SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind IN ('r','p','S') AND c.relname LIKE 'metadata\_%' ESCAPE '\'
  AND pg_get_userbyid(c.relowner) <> 'schemii_metadata_owner';
SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
WHERE n.nspname='public' AND p.proname LIKE 'metadata\_%' ESCAPE '\'
  AND pg_get_userbyid(p.proowner) <> 'schemii_metadata_owner';
SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind IN ('r','p') AND c.relname LIKE 'metadata\_%' ESCAPE '\'
  AND c.relname <> 'metadata_schema_migrations' AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity);
SELECT rolname || ':' || rolcanlogin || ':' || rolinherit || ':' || rolsuper || ':' ||
       rolcreaterole || ':' || rolcreatedb || ':' || rolbypassrls
FROM pg_roles WHERE rolname IN (
  'schemii_metadata_owner', 'schemii_metadata_migration',
  'schemii_metadata_schemii', 'schemii_metadata_schemer'
) ORDER BY rolname;
"""
                catalog = self.run_command(psql + [catalog_sql], env).stdout.splitlines()
                self.assertEqual(catalog, [
                    "schemii_metadata_bootstrap:true:false",
                    "schemii_metadata_migration:false:true",
                    "0",
                    "true:schemii_metadata_bootstrap:search_path=pg_catalog",
                    "false:true",
                    "0",
                    "0",
                    "0",
                    "schemii_metadata_migration:true:true:false:false:false:false",
                    "schemii_metadata_owner:false:true:false:false:false:false",
                    "schemii_metadata_schemer:true:true:false:false:false:false",
                    "schemii_metadata_schemii:true:true:false:false:false:false",
                ])
                self.run_command(psql + ["GRANT SELECT ON metadata_applications TO schemii_metadata_migration"], env)
                invalid_acl = self.run_command(
                    recovery_compose + ["run", "--rm", "--no-deps", "metadata-recovery", "verify-metadata"],
                    env, check=False,
                )
                self.assertNotEqual(invalid_acl.returncode, 0)
                self.run_command(psql + ["REVOKE SELECT ON metadata_applications FROM schemii_metadata_migration"], env)
                self.run_command(psql + ["GRANT SELECT ON metadata_applications TO schemii_metadata_schemii WITH GRANT OPTION"], env)
                invalid_grant_option = self.run_command(
                    recovery_compose + ["run", "--rm", "--no-deps", "metadata-recovery", "verify-metadata"],
                    env, check=False,
                )
                self.assertNotEqual(invalid_grant_option.returncode, 0)
                self.run_command(psql + ["REVOKE GRANT OPTION FOR SELECT ON metadata_applications FROM schemii_metadata_schemii"], env)
                self.run_command(psql + [
                    "ALTER DEFAULT PRIVILEGES FOR ROLE schemii_metadata_owner IN SCHEMA public "
                    "GRANT SELECT ON TABLES TO schemii_metadata_migration"
                ], env)
                invalid_default_acl = self.run_command(
                    recovery_compose + ["run", "--rm", "--no-deps", "metadata-recovery", "verify-metadata"],
                    env, check=False,
                )
                self.assertNotEqual(invalid_default_acl.returncode, 0)
                self.run_command(psql + [
                    "ALTER DEFAULT PRIVILEGES FOR ROLE schemii_metadata_owner IN SCHEMA public "
                    "REVOKE SELECT ON TABLES FROM schemii_metadata_migration"
                ], env)
                self.run_command(psql + [
                    "ALTER DEFAULT PRIVILEGES FOR ROLE schemii_metadata_owner IN SCHEMA public "
                    "GRANT SELECT ON TABLES TO schemii_metadata_schemii WITH GRANT OPTION"
                ], env)
                invalid_default_grant_option = self.run_command(
                    recovery_compose + ["run", "--rm", "--no-deps", "metadata-recovery", "verify-metadata"],
                    env, check=False,
                )
                self.assertNotEqual(invalid_default_grant_option.returncode, 0)
                self.run_command(psql + [
                    "ALTER DEFAULT PRIVILEGES FOR ROLE schemii_metadata_owner IN SCHEMA public "
                    "REVOKE GRANT OPTION FOR SELECT ON TABLES FROM schemii_metadata_schemii"
                ], env)
                self.run_command(psql + [
                    "ALTER DEFAULT PRIVILEGES FOR ROLE schemii_metadata_owner IN SCHEMA schemii_admin "
                    "GRANT USAGE ON TYPES TO schemii_metadata_schemii"
                ], env)
                invalid_default_scope = self.run_command(
                    recovery_compose + ["run", "--rm", "--no-deps", "metadata-recovery", "verify-metadata"],
                    env, check=False,
                )
                self.assertNotEqual(invalid_default_scope.returncode, 0)
                self.run_command(psql + [
                    "ALTER DEFAULT PRIVILEGES FOR ROLE schemii_metadata_owner IN SCHEMA schemii_admin "
                    "REVOKE USAGE ON TYPES FROM schemii_metadata_schemii"
                ], env)
                self.run_command(psql + ["ALTER TABLE metadata_applications NO FORCE ROW LEVEL SECURITY"], env)
                invalid_rls = self.run_command(
                    recovery_compose + ["run", "--rm", "--no-deps", "metadata-recovery", "verify-metadata"],
                    env, check=False,
                )
                self.assertNotEqual(invalid_rls.returncode, 0)
                self.run_command(psql + ["ALTER TABLE metadata_applications FORCE ROW LEVEL SECURITY"], env)
                self.run_command(psql + ["ALTER TABLE metadata_schema_migrations ENABLE ROW LEVEL SECURITY"], env)
                invalid_migration_rls = self.run_command(
                    recovery_compose + ["run", "--rm", "--no-deps", "metadata-recovery", "verify-metadata"],
                    env, check=False,
                )
                self.assertNotEqual(invalid_migration_rls.returncode, 0)
                self.run_command(psql + ["ALTER TABLE metadata_schema_migrations DISABLE ROW LEVEL SECURITY"], env)
                self.run_command(recovery_compose + ["run", "--rm", "--no-deps", "metadata-recovery", "verify-metadata"], env)
                for application in ("schemii", "schemer"):
                    runtime_psql = [
                        "docker", "exec", "-u", "postgres", "-e",
                        f"PGPASSFILE=/tmp/schemii-metadata-secrets/metadata_{application}_password.pgpass",
                        container, "psql", "--quiet", "--tuples-only", "--no-align",
                        "--host", "127.0.0.1", "--username", f"schemii_metadata_{application}",
                        "--dbname", "schemii_metadata", "--command",
                    ]
                    visible = self.run_command(
                        runtime_psql + ["SELECT string_agg(application_id, ',' ORDER BY application_id) FROM metadata_applications"],
                        env,
                    ).stdout.strip()
                    self.assertEqual(visible, application)
                cross_application_insert = self.run_command(
                    runtime_psql + ["INSERT INTO metadata_applications (application_id) VALUES ('schemii-cross-application')"],
                    env, check=False,
                )
                self.assertNotEqual(cross_application_insert.returncode, 0)
                rejected = self.run_command(
                    psql + ["SELECT schemii_admin.rotate_metadata_passwords('short','short','short')"],
                    env, check=False,
                )
                self.assertNotEqual(rejected.returncode, 0)
                rejected_characters = self.run_command(
                    psql + ["SELECT schemii_admin.rotate_metadata_passwords('invalid-password!','invalid-password!','invalid-password!')"],
                    env, check=False,
                )
                self.assertNotEqual(rejected_characters.returncode, 0)

                migration_file = credential_path / "metadata_migration_password"
                bootstrap_file = credential_path / "metadata_bootstrap_password"
                original_digest = hashlib.sha256(migration_file.read_bytes()).digest()
                bootstrap_digest = hashlib.sha256(bootstrap_file.read_bytes()).digest()
                self.run_command(["bash", "./start.sh", "credentials-backup", backup_dir], env)
                self.assertFalse(Path(f"{credential_dir}.lock").exists())

                wrapper_dir = Path(backup_dir) / "bin"
                wrapper_dir.mkdir()
                docker_wrapper = wrapper_dir / "docker"
                docker_wrapper.write_text(
                    "#!/bin/sh\n"
                    "if [ \"${1:-}\" = restart ] && [ ! -e \"$SCHEMII_TEST_RESTART_FAILED\" ]; then\n"
                    "  : > \"$SCHEMII_TEST_RESTART_FAILED\"\n"
                    "  exit 70\n"
                    "fi\n"
                    "exec \"$SCHEMII_TEST_REAL_DOCKER\" \"$@\"\n",
                    encoding="utf-8",
                )
                docker_wrapper.chmod(0o700)
                failure_env = {
                    **env,
                    "PATH": f"{wrapper_dir}{os.pathsep}{env['PATH']}",
                    "SCHEMII_TEST_REAL_DOCKER": shutil.which("docker"),
                    "SCHEMII_TEST_RESTART_FAILED": str(Path(backup_dir) / "restart-failed"),
                }
                failed_rotation = self.run_command(["bash", "./start.sh", "credentials-rotate"], failure_env, check=False)
                self.assertNotEqual(failed_rotation.returncode, 0, failed_rotation.stdout + failed_rotation.stderr)
                self.assertEqual(hashlib.sha256(migration_file.read_bytes()).digest(), original_digest)
                self.assertFalse((credential_path / ".credential-transaction").exists())
                self.assertEqual(self.run_command(psql + ["SELECT current_user"], env).stdout.strip(), "schemii_metadata_migration")

                self.run_command(["bash", "./start.sh", "credentials-rotate"], env)
                self.assertFalse(Path(f"{credential_dir}.lock").exists())
                self.assertNotEqual(hashlib.sha256(migration_file.read_bytes()).digest(), original_digest)
                self.assertEqual(hashlib.sha256(bootstrap_file.read_bytes()).digest(), bootstrap_digest)
                self.assertEqual(
                    self.run_command(psql + ["SELECT current_user"], env).stdout.strip(),
                    "schemii_metadata_migration",
                )
                backup_marker = Path(backup_dir) / project / "instance"
                backup_marker.write_text(f"{project}-other\n", encoding="utf-8")
                rejected_restore = self.run_command(["bash", "./start.sh", "credentials-restore", backup_dir], env, check=False)
                self.assertNotEqual(rejected_restore.returncode, 0)
                self.assertNotEqual(hashlib.sha256(migration_file.read_bytes()).digest(), original_digest)
                backup_marker.write_text(f"{project}\n", encoding="utf-8")
                self.run_command(["bash", "./start.sh", "credentials-restore", backup_dir], env)
                self.assertFalse(Path(f"{credential_dir}.lock").exists())
                self.assertEqual(hashlib.sha256(migration_file.read_bytes()).digest(), original_digest)
                authenticated = self.run_command(psql + ["SELECT current_user"], env).stdout.strip()
                self.assertEqual(authenticated, "schemii_metadata_migration")
            finally:
                self.run_command(recovery_compose + ["down", "--volumes", "--remove-orphans"], env, check=False)

    def test_v12_metadata_upgrades_in_place_with_atomic_chat_cleanup(self):
        if shutil.which("docker") is None:
            self.skipTest("Docker is unavailable")
        project = f"schemii-metadata-upgrade-{secrets.token_hex(4)}"
        with tempfile.TemporaryDirectory() as credential_dir, tempfile.TemporaryDirectory() as backup_dir:
            credential_path = Path(credential_dir)
            credential_path.chmod(0o700)
            for name in (
                "metadata_bootstrap_password", "metadata_migration_password",
                "metadata_schemii_password", "metadata_schemer_password", "opencode_password",
            ):
                path = credential_path / name
                path.write_text(f"{secrets.token_hex(32)}\n", encoding="utf-8")
                path.chmod(0o600)
            env = {**os.environ, "SCHEMII_CREDENTIAL_DIR": credential_dir, "SCHEMII_INSTANCE": project}
            compose = ["docker", "compose", "--project-name", project, "-f", "compose.yaml"]
            recovery_compose = compose + ["-f", "compose.recovery.yaml"]
            try:
                self.run_command(compose + ["up", "--no-build", "-d", "--wait", "metadata-postgres"], env)
                container = self.run_command(compose + ["ps", "-q", "metadata-postgres"], env).stdout.strip()
                psql = [
                    "docker", "exec", "-i", "-u", "postgres", "-e",
                    "PGPASSFILE=/tmp/schemii-metadata-secrets/metadata_migration_password.pgpass",
                    "-e", "PGOPTIONS=-c role=schemii_metadata_owner",
                    container, "psql", "--quiet", "--set", "ON_ERROR_STOP=1",
                    "--host", "127.0.0.1", "--username", "schemii_metadata_migration",
                    "--dbname", "schemii_metadata",
                ]
                self.run_command(psql, env, input_text="""
CREATE TABLE metadata_schema_migrations (
  version integer PRIMARY KEY CHECK (version > 0),
  name text NOT NULL UNIQUE,
  checksum char(64) NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
  applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
""")
                migrations = packaged_migrations()
                self.assertEqual([item.version for item in migrations], list(range(1, 14)))
                for migration in migrations[:10]:
                    self.run_command(psql, env, input_text=migration.sql)
                    self.run_command(
                        psql, env,
                        input_text=(
                            "INSERT INTO metadata_schema_migrations (version, name, checksum) "
                            f"VALUES ({migration.version}, '{migration.name}', '{migration.checksum}');\n"
                        ),
                    )
                self.assertEqual(
                    self.run_command(psql + ["--tuples-only", "--no-align", "--command", "SELECT max(version) FROM metadata_schema_migrations"], env).stdout.strip(),
                    "10",
                )
                self.run_command(psql, env, input_text="""
ALTER DEFAULT PRIVILEGES FOR ROLE schemii_metadata_owner
  GRANT EXECUTE ON FUNCTIONS TO PUBLIC;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO PUBLIC;
REVOKE ALL ON FUNCTION metadata_current_application() FROM PUBLIC;
INSERT INTO metadata_chats (
  chat_id, application_id, resource_kind, resource_id, external_session_id, display_title, conversation_title
) VALUES
  ('00000000-0000-0000-0000-000000000010', 'schemii', 'schema', 'manual-schemii', 'manual-schemii', 'Manual Schemii', 'Preserved Schemii chat'),
  ('00000000-0000-0000-0000-000000000011', 'schemer', 'dashboard', 'manual-schemer', 'manual-schemer', 'Manual Schemer', 'Preserved Schemer chat');
INSERT INTO metadata_console_settings (
  application_id, revision, write_intent, default_mode, statement_limit, row_page_size
) VALUES
  ('schemii', 7, 'enabled', 'managed', 9, 73),
  ('schemer', 4, 'disabled', 'managed_read', 6, 41);
""")

                self.run_command(recovery_compose + ["run", "--rm", "--no-deps", "metadata-recovery", "prepare"], env)

                def assert_backup_rejected(expected_error):
                    rejected = self.run_command(
                        recovery_compose + ["run", "--rm", "--no-deps", "metadata-recovery", "backup"],
                        env, check=False,
                    )
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertIn(expected_error, rejected.stderr)

                self.run_command(psql, env, input_text="""
ALTER DEFAULT PRIVILEGES FOR ROLE schemii_metadata_owner
  REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
""")
                assert_backup_rejected("default_records=")
                self.run_command(psql, env, input_text="""
ALTER DEFAULT PRIVILEGES FOR ROLE schemii_metadata_owner
  GRANT EXECUTE ON FUNCTIONS TO PUBLIC;
""")

                self.run_command(psql, env, input_text="""
REVOKE EXECUTE ON FUNCTION metadata_agent_policy_immutable() FROM PUBLIC;
""")
                assert_backup_rejected("function_acl=")
                self.run_command(psql, env, input_text="""
GRANT EXECUTE ON FUNCTION metadata_agent_policy_immutable() TO PUBLIC;
""")

                self.run_command(psql, env, input_text="ALTER TABLE metadata_applications DISABLE ROW LEVEL SECURITY;")
                assert_backup_rejected("row_security=")
                self.run_command(psql, env, input_text="ALTER TABLE metadata_applications ENABLE ROW LEVEL SECURITY;")

                self.run_command(
                    psql, env,
                    input_text=(
                        "ALTER POLICY metadata_chats_isolation ON metadata_chats "
                        "USING (true) WITH CHECK (true);"
                    ),
                )
                assert_backup_rejected("policies=")
                self.run_command(psql, env, input_text="""
ALTER POLICY metadata_chats_isolation ON metadata_chats
  USING (application_id = metadata_current_application())
  WITH CHECK (application_id = metadata_current_application());
""")

                self.run_command(
                    psql, env,
                    input_text="SET ROLE NONE; ALTER TABLE metadata_applications OWNER TO schemii_metadata_migration;",
                )
                assert_backup_rejected("owners=")
                self.run_command(
                    psql, env,
                    input_text="SET ROLE NONE; ALTER TABLE metadata_applications OWNER TO schemii_metadata_owner;",
                )

                self.run_command(
                    psql, env,
                    input_text="UPDATE metadata_schema_migrations SET checksum = repeat('0', 64) WHERE version = 10;",
                )
                assert_backup_rejected("migrations=")
                self.run_command(
                    psql, env,
                    input_text=(
                        "UPDATE metadata_schema_migrations SET checksum = "
                        f"'{migrations[9].checksum}' WHERE version = 10;"
                    ),
                )

                self.run_command(psql, env, input_text="DELETE FROM metadata_schema_migrations WHERE version = 9;")
                assert_backup_rejected("migrations=")
                self.run_command(
                    psql, env,
                    input_text=(
                        "INSERT INTO metadata_schema_migrations (version, name, checksum) "
                        f"VALUES (9, '{migrations[8].name}', '{migrations[8].checksum}');"
                    ),
                )

                self.run_command(psql, env, input_text="""
CREATE OR REPLACE FUNCTION metadata_current_application() RETURNS text
LANGUAGE sql STABLE PARALLEL SAFE
AS $$ SELECT 'schemii'::text $$;
""")
                assert_backup_rejected("row-level isolation verification failed for schemer")
                self.run_command(psql, env, input_text="""
CREATE OR REPLACE FUNCTION metadata_current_application() RETURNS text
LANGUAGE sql STABLE PARALLEL SAFE
AS $$
  SELECT CASE session_user
    WHEN 'schemii_metadata_schemii' THEN 'schemii'
    WHEN 'schemii_metadata_schemer' THEN 'schemer'
    ELSE NULL
  END
$$;
""")

                self.run_command(psql, env, input_text=migrations[10].sql)
                self.run_command(
                    psql, env,
                    input_text=(
                        "INSERT INTO metadata_schema_migrations (version, name, checksum) "
                        f"VALUES (11, '{migrations[10].name}', '{migrations[10].checksum}');"
                    ),
                )
                self.run_command(
                    recovery_compose + ["run", "--rm", "--no-deps", "metadata-recovery", "backup"], env,
                )
                self.run_command(psql, env, input_text="DELETE FROM metadata_schema_migrations WHERE version = 11;")

                backup_container = f"{project}-older-metadata-backup"
                self.run_command(
                    recovery_compose + [
                        "run", "--name", backup_container, "--no-deps",
                        "metadata-recovery", "backup",
                    ],
                    env,
                )
                older_backup = Path(backup_dir) / "older"
                older_backup.mkdir()
                self.run_command(["docker", "cp", f"{backup_container}:/transaction/output/.", str(older_backup)], env)
                self.run_command(["docker", "rm", backup_container], env)

                self.run_command(
                    psql, env,
                    input_text=(
                        "INSERT INTO metadata_schema_migrations (version, name, checksum) "
                        f"VALUES (11, '{migrations[10].name}', '{migrations[10].checksum}');\n"
                    ),
                )
                self.run_command(psql, env, input_text=migrations[11].sql)
                self.run_command(
                    psql, env,
                    input_text=(
                        "INSERT INTO metadata_schema_migrations (version, name, checksum) "
                        f"VALUES (12, '{migrations[11].name}', '{migrations[11].checksum}');\n"
                    ),
                )
                self.assertEqual(
                    self.run_command(psql + ["--tuples-only", "--no-align", "--command", "SELECT max(version) FROM metadata_schema_migrations"], env).stdout.strip(),
                    "12",
                )
                self.assert_v12_cleanup_fails_atomically(compose, psql, env)
                self.run_command(compose + ["run", "--rm", "metadata-migrate"], env)
                self.run_cleanup_maintenance(compose, env, "schemii")
                self.run_cleanup_maintenance(compose, env, "schemer")
                self.assert_cleanup_graph_state(psql, env, owned_count=0, retained_count=2)

                self.run_command(psql, env, input_text="""
CREATE TABLE metadata_destination_only_parent (id integer PRIMARY KEY);
CREATE TABLE metadata_destination_only_child (
  id integer PRIMARY KEY,
  parent_id integer NOT NULL,
  CONSTRAINT metadata_destination_only_child_parent_fk
    FOREIGN KEY (parent_id) REFERENCES metadata_destination_only_parent(id)
);
""")
                restore_command = recovery_compose + [
                    "run", "--rm", "--no-deps",
                    "-e", f"SCHEMII_RECOVERY_CONFIRM=RESTORE:{project}",
                    "-v", f"{older_backup}:/backup:ro",
                    "metadata-recovery", "restore",
                ]
                drift_rejected = self.run_command(restore_command, env, check=False)
                self.assertNotEqual(drift_rejected.returncode, 0)
                self.assertIn("relation_inventory=", drift_rejected.stderr)
                drift_preserved = self.run_command(
                    psql + [
                        "--tuples-only", "--no-align", "--command",
                        "SELECT to_regclass('public.metadata_destination_only_parent') IS NOT NULL "
                        "AND to_regclass('public.metadata_destination_only_child') IS NOT NULL "
                        "AND EXISTS (SELECT 1 FROM pg_constraint WHERE conname = "
                        "'metadata_destination_only_child_parent_fk')",
                    ],
                    env,
                )
                self.assertEqual(drift_preserved.stdout.strip(), "t")
                self.run_command(
                    psql, env,
                    input_text="DROP TABLE metadata_destination_only_child, metadata_destination_only_parent;",
                )
                self.run_command(restore_command, env)
                self.run_command(compose + ["run", "--rm", "metadata-migrate"], env)
                self.run_command(recovery_compose + ["run", "--rm", "--no-deps", "metadata-recovery", "verify-metadata"], env)

                history = self.run_command(
                    psql + ["--tuples-only", "--no-align", "--command", "SELECT string_agg(version::text, ',' ORDER BY version) FROM metadata_schema_migrations"],
                    env,
                ).stdout.strip()
                self.assertEqual(history, ",".join(str(value) for value in range(1, 14)))
                preserved = self.run_command(
                    psql + [
                        "--tuples-only", "--no-align", "--command",
                        "SELECT string_agg(application_id || ':' || conversation_title, ',' ORDER BY application_id) "
                        "FROM metadata_chats WHERE external_session_id LIKE 'manual-%'; "
                        "SELECT string_agg(application_id || ':' || revision || ':' || statement_limit || ':' || row_page_size, ',' ORDER BY application_id) "
                        "FROM metadata_console_settings;",
                    ],
                    env,
                ).stdout.splitlines()
                self.assertEqual(preserved, [
                    "schemer:Preserved Schemer chat,schemii:Preserved Schemii chat",
                    "schemer:4:6:41,schemii:7:9:73",
                ])
                self.run_command(recovery_compose + ["run", "--rm", "--no-deps", "metadata-recovery", "commit"], env)
            finally:
                self.run_command(recovery_compose + ["down", "--volumes", "--remove-orphans"], env, check=False)


if __name__ == "__main__":
    unittest.main()
