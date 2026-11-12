from __future__ import annotations

import base64
import fcntl
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
START = ROOT / "start.sh"


def _install_mock_git(
    tmp_path: Path,
    environment: dict[str, str],
    worktrees: list[Path] | None = None,
    *,
    command_directory: Path | None = None,
) -> None:
    roots = worktrees or [tmp_path / "primary"]
    for path in roots:
        path.mkdir(parents=True, exist_ok=True)
    common_git_directory = tmp_path / "shared-git"
    common_git_directory.mkdir(exist_ok=True)
    worktree_list = tmp_path / "worktrees.txt"
    worktree_list.write_text(
        "\n\n".join(
            f"worktree {path}\nHEAD 0123456789abcdef0123456789abcdef01234567\n"
            + ("branch refs/heads/main" if index == 0 else "detached")
            for index, path in enumerate(roots)
        )
        + "\n",
        encoding="utf-8",
    )
    executable_directory = command_directory or tmp_path
    git = executable_directory / "git"
    git.write_text(
        "#!/bin/sh\n"
        "case \"$*\" in\n"
        "  *'rev-parse --path-format=absolute --git-common-dir'*) printf '%s\\n' \"$FAKE_GIT_COMMON_DIRECTORY\" ;;\n"
        "  *'worktree list --porcelain'*) cat -- \"$FAKE_WORKTREE_LIST\" ;;\n"
        "  *) exit 64 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    git.chmod(0o755)
    environment.update(
        {
            "PATH": f"{executable_directory}:{environment.get('PATH', os.defpath)}",
            "FAKE_GIT_COMMON_DIRECTORY": str(common_git_directory),
            "FAKE_WORKTREE_LIST": str(worktree_list),
        }
    )


def _isolated_launcher_environment(tmp_path: Path, **overrides: str) -> dict[str, str]:
    """Keep launcher subprocesses away from machine-retained QA and secret state."""
    environment = {
        **os.environ,
        "SCHEMII_QA_STATE_DIRECTORY": str(tmp_path / "qa-state"),
        "SCHEMII_TLS_DIRECTORY": str(tmp_path / "tls"),
        "SCHEMII_SECRET_DIRECTORY": str(tmp_path / "secrets"),
        "SCHEMII_LAUNCH_LOCK_FILE": str(tmp_path / "start.lock"),
    }
    environment.update(overrides)
    return environment


def test_startup_script_owns_the_compose_launch_contract() -> None:
    source = START.read_text(encoding="utf-8")

    assert source.startswith("#!/usr/bin/env bash\nset -Eeuo pipefail")
    assert 'COMPOSE_FILE="${ROOT_DIR}/compose.test.yaml"' in source
    assert 'SCHEMII_TEST_APP_PORT="${SCHEMII_TEST_APP_PORT-8001}"' in source
    assert 'SCHEMII_TEST_POSTGRES_DB="${SCHEMII_TEST_POSTGRES_DB-schemii_test}"' in source
    assert 'SCHEMII_TEST_POSTGRES_USER="${SCHEMII_TEST_POSTGRES_USER-schemii}"' in source
    assert 'SCHEMII_TEST_POSTGRES_PASSWORD="${SCHEMII_TEST_POSTGRES_PASSWORD-schemii-local-test}"' in source
    assert 'SCHEMII_METADATA_APP_USER="${SCHEMII_METADATA_APP_USER-schemii_metadata_app}"' in source
    assert 'SCHEMII_STARTUP_TIMEOUT="${SCHEMII_STARTUP_TIMEOUT-120}"' in source
    assert 'SCHEMII_TLS_DIRECTORY="${SCHEMII_TLS_DIRECTORY-${ROOT_DIR}/.schemii/tls}"' in source
    assert 'SCHEMII_TLS_CERTIFICATE_DAYS="${SCHEMII_TLS_CERTIFICATE_DAYS-365}"' in source
    assert 'SCHEMII_PRIMARY_WORKTREE_ROOT="$(git --git-dir="$SCHEMII_QA_LOCK_DIRECTORY" worktree list --porcelain' in source
    assert 'SCHEMII_SECRET_DIRECTORY="${SCHEMII_PRIMARY_WORKTREE_ROOT}/.schemii/secrets"' in source
    assert 'SCHEMII_LAUNCH_LOCK_FILE="${SCHEMII_LAUNCH_LOCK_FILE-${ROOT_DIR}/.schemii/start.lock}"' in source
    assert "openssl req -x509" in source
    assert "subjectAltName=DNS:localhost,IP:127.0.0.1" in source
    assert "basicConstraints=critical,CA:FALSE" in source
    assert "extendedKeyUsage=serverAuth" in source
    assert "docker compose version" in source
    assert "docker info" in source
    assert 'exec newgrp docker -c "$restart_command"' in source
    assert 'docker "${compose_args[@]}" build schemii' in source
    assert 'docker "${compose_args[@]}" up --detach --remove-orphans --wait --wait-timeout' in source
    assert 'docker "${compose_args[@]}" logs --no-color --tail 200 schemii' in source
    assert 'docker "${compose_args[@]}" logs --no-color --tail 200 metadata-bootstrap' in source
    assert 'docker "${compose_args[@]}" logs --no-color --tail 200 postgres-seed' in source
    assert 'fail "the application service did not become healthy"' in source
    assert "sudo" not in source
    assert "uvicorn" not in source
    assert "docker.sock" not in source
    assert "label=com.docker.compose.project=schemii-test" in source
    assert "label=com.docker.compose.service=postgres" in source
    assert "SCHEMII_METADATA_BOOTSTRAP_PASSWORD_SECRET_FILE" in source
    assert "SCHEMII_METADATA_APP_PASSWORD_SECRET_FILE" in source
    assert "SCHEMII_DEMO_ADMIN_PASSWORD_SECRET_FILE" in source
    assert "SCHEMII_DEMO_TARGET_PASSWORD_SECRET_FILE" in source
    assert "SCHEMII_METADATA_ENCRYPTION_KEY_SECRET_FILE" in source
    assert "openssl rand -base64 32" in source
    assert "openssl rand -hex 32" in source
    assert "flock --nonblock" in source


def _copy_launcher_worktree(destination: Path) -> None:
    destination.mkdir(parents=True)
    shutil.copy2(START, destination / "start.sh")
    shutil.copy2(ROOT / "compose.test.yaml", destination / "compose.test.yaml")
    manifest = destination / "dev/postgres/demo-scenarios/baseline/manifest.json"
    manifest.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "dev/postgres/demo-scenarios/baseline/manifest.json", manifest)


