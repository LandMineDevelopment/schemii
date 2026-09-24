"""Private, versioned recovery bundles. Uses only the host Python standard library."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
from datetime import datetime, timezone

SECRETS = (
    "metadata_password", "metadata_app_password", "metadata_encryption_key",
    "demo_admin_password", "demo_target_password", "account_setup_token", "opencode_password",
)
PAYLOADS = ("metadata.dump", "key-probe.json", "identity.json", *(f"secrets/{name}" for name in SECRETS))


def private_file(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError(f"Bundle file must be a private regular file: {path.name}")


def digest(path: Path) -> str:
    hashed = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hashed.update(chunk)
    return hashed.hexdigest()


def prepare(root: Path, secrets: Path, database: str, bootstrap_user: str, app_user: str) -> None:
    (root / "secrets").mkdir(mode=0o700)
    for name in SECRETS:
        source = secrets / name
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"Required original secret is missing or a symbolic link: {name}")
        shutil.copyfile(source, root / "secrets" / name)
        (root / "secrets" / name).chmod(0o600)
    (root / "identity.json").write_text(json.dumps({
        "database": database, "bootstrap_user": bootstrap_user, "app_user": app_user,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }) + "\n")
    (root / "identity.json").chmod(0o600)


def seal(root: Path) -> None:
    for name in PAYLOADS:
        private_file(root / name)
        if (root / name).stat().st_size == 0:
            raise ValueError(f"Empty recovery file: {name}")
    (root / "manifest.json").write_text(json.dumps({
        "format": "schemii-metadata-backup-v1",
        "sha256": {name: digest(root / name) for name in PAYLOADS},
    }, indent=2) + "\n")
    (root / "manifest.json").chmod(0o600)
    validate(root)


def validate(root: Path) -> None:
    for directory in (root, root / "secrets"):
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("Bundle directories must be private directories, not symbolic links")
    private_file(root / "manifest.json")
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest.get("format") != "schemii-metadata-backup-v1" or set(manifest.get("sha256", {})) != set(PAYLOADS):
        raise ValueError("Unsupported or incomplete backup manifest")
    for name in PAYLOADS:
        private_file(root / name)
        if digest(root / name) != manifest["sha256"][name]:
            raise ValueError(f"Backup checksum failed: {name}")
    with (root / "metadata.dump").open("rb") as stream:
        if stream.read(5) != b"PGDMP":
            raise ValueError("Backup is not a PostgreSQL custom-format archive")


if __name__ == "__main__":
    try:
        action, directory, *arguments = sys.argv[1:]
        if action == "prepare":
            prepare(Path(directory), Path(arguments[0]), *arguments[1:])
        elif action == "seal":
            seal(Path(directory))
        elif action == "validate":
            validate(Path(directory))
        elif action == "identity":
            identity = json.loads((Path(directory) / "identity.json").read_text())
            if arguments != [identity[key] for key in ("database", "bootstrap_user", "app_user")]:
                raise ValueError("Set SCHEMII_TEST_POSTGRES_DB, SCHEMII_TEST_POSTGRES_USER, and SCHEMII_METADATA_APP_USER to the nonsecret values in identity.json, then retry")
        elif action == "source-identity":
            root = Path(directory)
            identity = json.loads((root / "identity.json").read_text())
            probe = json.loads((root / "key-probe.json").read_text())
            if any(identity[key] != probe[key] for key in ("database", "app_user")):
                raise ValueError("Backup identity settings do not match the running application; use its original database and role settings")
        else:
            raise ValueError("Unknown recovery action")
    except (OSError, ValueError, TypeError) as error:
        # Never include secret values or database contents in recovery errors.
        print(f"Recovery bundle validation failed: {error}", file=sys.stderr)
        sys.exit(1)
