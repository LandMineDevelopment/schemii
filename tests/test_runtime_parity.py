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
        ".dockerignore",
        "Dockerfile",
        "compose.test.yaml",
        "pyproject.toml",
        "constraints.docker.txt",
        "README.md",
        "LICENSE",
        "start.sh",
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
    ("old", "new"),
    [
        ('self._cluster("target", 5434)', 'self._cluster("target", 5433)'),
        ('self._cluster("metadata", 5433)', 'self._cluster("metadata", 5434)'),
        ('self._cluster("target", 5434)', 'self._cluster("metadata", 5434)'),
        ('self._cluster("target", 5434)', 'self._cluster("unexpected", 5434)'),
        ('self._cluster("target", 5434)', "pass  # target startup omitted"),
        (
            'self._cluster("target", 5434)',
            'self._cluster("target", 5434); self._cluster("target", 5434)',
        ),
    ],
)
def test_actual_cluster_startup_endpoints_cannot_drift(source, old, new):
    change(source, "dev/android/runtime.py", old, new)
    with pytest.raises(ParityError, match="PostgreSQL startup cluster"):
        check(source)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('self._bootstrap("target", 5434,', 'self._bootstrap("target", 5433,'),
        ('self._bootstrap("target", 5434,', 'self._bootstrap("metadata", 5434,'),
        (
            'self._bootstrap("target", 5434, "schemii_target", "target_password")',
            "pass  # target bootstrap omitted",
        ),
        (
            'self._bootstrap("target", 5434, "schemii_target", "target_password")',
            'self._bootstrap("target", 5434, "schemii_target", "target_password"); '
            'self._bootstrap("target", 5434, "schemii_target", "target_password")',
        ),
    ],
)
def test_bootstrap_startup_endpoints_cannot_drift(source, old, new):
    change(source, "dev/android/runtime.py", old, new)
    with pytest.raises(ParityError, match="PostgreSQL startup cluster"):
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


def stage_docker_builder(source, destination):
    """Stage actual first-stage COPY inputs; never invoke Docker or a server."""
    import shlex
    import fnmatch

    # Exercise the actual recursive basename/suffix rules used for private
    # context inputs. This is deliberately not a general Dockerignore parser.
    recursive = [
        rule.removeprefix("**/")
        for rule in (source / ".dockerignore").read_text().splitlines()
        if rule.startswith("**/")
    ]

    def ignored(directory, names):
        return [
            name
            for name in names
            if name == "__pycache__"
            or name.endswith(".egg-info")
            or any(fnmatch.fnmatchcase(name, rule) for rule in recursive)
        ]

    destination.mkdir()
    for line in (source / "Dockerfile").read_text().split("\nFROM ")[0].splitlines():
        if not line.startswith("COPY "):
            continue
        tokens = shlex.split(line)[1:]
        target = destination / tokens[-1]
        for pattern in tokens[:-1]:
            found = list(source.glob(pattern))
            assert found, pattern
            for path in found:
                if path.is_dir():
                    shutil.copytree(
                        path,
                        target,
                        dirs_exist_ok=True,
                        ignore=ignored,
                    )
                else:
                    target.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, target / path.name)
    return destination


def test_real_docker_copy_inventory_binds_wheel_inputs_and_excludes_private_ai_state(
    source, tmp_path
):
    for name in (
        "credentials.json",
        "password.txt",
        ".env",
        "private/secret.json",
        "secrets/key.json",
    ):
        private = source / "ai/prototype" / name
        private.parent.mkdir(parents=True, exist_ok=True)
        private.write_text("synthetic-private-marker")
    for suffix in (".p12", ".pfx", ".pgpass", ".db"):
        asset = source / "src/schemii/common/web/assets" / ("test-owned" + suffix)
        asset.write_text("synthetic-private-marker")
    stage = stage_docker_builder(source, tmp_path / "docker-stage")
    record = manifest(stage, "a" * 40)
    actual = {str(p.relative_to(stage)) for p in stage.rglob("*") if p.is_file()}
    assert set(record["source_files"]) == actual
    assert not any(
        "synthetic-private-marker" in p.read_text(errors="replace")
        for p in stage.rglob("*")
        if p.is_file()
    )
    for name in ("README.md", "LICENSE"):
        before = record["source_files"][name]
        (stage / name).write_text(
            (stage / name).read_text() + "\nchanged test fixture\n"
        )
        assert manifest(stage, "a" * 40)["source_files"][name] != before
    # The source-checkout helper also excludes known private names even when
    # invoked before context assembly; it does not depend on Docker for hygiene.
    assert not any(
        name.endswith("credentials.json")
        for name in manifest(source, "a" * 40)["source_files"]
    )


