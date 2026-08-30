from __future__ import annotations

import importlib.metadata
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from .dashboard_store import DashboardStore
from .metadata.migrator import MetadataMigrator
from .postgres_profiles import validate_profile_document
from .schema_store import SchemaStore


VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
MINIMUM_SUPPORTED_METADATA_VERSION = 10


def _read_document(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_application_data(config_dir: Path, schema_dir: Path, dashboard_dir: Path) -> None:
    profile_path = config_dir / "postgres_profiles.json"
    if profile_path.exists():
        validate_profile_document(_read_document(profile_path))

    schema_store = SchemaStore(schema_dir, read_only=True)
    for path in sorted(schema_dir.glob("*.json")):
        record = schema_store.get(path.stem)
        if record["id"] != path.stem:
            raise ValueError(f"Schema file identity does not match its name: {path.name}")

    dashboard_store = DashboardStore(dashboard_dir, read_only=True)
    for path in sorted(dashboard_dir.glob("*.json")):
        record = dashboard_store.get(path.stem)
        if record["id"] != path.stem:
            raise ValueError(f"Dashboard file identity does not match its name: {path.name}")
    for path in sorted((dashboard_dir / ".ai-receipts").glob("*.json")):
        dashboard_store._read_archived_receipt(path)


def _extract_regular_archive(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:gz") as source:
        for member in source.getmembers():
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or ".." in relative.parts or member.issym() or member.islnk():
                raise ValueError(f"Archive contains an unsafe path: {member.name}")
            target = destination.joinpath(*[part for part in relative.parts if part not in {"", "."}])
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                content = source.extractfile(member)
                if content is None:
                    raise ValueError(f"Archive entry could not be read: {member.name}")
                with target.open("wb") as output:
                    shutil.copyfileobj(content, output)
            else:
                raise ValueError(f"Archive contains an unsupported entry: {member.name}")


def _version_tuple(value: str, name: str) -> tuple[int, int, int]:
    if not VERSION_RE.fullmatch(value):
        raise ValueError(f"{name} is invalid")
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def _compatibility_line(version: tuple[int, int, int]) -> tuple[int, ...]:
    return version[:2] if version[0] == 0 else version[:1]


def verify_backup(backup_dir: Path) -> None:
    current_version = _version_tuple(importlib.metadata.version("schemii"), "Installed application version")
    backup_version = _version_tuple((backup_dir / "release-version").read_text(encoding="utf-8").strip(), "Backup application version")
    if backup_version > current_version:
        raise ValueError("Backup was created by a newer application version")
    if _compatibility_line(backup_version) != _compatibility_line(current_version):
        raise ValueError("Backup application version is not restore-compatible")

    raw_metadata_version = (backup_dir / "metadata-version").read_text(encoding="utf-8").strip()
    if not raw_metadata_version.isdigit():
        raise ValueError("Backup metadata version is invalid")
    if int(raw_metadata_version) < MINIMUM_SUPPORTED_METADATA_VERSION:
        raise ValueError("Backup metadata schema is older than the minimum supported version")
    expected_metadata_version = MetadataMigrator(lambda: None).expected_version
    if int(raw_metadata_version) > expected_metadata_version:
        raise ValueError("Backup metadata schema is newer than this application")

    instance = os.environ["SCHEMII_INSTANCE"]
    if (backup_dir / "instance").read_text(encoding="utf-8").rstrip("\n") != instance:
        raise ValueError("Backup instance marker does not match the selected instance")

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        config_dir = root / "config"
        schema_dir = root / "schemas"
        dashboard_dir = root / "dashboards"
        for path in (config_dir, schema_dir, dashboard_dir):
            path.mkdir()
        _extract_regular_archive(backup_dir / "schemii-config.tar.gz", config_dir)
        _extract_regular_archive(backup_dir / "schemii-schemas.tar.gz", schema_dir)
        _extract_regular_archive(backup_dir / "schemer-dashboards.tar.gz", dashboard_dir)
        verify_application_data(config_dir, schema_dir, dashboard_dir)


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "backup":
        verify_backup(Path(sys.argv[2]))
        return
    if len(sys.argv) != 1:
        raise SystemExit("Usage: python -m schemii.recovery_verify [backup <directory>]")
    verify_application_data(
        Path(os.environ["SCHEMII_CONFIG_DIR"]),
        Path(os.environ["SCHEMII_SCHEMA_DIR"]),
        Path(os.environ["SCHEMER_DASHBOARD_DIR"]),
    )


if __name__ == "__main__":
    main()
