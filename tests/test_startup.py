from __future__ import annotations

import base64
import fcntl
import os
from pathlib import Path
import shutil
import stat
import subprocess

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
