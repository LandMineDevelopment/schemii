import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CREDENTIAL_NAMES = (
    "metadata_bootstrap_password",
    "metadata_migration_password",
    "metadata_schemii_password",
    "metadata_schemer_password",
    "opencode_password",
)

FAKE_DOCKER = r'''#!/bin/sh
printf '%s\n' "$*" >> "$DOCKER_LOG"
if [ "$1 $2" = "volume inspect" ] && [ "$3" = "--format" ]; then
  format=$4
  volume=$5
  logical=${volume#schemii_}
  case "$format" in
    '{{.Name}}|'*)
      generation=${SCHEMII_TEST_REPLACED:-0}
      printf '%s|2026-08-24T00:00:0%sZ|local|/var/lib/docker/volumes/%s/_data-%s|local|null\n' \
        "$volume" "$generation" "$volume" "$generation"
      exit 0 ;;
    '{{ index .Labels '*'com.docker.compose.volume'*)
      case "$logical" in
        schemii-config|schemii-schemas) printf '|\n' ;;
        *) printf 'schemii|%s\n' "$logical" ;;
      esac
      exit 0 ;;
  esac
fi
case "$*" in
  info|"compose version") exit 0 ;;
  "volume inspect schemii_schemii-metadata-postgres") exit 0 ;;
  "ps -aq --filter label=com.docker.compose.project=schemii")
    [ "${SCHEMII_TEST_NO_CONTAINERS:-0}" = 1 ] && exit 0
    [ "${SCHEMII_TEST_WITNESS_MODE:-}" = no-witness ] || printf 'witness\n'
    exit 0 ;;
  "ps -aq")
    [ "${SCHEMII_TEST_NO_CONTAINERS:-0}" = 1 ] && exit 0
    [ "${SCHEMII_TEST_WITNESS_MODE:-}" = no-witness ] && exit 0
    printf 'witness\n'
    [ "${SCHEMII_TEST_WITNESS_MODE:-}" = foreign ] && printf 'foreign\n'
    exit 0 ;;
  "inspect --format {{.State.Running}} witness") printf 'false\n'; exit 0 ;;
  "inspect --format "*".Mounts"*" witness")
    printf 'volume|schemii_schemii-config|/data/config\nvolume|schemii_schemii-schemas|/data/schemas\n'
    exit 0 ;;
  "inspect --format "*".Mounts"*" foreign")
    printf 'volume|schemii_schemii-config|/data/config\n'
    exit 0 ;;
  "inspect --format "*"com.docker.compose.project.working_dir"*" witness")
    case "${SCHEMII_TEST_WITNESS_MODE:-}" in
      wrong-project) printf 'other|schemii|%s\n' "$REPOSITORY" ;;
      wrong-repository) printf 'schemii|schemii|/tmp/other-repository\n' ;;
      *) printf 'schemii|schemii|%s\n' "$REPOSITORY" ;;
    esac
    exit 0 ;;
  "inspect --format "*"com.docker.compose.project.working_dir"*" foreign")
    printf 'foreign|schemii|%s\n' "$REPOSITORY"
    exit 0 ;;
  "image inspect "*) [ "${SCHEMII_TEST_NO_CONTAINERS:-0}" = 1 ] && exit 71; exit 0 ;;
  *) exit 0 ;;
esac
'''


