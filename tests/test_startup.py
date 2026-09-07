from __future__ import annotations

import fcntl
import os
from pathlib import Path
import stat
import subprocess

ROOT = Path(__file__).resolve().parents[1]
START = ROOT / "start.sh"


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
    assert 'SCHEMII_SECRET_DIRECTORY="${SCHEMII_SECRET_DIRECTORY-${ROOT_DIR}/.schemii/secrets}"' in source
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


def test_startup_script_rejects_invalid_configuration_before_requesting_privilege() -> None:
    environment = {**os.environ, "SCHEMII_TEST_APP_PORT": "80"}

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


def test_startup_script_rejects_empty_database_configuration() -> None:
    environment = {**os.environ, "SCHEMII_TEST_POSTGRES_DB": ""}

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
    environment = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "COMMAND_LOG": str(command_log),
        "SCHEMII_TLS_DIRECTORY": str(tmp_path / "tls"),
        "SCHEMII_SECRET_DIRECTORY": str(tmp_path / "secrets"),
        "SCHEMII_LAUNCH_LOCK_FILE": str(tmp_path / "start.lock"),
    }
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
    environment = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "COMMAND_LOG": str(command_log),
        "SCHEMII_TEST_APP_PORT": "8123",
        "SCHEMII_TEST_POSTGRES_DB": "startup_db",
        "SCHEMII_TEST_POSTGRES_USER": "startup_user",
        "SCHEMII_TEST_POSTGRES_PASSWORD": "local-test-password",
        "SCHEMII_STARTUP_TIMEOUT": "7",
        "SCHEMII_TLS_DIRECTORY": str(tls_directory),
        "SCHEMII_SECRET_DIRECTORY": str(tmp_path / "secrets"),
        "SCHEMII_LAUNCH_LOCK_FILE": str(tmp_path / "start.lock"),
    }

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
    environment = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "COMMAND_LOG": str(command_log),
        "SCHEMII_TEST_POSTGRES_PASSWORD": "replacement-password",
        "SCHEMII_TLS_DIRECTORY": str(tmp_path / "tls"),
        "SCHEMII_SECRET_DIRECTORY": str(tmp_path / "secrets"),
        "SCHEMII_LAUNCH_LOCK_FILE": str(tmp_path / "start.lock"),
    }
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
    environment = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "COMMAND_LOG": str(command_log),
        "SCHEMII_TLS_DIRECTORY": str(tmp_path / "tls"),
        "SCHEMII_SECRET_DIRECTORY": str(tmp_path / "secrets"),
        "SCHEMII_LAUNCH_LOCK_FILE": str(lock_file),
    }

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