def _mock_launcher_environment(
    tmp_path: Path,
    worktrees: list[Path],
    command_log: Path,
    *,
    tls_directory: Path,
    lock_file: Path,
    qa_state_directory: Path,
    mock_git: bool = True,
) -> dict[str, str]:
    bin_directory = tmp_path / "mock-bin"
    bin_directory.mkdir(exist_ok=True)
    docker = bin_directory / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "printf '%s|secret-files=%s,%s,%s,%s,%s\\n' \"$*\" "
        "\"$SCHEMII_METADATA_BOOTSTRAP_PASSWORD_SECRET_FILE\" "
        "\"$SCHEMII_METADATA_APP_PASSWORD_SECRET_FILE\" "
        "\"$SCHEMII_DEMO_ADMIN_PASSWORD_SECRET_FILE\" "
        "\"$SCHEMII_DEMO_TARGET_PASSWORD_SECRET_FILE\" "
        "\"$SCHEMII_METADATA_ENCRYPTION_KEY_SECRET_FILE\" >> \"$COMMAND_LOG\"\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    environment = dict(os.environ)
    for name in tuple(environment):
        if name.startswith("SCHEMII_"):
            environment.pop(name)
    environment.update(
        {
            "PATH": f"{bin_directory}:{environment['PATH']}",
            "COMMAND_LOG": str(command_log),
            "SCHEMII_TLS_DIRECTORY": str(tls_directory),
            "SCHEMII_LAUNCH_LOCK_FILE": str(lock_file),
            "SCHEMII_QA_STATE_DIRECTORY": str(qa_state_directory),
            "SCHEMII_STARTUP_TIMEOUT": "7",
            "SCHEMII_TEST_APP_PORT": "8123",
            "SCHEMII_TEST_POSTGRES_PASSWORD": "schemii-local-test",
        }
    )
    if mock_git:
        _install_mock_git(tmp_path, environment, worktrees, command_directory=bin_directory)
    return environment