@unittest.skipIf(os.name == "nt", "POSIX legacy volume adoption is tested on POSIX runners")
class LegacyVolumeAdoptionTests(unittest.TestCase):
    def prepare(self, root):
        binary = root / "bin"
        binary.mkdir()
        docker = binary / "docker"
        docker.write_text(FAKE_DOCKER, encoding="utf-8")
        docker.chmod(0o755)
        credentials = root / "credentials"
        credentials.mkdir(mode=0o700)
        (credentials / "instance").write_text("schemii\n", encoding="utf-8")
        (credentials / "instance").chmod(0o600)
        for name in CREDENTIAL_NAMES:
            path = credentials / name
            path.write_text("a" * 32 + "\n", encoding="utf-8")
            path.chmod(0o600)
        return binary, credentials

    def run_launcher(self, root, binary, credentials, *arguments, **changes):
        environment = {
            **os.environ,
            "PATH": f"{binary}:/usr/bin:/bin",
            "DOCKER_LOG": str(root / "docker.log"),
            "REPOSITORY": str(ROOT),
            "SCHEMII_INSTANCE": "schemii",
            "SCHEMII_CREDENTIAL_DIR": str(credentials),
        }
        environment.update({key: str(value) for key, value in changes.items()})
        return subprocess.run(
            ["/bin/bash", str(ROOT / "start.sh"), *arguments],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def test_adoption_rejects_foreign_wrong_project_and_wrong_repository_consumers(self):
        for witness_mode in ("foreign", "wrong-project", "wrong-repository", "no-witness"):
            with self.subTest(witness_mode=witness_mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                binary, credentials = self.prepare(root)
                result = self.run_launcher(
                    root,
                    binary,
                    credentials,
                    "legacy-volume-adopt",
                    "ADOPT:schemii",
                    SCHEMII_TEST_WITNESS_MODE=witness_mode,
                )
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                expected = "no expected Compose project/service/repository witness" if witness_mode == "no-witness" else "foreign or unexpected consumer"
                self.assertIn(expected, result.stderr)
                self.assertFalse((credentials / "legacy-volume-adoptions.v1").exists())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary, credentials = self.prepare(root)
            (credentials / "instance").write_text("schemii-other\n", encoding="utf-8")
            result = self.run_launcher(
                root,
                binary,
                credentials,
                "legacy-volume-adopt",
                "ADOPT:schemii-other",
                SCHEMII_INSTANCE="schemii-other",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("limited to the historical schemii volume pair", result.stderr)
            self.assertFalse((credentials / "legacy-volume-adoptions.v1").exists())

    def test_adoption_is_owner_only_survives_container_recreation_and_detects_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary, credentials = self.prepare(root)

            missing = self.run_launcher(
                root, binary, credentials, "instance-backup", str(root / "missing-backup")
            )
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("lacks unchanged adoption evidence", missing.stderr)

            adopted = self.run_launcher(
                root, binary, credentials, "legacy-volume-adopt", "ADOPT:schemii"
            )
            self.assertEqual(adopted.returncode, 0, adopted.stdout + adopted.stderr)
            adoption_dir = credentials / "legacy-volume-adoptions.v1"
            self.assertEqual(adoption_dir.stat().st_mode & 0o777, 0o700)
            self.assertEqual(
                sorted(path.name for path in adoption_dir.iterdir()),
                ["schemii-config.manifest", "schemii-schemas.manifest"],
            )
            for logical in ("schemii-config", "schemii-schemas"):
                manifest = adoption_dir / f"{logical}.manifest"
                self.assertEqual(manifest.stat().st_mode & 0o777, 0o600)
                body = manifest.read_text(encoding="utf-8")
                self.assertIn("format=schemii-legacy-volume-adoption-v1\n", body)
                self.assertIn(f"repository={ROOT}\n", body)
                self.assertIn(f"logical={logical}\n", body)

            recreated = self.run_launcher(
                root,
                binary,
                credentials,
                "instance-backup",
                str(root / "recreated-backup"),
                SCHEMII_TEST_NO_CONTAINERS=1,
            )
            self.assertEqual(recreated.returncode, 71, recreated.stdout + recreated.stderr)
            self.assertIn("Selected immutable recovery images are not loaded", recreated.stderr)

            replaced = self.run_launcher(
                root,
                binary,
                credentials,
                "instance-backup",
                str(root / "replaced-backup"),
                SCHEMII_TEST_REPLACED=1,
            )
            self.assertNotEqual(replaced.returncode, 0)
            self.assertIn("lacks unchanged adoption evidence", replaced.stderr)

            schemas_manifest = adoption_dir / "schemii-schemas.manifest"
            schemas_body = schemas_manifest.read_bytes()
            schemas_manifest.unlink()
            partial = self.run_launcher(
                root, binary, credentials, "instance-backup", str(root / "partial-backup")
            )
            self.assertNotEqual(partial.returncode, 0)
            self.assertIn("lacks unchanged adoption evidence", partial.stderr)
            schemas_manifest.write_bytes(schemas_body)
            schemas_manifest.chmod(0o600)

            config_manifest = adoption_dir / "schemii-config.manifest"
            config_manifest.chmod(0o640)
            permission_drift = self.run_launcher(
                root, binary, credentials, "instance-backup", str(root / "permission-backup")
            )
            self.assertNotEqual(permission_drift.returncode, 0)
            self.assertIn("lacks unchanged adoption evidence", permission_drift.stderr)
            config_manifest.chmod(0o600)

            manifest = config_manifest
            manifest.write_text(
                manifest.read_text(encoding="utf-8") + "tampered=true\n", encoding="utf-8"
            )
            tampered = self.run_launcher(
                root, binary, credentials, "instance-backup", str(root / "tampered-backup")
            )
            self.assertNotEqual(tampered.returncode, 0)
            self.assertIn("lacks unchanged adoption evidence", tampered.stderr)

            calls = (root / "docker.log").read_text(encoding="utf-8")
            self.assertNotIn("volume rm", calls)
            self.assertNotIn("volume create", calls)

    def test_uninstall_removes_unlabeled_volumes_only_with_exact_attestation(self):
        uninstaller_docker = r'''#!/bin/sh
printf '%s\n' "$*" >> "$DOCKER_LOG"
if [ "$1 $2" = "volume inspect" ] && [ "$3" = "--format" ]; then
  format=$4
  volume=$5
  case "$format" in
    '{{.Name}}|'*)
      generation=0
      if [ "$volume" = schemii_schemii-schemas ] && [ -n "${SCHEMII_TEST_DRIFT_STATE:-}" ] && [ -f "$SCHEMII_TEST_DRIFT_STATE" ]; then generation=1; fi
      printf '%s|2026-08-24T00:00:0%sZ|local|/var/lib/docker/volumes/%s/_data-%s|local|null\n' "$volume" "$generation" "$volume" "$generation"
      exit 0 ;;
    *'{{.Name}}'*) printf '||%s\n' "$volume"; exit 0 ;;
    *) printf '|\n'; exit 0 ;;
  esac
fi
case "$*" in
  info|"ps -aq"|"network ls -q --filter label=com.docker.compose.project=schemii") exit 0 ;;
  "volume ls -q") printf 'schemii_schemii-config\nschemii_schemii-schemas\n'; exit 0 ;;
  "volume rm schemii_schemii-config")
    [ -z "${SCHEMII_TEST_DRIFT_STATE:-}" ] || : > "$SCHEMII_TEST_DRIFT_STATE"
    exit 0 ;;
  "volume rm "*) exit 0 ;;
  *) exit 0 ;;
esac
'''

        for mode in ("valid", "tampered", "drift"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repository = root / "schemii-copy"
                (repository / "src/schemii").mkdir(parents=True)
                shutil.copy2(ROOT / "uninstall.sh", repository / "uninstall.sh")
                (repository / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
                (repository / "start.sh").write_text("#!/bin/sh\n", encoding="utf-8")
                binary = root / "bin"
                binary.mkdir()
                docker = binary / "docker"
                docker.write_text(uninstaller_docker, encoding="utf-8")
                docker.chmod(0o755)
                credentials = root / "credentials/schemii"
                adoption = credentials / "legacy-volume-adoptions.v1"
                adoption.mkdir(parents=True, mode=0o700)
                credentials.parent.chmod(0o700)
                (credentials / "instance").write_text("schemii\n", encoding="utf-8")
                for logical in ("schemii-config", "schemii-schemas"):
                    volume = f"schemii_{logical}"
                    body = (
                        "format=schemii-legacy-volume-adoption-v1\n"
                        "project=schemii\n"
                        f"repository={repository}\n"
                        f"logical={logical}\n"
                        f"volume={volume}\n"
                        "created-at=2026-08-24T00:00:00Z\n"
                        "driver=local\n"
                        f"mountpoint=/var/lib/docker/volumes/{volume}/_data-0\n"
                        "scope=local\n"
                    )
                    manifest = adoption / f"{logical}.manifest"
                    manifest.write_text(body, encoding="utf-8")
                    manifest.chmod(0o600)
                if mode == "tampered":
                    with (adoption / "schemii-config.manifest").open("a", encoding="utf-8") as file:
                        file.write("tampered=true\n")
                log = root / "docker.log"

                environment = {
                    **os.environ,
                    "PATH": f"{binary}:/usr/bin:/bin",
                    "DOCKER_LOG": str(log),
                    "SCHEMII_CREDENTIAL_ROOT": str(root / "credentials"),
                }
                if mode == "drift":
                    environment["SCHEMII_TEST_DRIFT_STATE"] = str(root / "drift.state")
                result = subprocess.run(
                    ["/bin/bash", str(repository / "uninstall.sh"), "--yes"],
                    cwd=repository,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )

                calls = log.read_text(encoding="utf-8")
                if mode == "tampered":
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("Detected Schemii instances: none", result.stdout)
                    self.assertNotIn("volume rm", calls)
                    self.assertTrue(credentials.exists())
                elif mode == "drift":
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("changed during uninstall", result.stderr)
                    self.assertIn("volume rm schemii_schemii-config", calls)
                    self.assertNotIn("volume rm schemii_schemii-schemas", calls)
                    self.assertTrue(credentials.exists())
                    self.assertTrue(repository.exists())
                else:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("volume rm schemii_schemii-config", calls)
                    self.assertIn("volume rm schemii_schemii-schemas", calls)
                    self.assertFalse(credentials.exists())


if __name__ == "__main__":
    unittest.main()
