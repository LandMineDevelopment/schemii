"""Real-Git release and hostile-archive controls; never start the application."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
TAG = "android-v0.1.0-preview.1"
SPEC = importlib.util.spec_from_file_location(
    "android_release_build", ROOT / "scripts/build-android-release.py"
)
assert SPEC and SPEC.loader
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], stderr=subprocess.PIPE, text=True
    ).strip()


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Android release test")
    git(root, "config", "user.email", "android-release@example.invalid")
    (root / ".gitignore").write_text(".schemii/\n")
    for path in BUILDER.REQUIRED:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# test-owned source " + path + "\n")
    (root / "dev/android/download.sh.in").write_bytes(
        (ROOT / "dev/android/download.sh.in").read_bytes()
    )
    (root / "start.sh").chmod(0o755)
    git(root, "add", ".")
    git(root, "commit", "-m", "Android release fixture")
    return root


def release(repository, tmp_path, name="release"):
    return BUILDER.build(repository, TAG, tmp_path / name)


def installer_module():
    template = (ROOT / "dev/android/download.sh.in").read_text()
    python = template.split("# BEGIN_RELEASE_INSTALLER_PYTHON\n", 1)[1].split(
        "# END_RELEASE_INSTALLER_PYTHON", 1
    )[0]
    namespace = {"__name__": "android_release_installer_test"}
    exec(compile(python, str(ROOT / "dev/android/download.sh.in"), "exec"), namespace)
    return namespace


def install(artifact, destination):
    return installer_module()["install_archive"](
        artifact["archive"], destination, TAG, artifact["sha256"], artifact["commit"]
    )


def rewrite_archive(artifact, path, transform):
    with tarfile.open(artifact["archive"], "r:gz") as source:
        members = [
            (member, source.extractfile(member).read())
            for member in source.getmembers()
        ]
    transformed = transform(members)
    with tarfile.open(path, "w:gz", format=tarfile.USTAR_FORMAT) as output:
        for member, data in transformed:
            output.addfile(member, io.BytesIO(data) if member.isfile() else None)
    return {
        **artifact,
        "archive": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def test_exact_source_build_is_deterministic_and_contains_versioned_file_inventory(
    repository, tmp_path
):
    first = release(repository, tmp_path, "first")
    second = release(repository, tmp_path, "second")
    assert Path(first["archive"]).read_bytes() == Path(second["archive"]).read_bytes()
    assert first["sha256"] == second["sha256"]
    assert (
        Path(first["installer"]).read_bytes() == Path(second["installer"]).read_bytes()
    )
    metadata = json.loads(
        (Path(first["archive"]).parent / "ANDROID_RELEASE.json").read_text()
    )
    assert metadata["commit"] == git(repository, "rev-parse", "HEAD")
    assert metadata["tag"] == TAG
    assert metadata["experimental"] is True
    assert metadata["device_acceptance"] == "pending"
    assert metadata["runtime"]["postgresql"] == "17"
    assert set(metadata["files"]) == BUILDER.REQUIRED
    assert metadata["files"]["start.sh"]["mode"] == 0o755
    assert first["sha256"] in Path(first["installer"]).read_text()
    assert "@SHA256@" not in Path(first["installer"]).read_text()
    assert (
        Path(first["archive"] + ".sha256")
        .read_text()
        .startswith(first["sha256"] + "  ")
    )


def test_private_tracked_and_untracked_material_is_excluded(repository, tmp_path):
    private = (
        "dev/android/credentials.json",
        "src/private.key",
        "ai/prototype/node_modules/package/index.js",
        "ai/prototype/logs/output.txt",
        "dev/android/.env.local",
        "dev/android/.schemii/login.txt",
    )
    for name in private:
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("RELEASE-SECRET-SENTINEL")
    git(repository, "add", "-f", *private)
    git(repository, "commit", "-m", "Private fixture must be excluded")
    (repository / ".schemii").mkdir()
    (repository / ".schemii/state.json").write_text("UNTRACKED-PRIVATE-SENTINEL")
    artifact = release(repository, tmp_path)
    with tarfile.open(artifact["archive"], "r:gz") as bundle:
        names = [member.name for member in bundle.getmembers()]
        content = b"".join(
            bundle.extractfile(member).read() for member in bundle.getmembers()
        )
    assert not any(name.endswith(private) for name in names)
    assert b"RELEASE-SECRET-SENTINEL" not in content
    assert b"UNTRACKED-PRIVATE-SENTINEL" not in content


def test_tracked_symlink_cannot_become_a_release_source(repository, tmp_path):
    (repository / "src/escape.py").symlink_to("/etc/passwd")
    git(repository, "add", "src/escape.py")
    git(repository, "commit", "-m", "Hostile symlink fixture")
    with pytest.raises(BUILDER.BuildError, match="regular file"):
        release(repository, tmp_path)
    assert not (tmp_path / "release").exists()


@pytest.mark.parametrize(
    "change", ["dirty", "staged", "untracked", "concealed", "mode"]
)
def test_current_head_cannot_masquerade_as_changed_source(repository, tmp_path, change):
    source = repository / "start.sh"
    if change == "untracked":
        (repository / "src/new.py").write_text("new source\n")
    elif change == "mode":
        source.chmod(0o644)
    else:
        if change == "concealed":
            git(repository, "update-index", "--assume-unchanged", "start.sh")
        source.write_text(source.read_text() + "# user edit\n")
        if change == "staged":
            git(repository, "add", "start.sh")
    with pytest.raises(BUILDER.BuildError, match="Commit|mode differs"):
        release(repository, tmp_path)
    assert not (tmp_path / "release").exists()


def test_explicit_historical_commit_uses_git_objects_without_reading_worktree(
    repository, tmp_path
):
    original = git(repository, "rev-parse", "HEAD")
    (repository / "src/schemii/main.py").write_text("# newer source\n")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "New source")
    (repository / "src/schemii/main.py").write_text("# uncommitted source\n")
    artifact = BUILDER.build(repository, TAG, tmp_path / "old", original)
    assert artifact["commit"] == original
    with tarfile.open(artifact["archive"], "r:gz") as bundle:
        original_source = bundle.extractfile(
            f"schemii-{TAG}/src/schemii/main.py"
        ).read()
    assert original_source == b"# test-owned source src/schemii/main.py\n"


def test_output_cannot_overwrite_an_existing_directory(repository, tmp_path):
    output = tmp_path / "release"
    output.mkdir()
    marker = output / "user-file"
    marker.write_text("preserve")
    with pytest.raises(BUILDER.BuildError, match="already exists"):
        release(repository, tmp_path)
    assert marker.read_text() == "preserve"


def test_download_installs_verified_source_and_idempotently_reuses_it(
    repository, tmp_path
):
    artifact = release(repository, tmp_path)
    destination = tmp_path / "installed"
    source = install(artifact, destination)
    assert source == destination / TAG
    assert (source / "start.sh").read_bytes() == (repository / "start.sh").read_bytes()
    assert (source / "ANDROID_RELEASE.json").is_file()
    assert (source / "start.sh").stat().st_mode & 0o777 == 0o755
    assert destination.stat().st_mode & 0o777 == 0o700
    private = source / ".schemii/private-export"
    private.parent.mkdir()
    private.write_text("user-owned data")
    assert install(artifact, destination) == source
    assert private.read_text() == "user-owned data"


def test_download_checksum_tamper_is_rejected_before_creating_install_root(
    repository, tmp_path
):
    artifact = release(repository, tmp_path)
    with Path(artifact["archive"]).open("ab") as output:
        output.write(b"tampered")
    destination = tmp_path / "installed"
    with pytest.raises(ValueError, match="checksum mismatch"):
        install(artifact, destination)
    assert not destination.exists()


def test_changed_existing_release_is_preserved_and_not_overwritten(
    repository, tmp_path
):
    artifact = release(repository, tmp_path)
    destination = tmp_path / "installed"
    source = install(artifact, destination)
    changed = source / "start.sh"
    changed.write_text("user-owned source edit")
    with pytest.raises(ValueError, match="preserved"):
        install(artifact, destination)
    assert changed.read_text() == "user-owned source edit"


def test_additional_package_source_cannot_masquerade_as_the_same_release(
    repository, tmp_path
):
    artifact = release(repository, tmp_path)
    destination = tmp_path / "installed"
    source = install(artifact, destination)
    added = source / "src/schemii/extra_unreleased.py"
    added.write_text("# local source must not become an exact-release installation\n")
    with pytest.raises(ValueError, match="unexpected source.*preserved"):
        install(artifact, destination)
    assert added.is_file()


def test_generated_dependency_and_build_directories_do_not_change_source_identity(
    repository, tmp_path
):
    artifact = release(repository, tmp_path)
    destination = tmp_path / "installed"
    source = install(artifact, destination)
    for relative in (
        "ai/prototype/node_modules/example/index.js",
        "src/schemii.egg-info/PKG-INFO",
        "src/schemii/__pycache__/main.cpython-313.pyc",
        "build/lib/schemii/main.py",
    ):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("generated install product")
    assert install(artifact, destination) == source


@pytest.mark.parametrize("kind", ["traversal", "symlink", "hardlink", "duplicate"])
def test_hostile_archive_entries_are_rejected_before_extraction(
    repository, tmp_path, kind
):
    artifact = release(repository, tmp_path)

    def transform(members):
        member = tarfile.TarInfo(f"schemii-{TAG}/src/evil.py")
        member.mode = 0o644
        content = b"evil"
        member.size = len(content)
        if kind == "traversal":
            member.name = f"schemii-{TAG}/../escaped"
        elif kind == "symlink":
            member.type, member.linkname, member.size = (
                tarfile.SYMTYPE,
                "/etc/passwd",
                0,
            )
        elif kind == "hardlink":
            member.type, member.linkname, member.size = tarfile.LNKTYPE, "start.sh", 0
        else:
            member.name = members[0][0].name
        return members + [(member, content)]

    malicious = rewrite_archive(artifact, tmp_path / "malicious.tar.gz", transform)
    destination = tmp_path / "installed"
    with pytest.raises(ValueError, match="unsafe path|regular files|duplicate"):
        install(malicious, destination)
    assert not destination.exists()
    assert not (tmp_path / "escaped").exists()


def test_manifest_file_hash_is_checked_even_after_outer_archive_hash_is_updated(
    repository, tmp_path
):
    artifact = release(repository, tmp_path)

    def transform(members):
        altered = []
        for member, content in members:
            if member.name.endswith("/start.sh"):
                content = b"# replaced\n"
                member.size = len(content)
            altered.append((member, content))
        return altered

    malicious = rewrite_archive(artifact, tmp_path / "tampered.tar.gz", transform)
    with pytest.raises(
        ValueError, match="attributes do not match|checksum does not match"
    ):
        install(malicious, tmp_path / "installed")


def test_download_shell_help_and_non_android_guard_are_read_only(tmp_path):
    script = ROOT / "dev/android/download.sh.in"
    help_result = subprocess.run(
        ["bash", str(script), "--help"], capture_output=True, text=True, check=False
    )
    assert help_result.returncode == 0
    environment = {**os.environ, "PREFIX": str(tmp_path / "not-termux")}
    denied = subprocess.run(
        ["bash", str(script)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert denied.returncode == 1
    assert "inside the official Termux" in denied.stderr
    assert list(tmp_path.iterdir()) == []
