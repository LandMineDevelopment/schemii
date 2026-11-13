#!/usr/bin/env python3
"""Build a private-data-free Android preview from one exact committed Git tree."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[1]
TAG_PATTERN = re.compile(r"android-v[0-9]+\.[0-9]+\.[0-9]+-preview\.[1-9][0-9]*\Z")
FILES = {
    "LICENSE",
    "README.md",
    "pyproject.toml",
    "constraints.docker.txt",
    "start.sh",
    "dev/schemii.toml",
    "docs/android-release.md",
}
PREFIXES = ("src/", "dev/android/", "dev/postgres/", "ai/prototype/")
REQUIRED = FILES | {
    "src/schemii/main.py",
    "dev/android/runtime.py",
    "dev/android/recovery.py",
    "dev/android/termux.sh",
    "dev/android/install-guest.sh",
    "dev/android/nginx.conf.in",
    "dev/android/download.sh.in",
    "ai/prototype/server.js",
    "ai/prototype/package.json",
    "ai/prototype/package-lock.json",
}
PRIVATE_COMPONENTS = {
    ".git",
    ".schemii",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "credentials",
    "secrets",
    "private",
    "logs",
    "artifacts",
    "test-results",
    "backups",
    "downloads",
}
PRIVATE_NAMES = {
    "credentials.json",
    "credentials.txt",
    "login.txt",
    "pgpass",
    "password",
    "password.txt",
    "account_setup_token",
    "metadata_encryption_key",
    "metadata_password",
    "metadata_app_password",
    "demo_admin_password",
    "demo_target_password",
    "opencode_password",
}
PRIVATE_SUFFIXES = (
    ".key",
    ".pem",
    ".p12",
    ".pfx",
    ".pgpass",
    ".log",
    ".pyc",
    ".sqlite",
    ".db",
)
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024


class BuildError(ValueError):
    """A release identity or source hygiene check failed."""


def selected(path: str) -> bool:
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or any(
        part in ("", ".", "..") for part in path.split("/")
    ):
        raise BuildError(f"Unsafe Git path: {path}")
    if path not in FILES and not path.startswith(PREFIXES):
        return False
    if any(
        part in PRIVATE_COMPONENTS or part.startswith((".env", ".venv"))
        for part in candidate.parts
    ):
        return False
    if candidate.name in PRIVATE_NAMES or candidate.name.endswith(PRIVATE_SUFFIXES):
        return False
    if path.startswith("ai/prototype/test/"):
        return False
    return True


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=False
    )
    if result.returncode:
        raise BuildError(
            f"Git {' '.join(args[:2])} failed: {result.stderr.decode(errors='replace').strip()}"
        )
    return result.stdout


def source_files(root: Path, ref: str) -> tuple[str, dict[str, tuple[bytes, int]]]:
    commit = git(root, "rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()
    records = git(root, "ls-tree", "-rz", "--full-tree", commit).split(b"\0")
    files: dict[str, tuple[bytes, int]] = {}
    total = 0
    for record in records:
        if not record:
            continue
        header, raw_path = record.split(b"\t", 1)
        path = raw_path.decode("utf-8")
        if not selected(path):
            continue
        mode, kind, blob = header.decode().split()
        if kind != "blob" or mode not in ("100644", "100755"):
            raise BuildError(f"Release source must be a regular file: {path}")
        data = git(root, "cat-file", "blob", blob)
        total += len(data)
        if len(data) > MAX_FILE_BYTES or total > MAX_ARCHIVE_BYTES:
            raise BuildError("Release source exceeds the archive size budget")
        files[path] = (data, 0o755 if mode == "100755" else 0o644)
    missing = REQUIRED - files.keys()
    if missing:
        raise BuildError(
            "Committed Android source is incomplete: " + ", ".join(sorted(missing))
        )
    if commit == git(root, "rev-parse", "HEAD").decode().strip():
        # Compare actual bytes and modes too: index flags/core.filemode can hide edits.
        for path, (data, mode) in files.items():
            local = root / path
            if any(
                parent.is_symlink()
                for parent in (local, *local.parents)
                if parent != root.parent
            ):
                raise BuildError(f"Working source is symlinked: {path}")
            if not local.is_file() or local.read_bytes() != data:
                raise BuildError(f"Commit Android source before building: {path}")
            if bool(local.stat().st_mode & stat.S_IXUSR) != bool(mode & stat.S_IXUSR):
                raise BuildError(f"Working source executable mode differs: {path}")
        changed = set()
        for args in (
            ("diff", "--name-only", "-z"),
            ("diff", "--cached", "--name-only", "-z"),
            ("ls-files", "--others", "--exclude-standard", "-z"),
        ):
            changed.update(
                path.decode() for path in git(root, *args).split(b"\0") if path
            )
        if any(selected(path) for path in changed):
            raise BuildError(
                "Commit all selected Android source changes before building"
            )
    return commit, files


def metadata(tag: str, commit: str, files: dict[str, tuple[bytes, int]]) -> dict:
    return {
        "schema": 1,
        "tag": tag,
        "commit": commit,
        "archive_root": f"schemii-{tag}",
        "experimental": True,
        "runtime": {
            "architecture": "aarch64",
            "debian": "13",
            "python": "3.13",
            "postgresql": "17",
            "node": "22.22.2",
        },
        "device_acceptance": "pending",
        "files": {
            path: {
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "mode": mode,
            }
            for path, (data, mode) in sorted(files.items())
        },
    }


def build(root: Path, tag: str, output: Path, ref: str = "HEAD") -> dict[str, str]:
    if not TAG_PATTERN.fullmatch(tag):
        raise BuildError("Tag must have the form android-v0.1.0-preview.1")
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise BuildError("Output directory already exists; choose a new directory")
    commit, files = source_files(root, ref)
    manifest = metadata(tag, commit, files)
    manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    archive_root = manifest["archive_root"]
    archive_name = f"{archive_root}.tar.gz"
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".android-release-", dir=output.parent
    ) as temporary:
        stage = Path(temporary)
        archive = stage / archive_name
        with archive.open("xb") as raw:
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=raw, mtime=0
            ) as compressed:
                with tarfile.open(
                    fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT
                ) as bundle:
                    entries = {**files, "ANDROID_RELEASE.json": (manifest_bytes, 0o644)}
                    for path, (data, mode) in sorted(entries.items()):
                        member = tarfile.TarInfo(f"{archive_root}/{path}")
                        member.size, member.mode, member.mtime = len(data), mode, 0
                        member.uid = member.gid = 0
                        member.uname = member.gname = ""
                        bundle.addfile(member, io.BytesIO(data))
        checksum = hashlib.sha256()
        with archive.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                checksum.update(chunk)
        digest = checksum.hexdigest()
        (stage / f"{archive_name}.sha256").write_text(f"{digest}  {archive_name}\n")
        template = files["dev/android/download.sh.in"][0].decode()
        replacements = {
            "@TAG@": tag,
            "@ARCHIVE@": archive_name,
            "@SHA256@": digest,
            "@COMMIT@": commit,
        }
        for placeholder, value in replacements.items():
            template = template.replace(placeholder, value)
        if re.search(r"@[A-Z_]+@", template):
            raise BuildError("Unresolved download installer placeholder")
        installer = stage / "install-android.sh"
        installer.write_text(template)
        installer.chmod(0o755)
        (stage / "ANDROID_RELEASE.json").write_bytes(manifest_bytes)
        os.rename(stage, output)
    return {
        "tag": tag,
        "commit": commit,
        "archive": str(output / archive_name),
        "sha256": digest,
        "installer": str(output / "install-android.sh"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument(
        "--output", required=True, type=Path, help="New output directory"
    )
    parser.add_argument(
        "--ref", default="HEAD", help="Exact committed source, default HEAD"
    )
    options = parser.parse_args()
    try:
        result = build(ROOT, options.tag, options.output, options.ref)
    except (BuildError, OSError, UnicodeError, tarfile.TarError) as error:
        parser.exit(1, f"Android release error: {error}\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
