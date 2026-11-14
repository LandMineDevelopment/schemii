"""Mutate real adapter source; no Docker, database, phone or operational state."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from scripts.check_runtime_parity import ParityError, ROOT, check
from dev.runtime_manifest import manifest


@pytest.fixture
def source(tmp_path):
    for name in (
        "Dockerfile",
        "compose.test.yaml",
        "pyproject.toml",
        "constraints.docker.txt",
    ):
        shutil.copyfile(ROOT / name, tmp_path / name)
    for name in ("src", "dev", "ai/prototype"):
        shutil.copytree(
            ROOT / name,
            tmp_path / name,
            ignore=shutil.ignore_patterns(
                "__pycache__", "node_modules", ".schemii", "*.pyc"
            ),
        )
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(
        ROOT / "scripts/build-android-release.py",
        tmp_path / "scripts/build-android-release.py",
    )
    return tmp_path


def change(root, name, old, new):
    path = root / name
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


def test_current_adapters_have_one_runtime_contract():
    assert check()["status"] == "passed"


@pytest.mark.parametrize(
    ("path", "old", "new", "failure"),
    [
        (
            "compose.test.yaml",
            "  ai-prototype-runtime:\n",
            "  removed-pi:\n",
            "service inventory",
        ),
        (
            "compose.test.yaml",
            "\nvolumes:\n",
            "\n  surprise-service:\n    image: surprise\n\nvolumes:\n",
            "service inventory",
        ),
        (
            "compose.test.yaml",
            'SCHEMII_METADATA_MAXIMUM_CONNECTIONS: "16"',
            'SCHEMII_METADATA_MAXIMUM_CONNECTIONS: "4"',
            "MAXIMUM_CONNECTIONS",
        ),
        (
            "dev/android/runtime.py",
            'env.update(contract["shared_environment"])',
            'env.update(contract["shared_environment"])\n        env["SCHEMII_METADATA_MAXIMUM_CONNECTIONS"] = "4"',
            "MAXIMUM_CONNECTIONS",
        ),
        (
            "dev/android/runtime.py",
            '"pi",\n                        [str(NODE)',
            '"omitted-pi",\n                        [str(NODE)',
            "service not launched",
        ),
        (
            "dev/android/runtime.py",
            "max_connections = 24",
            "max_connections = 16",
            "PostgreSQL budget",
        ),
        ("dev/android/runtime.py", "fsync = on", "fsync = off", "durability/security"),
        (
            "dev/android/runtime.py",
            "port=5433 dbname=schemii",
            "port=5434 dbname=schemii",
            "metadata endpoint",
        ),
        ("dev/android/runtime.py", '"localhost,127.0.0.1,::1"', '""', "fence alias"),
        (
            "compose.test.yaml",
            '"max_connections=100"',
            '"max_connections=16"',
            "connection budget",
        ),
        (
            "compose.test.yaml",
            "      metadata-bootstrap:\n        condition: service_completed_successfully",
            "      metadata-bootstrap:\n        condition: service_started",
            "bootstrap dependency",
        ),
        ("Dockerfile", "/api/v1/readiness", "/api/v1/health", "readiness"),
        (
            "constraints.docker.txt",
            "psycopg==3.3.4",
            "psycopg==3.3.3",
            "dependency input",
        ),
        ("ai/prototype/package.json", "0.87.1", "0.87.2", "dependency input"),
        (
            "ai/prototype/package-lock.json",
            '"lockfileVersion": 3',
            '"lockfileVersion": 2',
            "dependency input",
        ),
        (
            "dev/android/tailnet-proxy/go.sum",
            "tailscale.com",
            "changed.example",
            "dependency input",
        ),
        (
            "Dockerfile",
            "python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a",
            "python:3.13-slim",
            "Python image",
        ),
        (
            "dev/android/install-guest.sh",
            "NODE_VERSION=22.22.2",
            "NODE_VERSION=22.22.3",
            "Node identity",
        ),
        (
            "dev/android/host.py",
            "            self.proxy_command(),",
            '            self.guest_command("run"),',
            "proxy must be launched directly",
        ),
        (
            "dev/android/install-guest.sh",
            "--only-binary=:all:",
            "--prefer-binary",
            "shared dependency",
        ),
        (
            "src/schemii/common/metadata/config.py",
            "maximum_connections: int = 16",
            "maximum_connections: int = 4",
            "metadata default",
        ),
    ],
)
def test_real_adapter_drift_fails(source, path, old, new, failure):
    change(source, path, old, new)
    with pytest.raises(ParityError, match=failure):
        check(source)


@pytest.mark.parametrize(
    ("section", "key", "value", "failure"),
    [
        ("android", "metadata_reserve", 0, "headroom"),
        ("android", "postgres_max_connections", 20, "headroom"),
        ("android", "reason", "", "exception reason"),
    ],
)
def test_contract_cannot_hide_lost_recovery_headroom(
    source, section, key, value, failure
):
    path = source / "dev/runtime-contract.json"
    contract = json.loads(path.read_text())
    contract["platforms"][section][key] = value
    path.write_text(json.dumps(contract))
    with pytest.raises(ParityError, match=failure):
        check(source)


def test_desktop_target_admission_change_cannot_silently_skip_phone_rewrite(source):
    change(source, "dev/schemii.toml", "maximum_total = 20", "maximum_total = 19")
    with pytest.raises(ParityError, match="target admission"):
        check(source)


@pytest.mark.parametrize("name", ["dev/android/termux.sh", "dev/android/host.py"])
def test_required_proot_ipc_mode_cannot_drift(source, name):
    change(
        source,
        name,
        "PROOT_DONT_SHARE_LIBANDROID_SHMEM=1",
        "PROOT_DONT_SHARE_LIBANDROID_SHMEM=0",
    )
    with pytest.raises(ParityError, match="IPC startup guard"):
        check(source)


def test_backup_cannot_drop_original_encryption_key(source):
    change(source, "dev/android/recovery.py", '    "metadata_encryption_key",', "")
    with pytest.raises(ParityError, match="original secret backup"):
        check(source)


def test_release_cannot_omit_shared_contract(source):
    # The fixture's builder is source, not a mocked inventory.
    change(
        source,
        "scripts/build-android-release.py",
        '    "dev/runtime-contract.json",',
        "",
    )
    with pytest.raises(ParityError, match="release contract missing"):
        check(source)


def test_docker_provenance_binds_source_and_dependencies_not_private_state(tmp_path):
    first = manifest(ROOT, "a" * 40)
    assert first["runtime_contract"]["schema"] == 1
    for name in (
        "constraints.docker.txt",
        "ai/prototype/package-lock.json",
        "dev/runtime-contract.json",
        "src/schemii/main.py",
    ):
        assert len(first["source_files"][name]) == 64
    assert not any(
        set(Path(name).parts)
        & {".schemii", "node_modules", "secrets", "credentials", "backups"}
        for name in first["source_files"]
    )
    assert manifest(ROOT, "a" * 40 + "+dirty")["commit"].endswith("+dirty")
    with pytest.raises(ValueError):
        manifest(tmp_path, "not-a-source-id")


def test_full_ci_runs_parity_before_expensive_layers():
    from scripts.ci.test_selection import commands

    assert commands("full", "HEAD")[0] == ["python", "scripts/check_runtime_parity.py"]