@pytest.mark.parametrize(
    ("status", "failure", "expected"),
    [
        ("", False, "a" * 40),
        (" M source", False, "a" * 40 + "+dirty"),
        ("", True, "unknown"),
    ],
)
def test_launcher_provenance_needs_no_host_python_and_does_not_claim_clean_after_failed_git_status(
    tmp_path, status, failure, expected
):
    import os
    import subprocess

    git = tmp_path / "git"
    git.write_text(
        '#!/bin/sh\ncase "$*" in *rev-parse*) printf "%s" "$FIXTURE_COMMIT";; *status*) printf "%s" "$FIXTURE_STATUS"; exit "$FIXTURE_FAILURE";; esac\n'
    )
    git.chmod(0o700)
    source = (ROOT / "start.sh").read_text()
    block = source.split(
        "# Bind each newly built image to its source; content hashes also cover dirty builds.\n",
        1,
    )[1].split("printf 'Building the current Schemii application image", 1)[0]
    assert "python" not in block
    result = subprocess.run(
        ["bash", "-c", block + '\nprintf "%s" "$SCHEMII_SOURCE_COMMIT"'],
        capture_output=True,
        text=True,
        check=True,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:" + os.environ["PATH"],
            "ROOT_DIR": str(tmp_path),
            "FIXTURE_COMMIT": "a" * 40,
            "FIXTURE_STATUS": status,
            "FIXTURE_FAILURE": str(int(failure)),
        },
    )
    assert result.stdout == expected


@pytest.mark.parametrize("suffix", [".p12", ".pfx", ".pgpass", ".db"])
def test_real_docker_context_cannot_lose_private_asset_exclusion(source, suffix):
    change(source, ".dockerignore", "**/*" + suffix + "\n", "")
    with pytest.raises(ParityError, match="private suffix exclusion missing"):
        check(source)


@pytest.fixture
def apk_package(source, tmp_path):
    """An actual committed shared source export, never an APK acceptance fixture."""
    import subprocess

    from scripts.check_runtime_parity import apk_source_manifest

    (source / "docs").mkdir()
    shutil.copyfile(
        ROOT / "docs/android-release.md", source / "docs/android-release.md"
    )
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Parity Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "Committed parity fixture",
        ],
        check=True,
    )
    commit = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    record = apk_source_manifest(source, commit)
    payload = tmp_path / "payload"
    for name in record["source_files"]:
        path = payload / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, path)
    return source, payload, record, commit


def test_apk_shared_source_payload_compares_actual_bytes(apk_package):
    from scripts.check_runtime_parity import check_apk_sources

    root, payload, record, commit = apk_package
    result = check_apk_sources(root, payload, record, commit)
    assert result["status"] == "passed"
    assert result["apk_runtime_acceptance"] == "pending"
    assert result["shared_file_count"] == len(record["source_files"])
    assert not any(
        name.startswith("dev/android/runtime") for name in record["source_files"]
    )


@pytest.mark.parametrize(
    "boundary", ["api", "auth", "web", "migration", "pi", "postgres", "dependency"]
)
def test_apk_cannot_drop_real_shared_payload_even_with_rewritten_manifest(
    apk_package, boundary
):
    from scripts.check_runtime_parity import check_apk_sources

    root, payload, record, commit = apk_package
    selectors = {
        "api": lambda n: n == "src/schemii/main.py",
        "auth": lambda n: n == "src/schemii/common/auth/routes.py",
        "web": lambda n: n.endswith("web/index.html"),
        "migration": lambda n: "/migrations/" in n and n.endswith(".sql"),
        "pi": lambda n: n == "ai/prototype/server.js",
        "postgres": lambda n: n == "dev/postgres/metadata-bootstrap.sh",
        "dependency": lambda n: n == "ai/prototype/package-lock.json",
    }
    name = next(n for n in record["source_files"] if selectors[boundary](n))
    (payload / name).unlink()
    with pytest.raises(ParityError, match="inventory/content"):
        check_apk_sources(root, payload, record, commit)
    del record["source_files"][name]
    with pytest.raises(ParityError, match="manifest drift"):
        check_apk_sources(root, payload, record, commit)