def _run_mocked_launcher(worktree: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(worktree / "start.sh")],
        cwd=worktree,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_fresh_worktree_reuses_primary_worktree_secrets_without_exposing_them(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    fresh = tmp_path / "fresh"
    _copy_launcher_worktree(primary)
    _copy_launcher_worktree(fresh)
    secret_directory = primary / ".schemii/secrets"
    secret_directory.mkdir(parents=True, mode=0o750)
    known_good = {
        "metadata_password": b"retained-metadata-bootstrap-secret\n",
        "metadata_app_password": b"retained-metadata-app-secret\n",
        "demo_admin_password": b"retained-demo-admin-secret\n",
        "demo_target_password": b"schemii-local-test\n",
        "metadata_encryption_key": base64.b64encode(bytes(range(32))) + b"\n",
        "opencode_password": b"retained-opencode-secret\n",
        "account_setup_token": b"retained-account-setup-secret\n",
    }
    for name, value in known_good.items():
        path = secret_directory / name
        path.write_bytes(value)
        path.chmod(0o640)
    secret_directory.chmod(0o750)

    command_log = tmp_path / "commands.log"
    environment = _mock_launcher_environment(
        tmp_path,
        [primary, fresh],
        command_log,
        tls_directory=tmp_path / "tls-fresh",
        lock_file=tmp_path / "launch-fresh.lock",
        qa_state_directory=fresh / ".schemii/testing",
    )
    environment.pop("SCHEMII_SECRET_DIRECTORY", None)
    result = _run_mocked_launcher(fresh, environment)

    output = result.stdout + result.stderr
    assert result.returncode == 0, result.stderr
    assert "Schemii is ready at https://localhost:8123/" in result.stdout
    assert all(value.strip().decode() not in output for value in known_good.values())
    assert all((secret_directory / name).read_bytes() == value for name, value in known_good.items())
    assert not (fresh / ".schemii/secrets").exists()
    commands = command_log.read_text(encoding="utf-8")
    assert f"secret-files={secret_directory / 'metadata_password'}" in commands
    assert f"{secret_directory / 'metadata_app_password'}" in commands
    assert str(fresh / ".schemii/secrets") not in commands


def test_linked_worktree_git_file_reuses_primary_secret_bytes(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    fresh = tmp_path / "fresh"
    _copy_launcher_worktree(primary)
    for args in (
        ["git", "-C", str(primary), "init", "-b", "main"],
        ["git", "-C", str(primary), "config", "user.name", "Startup Test"],
        ["git", "-C", str(primary), "config", "user.email", "startup-test@example.invalid"],
        ["git", "-C", str(primary), "add", "start.sh", "compose.test.yaml", "dev"],
        ["git", "-C", str(primary), "commit", "-m", "startup test fixture"],
        ["git", "-C", str(primary), "worktree", "add", "-b", "fresh", str(fresh)],
    ):
        subprocess.run(args, capture_output=True, text=True, check=True, timeout=10)

    assert (primary / ".git").is_dir()
    assert (fresh / ".git").is_file()
    common_git_directory = subprocess.run(
        ["git", "-C", str(fresh), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    assert Path(common_git_directory) == primary / ".git"

    secret_directory = primary / ".schemii/secrets"
    secret_directory.mkdir(parents=True, mode=0o750)
    known_good_password = b"retained-primary-bootstrap-password\n"
    (secret_directory / "metadata_password").write_bytes(known_good_password)
    (secret_directory / "demo_target_password").write_text("schemii-local-test\n", encoding="utf-8")
    for path in secret_directory.iterdir():
        path.chmod(0o640)

    command_log = tmp_path / "commands.log"
    environment = _mock_launcher_environment(
        tmp_path,
        [primary, fresh],
        command_log,
        tls_directory=tmp_path / "tls-fresh",
        lock_file=tmp_path / "launch-fresh.lock",
        qa_state_directory=fresh / ".schemii/testing",
        mock_git=False,
    )
    environment.pop("SCHEMII_SECRET_DIRECTORY", None)
    result = _run_mocked_launcher(fresh, environment)

    output = result.stdout + result.stderr
    assert result.returncode == 0, result.stderr
    assert known_good_password.decode().strip() not in output
    assert (secret_directory / "metadata_password").read_bytes() == known_good_password
    assert not (fresh / ".schemii/secrets").exists()
    assert str(secret_directory / "metadata_password") in command_log.read_text(encoding="utf-8")


def test_first_fresh_worktree_launch_uses_stable_primary_secret_owner(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    first = tmp_path / "fresh-one"
    second = tmp_path / "fresh-two"
    for worktree in (primary, first, second):
        _copy_launcher_worktree(worktree)
    secret_directory = primary / ".schemii/secrets"
    command_log = tmp_path / "commands.log"
    first_environment = _mock_launcher_environment(
        tmp_path,
        [primary, first, second],
        command_log,
        tls_directory=tmp_path / "tls-one",
        lock_file=tmp_path / "launch-one.lock",
        qa_state_directory=first / ".schemii/testing",
    )
    first_environment.pop("SCHEMII_SECRET_DIRECTORY", None)
    first_result = _run_mocked_launcher(first, first_environment)

    assert first_result.returncode == 0, first_result.stderr
    assert secret_directory.is_dir()
    assert not (first / ".schemii/secrets").exists()
    retained = {path.name: path.read_bytes() for path in secret_directory.iterdir() if path.is_file()}
    assert {"metadata_password", "demo_admin_password", "metadata_encryption_key"}.issubset(retained)

    second_environment = _mock_launcher_environment(
        tmp_path,
        [primary, first, second],
        command_log,
        tls_directory=tmp_path / "tls-two",
        lock_file=tmp_path / "launch-two.lock",
        qa_state_directory=second / ".schemii/testing",
    )
    second_environment.pop("SCHEMII_SECRET_DIRECTORY", None)
    second_result = _run_mocked_launcher(second, second_environment)

    assert second_result.returncode == 0, second_result.stderr
    assert not (second / ".schemii/secrets").exists()
    assert all((secret_directory / name).read_bytes() == value for name, value in retained.items())


def test_startup_script_rejects_invalid_configuration_before_requesting_privilege(tmp_path: Path) -> None:
    environment = _isolated_launcher_environment(tmp_path, SCHEMII_TEST_APP_PORT="80")
    _install_mock_git(tmp_path, environment)

    result = subprocess.run(
        [str(START)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode != 0
    assert "SCHEMII_TEST_APP_PORT must be an integer from 1024 through 65535" in result.stderr
    assert "Refreshing this process" not in result.stdout


def test_startup_script_rejects_empty_database_configuration(tmp_path: Path) -> None:
    environment = _isolated_launcher_environment(tmp_path, SCHEMII_TEST_POSTGRES_DB="")
    _install_mock_git(tmp_path, environment)

    result = subprocess.run(
        [str(START)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode != 0
    assert "SCHEMII_TEST_POSTGRES_DB must not be empty" in result.stderr


def test_pi_runtime_is_default_and_normal_restart_cannot_redirect_it(tmp_path: Path) -> None:
    command_log = tmp_path / "commands.log"
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "printf '%s|pi=%s\\n' \"$*\" \"$SCHEMII_PI_PROTOTYPE_URL\" >> \"$COMMAND_LOG\"\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    environment = _isolated_launcher_environment(
        tmp_path,
        PATH=f"{tmp_path}:{os.environ['PATH']}",
        COMMAND_LOG=str(command_log),
    )
    _install_mock_git(tmp_path, environment)
    enabled = subprocess.run(
        [str(START), "--ai-prototype"], cwd=ROOT, env=environment,
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert enabled.returncode == 0, enabled.stderr
    commands = command_log.read_text(encoding="utf-8")
    assert "--profile ai-prototype-runtime build schemii ai-prototype-runtime|pi=http://ai-prototype-runtime:4097" in commands
    assert "--profile ai-prototype-runtime up --detach --remove-orphans --wait" in commands
    assert "build schemii opencode" not in commands
    assert "run --rm --no-deps -T ai-prototype" not in commands
    command_log.write_text("", encoding="utf-8")
    restarted = subprocess.run(
        [str(START)], cwd=ROOT,
        env={**environment, "SCHEMII_PI_PROTOTYPE_URL": "http://untrusted.invalid"},
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert restarted.returncode == 0, restarted.stderr
    commands = command_log.read_text(encoding="utf-8")
    assert "build schemii ai-prototype-runtime|pi=http://ai-prototype-runtime:4097" in commands
    assert "build schemii opencode" not in commands
    assert "rm --stop --force ai-prototype-runtime|pi=http://ai-prototype-runtime:4097" in commands
    assert "--profile ai-prototype-runtime up --detach --remove-orphans --wait" in commands
    assert "untrusted.invalid" not in commands


def test_startup_script_builds_waits_and_reports_compose_state(tmp_path: Path) -> None:
    command_log = tmp_path / "commands.log"
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "printf 'docker:%s|port=%s|db=%s|user=%s|cert=%s|key=%s\\n' \"$*\" \"$SCHEMII_TEST_APP_PORT\" "
        "\"$SCHEMII_TEST_POSTGRES_DB\" \"$SCHEMII_TEST_POSTGRES_USER\" \"$SCHEMII_TEST_TLS_CERTIFICATE\" "
        "\"$SCHEMII_TEST_TLS_PRIVATE_KEY\" >> \"$COMMAND_LOG\"\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    tls_directory = tmp_path / "tls"
    environment = _isolated_launcher_environment(
        tmp_path,
        PATH=f"{tmp_path}:{os.environ['PATH']}",
        COMMAND_LOG=str(command_log),
        SCHEMII_TEST_APP_PORT="8123",
        SCHEMII_TEST_POSTGRES_DB="startup_db",
        SCHEMII_TEST_POSTGRES_USER="startup_user",
        SCHEMII_TEST_POSTGRES_PASSWORD="local-test-password",
        SCHEMII_STARTUP_TIMEOUT="7",
        SCHEMII_TLS_DIRECTORY=str(tls_directory),
    )
    _install_mock_git(tmp_path, environment)

    result = subprocess.run(
        [str(START)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Schemii is ready at https://localhost:8123/" in result.stdout
    commands = command_log.read_text(encoding="utf-8")
    # Developer-local host routing may add this overlay; it does not alter the
    # build/restart contract being asserted here.
    commands = commands.replace(f" --file {ROOT / '.schemii/compose.local.yaml'}", "")
    assert "docker:compose version" in commands
    assert "docker:info" in commands
    assert (
        f"compose --project-name schemii-test --project-directory {ROOT} --file {ROOT / 'compose.test.yaml'} "
        "--profile ai-prototype-runtime build schemii ai-prototype-runtime"
    ) in commands
    assert (
        f"compose --project-name schemii-test --project-directory {ROOT} --file {ROOT / 'compose.test.yaml'} "
        "--profile ai-prototype-runtime rm --stop --force ingress schemii metadata-bootstrap"
    ) in commands
    assert (
        f"compose --project-name schemii-test --project-directory {ROOT} --file {ROOT / 'compose.test.yaml'} "
        "--profile ai-prototype-runtime up --detach --remove-orphans --wait --wait-timeout 7"
    ) in commands
    assert f"compose --project-name schemii-test --project-directory {ROOT} --file {ROOT / 'compose.test.yaml'} --profile ai-prototype-runtime ps" in commands
    assert "port=8123|db=startup_db|user=startup_user" in commands
    certificate = tls_directory / "localhost.crt"
    private_key = tls_directory / "localhost.key"
    assert f"cert={certificate}|key={private_key}" in commands
    assert stat.S_IMODE(certificate.stat().st_mode) == 0o644
    assert stat.S_IMODE(private_key.stat().st_mode) == 0o640
    metadata_password = tmp_path / "secrets" / "metadata_password"
    metadata_app_password = tmp_path / "secrets" / "metadata_app_password"
    demo_admin_password = tmp_path / "secrets" / "demo_admin_password"
    demo_target_password = tmp_path / "secrets" / "demo_target_password"
    metadata_key = tmp_path / "secrets" / "metadata_encryption_key"
    assert metadata_password.read_text(encoding="utf-8").strip()
    assert demo_target_password.read_text(encoding="utf-8") == "local-test-password\n"
    assert stat.S_IMODE(metadata_password.stat().st_mode) == 0o640
    assert stat.S_IMODE(metadata_app_password.stat().st_mode) == 0o640
    assert stat.S_IMODE(demo_admin_password.stat().st_mode) == 0o640
    assert stat.S_IMODE(demo_target_password.stat().st_mode) == 0o640
    assert stat.S_IMODE(metadata_key.stat().st_mode) == 0o640
    assert not (tmp_path / "primary/.schemii/secrets").exists()
    certificate_details = subprocess.run(
        ["openssl", "x509", "-in", str(certificate), "-noout", "-ext", "subjectAltName"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout
    assert "DNS:localhost" in certificate_details
    assert "IP Address:127.0.0.1" in certificate_details
    basic_constraints = subprocess.run(
        ["openssl", "x509", "-in", str(certificate), "-noout", "-ext", "basicConstraints"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout
    assert "CA:FALSE" in basic_constraints
    purposes = subprocess.run(
        ["openssl", "x509", "-in", str(certificate), "-noout", "-purpose"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout
    assert "SSL server : Yes" in purposes

    certificate_bytes = certificate.read_bytes()
    metadata_password_bytes = metadata_password.read_bytes()
    encryption_key_bytes = metadata_key.read_bytes()
    metadata_app_password_bytes = metadata_app_password.read_bytes()
    demo_admin_password_bytes = demo_admin_password.read_bytes()
    command_log.write_text("", encoding="utf-8")
    second_result = subprocess.run(
        [str(START)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert second_result.returncode == 0, second_result.stderr
    assert "Creating a persistent local HTTPS certificate" not in second_result.stdout
    assert certificate.read_bytes() == certificate_bytes
    assert metadata_password.read_bytes() == metadata_password_bytes
    assert metadata_key.read_bytes() == encryption_key_bytes
    assert metadata_app_password.read_bytes() == metadata_app_password_bytes
    assert demo_admin_password.read_bytes() == demo_admin_password_bytes
    second_commands = command_log.read_text(encoding="utf-8")
    assert "rm --stop --force ingress schemii metadata-bootstrap" in second_commands
    assert "build schemii ai-prototype-runtime" in second_commands
    assert "build schemii opencode" not in second_commands


def test_startup_preserves_a_provisioned_qa_overlay_using_resolved_compose_config(
    tmp_path: Path,
) -> None:
    command_log = tmp_path / "commands.log"
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "printf 'docker:%s|qa_allowed=%s\\n' \"$*\" \"${SCHEMII_QA_EFFECTIVE_ALLOWED_TARGET_HOSTS-}\" >> \"$COMMAND_LOG\"\n"
        "case \"$*\" in\n"
        "  *' config --format json')\n"
        "    printf '%s\\n' '{\"services\":{\"schemii\":{\"environment\":{\"SCHEMII_ALLOWED_TARGET_HOSTS\":\"organization-postgres\"}}}}'\n"
        "    ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    qa_state_directory = tmp_path / "qa-state"
    qa_state_directory.mkdir(mode=0o700)
    qa_state_directory.chmod(0o700)
    qa_registry = qa_state_directory / "registry.json"
    qa_registry.write_text('{"fixture":"synthetic test state"}\n', encoding="utf-8")
    qa_registry_before = qa_registry.read_bytes()

    environment = _isolated_launcher_environment(
        tmp_path,
        PATH=f"{tmp_path}:{os.environ['PATH']}",
        COMMAND_LOG=str(command_log),
        SCHEMII_QA_STATE_DIRECTORY=str(qa_state_directory),
    )
    result = subprocess.run(
        [str(START)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    commands = command_log.read_text(encoding="utf-8")
    command_lines = commands.splitlines()
    assert any(
        f"--file {ROOT / 'testing/compose.yaml'}" in line
        and "config --format json|qa_allowed=" in line
        for line in command_lines
    )
    assert any(
        f"--file {ROOT / 'testing/egress.yaml'}" in line
        and "qa_allowed=organization-postgres,qa-postgres" in line
        for line in command_lines
    )
    assert qa_registry.read_bytes() == qa_registry_before
    writable_credentials = qa_state_directory / "writable-credentials.tsv"
    assert writable_credentials.read_bytes() == b""
    assert stat.S_IMODE(writable_credentials.stat().st_mode) == 0o600
    assert not (qa_state_directory / "database-credentials.tsv").exists()


def test_startup_rejects_a_password_change_that_would_desynchronize_persisted_roles(
    tmp_path: Path,
) -> None:
    command_log = tmp_path / "commands.log"
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "printf 'docker:%s\\n' \"$*\" >> \"$COMMAND_LOG\"\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    environment = _isolated_launcher_environment(
        tmp_path,
        PATH=f"{tmp_path}:{os.environ['PATH']}",
        COMMAND_LOG=str(command_log),
        SCHEMII_TEST_POSTGRES_PASSWORD="replacement-password",
    )
    _install_mock_git(tmp_path, environment)
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "demo_target_password").write_text(
        "original-password\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [str(START)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode != 0
    assert "does not match the persisted demo-target-password secret" in result.stderr
    assert "build schemii" not in command_log.read_text(encoding="utf-8")
    assert "rm --stop" not in command_log.read_text(encoding="utf-8")


def test_startup_rejects_a_concurrent_lifecycle_before_mutating_state(
    tmp_path: Path,
) -> None:
    command_log = tmp_path / "commands.log"
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "printf 'docker:%s\\n' \"$*\" >> \"$COMMAND_LOG\"\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    lock_file = tmp_path / "start.lock"
    lock_file.touch()
    environment = _isolated_launcher_environment(
        tmp_path,
        PATH=f"{tmp_path}:{os.environ['PATH']}",
        COMMAND_LOG=str(command_log),
        SCHEMII_LAUNCH_LOCK_FILE=str(lock_file),
    )
    _install_mock_git(tmp_path, environment)

    with lock_file.open("w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(
            [str(START)],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    assert result.returncode != 0
    assert "another ./start.sh lifecycle operation is already running" in result.stderr
    commands = command_log.read_text(encoding="utf-8")
    assert "build schemii" not in commands
    assert "rm --stop" not in commands


def _demo_launcher_fixture(tmp_path: Path):
    primary, worktree = tmp_path / "primary", tmp_path / "demo-checkout"
    _copy_launcher_worktree(primary)
    _copy_launcher_worktree(worktree)
    command_log = tmp_path / "demo-commands.log"
    environment = _mock_launcher_environment(
        tmp_path,
        [primary, worktree],
        command_log,
        tls_directory=tmp_path / "tls",
        lock_file=tmp_path / "launch.lock",
        qa_state_directory=tmp_path / "qa",
    )
    environment["SCHEMII_SECRET_DIRECTORY"] = str(tmp_path / "secrets")
    helper = worktree / "dev/demo/prepare.py"
    helper.parent.mkdir(parents=True)
    helper.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "mode = sys.argv[1]\n"
        "with Path(os.environ['COMMAND_LOG']).open('a') as log:\n"
        "    log.write('helper:' + ' '.join(sys.argv[1:]) + '\\n')\n"
        "if mode == 'request':\n"
        "    if os.environ.get('FAIL_DEMO_REQUEST'): sys.exit(41)\n"
        "    print(json.dumps({'password': 'PRIVATE_DEMO_STDIN_SENTINEL'}))\n"
        "else:\n"
        "    assert json.load(sys.stdin)['workspace_id'].startswith('ws_')\n"
        "    if os.environ.get('FAIL_DEMO_VERIFY'): sys.exit(43)\n"
        "    print('Verified demo workspace and actual database rows')\n",
        encoding="utf-8",
    )
    (tmp_path / "mock-bin/docker").write_text(
        "#!/bin/sh\n"
        "case \"$*\" in\n"
        "  *' exec -T schemii python -c '*)\n"
        "    printf 'runtime:provision\\n' >> \"$COMMAND_LOG\"\n"
        "    cat >/dev/null\n"
        "    [ -z \"$FAIL_DEMO_PROVISION\" ] || exit 42\n"
        "    printf '{\"workspace_id\":\"ws_0123456789abcdef0123456789abcdef\"}\\n' ;;\n"
        "  *'up --detach --remove-orphans --wait'*)\n"
        "    printf 'runtime:health-reset=%s\\n' \"$SCHEMII_RESET_MIGRATION_DEMO\" >> \"$COMMAND_LOG\"\n"
        "    [ -z \"$FAIL_DEMO_HEALTH\" ] || exit 40 ;;\n"
        "  *) printf 'runtime:%s\\n' \"$*\" >> \"$COMMAND_LOG\" ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    return primary, worktree, environment, command_log


def _run_demo_launcher(worktree: Path, environment: dict[str, str]):
    return subprocess.run(
        [str(worktree / "start.sh"), "--demo"],
        cwd=worktree,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_demo_launch_prepares_after_health_and_verifies_before_ready(tmp_path: Path):
    primary, worktree, environment, commands = _demo_launcher_fixture(tmp_path)
    environment["SCHEMII_RESET_MIGRATION_DEMO"] = "1"
    result = _run_demo_launcher(worktree, environment)

    assert result.returncode == 0, result.stderr
    events = commands.read_text()
    assert events.index("runtime:health-reset=0") < events.index("helper:request")
    assert events.index("runtime:provision") < events.index("helper:verify")
    assert f"--state-directory {primary}/.schemii/demo" in events
    assert "--target-database schemii_test --target-username schemii" in events
    assert "--origin https://localhost:8123" in events
    assert "--preview-origin https://omarchy.taile4f57f.ts.net" in events
    assert "demo-fixture" not in events
    assert "PRIVATE_DEMO_STDIN_SENTINEL" not in result.stdout + result.stderr
    assert result.stdout.index("Verified demo workspace") < result.stdout.index("Schemii is ready")


def test_demo_launch_propagates_each_failure_without_announcing_ready(tmp_path: Path):
    failures = {
        "FAIL_DEMO_HEALTH": "the application service did not become healthy",
        "FAIL_DEMO_REQUEST": "demo account and database preparation failed",
        "FAIL_DEMO_PROVISION": "demo account and database preparation failed",
        "FAIL_DEMO_VERIFY": "demo readiness verification failed",
    }
    for flag, message in failures.items():
        case = tmp_path / flag
        case.mkdir()
        _, worktree, environment, commands = _demo_launcher_fixture(case)
        environment[flag] = "1"
        result = _run_demo_launcher(worktree, environment)

        assert result.returncode != 0
        assert message in result.stderr
        assert "Schemii is ready" not in result.stdout
        assert "PRIVATE_DEMO_STDIN_SENTINEL" not in result.stdout + result.stderr
        if flag == "FAIL_DEMO_HEALTH":
            assert "helper:request" not in commands.read_text()


def test_demo_launch_rejects_relative_private_state_before_runtime(tmp_path: Path):
    _, worktree, environment, commands = _demo_launcher_fixture(tmp_path)
    environment["SCHEMII_DEMO_STATE_DIRECTORY"] = "relative-demo-state"
    result = _run_demo_launcher(worktree, environment)

    assert result.returncode != 0
    assert "demo state directory must be an absolute path" in result.stderr
    assert not commands.exists()


RUNTIME_INSPECT_TEMPLATE = '{"id":{{json .Id}},"name":{{json .Name}},"status":{{json .State.Status}},"exitCode":{{json .State.ExitCode}},"restartCount":{{json .RestartCount}},"restartPolicy":{{json .HostConfig.RestartPolicy.Name}},"restartRetryLimit":{{json .HostConfig.RestartPolicy.MaximumRetryCount}},"composeProject":{{json (index .Config.Labels "com.docker.compose.project")}},"composeService":{{json (index .Config.Labels "com.docker.compose.service")}},"composeWorkingDirectory":{{json (index .Config.Labels "com.docker.compose.project.working_dir")}},"composeConfigFiles":{{json (index .Config.Labels "com.docker.compose.project.config_files")}}}'
RUNTIME_ID = "a1" * 32
RUNTIME_OTHER_ID = "b2" * 32


def _runtime_record(identifier: str = RUNTIME_ID, **changes):
    return {
        "id": identifier, "name": "/foreign-workload-1", "status": "restarting",
        "exitCode": 1, "restartCount": 25001, "restartPolicy": "on-failure",
        "restartRetryLimit": 0, "composeProject": "another-project",
        "composeService": "worker", "composeWorkingDirectory": "/untrusted/foreign checkout",
        "composeConfigFiles": "/untrusted/foreign checkout/compose.yaml", **changes,
    }


def _runtime_inspection_fixture(tmp_path: Path, payloads=None):
    # Deliberately no Compose file, Git metadata, or application configuration.
    worktree = tmp_path / "runtime checkout ' dollar$ semicolon;"
    worktree.mkdir()
    shutil.copy2(START, worktree / "start.sh")
    commands = tmp_path / "diagnostic-commands.jsonl"
    payload_file = tmp_path / "diagnostic-payloads.json"
    payload_file.write_text(json.dumps(payloads or {
        RUNTIME_ID: {"stdout": json.dumps(_runtime_record())},
        RUNTIME_OTHER_ID: {"stdout": json.dumps(_runtime_record(RUNTIME_OTHER_ID))},
    }))
    protected = tmp_path / "untouched"
    protected.mkdir()
    for name in ("secrets", "tls", "qa-state", "git-state"):
        (protected / name).mkdir()
        (protected / name / "retained").write_text("PRIVATE_RETAINED_STATE_SENTINEL")
    reported_path = protected / "reported-path-must-not-open"
    os.mkfifo(reported_path)
    binary = tmp_path / "inspect-bin"
    binary.mkdir()
    for name, target in (("bash", "/bin/bash"), ("dirname", shutil.which("dirname")), ("python3", sys.executable)):
        (binary / name).symlink_to(target)
    docker_source = (
        f"#!{sys.executable}\n"
        "import json,os,re,sys\n"
        "from pathlib import Path\n"
        "args=sys.argv[1:]\n"
        "with Path(os.environ['COMMAND_LOG']).open('a') as log: log.write(json.dumps(['docker',*args])+'\\n')\n"
        "if args==['info']:\n"
        " print('PRIVATE_INFO_STDOUT_SENTINEL')\n"
        " print('PRIVATE_INFO_STDERR_SENTINEL',file=sys.stderr)\n"
        " sys.exit(1 if os.environ.get('FAKE_STALE_GROUP') and not os.environ.get('FAKE_GROUP_REFRESHED') else 0)\n"
        f"expected={RUNTIME_INSPECT_TEMPLATE!r}\n"
        "if len(args)!=5 or args[:3]!=['container','inspect','--format'] or args[3]!=expected or not re.fullmatch('[0-9a-f]{64}',args[4]):\n"
        " print('PRIVATE_FORBIDDEN_DOCKER_COMMAND',file=sys.stderr);sys.exit(93)\n"
        "payload=json.loads(Path(os.environ['FAKE_INSPECT_PAYLOADS']).read_text()).get(args[4],{})\n"
        "print(payload.get('stderr','PRIVATE_INSPECT_STDERR_SENTINEL'),file=sys.stderr)\n"
        "sys.stdout.write(payload.get('stdout','PRIVATE_MISSING_CONTAINER_SENTINEL'))\n"
        "sys.exit(payload.get('returncode',0))\n"
    )
    (binary / "docker").write_text(docker_source)
    (binary / "docker").chmod(0o755)
    tripwire = (
        f"#!{sys.executable}\n"
        "import json,os,sys\n"
        "from pathlib import Path\n"
        "with Path(os.environ['COMMAND_LOG']).open('a') as log: log.write(json.dumps(['FORBIDDEN',Path(sys.argv[0]).name,*sys.argv[1:]])+'\\n')\n"
        "sys.exit(94)\n"
    )
    for name in ("git", "flock", "openssl", "compose", "mkdir", "mktemp", "chmod", "rm", "mv", "touch", "cat", "sed", "readlink", "node", "sudo", "newgrp", "id"):
        (binary / name).write_text(tripwire)
        (binary / name).chmod(0o755)
    environment = {key: value for key, value in os.environ.items() if not key.startswith("SCHEMII_")}
    environment.update(
        PATH=str(binary), COMMAND_LOG=str(commands), FAKE_INSPECT_PAYLOADS=str(payload_file),
        PYTHONDONTWRITEBYTECODE="1", SCHEMII_TEST_APP_PORT="invalid-port",
        SCHEMII_TEST_POSTGRES_DB="", SCHEMII_TEST_POSTGRES_PASSWORD="PRIVATE_ENV_SECRET_SENTINEL",
        SCHEMII_STARTUP_TIMEOUT="invalid-timeout", SCHEMII_QA_LEASE_FD="invalid-fd",
        SCHEMII_SECRET_DIRECTORY=str(protected / "secrets"),
        SCHEMII_TLS_DIRECTORY=str(protected / "tls"),
        SCHEMII_QA_STATE_DIRECTORY=str(protected / "qa-state"),
        SCHEMII_LAUNCH_LOCK_FILE=str(protected / "must-not-create.lock"),
        SCHEMII_START_TIMING_FILE=str(protected / "must-not-create-timing.json"),
        SCHEMII_RUNTIME_RECEIPT=str(protected / "must-not-create-runtime.json"),
    )
    return worktree, environment, commands, protected, reported_path


def _filesystem_snapshot(directory: Path):
    result = {}
    for path in directory.rglob("*"):
        mode = path.lstat().st_mode
        result[str(path.relative_to(directory))] = (
            mode, path.read_bytes() if stat.S_ISREG(mode) else None,
        )
    return result


def _run_runtime_inspection(worktree, environment, identifiers):
    return subprocess.run([str(worktree / "start.sh"), "--inspect-runtime", *identifiers],
        cwd=worktree, env=environment, capture_output=True, text=True, timeout=15, check=False)


@pytest.mark.parametrize("identifiers", [
    [], [RUNTIME_ID, RUNTIME_ID], ["a" * 63], ["a" * 65], ["A" * 64],
    ["container-name"], ["--help"], ["--"], ["--format", RUNTIME_ID],
    [RUNTIME_ID + "\n"], ["$(touch injected)"], [";touch injected"],
    [f"{index:064x}" for index in range(9)],
])
def test_runtime_inspection_rejects_arguments_before_any_runtime_or_preflight(tmp_path, identifiers):
    worktree, environment, commands, protected, _ = _runtime_inspection_fixture(tmp_path)
    before = _filesystem_snapshot(protected)
    result = _run_runtime_inspection(worktree, environment, identifiers)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "Schemii runtime inspection requires 1 through 8 distinct full lowercase container IDs\n"
    assert not commands.exists()
    assert _filesystem_snapshot(protected) == before
    assert sorted(path.name for path in worktree.iterdir()) == ["start.sh"]


def test_runtime_inspection_is_exact_bounded_foreign_metadata_without_side_effects(tmp_path):
    identifiers = [f"{index:064x}" for index in range(1, 9)]
    worktree, environment, commands, protected, reported_path = _runtime_inspection_fixture(tmp_path)
    records = [_runtime_record(identifier, status="exited" if index % 2 else "restarting",
        composeWorkingDirectory=str(reported_path), composeConfigFiles=str(reported_path))
        for index, identifier in enumerate(identifiers)]
    Path(environment["FAKE_INSPECT_PAYLOADS"]).write_text(json.dumps({
        record["id"]: {"stdout": json.dumps(record)} for record in records
    }))
    before = _filesystem_snapshot(protected)
    result = _run_runtime_inspection(worktree, environment, identifiers)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert [json.loads(line) for line in result.stdout.splitlines()] == records
    events = [json.loads(line) for line in commands.read_text().splitlines()]
    assert events == [["docker", "info"], *[
        ["docker", "container", "inspect", "--format", RUNTIME_INSPECT_TEMPLATE, identifier]
        for identifier in identifiers]]
    assert _filesystem_snapshot(protected) == before
    assert sorted(path.name for path in worktree.iterdir()) == ["start.sh"]
    assert "PRIVATE_" not in result.stdout + result.stderr


@pytest.mark.parametrize("changes,expected", [
    ({key: "" for key in ("composeProject", "composeService", "composeWorkingDirectory", "composeConfigFiles")},
     {key: None for key in ("composeProject", "composeService", "composeWorkingDirectory", "composeConfigFiles")}),
    ({"composeProject": None, "composeService": None, "composeWorkingDirectory": None, "composeConfigFiles": None},
     {"composeProject": None, "composeService": None, "composeWorkingDirectory": None, "composeConfigFiles": None}),
    ({"composeProject": "foreign-project", "composeService": "", "composeWorkingDirectory": "/tmp/a\\b\"c\nline", "composeConfigFiles": "relative.yaml, /tmp/\u2603.yaml"},
     {"composeProject": "foreign-project", "composeService": None, "composeWorkingDirectory": "/tmp/a\\b\"c\nline", "composeConfigFiles": "relative.yaml, /tmp/\u2603.yaml"}),
])
def test_runtime_inspection_keeps_unknown_ownership_and_json_escapes_opaque_paths(tmp_path, changes, expected):
    record = _runtime_record(**changes)
    worktree, environment, _, protected, _ = _runtime_inspection_fixture(tmp_path, {
        RUNTIME_ID: {"stdout": json.dumps(record)},
    })
    before = _filesystem_snapshot(protected)
    result = _run_runtime_inspection(worktree, environment, [RUNTIME_ID])
    assert result.returncode == 0, result.stderr
    assert len(result.stdout.splitlines()) == 1
    assert json.loads(result.stdout) == {**record, **expected}
    assert _filesystem_snapshot(protected) == before


@pytest.mark.parametrize("payload", [
    {"stdout": "PRIVATE_MALFORMED_JSON_SENTINEL"},
    {"stdout": "[]"}, {"stdout": "null"},
    {"stdout": json.dumps(_runtime_record(RUNTIME_ID))},
    {"stdout": json.dumps(_runtime_record("a" * 12))},
    {"stdout": json.dumps(_runtime_record(RUNTIME_OTHER_ID, Env=["PRIVATE_ENV_METADATA_SENTINEL"]))},
    {"stdout": json.dumps(_runtime_record(RUNTIME_OTHER_ID, Args=["PRIVATE_ARGS_SENTINEL"]))},
    {"stdout": json.dumps(_runtime_record(RUNTIME_OTHER_ID, Cmd=["PRIVATE_CMD_SENTINEL"]))},
    {"stdout": json.dumps(_runtime_record(RUNTIME_OTHER_ID, Mounts=["PRIVATE_MOUNT_SENTINEL"]))},
    {"stdout": json.dumps(_runtime_record(RUNTIME_OTHER_ID, labels={"other": "PRIVATE_ALL_LABELS_SENTINEL"}))},
    {"stdout": json.dumps(_runtime_record(RUNTIME_OTHER_ID, exitCode=True))},
    {"stdout": json.dumps(_runtime_record(RUNTIME_OTHER_ID, exitCode="1"))},
    {"stdout": json.dumps(_runtime_record(RUNTIME_OTHER_ID, restartCount=-1))},
    {"stdout": json.dumps(_runtime_record(RUNTIME_OTHER_ID, restartRetryLimit=2**64))},
    {"stdout": json.dumps(_runtime_record(RUNTIME_OTHER_ID, status="PRIVATE_INVALID_STATUS_SENTINEL"))},
    {"stdout": json.dumps(_runtime_record(RUNTIME_OTHER_ID, restartPolicy="PRIVATE_POLICY_SENTINEL"))},
    {"stdout": json.dumps(_runtime_record(RUNTIME_OTHER_ID, name=None))},
    {"stdout": json.dumps(_runtime_record(RUNTIME_OTHER_ID, composeProject={"token": "PRIVATE_LABEL_SENTINEL"}))},
    {"stdout": json.dumps({key: value for key, value in _runtime_record(RUNTIME_OTHER_ID).items() if key != "restartCount"})},
    {"stdout": json.dumps(_runtime_record(RUNTIME_OTHER_ID))[:-1] + ',"id":"' + RUNTIME_OTHER_ID + '"}'},
    {"stdout": json.dumps(_runtime_record(RUNTIME_OTHER_ID, composeConfigFiles="PRIVATE_OVERSIZE_SENTINEL" * 1000))},
    {"stdout": json.dumps(_runtime_record(RUNTIME_OTHER_ID)), "returncode": 1, "stderr": "PRIVATE_DAEMON_ERROR_SENTINEL"},
])
def test_runtime_inspection_fails_closed_without_daemon_errors_or_partial_stdout(tmp_path, payload):
    worktree, environment, commands, protected, _ = _runtime_inspection_fixture(tmp_path, {
        RUNTIME_ID: {"stdout": json.dumps(_runtime_record())}, RUNTIME_OTHER_ID: payload,
    })
    before = _filesystem_snapshot(protected)
    result = _run_runtime_inspection(worktree, environment, [RUNTIME_ID, RUNTIME_OTHER_ID])
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == f"Schemii runtime inspection failed for container {RUNTIME_OTHER_ID}\n"
    assert "PRIVATE_" not in result.stderr
    events = [json.loads(line) for line in commands.read_text().splitlines()]
    assert all(event[0] == "docker" for event in events)
    assert [event[-1] for event in events[1:]] == [RUNTIME_ID, RUNTIME_OTHER_ID]
    assert _filesystem_snapshot(protected) == before


def test_runtime_inspection_stale_group_genuinely_reexecutes_with_all_original_arguments(tmp_path):
    import shlex

    worktree, environment, commands, protected, _ = _runtime_inspection_fixture(tmp_path)
    binary = Path(environment["PATH"])
    (binary / "id").write_text(
        "#!/bin/bash\n"
        'case "$*" in\n'
        '  -nG) if [[ -n "${FAKE_GROUP_REFRESHED-}" ]]; then printf "users docker\\n"; else printf "users\\n"; fi ;;\n'
        '  -un) printf "runtime-test-account\\n" ;;\n'
        '  "-nG runtime-test-account") printf "users docker\\n" ;;\n'
        '  *) exit 95 ;;\n'
        'esac\n'
    )
    (binary / "newgrp").write_text(
        f"#!{sys.executable}\n"
        "import json,os,sys\n"
        "from pathlib import Path\n"
        "assert sys.argv[1:3]==['docker','-c'] and len(sys.argv)==4\n"
        "with Path(os.environ['COMMAND_LOG']).open('a') as log: log.write(json.dumps(['newgrp',*sys.argv[1:]])+'\\n')\n"
        "environment={**os.environ,'FAKE_GROUP_REFRESHED':'1'}\n"
        "os.execve('/bin/bash',['bash','-c',sys.argv[3]],environment)\n"
    )
    environment["FAKE_STALE_GROUP"] = "1"
    before = _filesystem_snapshot(protected)
    result = _run_runtime_inspection(worktree, environment, [RUNTIME_ID, RUNTIME_OTHER_ID])
    assert result.returncode == 0, result.stderr
    assert result.stderr == "Refreshing this process with the account's Docker group membership...\n"
    assert [json.loads(line)["id"] for line in result.stdout.splitlines()] == [RUNTIME_ID, RUNTIME_OTHER_ID]
    events = [json.loads(line) for line in commands.read_text().splitlines()]
    assert [event[:2] for event in events] == [
        ["docker", "info"], ["newgrp", "docker"], ["docker", "info"],
        ["docker", "container"], ["docker", "container"],
    ]
    assert shlex.split(events[1][3]) == ["exec", str(worktree / "start.sh"), "--inspect-runtime", RUNTIME_ID, RUNTIME_OTHER_ID]
    assert _filesystem_snapshot(protected) == before
    assert "PRIVATE_" not in result.stdout + result.stderr


@pytest.mark.parametrize("failure", ["missing-docker", "no-group-access", "missing-newgrp"])
def test_runtime_inspection_runtime_access_failure_is_sanitized_without_side_effects(tmp_path, failure):
    worktree, environment, commands, protected, _ = _runtime_inspection_fixture(tmp_path)
    binary = Path(environment["PATH"])
    if failure == "missing-docker":
        (binary / "docker").unlink()
    else:
        environment["FAKE_STALE_GROUP"] = "1"
        groups = "users docker" if failure == "missing-newgrp" else "users"
        (binary / "id").write_text(
            '#!/bin/bash\n'
            'case "$*" in\n'
            '  -nG) printf "users\\n" ;;\n'
            '  -un) printf "runtime-test-account\\n" ;;\n'
            f'  "-nG runtime-test-account") printf "{groups}\\n" ;;\n'
            '  *) exit 95 ;;\n'
            'esac\n'
        )
        if failure == "missing-newgrp":
            (binary / "newgrp").unlink()
    before = _filesystem_snapshot(protected)
    result = _run_runtime_inspection(worktree, environment, [RUNTIME_ID])
    assert result.returncode == 1
    assert result.stdout == ""
    assert "PRIVATE_" not in result.stderr
    expected = {
        "missing-docker": "Docker is not installed or is not on PATH",
        "no-group-access": "Docker is unavailable; confirm that the daemon is running and the current account belongs to the docker group",
        "missing-newgrp": "the account has Docker access, but this session is stale and newgrp is unavailable; start a new login session",
    }
    assert result.stderr == f"Schemii startup error: {expected[failure]}\n"
    assert _filesystem_snapshot(protected) == before
    if commands.exists():
        assert [json.loads(line) for line in commands.read_text().splitlines()] == [["docker", "info"]]
