import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.recovery_verify import verify_backup


RECOVERY = ROOT / "docker" / "recovery.sh"
CREDENTIAL_FILES = (
    "metadata_bootstrap_password",
    "metadata_migration_password",
    "metadata_schemii_password",
    "metadata_schemer_password",
    "opencode_password",
)


@unittest.skipIf(os.name == "nt", "Container recovery shell is tested on POSIX runners")
class RecoveryScriptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = self.root / "config"
        self.schemas = self.root / "schemas"
        self.dashboards = self.root / "dashboards"
        self.transaction = self.root / "transaction"
        self.output = self.root / "output"
        self.secrets = self.root / "secrets"
        self.bin = self.root / "bin"
        for path in (self.config, self.schemas, self.dashboards, self.transaction, self.output, self.secrets, self.bin):
            path.mkdir(mode=0o700)
        for name in CREDENTIAL_FILES:
            path = self.secrets / name
            path.write_text("a" * 32 + "\n", encoding="utf-8")
            path.chmod(0o600)
        self._write_fake_commands()
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "SCHEMII_INSTANCE": "schemii-recovery-test",
            "SCHEMII_RECOVERY_VERSION_FILE": str(ROOT / "VERSION"),
            "SCHEMII_RECOVERY_CONFIG_DIR": str(self.config),
            "SCHEMII_RECOVERY_SCHEMA_DIR": str(self.schemas),
            "SCHEMII_RECOVERY_DASHBOARD_DIR": str(self.dashboards),
            "SCHEMII_RECOVERY_TRANSACTION_DIR": str(self.transaction),
            "SCHEMII_RECOVERY_OUTPUT_DIR": str(self.output),
            "SCHEMII_RECOVERY_SECRET_DIR": str(self.secrets),
            "SCHEMII_RECOVERY_SECURITY_SQL": str(ROOT / "docker/metadata/verify_security.sql"),
            "SCHEMII_RECOVERY_MIGRATION_DIR": str(ROOT / "src/schemii/metadata/migrations"),
            "SCHEMII_RECOVERY_CONFIRM": "RESTORE:schemii-recovery-test",
        }

    def tearDown(self):
        self.temporary.cleanup()

    def _write_fake_commands(self):
        psql = self.bin / "psql"
        psql.write_text(
            "#!/bin/sh\n"
            "if [ -n \"${SCHEMII_TEST_PSQL_LOG:-}\" ]; then printf '%s\\n' \"$*\" >> \"$SCHEMII_TEST_PSQL_LOG\"; fi\n"
            "case \"$*\" in\n"
            "  *'COALESCE(max(version)'*) printf '%s\\n' \"${SCHEMII_TEST_METADATA_VERSION:-10}\" ;;\n"
            "  *'--username schemii_metadata_schemii '*'string_agg(application_id'*) printf 'schemii\\n' ;;\n"
            "  *'--username schemii_metadata_schemer '*'string_agg(application_id'*) printf 'schemer\\n' ;;\n"
            "  *) printf 'verified\\n' ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        pg_dump = self.bin / "pg_dump"
        pg_dump.write_text(
            "#!/bin/sh\n"
            "destination=\n"
            "while [ $# -gt 0 ]; do\n"
            "  if [ \"$1\" = --file ]; then destination=$2; shift 2; else shift; fi\n"
            "done\n"
            "printf 'metadata snapshot\\n' > \"$destination\"\n"
            "case \"$destination\" in\n"
            "  */.rollback-stage.*/*)\n"
            "    if [ \"${SCHEMII_TEST_INTERRUPT_SNAPSHOT:-0}\" = 1 ]; then kill -KILL \"$PPID\"; sleep 1; fi\n"
            "    [ \"${SCHEMII_TEST_FAIL_SNAPSHOT:-0}\" != 1 ] || exit 71 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        pg_restore = self.bin / "pg_restore"
        pg_restore.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in *--list*) exit 0 ;; esac\n"
            "for argument do archive=$argument; done\n"
            "case \"$archive\" in\n"
            "  */backup/metadata.dump)\n"
            "    if [ \"${SCHEMII_TEST_INTERRUPT:-0}\" = 1 ]; then kill -KILL \"$PPID\"; sleep 1; fi\n"
            "    if [ \"${SCHEMII_TEST_FAIL_RESTORE:-0}\" = 1 ]; then exit 70; fi ;;\n"
            "esac\n"
            "exit 0\n",
            encoding="utf-8",
        )
        shasum = self.bin / "shasum"
        shasum.write_text(
            f"#!{sys.executable}\n"
            "import hashlib, pathlib, sys\n"
            "arguments = sys.argv[1:]\n"
            "if arguments[:2] != ['-a', '256']:\n"
            "    raise SystemExit(2)\n"
            "arguments = arguments[2:]\n"
            "if arguments[:1] == ['-c']:\n"
            "    manifest = pathlib.Path(arguments[1])\n"
            "    valid = True\n"
            "    for line in manifest.read_text(encoding='utf-8').splitlines():\n"
            "        expected, name = line.split('  ', 1)\n"
            "        actual = hashlib.sha256(pathlib.Path(name).read_bytes()).hexdigest()\n"
            "        print(f'{name}: {\"OK\" if actual == expected else \"FAILED\"}')\n"
            "        valid = valid and actual == expected\n"
            "    raise SystemExit(0 if valid else 1)\n"
            "for name in arguments:\n"
            "    digest = hashlib.sha256(pathlib.Path(name).read_bytes()).hexdigest()\n"
            "    print(f'{digest}  {name}')\n",
            encoding="utf-8",
        )
        real_rm = shutil.which("rm")
        real_mv = shutil.which("mv")
        self.assertIsNotNone(real_rm)
        self.assertIsNotNone(real_mv)
        rm = self.bin / "rm"
        rm.write_text(
            "#!/bin/sh\n"
            "for argument do target=$argument; done\n"
            "name=${target##*/}\n"
            "if [ -n \"${SCHEMII_TEST_COMMIT_CLEANUP_STEP:-}\" ] "
            "&& [ \"$name\" = \"$SCHEMII_TEST_COMMIT_CLEANUP_STEP\" ]; then\n"
            "  case \"${SCHEMII_TEST_COMMIT_CLEANUP_MODE:-}\" in\n"
            "    failure) exit 71 ;;\n"
            f"    interruption) {real_rm} \"$@\" || exit $?; kill -KILL \"$PPID\"; sleep 1; exit 72 ;;\n"
            "  esac\n"
            "fi\n"
            f"exec {real_rm} \"$@\"\n",
            encoding="utf-8",
        )
        mv = self.bin / "mv"
        mv.write_text(
            "#!/bin/sh\n"
            "for argument do target=$argument; done\n"
            "if [ \"${target##*/}\" = committed ]; then\n"
            "  case \"${SCHEMII_TEST_COMMIT_MARKER_MODE:-}\" in\n"
            "    failure) exit 71 ;;\n"
            f"    interruption) {real_mv} \"$@\" || exit $?; kill -KILL \"$PPID\"; sleep 1; exit 72 ;;\n"
            "  esac\n"
            "fi\n"
            f"exec {real_mv} \"$@\"\n",
            encoding="utf-8",
        )
        for path in (psql, pg_dump, pg_restore, shasum, rm, mv):
            path.chmod(0o700)

    def run_recovery(self, action, *, env=None, check=True):
        result = subprocess.run(
            ["/bin/sh", str(RECOVERY), action],
            cwd=ROOT,
            env=self.env if env is None else {**self.env, **env},
            capture_output=True,
            text=True,
            timeout=20,
        )
        if check and result.returncode:
            self.fail(f"recovery {action} failed ({result.returncode})\n{result.stdout}\n{result.stderr}")
        return result

    def create_backup(self):
        (self.config / "profile.json").write_text("new config\n", encoding="utf-8")
        (self.schemas / "schema.json").write_bytes(b'{"layout":{"x":17}}\n')
        (self.dashboards / "dashboard.json").write_text("new dashboard\n", encoding="utf-8")
        for path in (self.config / "profile.json", self.schemas / "schema.json", self.dashboards / "dashboard.json"):
            path.chmod(0o600)
        self.run_recovery("backup")
        backup = self.root / "backup"
        shutil.copytree(self.output, backup)
        self.env["SCHEMII_RECOVERY_BACKUP_DIR"] = str(backup)

    def test_backup_is_complete_marker_bound_and_checksum_verified(self):
        self.create_backup()

        self.assertEqual((self.output / "instance").read_text(encoding="utf-8"), "schemii-recovery-test\n")
        self.assertEqual((self.output / "metadata-version").read_text(encoding="utf-8"), "10\n")
        self.assertTrue((self.output / "schemii-schemas.tar.gz").is_file())
        self.assertTrue((self.output / "complete").is_file())
        self.run_recovery("verify")

        (self.root / "backup" / "complete").write_text("not-complete\n", encoding="utf-8")
        failed = self.run_recovery("verify", check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("incomplete", failed.stderr.lower())
        (self.root / "backup" / "complete").write_text("complete\n", encoding="utf-8")

        with (self.root / "backup" / "schemii-config.tar.gz").open("ab") as archive:
            archive.write(b"corrupt")
        failed = self.run_recovery("verify", check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("checksum", failed.stderr.lower())

    def test_backup_and_restore_support_the_macos_shasum_interface(self):
        self.env["SCHEMII_RECOVERY_SHA256_TOOL"] = "shasum"
        self.create_backup()
        (self.config / "profile.json").write_text("old config\n", encoding="utf-8")

        self.run_recovery("verify")
        self.run_recovery("restore")

        self.assertEqual((self.config / "profile.json").read_text(encoding="utf-8"), "new config\n")

    def test_manifest_rejects_missing_extra_duplicate_and_traversal_entries_before_hashing(self):
        mutations = {
            "missing": lambda lines: lines[1:],
            "extra": lambda lines: lines + [f"{'0' * 64}  unexpected"],
            "duplicate": lambda lines: lines + [lines[0]],
            "traversal": lambda lines: lines + [f"{'0' * 64}  ../outside"],
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                self.create_backup()
                manifest = self.root / "backup/checksums.sha256"
                lines = manifest.read_text(encoding="utf-8").splitlines()
                manifest.write_text("\n".join(mutate(lines)) + "\n", encoding="utf-8")

                failed = self.run_recovery("verify", check=False)

                self.assertNotEqual(failed.returncode, 0)
                self.assertIn("manifest", failed.stderr.lower())
                shutil.rmtree(self.root / "backup")
                shutil.rmtree(self.output)
                self.output.mkdir()

    def test_failed_restore_rolls_back_config_dashboards_and_transaction(self):
        self.create_backup()
        shutil.rmtree(self.output)
        self.output.mkdir()
        (self.config / "profile.json").write_text("old config\n", encoding="utf-8")
        (self.schemas / "schema.json").write_bytes(b'{"layout":{"x":99}}\n')
        (self.dashboards / "dashboard.json").write_text("old dashboard\n", encoding="utf-8")

        failed = self.run_recovery("restore", env={"SCHEMII_TEST_FAIL_RESTORE": "1"}, check=False)

        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual((self.config / "profile.json").read_text(encoding="utf-8"), "old config\n")
        self.assertEqual((self.schemas / "schema.json").read_bytes(), b'{"layout":{"x":99}}\n')
        self.assertEqual((self.dashboards / "dashboard.json").read_text(encoding="utf-8"), "old dashboard\n")
        self.assertEqual(list(self.transaction.iterdir()), [])

    def test_restore_requires_exact_intent_and_preserves_file_permissions(self):
        self.create_backup()
        (self.config / "profile.json").write_text("old config\n", encoding="utf-8")
        (self.config / "profile.json").chmod(0o644)

        rejected = self.run_recovery("restore", env={"SCHEMII_RECOVERY_CONFIRM": ""}, check=False)

        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("RESTORE:schemii-recovery-test", rejected.stderr)
        self.assertEqual((self.config / "profile.json").read_text(encoding="utf-8"), "old config\n")
        self.assertEqual(list(self.transaction.iterdir()), [])

        self.run_recovery("restore")
        self.assertEqual((self.config / "profile.json").read_text(encoding="utf-8"), "new config\n")
        self.assertEqual(stat.S_IMODE((self.config / "profile.json").stat().st_mode), 0o600)

    def test_prepare_is_non_mutating(self):
        (self.config / "profile.json").write_text("current\n", encoding="utf-8")

        self.run_recovery("prepare")

        self.assertEqual((self.config / "profile.json").read_text(encoding="utf-8"), "current\n")
        self.assertEqual(list(self.transaction.iterdir()), [])

    def test_security_verification_is_bound_to_recorded_and_current_versions(self):
        log = self.root / "psql.log"
        version_env = {"SCHEMII_TEST_PSQL_LOG": str(log)}

        self.run_recovery("backup", env=version_env)
        backup_calls = log.read_text(encoding="utf-8")
        self.assertIn("--set expected_metadata_version=10", backup_calls)

        log.unlink()
        self.run_recovery("verify-metadata", env=version_env)
        current_calls = log.read_text(encoding="utf-8")
        self.assertIn("--set expected_metadata_version=13", current_calls)

    def test_backup_rejects_unsupported_metadata_versions(self):
        for version, message in (("9", "older"), ("14", "newer"), ("010", "invalid")):
            with self.subTest(version=version):
                failed = self.run_recovery(
                    "backup", env={"SCHEMII_TEST_METADATA_VERSION": version}, check=False,
                )
                self.assertNotEqual(failed.returncode, 0)
                self.assertIn(message, failed.stderr.lower())

    def test_snapshot_failure_cleans_unpublished_phase_without_mutating_destination(self):
        self.create_backup()
        shutil.rmtree(self.output)
        self.output.mkdir()
        (self.config / "profile.json").write_text("old config\n", encoding="utf-8")
        (self.transaction / ".rollback-stage.crashed").mkdir()
        (self.transaction / ".rollback-stage.crashed/partial").write_text("partial\n", encoding="utf-8")

        failed = self.run_recovery("restore", env={"SCHEMII_TEST_FAIL_SNAPSHOT": "1"}, check=False)

        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("durably stage", failed.stderr)
        self.assertEqual((self.config / "profile.json").read_text(encoding="utf-8"), "old config\n")
        self.assertFalse((self.transaction / "instance").exists())
        self.assertEqual(list(self.transaction.iterdir()), [])

    def test_snapshot_interruption_leaves_no_published_marker_and_retry_cleans_staging(self):
        self.create_backup()
        shutil.rmtree(self.output)
        self.output.mkdir()
        (self.config / "profile.json").write_text("old config\n", encoding="utf-8")

        interrupted = self.run_recovery("restore", env={"SCHEMII_TEST_INTERRUPT_SNAPSHOT": "1"}, check=False)

        self.assertNotEqual(interrupted.returncode, 0)
        self.assertEqual((self.config / "profile.json").read_text(encoding="utf-8"), "old config\n")
        self.assertFalse((self.transaction / "instance").exists())
        self.run_recovery("restore")
        self.assertEqual((self.config / "profile.json").read_text(encoding="utf-8"), "new config\n")
        self.assertFalse(any(path.name.startswith(".rollback-stage.") for path in self.transaction.iterdir()))

    def test_interrupted_restore_is_rolled_back_before_retry_and_commit(self):
        self.create_backup()
        shutil.rmtree(self.output)
        self.output.mkdir()
        (self.config / "profile.json").write_text("old config\n", encoding="utf-8")
        (self.schemas / "schema.json").write_bytes(b'{"layout":{"x":99}}\n')
        (self.dashboards / "dashboard.json").write_text("old dashboard\n", encoding="utf-8")

        interrupted = self.run_recovery("restore", env={"SCHEMII_TEST_INTERRUPT": "1"}, check=False)
        self.assertNotEqual(interrupted.returncode, 0)
        self.assertTrue((self.transaction / "instance").is_file())
        self.assertEqual((self.config / "profile.json").read_text(encoding="utf-8"), "new config\n")
        self.assertEqual((self.schemas / "schema.json").read_bytes(), b'{"layout":{"x":17}}\n')

        retried = self.run_recovery("restore")
        self.assertIn("Rolled back the incomplete recovery transaction", retried.stderr)
        self.assertEqual((self.config / "profile.json").read_text(encoding="utf-8"), "new config\n")
        self.assertEqual((self.transaction / "phase").read_text(encoding="utf-8"), "data-restored\n")
        self.run_recovery("commit")
        self.assertEqual([path.name for path in self.transaction.iterdir()], ["committed"])
        self.run_recovery("finalize-commit")
        self.assertEqual(list(self.transaction.iterdir()), [])

    def test_commit_cleanup_failures_and_interruptions_resume_forward_without_rollback(self):
        self.create_backup()
        shutil.rmtree(self.output)
        self.output.mkdir()
        cleanup_steps = (
            "config.tar.gz",
            "schemas.tar.gz",
            "dashboards.tar.gz",
            "metadata.dump",
            "metadata-version",
            "rollback-checksums.sha256",
            "phase",
            "instance",
            ".instance.pending",
        )
        for mode in ("failure", "interruption"):
            for step in cleanup_steps:
                with self.subTest(mode=mode, step=step):
                    (self.config / "profile.json").write_text("old config\n", encoding="utf-8")
                    (self.schemas / "schema.json").write_bytes(b'{"layout":{"x":99}}\n')
                    (self.dashboards / "dashboard.json").write_text("old dashboard\n", encoding="utf-8")
                    self.run_recovery("restore")

                    failed = self.run_recovery(
                        "commit",
                        env={
                            "SCHEMII_TEST_COMMIT_CLEANUP_STEP": step,
                            "SCHEMII_TEST_COMMIT_CLEANUP_MODE": mode,
                        },
                        check=False,
                    )

                    self.assertNotEqual(failed.returncode, 0)
                    self.assertEqual(self.run_recovery("state").stdout.strip(), "committed-cleanup-required")
                    rejected = self.run_recovery("rollback", check=False)
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertIn("rollback is forbidden", rejected.stderr)
                    self.assertEqual((self.config / "profile.json").read_text(encoding="utf-8"), "new config\n")
                    self.assertEqual((self.schemas / "schema.json").read_bytes(), b'{"layout":{"x":17}}\n')

                    self.run_recovery("commit")
                    self.assertEqual(sorted(path.name for path in self.transaction.iterdir()), ["committed"])
                    self.run_recovery("finalize-commit")
                    self.assertEqual(list(self.transaction.iterdir()), [])

    def test_commit_marker_failure_rolls_back_but_published_marker_interruption_only_cleans_forward(self):
        self.create_backup()
        shutil.rmtree(self.output)
        self.output.mkdir()
        (self.config / "profile.json").write_text("old config\n", encoding="utf-8")
        self.run_recovery("restore")

        unpublished = self.run_recovery(
            "commit", env={"SCHEMII_TEST_COMMIT_MARKER_MODE": "failure"}, check=False,
        )
        self.assertNotEqual(unpublished.returncode, 0)
        self.assertEqual(self.run_recovery("state").stdout.strip(), "rollback-required")
        self.run_recovery("rollback")
        self.assertEqual((self.config / "profile.json").read_text(encoding="utf-8"), "old config\n")

        self.run_recovery("restore")
        published = self.run_recovery(
            "commit", env={"SCHEMII_TEST_COMMIT_MARKER_MODE": "interruption"}, check=False,
        )
        self.assertNotEqual(published.returncode, 0)
        self.assertEqual(self.run_recovery("state").stdout.strip(), "committed-cleanup-required")
        self.run_recovery("commit")
        self.run_recovery("finalize-commit")
        self.assertEqual((self.config / "profile.json").read_text(encoding="utf-8"), "new config\n")

    def test_commit_marker_finalization_failure_is_restartable_and_interruption_is_already_complete(self):
        self.create_backup()
        shutil.rmtree(self.output)
        self.output.mkdir()
        (self.config / "profile.json").write_text("old config\n", encoding="utf-8")
        self.run_recovery("restore")
        self.run_recovery("commit")

        failed = self.run_recovery(
            "finalize-commit",
            env={
                "SCHEMII_TEST_COMMIT_CLEANUP_STEP": "committed",
                "SCHEMII_TEST_COMMIT_CLEANUP_MODE": "failure",
            },
            check=False,
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(self.run_recovery("state").stdout.strip(), "committed-cleanup-required")
        self.run_recovery("finalize-commit")
        self.assertEqual(self.run_recovery("state").stdout.strip(), "none")

        self.run_recovery("restore")
        self.run_recovery("commit")
        interrupted = self.run_recovery(
            "finalize-commit",
            env={
                "SCHEMII_TEST_COMMIT_CLEANUP_STEP": "committed",
                "SCHEMII_TEST_COMMIT_CLEANUP_MODE": "interruption",
            },
            check=False,
        )
        self.assertNotEqual(interrupted.returncode, 0)
        self.assertEqual(self.run_recovery("state").stdout.strip(), "none")
        self.assertEqual(list(self.transaction.iterdir()), [])
        self.assertEqual((self.config / "profile.json").read_text(encoding="utf-8"), "new config\n")

    def test_missing_rollback_marker_is_not_treated_as_a_no_op(self):
        self.create_backup()
        shutil.rmtree(self.output)
        self.output.mkdir()
        (self.config / "profile.json").write_text("old config\n", encoding="utf-8")
        self.run_recovery("restore")
        (self.transaction / "instance").unlink()

        state = self.run_recovery("state", check=False)
        rollback = self.run_recovery("rollback", check=False)

        self.assertNotEqual(state.returncode, 0)
        self.assertNotEqual(rollback.returncode, 0)
        self.assertIn("incomplete", state.stderr)
        self.assertIn("incomplete", rollback.stderr)
        self.assertEqual((self.config / "profile.json").read_text(encoding="utf-8"), "new config\n")


class RecoveryCompatibilityTests(unittest.TestCase):
    def test_security_verification_requires_schema_owners_and_forced_rls(self):
        sql = (ROOT / "docker/metadata/verify_security.sql").read_text(encoding="utf-8")
        self.assertIn("('public', 'schemii_metadata_owner')", sql)
        self.assertIn("('schemii_admin', 'schemii_metadata_bootstrap')", sql)
        self.assertIn("invalid_schema_owners", sql)
        self.assertIn("row_security_differences", sql)
        self.assertIn("relation.relrowsecurity", sql)
        self.assertIn("relation.relforcerowsecurity", sql)
        self.assertIn("expected.relation_name <> 'metadata_schema_migrations'", sql)
        self.assertIn("privilege.is_grantable", sql)
        self.assertIn("default_acl_record_differences", sql)
        self.assertIn("expected_migration_history", sql)
        self.assertIn("function_acl_differences", sql)
        self.assertIn("policy_differences", sql)
        self.assertIn("rolconnlimit", sql)
        self.assertIn("rolvaliduntil IS NULL", sql)
        self.assertIn("rolconfig", sql)

    def test_recovery_checksum_helper_supports_gnu_and_macos_tools(self):
        shell = RECOVERY.read_text(encoding="utf-8")
        self.assertIn("command -v sha256sum", shell)
        self.assertIn("command -v shasum", shell)
        self.assertIn("shasum -a 256", shell)
        self.assertNotRegex(shell, r"\bsha256sum (?:-c )?(?:checksums|rollback|config)")

    def test_commit_publishes_forward_state_before_fixed_evidence_cleanup(self):
        shell = RECOVERY.read_text(encoding="utf-8")
        publish = shell.index('mv "$pending" "$committed_marker"')
        cleanup = shell.index('rm -f -- "$transaction_dir/$name"')
        finalize = shell.index('rm -f -- "$committed_marker"')
        self.assertLess(publish, cleanup)
        self.assertLess(cleanup, finalize)
        self.assertIn('fail "Recovery commit has begun; rollback is forbidden', shell)

    def test_newer_and_incompatible_backups_are_rejected_before_archive_access(self):
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory)
            (backup / "metadata-version").write_text("1\n", encoding="utf-8")
            (backup / "instance").write_text("recovery-test\n", encoding="utf-8")
            current = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
            major, minor, patch_version = (int(part) for part in current.split("."))
            newer = f"{major}.{minor + 1}.{patch_version}"
            incompatible = f"{major}.{minor - 1}.999" if major == 0 and minor else f"{max(major - 1, 0)}.999.999"
            with patch.dict(os.environ, {"SCHEMII_INSTANCE": "recovery-test"}), patch(
                "schemii.recovery_verify.importlib.metadata.version", return_value=current,
            ):
                (backup / "release-version").write_text(f"{newer}\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "newer"):
                    verify_backup(backup)
                (backup / "release-version").write_text(f"{incompatible}\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "not restore-compatible"):
                    verify_backup(backup)

    def test_newer_metadata_backup_is_rejected_before_archive_access(self):
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory)
            current = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
            (backup / "release-version").write_text(f"{current}\n", encoding="utf-8")
            (backup / "metadata-version").write_text("999\n", encoding="utf-8")
            (backup / "instance").write_text("recovery-test\n", encoding="utf-8")
            with patch.dict(os.environ, {"SCHEMII_INSTANCE": "recovery-test"}), patch(
                "schemii.recovery_verify.importlib.metadata.version", return_value=current,
            ):
                with self.assertRaisesRegex(ValueError, "metadata schema is newer"):
                    verify_backup(backup)

    def test_older_metadata_backup_is_rejected_before_archive_access(self):
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory)
            current = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
            (backup / "release-version").write_text(f"{current}\n", encoding="utf-8")
            (backup / "metadata-version").write_text("9\n", encoding="utf-8")
            with patch.dict(os.environ, {"SCHEMII_INSTANCE": "recovery-test"}), patch(
                "schemii.recovery_verify.importlib.metadata.version", return_value=current,
            ):
                with self.assertRaisesRegex(ValueError, "metadata schema is older"):
                    verify_backup(backup)

if __name__ == "__main__":
    unittest.main()
