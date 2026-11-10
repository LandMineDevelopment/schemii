"""Real-Git regressions for the frozen demo launcher; no live stack is started."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
VERSION = "demo-v0.1.0"
RELEASE_URL = f"https://github.com/LandMineDevelopment/schemii/releases/tag/{VERSION}"


def git(root: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *arguments], text=True, stderr=subprocess.PIPE
    ).strip()


def install_files(root: Path) -> Path:
    launcher = root / "dev/demo/published.sh"
    launcher.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "dev/demo/published.sh", launcher)
    (root / ".gitignore").write_text(".schemii/\n", encoding="utf-8")
    start = root / "start.sh"
    start.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" >> \"$LAUNCH_RECORD\"\n"
        "exit \"${LAUNCH_STATUS:-0}\"\n",
        encoding="utf-8",
    )
    start.chmod(0o755)
    return launcher


@pytest.fixture
def release(tmp_path: Path):
    root = tmp_path / "release"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Published Demo Test")
    git(root, "config", "user.email", "demo-test@example.invalid")
    launcher = install_files(root)
    git(root, "add", ".")
    git(root, "commit", "-m", "Published demo fixture")
    git(root, "tag", "-a", VERSION, "-m", "Demo test snapshot")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GIT_", "SCHEMII_"))
    }
    environment["LAUNCH_RECORD"] = str(tmp_path / "launch-record")
    return root, launcher, environment


def run(release, *arguments: str):
    root, launcher, environment = release
    return subprocess.run(
        [str(launcher), *arguments],
        cwd=root.parent,
        env=environment,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )


def launched(release) -> bool:
    return Path(release[2]["LAUNCH_RECORD"]).exists()


def test_clean_published_tag_launches_canonical_demo_from_another_directory(release):
    result = run(release)

    assert result.returncode == 0, result.stderr
    assert Path(release[2]["LAUNCH_RECORD"]).read_text() == "--demo\n"


def test_launcher_failure_is_preserved_without_retry(release):
    release[2]["LAUNCH_STATUS"] = "37"
    result = run(release)

    assert result.returncode == 37
    assert Path(release[2]["LAUNCH_RECORD"]).read_text() == "--demo\n"


@pytest.mark.parametrize("changed", ["missing-tag", "different-commit", "different-tag"])
def test_other_versions_cannot_launch(release, changed):
    root = release[0]
    if changed == "missing-tag":
        git(root, "tag", "-d", VERSION)
    else:
        git(root, "commit", "--allow-empty", "-m", "Another version")
        if changed == "different-tag":
            git(root, "tag", "another-demo")
    result = run(release)

    assert result.returncode == 1
    assert "published demo error" in result.stderr
    assert not launched(release)


@pytest.mark.parametrize("changed", ["tracked", "staged", "untracked"])
def test_source_changes_refuse_to_launch_and_are_preserved(release, changed):
    root = release[0]
    path = root / ("start.sh" if changed != "untracked" else "extra-source.py")
    original = path.read_text() if path.exists() else ""
    changed_text = original + "# user-owned source change\n"
    path.write_text(changed_text)
    if changed == "staged":
        git(root, "add", str(path))
    result = run(release)

    assert result.returncode == 1
    assert "contains source changes" in result.stderr
    assert not launched(release)
    assert path.read_text() == changed_text


def test_private_ignored_runtime_state_does_not_prevent_launch(release):
    private = release[0] / ".schemii/demo/login.txt"
    private.parent.mkdir(parents=True)
    private.write_text("private test-owned state\n")
    result = run(release)

    assert result.returncode == 0, result.stderr
    assert launched(release)
    assert private.read_text() == "private test-owned state\n"


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_concealed_tracked_edits_cannot_launch(release, flag):
    root = release[0]
    git(root, "update-index", flag, "start.sh")
    path = root / "start.sh"
    changed = path.read_text() + "# concealed source change\n"
    path.write_text(changed)
    assert git(root, "status", "--porcelain") == ""

    result = run(release)

    assert result.returncode == 1
    assert "index flags that can hide source changes" in result.stderr
    assert not launched(release)
    assert path.read_text() == changed


def test_version_reports_exact_commit_and_release_without_launching(release):
    result = run(release, "--version")

    assert result.returncode == 0, result.stderr
    assert VERSION in result.stdout
    assert git(release[0], "rev-parse", "HEAD") in result.stdout
    assert RELEASE_URL in result.stdout
    assert not launched(release)


def test_help_explains_canonical_launch_and_private_credentials_without_launching(release):
    result = run(release, "--help")

    assert result.returncode == 0
    assert "start.sh --demo" in result.stdout
    assert "private administrator credentials file" in result.stdout
    assert not launched(release)


@pytest.mark.parametrize("arguments", [("--unknown",), ("--version", "--help")])
def test_unknown_or_multiple_arguments_do_not_launch(release, arguments):
    result = run(release, *arguments)

    assert result.returncode == 2
    assert "Usage:" in result.stderr
    assert not launched(release)


def test_directory_inside_an_unrelated_repo_is_not_accepted_as_release_root(release):
    root, _, environment = release
    nested = root / "unrelated-copy"
    nested.mkdir()
    launcher = install_files(nested)
    git(root, "add", ".")
    git(root, "commit", "-m", "Unrelated nested files")
    git(root, "tag", "-d", VERSION)
    git(root, "tag", VERSION)

    result = run((nested, launcher, environment))

    assert result.returncode == 1
    assert "not the published demo checkout root" in result.stderr
    assert not launched(release)
