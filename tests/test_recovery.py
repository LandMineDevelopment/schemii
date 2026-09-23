"""Recovery integrity and isolation checks; Docker commands use a fake executable."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import base64
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("recovery_bundle", ROOT / "dev/recovery/bundle.py")
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)


def make_bundle(tmp_path):
    root = tmp_path / "backup"
    root.mkdir(mode=0o700)
    (root / "secrets").mkdir(mode=0o700)
    for name in bundle.PAYLOADS:
        (root / name).write_bytes(b"PGDMP test archive" if name == "metadata.dump" else b"test")
        (root / name).chmod(0o600)
    bundle.seal(root)
    return root


def test_private_bundle_detects_tampering_and_missing_payloads(tmp_path):
    root = make_bundle(tmp_path)
    bundle.validate(root)
    (root / "metadata.dump").write_bytes(b"PGDMP changed")
    with pytest.raises(ValueError, match="checksum"):
        bundle.validate(root)
    (root / "metadata.dump").unlink()
    with pytest.raises(FileNotFoundError):
        bundle.validate(root)


def test_bundle_rejects_public_secrets_symlinks_and_manifest_path_injection(tmp_path):
    root = make_bundle(tmp_path)
    secret = root / "secrets/metadata_encryption_key"
    secret.chmod(0o644)
    with pytest.raises(ValueError, match="private regular"):
        bundle.validate(root)
    secret.unlink()
    secret.symlink_to(root / "identity.json")
    with pytest.raises(ValueError, match="private regular"):
        bundle.validate(root)
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["sha256"]["../../arbitrary"] = "ignored"
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="manifest"):
        bundle.validate(root)


def test_prepare_requires_original_secrets_and_never_follows_symlinks(tmp_path):
    root, secrets = tmp_path / "out", tmp_path / "source"
    root.mkdir()
    secrets.mkdir()
    (secrets / bundle.SECRETS[0]).symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="missing or a symbolic link"):
        bundle.prepare(root, secrets, "metadata", "bootstrap", "app")


@pytest.mark.parametrize("fail_restore", [False, True])
def test_verify_uses_only_generated_project_and_cleans_up_on_restore_failure(tmp_path, fail_restore):
    root = make_bundle(tmp_path)
    binary = tmp_path / "bin"
    binary.mkdir()
    log = tmp_path / "commands.jsonl"
    fake = binary / "docker"
    fake.write_text("""#!/usr/bin/env python3
import json, os, sys
with open(os.environ['COMMAND_LOG'], 'a') as log:
    log.write(json.dumps(sys.argv[1:]) + '\\n')
if 'pg_restore' in sys.argv:
    sys.stdin.buffer.read()
    if os.environ['FAIL_RESTORE'] == '1':
        sys.exit(7)
""")
    fake.chmod(0o700)
    env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ["PATH"],
               ROOT_DIR=str(ROOT), SCHEMII_LAUNCH_ACTION="verify-backup",
               SCHEMII_RECOVERY_DIRECTORY=str(root), COMMAND_LOG=str(log),
               FAIL_RESTORE=str(int(fail_restore)))
    result = subprocess.run(["bash", "-c", '''set -Eeuo pipefail
fail() { echo "$1" >&2; exit 1; }
source "$ROOT_DIR/dev/recovery/recovery.sh"
compose_args=(compose --project-name schemii-test)
recovery_main
'''], env=env, capture_output=True, text=True)
    assert (result.returncode != 0) == fail_restore, result.stderr
    commands = [json.loads(line) for line in log.read_text().splitlines()]
    assert commands[0] == ["compose", "--project-name", "schemii-test", "build", "schemii"]
    projects = {command[command.index("--project-name") + 1] for command in commands[1:]}
    assert len(projects) == 1
    assert next(iter(projects)).startswith("schemii-verify-")
    assert all("schemii-test" not in command for command in commands[1:])
    assert "down" in commands[-1] and "--volumes" in commands[-1]
    assert all("compose.local.yaml" not in " ".join(command) for command in commands)
    if fail_restore:
        assert not any("verifier" in command for command in commands)


def test_service_resource_limits_and_rotating_logs():
    text = (ROOT / "compose.test.yaml").read_text()
    for tag in ("APP", "METADATA", "DEMO", "INGRESS"):
        assert f"SCHEMII_{tag}_MEMORY" in text
        assert f"SCHEMII_{tag}_CPUS" in text
        assert f"SCHEMII_{tag}_PIDS" in text
    assert text.count("logging: *bounded-logging") == text.count("    image:")
    assert "SCHEMII_LOG_MAX_SIZE:-10m" in text
    assert "SCHEMII_LOG_MAX_FILES:-3" in text


@pytest.mark.parametrize("retained", ["volume", "container", "secrets", "override"])
def test_fresh_restore_refuses_retained_state_before_any_mutation(tmp_path, retained):
    fake = tmp_path / "docker"
    log = tmp_path / "calls"
    fake.write_text("""#!/usr/bin/env python3
