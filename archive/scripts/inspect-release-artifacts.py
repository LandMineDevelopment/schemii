#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


FORBIDDEN_PARTS = {".schemii", ".dev-test", "pgdata", "postgres-data", "dashboards", "credentials", "backups"}
FORBIDDEN_NAMES = {"postgres_profiles.json", "migration_history.json", ".env"}
DATABASE_SUFFIXES = {".dump", ".backup", ".bak", ".sqlite", ".sqlite3", ".db"}
APP_DATA_PREFIXES = ("data/config/", "data/schemas/", "data/dashboards/")


def normalized(name: str, strip_first: bool = False) -> str:
    path = PurePosixPath(name)
    parts = path.parts[1:] if strip_first and len(path.parts) > 1 else path.parts
    return PurePosixPath(*parts).as_posix()


def check_path(value: str, *, source: bool = False) -> None:
    path = PurePosixPath(value)
    if path.name in FORBIDDEN_NAMES or path.suffix.lower() in DATABASE_SUFFIXES or any(part in FORBIDDEN_PARTS for part in path.parts):
        raise SystemExit(f"Release artifact contains forbidden runtime or database data: {value}")
    if source and value.startswith(("schemas/", "dashboards/", "credentials/", "backups/")):
        raise SystemExit(f"Release source contains persisted user data: {value}")
    if source and path.suffix.lower() == ".sql":
        allowed = value in {
            "examples/postgres/001_bookstore.sql",
            "docker/metadata/002_rotation_function.sql",
            "docker/metadata/verify_security.sql",
        } or value.startswith("src/schemii/metadata/migrations/")
        if not allowed:
            raise SystemExit(f"Release artifact contains an unapproved SQL file: {value}")


def inspect_source(path: Path) -> None:
    database_seeds = []
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            value = normalized(member.name, strip_first=True)
            check_path(value, source=True)
            if value.startswith("examples/postgres/") and value.endswith(".sql"):
                database_seeds.append(value)
    if database_seeds != ["examples/postgres/001_bookstore.sql"]:
        raise SystemExit("Release source must contain exactly the approved synthetic bookstore database seed")


def inspect_packages(path: Path) -> None:
    wheel_count = 0
    sdist_count = 0
    with tarfile.open(path, "r:gz") as outer:
        for member in outer.getmembers():
            if not member.isfile():
                continue
            payload = outer.extractfile(member).read()
            if member.name.endswith(".whl"):
                wheel_count += 1
                with zipfile.ZipFile(io.BytesIO(payload)) as wheel:
                    for value in wheel.namelist():
                        if value.endswith("/"):
                            continue
                        check_path(value)
                        if value.endswith(".sql") and not value.startswith("schemii/metadata/migrations/"):
                            raise SystemExit(f"Wheel contains an unapproved SQL file: {value}")
            elif member.name.endswith(".tar.gz"):
                sdist_count += 1
                with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as sdist:
                    for nested in sdist.getmembers():
                        if nested.isfile():
                            check_path(normalized(nested.name, strip_first=True), source=True)
    if (wheel_count, sdist_count) != (1, 1):
        raise SystemExit("Python package archive must contain exactly one wheel and one sdist")


def inspect_image_files(component: str, members) -> tuple[bool, bool, bool]:
    packaged_revision = False
    metadata_version = False
    metadata_revision = False
    for member in members:
        if not member.isfile():
            continue
        value = normalized(member.name)
        while value.startswith("./"):
            value = value[2:]
        if value.startswith(APP_DATA_PREFIXES):
            raise SystemExit(f"Application image contains persisted user data: {value}")
        check_path(value)
        if value.endswith("/site-packages/schemii/build_revision.txt"):
            packaged_revision = True
        metadata_version = metadata_version or value == "opt/schemii-release-version"
        metadata_revision = metadata_revision or value == "opt/schemii-release-revision"
    return packaged_revision, metadata_version, metadata_revision


def inspect_image_archive(component: str, path: Path) -> None:
    packaged_revision = False
    metadata_version = False
    metadata_revision = False
    layer_count = 0
    with tarfile.open(path, "r:gz") as image:
        for member in image:
            if not member.isfile() or not member.name.endswith("/layer.tar"):
                continue
            layer_count += 1
            with image.extractfile(member) as layer_file, tarfile.open(fileobj=layer_file, mode="r|") as layer:
                found = inspect_image_files(component, layer)
                packaged_revision = packaged_revision or found[0]
                metadata_version = metadata_version or found[1]
                metadata_revision = metadata_revision or found[2]
    if not layer_count:
        raise SystemExit(f"{component.title()} image archive contains no inspectable Docker layers")
    if component == "application" and not packaged_revision:
        raise SystemExit("Application image has no packaged build revision")
    if component == "metadata" and not (metadata_version and metadata_revision):
        raise SystemExit("Metadata image has no embedded release identity")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--packages", type=Path, required=True)
    parser.add_argument("--image", action="append", required=True, metavar="COMPONENT=PATH")
    args = parser.parse_args()
    inspect_source(args.source)
    inspect_packages(args.packages)
    components = {}
    for specification in args.image:
        component, separator, value = specification.partition("=")
        if not separator or component not in {"application", "metadata", "opencode"} or component in components:
            raise SystemExit("Each --image must identify one unique application, metadata, or opencode image archive")
        components[component] = Path(value)
    if set(components) != {"application", "metadata", "opencode"}:
        raise SystemExit("Root filesystems for application, metadata, and opencode are all required")
    for component, path in components.items():
        inspect_image_archive(component, path)
    print("Release artifact inspection passed: only approved synthetic database data is present.")


if __name__ == "__main__":
    main()