def test_apk_rehashing_stale_payload_does_not_establish_parity(apk_package):
    import hashlib

    from scripts.check_runtime_parity import check_apk_sources

    root, payload, record, commit = apk_package
    name = "src/schemii/common/auth/routes.py"
    (payload / name).write_text("# stale or forked authentication\n")
    with pytest.raises(ParityError, match="inventory/content"):
        check_apk_sources(root, payload, record, commit)
    record["source_files"][name] = hashlib.sha256(
        (payload / name).read_bytes()
    ).hexdigest()
    with pytest.raises(ParityError, match="manifest drift"):
        check_apk_sources(root, payload, record, commit)


@pytest.mark.parametrize(
    "path",
    ["secrets/password", "src/schemii/stale_api.py", "ai/prototype/server-copy.js"],
)
def test_apk_payload_cannot_add_private_or_forked_files(apk_package, path):
    from scripts.check_runtime_parity import check_apk_sources

    root, payload, record, commit = apk_package
    extra = payload / path
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text("fixture-only")
    with pytest.raises(ParityError, match="unexpected payload"):
        check_apk_sources(root, payload, record, commit)


@pytest.mark.parametrize("directory", [False, True])
def test_apk_payload_cannot_follow_symlinks(apk_package, tmp_path, directory):
    from scripts.check_runtime_parity import check_apk_sources

    root, payload, record, commit = apk_package
    path = payload / ("src" if directory else "src/schemii/main.py")
    outside = tmp_path / "outside"
    path.rename(outside)
    path.symlink_to(outside, target_is_directory=directory)
    with pytest.raises(ParityError, match="links"):
        check_apk_sources(root, payload, record, commit)


def test_apk_source_identity_rejects_dirty_source_and_stale_commit(apk_package):
    from scripts.check_runtime_parity import apk_source_manifest, check_apk_sources

    root, payload, record, commit = apk_package
    record["commit"] = "a" * 40
    with pytest.raises(ParityError, match="manifest drift"):
        check_apk_sources(root, payload, record, commit)
    (root / "src/schemii/main.py").write_text("# uncommitted feature\n")
    with pytest.raises(ParityError, match="Commit Android source"):
        apk_source_manifest(root, commit)
    with pytest.raises(ParityError, match="source commit required"):
        apk_source_manifest(root, "unknown")


def test_apk_missing_payload_and_partial_cli_are_failures(apk_package, tmp_path):
    import subprocess
    import sys

    from scripts.check_runtime_parity import check_apk_sources

    root, payload, record, commit = apk_package
    with pytest.raises(ParityError, match="payload missing"):
        check_apk_sources(root, tmp_path / "absent", record, commit)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/check_runtime_parity.py"),
            "--apk-source-manifest",
            str(tmp_path / "absent.json"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "requires manifest, shared payload and source commit" in result.stderr


@pytest.mark.parametrize(
    ("path", "old", "new", "failure"),
    [
        (
            "dev/android/runtime.py",
            "fsync = on\\n",
            "fsync = on\\nfsync = off\\n",
            "duplicate PostgreSQL setting",
        ),
        (
            "dev/android/runtime.py",
            "listen_addresses = '127.0.0.1'",
            "listen_addresses = '*'",
            "durability/security",
        ),
        (
            "dev/android/runtime.py",
            'f"port = {port}\\n"',
            'f"port = {5433}\\n"',
            "PostgreSQL endpoint",
        ),
        (
            "dev/android/runtime.py",
            'str(PG_BIN / "postgres"), "-D", str(directory)',
            'str(PG_BIN / "postgres"), "-D", str(directory), "-F"',
            "cluster launch",
        ),
        (
            "dev/android/runtime.py",
            "LOGIN NOSUPERUSER NOCREATEDB",
            "LOGIN SUPERUSER NOCREATEDB",
            "role policy",
        ),
        (
            "dev/postgres/metadata-bootstrap.sh",
            "LOGIN NOSUPERUSER NOCREATEDB",
            "LOGIN SUPERUSER NOCREATEDB",
            "role policy",
        ),
        (
            "dev/postgres/demo-bootstrap.sh",
            "NOINHERIT NOREPLICATION",
            "INHERIT REPLICATION",
            "role policy",
        ),
    ],
)
def test_generated_cluster_and_bootstrap_policy_cannot_drift(
    source, path, old, new, failure
):
    change(source, path, old, new)
    with pytest.raises(ParityError, match=failure):
        check(source)


def test_contract_cannot_hide_weakened_postgres_policy(source):
    path = source / "dev/runtime-contract.json"
    contract = json.loads(path.read_text())
    contract["postgresql_policy"]["settings"]["fsync"] = "off"
    path.write_text(json.dumps(contract))
    with pytest.raises(ParityError, match="safety policy"):
        check(source)


def test_apk_payload_rejects_linked_ancestor(apk_package, tmp_path):
    from scripts.check_runtime_parity import check_apk_sources

    root, payload, record, commit = apk_package
    alias = tmp_path / "linked-parent"
    alias.symlink_to(payload.parent, target_is_directory=True)
    with pytest.raises(ParityError, match="payload missing or linked"):
        check_apk_sources(root, alias / payload.name, record, commit)


def test_apk_record_cannot_add_unreviewed_exception(apk_package):
    from scripts.check_runtime_parity import check_apk_sources

    root, payload, record, commit = apk_package
    record["exceptions"] = {"omitted_services": "temporary"}
    with pytest.raises(ParityError, match="manifest drift"):
        check_apk_sources(root, payload, record, commit)


@pytest.fixture
def apk_runtime_package(apk_package, tmp_path):
    from scripts.check_runtime_parity import apk_runtime_source_manifest

    root, shared, original, commit = apk_package
    record = apk_runtime_source_manifest(root, commit)
    assert record["shared_source"] == original
    assert not set(record["adapter_files"]) & set(original["source_files"])
    adapters = tmp_path / "runtime-support"
    for name in record["adapter_files"]:
        target = adapters / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)
    return root, shared, adapters, record, commit