import os, sys
with open(os.environ['COMMAND_LOG'], 'a') as log:
    log.write(' '.join(sys.argv[1:]) + '\\n')
if sys.argv[1:3] == ['volume','ls'] and os.environ['RETAINED'] == 'volume':
    print('schemii-test_schemii-test-postgres')
if sys.argv[1] == 'ps' and os.environ['RETAINED'] == 'container':
    print('existing-container')
""")
    fake.chmod(0o700)
    (tmp_path / ".schemii").mkdir()
    if retained == "secrets":
        (tmp_path / ".schemii/secrets").mkdir()
    if retained == "override":
        (tmp_path / ".schemii/compose.local.yaml").touch()
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ["PATH"],
               ROOT_DIR=str(tmp_path), SCHEMII_LAUNCH_ACTION="restore-backup-new",
               SCHEMII_SECRET_DIRECTORY=str(tmp_path / ".schemii/secrets"),
               COMMAND_LOG=str(log), RETAINED=retained,
               RECOVERY_SCRIPT=str(ROOT / "dev/recovery/recovery.sh"))
    result = subprocess.run(["bash", "-c", '''set -Eeuo pipefail
fail() { echo "$1" >&2; exit 1; }
source "$RECOVERY_SCRIPT"
recovery_main
'''], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "refuses" in result.stderr
    assert len(log.read_text().splitlines()) == 2


def test_verifier_rejects_wrong_paired_key_before_database_access(tmp_path, monkeypatch):
    from cryptography.exceptions import InvalidTag
    from schemii.common.metadata.crypto import CredentialCipher

    root = make_bundle(tmp_path)
    encrypted = CredentialCipher(b"a" * 32).encrypt("recovery", "key-probe", "schemii-recovery-v1")
    (root / "key-probe.json").write_text(json.dumps({
        "ciphertext": encrypted.ciphertext.hex(), "nonce": encrypted.nonce.hex(),
        "key_version": encrypted.key_version,
    }))
    (root / "secrets/metadata_encryption_key").write_bytes(base64.b64encode(b"b" * 32))
    bundle.seal(root)
    monkeypatch.syspath_prepend(str(ROOT / "dev/recovery"))
    spec = importlib.util.spec_from_file_location("recovery_verify", ROOT / "dev/recovery/verify.py")
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    monkeypatch.setattr(verifier, "Path", lambda _: root)
    monkeypatch.setattr(verifier, "connection", lambda: pytest.fail("must not connect with an invalid key"))
    with pytest.raises(InvalidTag):
        verifier.verify()


def test_fresh_restore_verifies_before_copying_keys_and_imports_as_original_role(tmp_path):
    root = make_bundle(tmp_path)
    (root / "identity.json").write_text(json.dumps({
        "database": "original_db", "bootstrap_user": "original_admin", "app_user": "original_app",
    }))
    bundle.seal(root)
    checkout = tmp_path / "checkout"
    shutil.copytree(ROOT / "dev/recovery", checkout / "dev/recovery")
    (checkout / ".schemii").mkdir()
    binary = tmp_path / "bin"
    binary.mkdir()
    log = tmp_path / "commands"
    fake = binary / "docker"
    fake.write_text("""#!/usr/bin/env python3
import json, os, pathlib, sys
with open(os.environ['COMMAND_LOG'], 'a') as log:
    log.write(json.dumps(sys.argv[1:]) + '\\n')
if 'verifier' in sys.argv:
    assert not pathlib.Path(os.environ['SCHEMII_SECRET_DIRECTORY']).exists()
if 'pg_restore' in sys.argv or any('exec pg_restore' in arg for arg in sys.argv):
    sys.stdin.buffer.read()
""")
    fake.chmod(0o700)
    secret_dir = checkout / ".schemii/secrets"
    env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ["PATH"],
               ROOT_DIR=str(checkout), SCHEMII_LAUNCH_ACTION="restore-backup-new",
               SCHEMII_SECRET_DIRECTORY=str(secret_dir), SCHEMII_RECOVERY_DIRECTORY=str(root),
               SCHEMII_TEST_POSTGRES_DB="original_db", SCHEMII_TEST_POSTGRES_USER="original_admin",
               SCHEMII_METADATA_APP_USER="original_app", SCHEMII_STARTUP_TIMEOUT="60",
               COMMAND_LOG=str(log))
    result = subprocess.run(["bash", "-c", '''set -Eeuo pipefail
fail() { echo "$1" >&2; exit 1; }
source "$ROOT_DIR/dev/recovery/recovery.sh"
compose_args=(compose --project-name schemii-test)
recovery_main
'''], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (secret_dir / "metadata_encryption_key").read_bytes() == (root / "secrets/metadata_encryption_key").read_bytes()
    commands = [json.loads(line) for line in log.read_text().splitlines()]
    restore = commands[-1]
    assert restore[-1] == "original_app"
    assert '--role="$1"' in restore[-3]
    assert commands[-2][-1] == "metadata-bootstrap"
    assert commands[-3][-1] == "metadata-postgres"
    assert not any("up" in command and "schemii" in command for command in commands)