def test_packaged_runtime_is_exact_separate_composition(apk_runtime_package):
    from scripts.check_runtime_parity import (
        APK_RUNTIME_SUPPORT,
        check_apk_runtime_sources,
    )

    root, shared, adapters, record, commit = apk_runtime_package
    assert set(record["adapter_files"]) == APK_RUNTIME_SUPPORT
    assert (
        set(record["source_files"])
        == set(record["shared_source"]["source_files"]) | APK_RUNTIME_SUPPORT
    )
    result = check_apk_runtime_sources(root, shared, adapters, record, commit)
    assert result["status"] == "passed"
    assert result["adapter_file_count"] == 4
    assert result["execution"] == "NOT_RUN"
    assert result["readiness"] == "PENDING"


@pytest.mark.parametrize(
    "name",
    [
        "dev/android/runtime.py",
        "dev/android/recovery.py",
        "dev/android/nginx.conf.in",
        "dev/schemii.toml",
    ],
)
def test_packaged_runtime_rejects_missing_or_rebound_adapter(apk_runtime_package, name):
    from scripts.check_runtime_parity import check_apk_runtime_sources

    root, shared, adapters, record, commit = apk_runtime_package
    path = adapters / name
    path.unlink()
    with pytest.raises(ParityError, match="inventory/content"):
        check_apk_runtime_sources(root, shared, adapters, record, commit)
    record["adapter_files"].pop(name)
    record["source_files"].pop(name)
    with pytest.raises(ParityError, match="manifest drift"):
        check_apk_runtime_sources(root, shared, adapters, record, commit)


@pytest.mark.parametrize(
    "extra",
    [
        "dev/android/host.py",
        "dev/android/bin/unreviewed",
        "secrets/password",
        "dev/android/pgdata/rows",
    ],
)
def test_packaged_runtime_refuses_untrusted_or_private_extras(
    apk_runtime_package, extra
):
    from scripts.check_runtime_parity import check_apk_runtime_sources

    root, shared, adapters, record, commit = apk_runtime_package
    target = adapters / extra
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("unreviewed")
    with pytest.raises(ParityError, match="unexpected payload"):
        check_apk_runtime_sources(root, shared, adapters, record, commit)


def test_packaged_runtime_refuses_changed_symlinked_and_wrong_source(
    apk_runtime_package,
):
    from scripts.check_runtime_parity import check_apk_runtime_sources

    root, shared, adapters, record, commit = apk_runtime_package
    path = adapters / "dev/android/runtime.py"
    path.write_text("# fork")
    with pytest.raises(ParityError, match="inventory/content"):
        check_apk_runtime_sources(root, shared, adapters, record, commit)
    path.unlink()
    path.symlink_to(root / "dev/android/runtime.py")
    with pytest.raises(ParityError, match="links"):
        check_apk_runtime_sources(root, shared, adapters, record, commit)
    with pytest.raises(ParityError):
        check_apk_runtime_sources(root, shared, adapters, record, "0" * 40)
