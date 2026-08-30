#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
MANIFEST_NAME = "release-manifest.json"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def artifact_records(release_dir: Path) -> list[dict[str, object]]:
    return [
        {"file": path.name, "sha256": digest(path), "bytes": path.stat().st_size}
        for path in sorted(release_dir.iterdir())
        if path.is_file() and path.name not in {MANIFEST_NAME, "SHA256SUMS"}
    ]


def expected_artifact_names(version: str, revision: str) -> set[str]:
    prefix = f"schemii-{version}-{revision}"
    return {
        f"{prefix}-source.tar.gz",
        f"{prefix}-python-packages.tar.gz",
        f"{prefix}-application-linux-amd64.tar.gz",
        f"{prefix}-metadata-linux-amd64.tar.gz",
        f"{prefix}-opencode-linux-amd64.tar.gz",
    }


def image_record(reference: str, version: str, revision: str) -> dict[str, str]:
    result = subprocess.run(
        ["docker", "image", "inspect", reference], check=True, capture_output=True, text=True,
    )
    records = json.loads(result.stdout)
    if len(records) != 1:
        raise SystemExit(f"Docker returned an invalid image record for {reference}")
    record = records[0]
    labels = record.get("Config", {}).get("Labels") or {}
    if labels.get("org.opencontainers.image.version") != version or labels.get("org.opencontainers.image.revision") != revision:
        raise SystemExit(f"Image labels do not match the release identity: {reference}")
    image_id = record.get("Id")
    if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise SystemExit(f"Docker returned an invalid image ID for {reference}")
    if record.get("Os") != "linux" or record.get("Architecture") != "amd64":
        raise SystemExit(f"Image platform is not linux/amd64: {reference}")
    return {"reference": reference, "id": image_id}


def create(args: argparse.Namespace) -> None:
    if not VERSION_RE.fullmatch(args.version) or not REVISION_RE.fullmatch(args.revision):
        raise SystemExit("Release version or revision is invalid")
    release_dir = args.release_dir.resolve()
    records = artifact_records(release_dir)
    if {record["file"] for record in records} != expected_artifact_names(args.version, args.revision):
        raise SystemExit("Release directory does not contain the exact expected artifact set")
    if args.platform != "linux/amd64":
        raise SystemExit("Release platform must be linux/amd64")
    manifest = {
        "formatVersion": 1,
        "version": args.version,
        "revision": args.revision,
        "platform": args.platform,
        "artifacts": records,
        "images": {
            "application": image_record(args.application_image, args.version, args.revision),
            "metadata": image_record(args.metadata_image, args.version, args.revision),
            "opencode": image_record(args.opencode_image, args.version, args.revision),
        },
    }
    (release_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )


def verify(args: argparse.Namespace) -> None:
    release_dir = args.release_dir.resolve()
    manifest = json.loads((release_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    if set(manifest) != {"formatVersion", "version", "revision", "platform", "artifacts", "images"} or manifest["formatVersion"] != 1:
        raise SystemExit("Release manifest structure is invalid")
    if not VERSION_RE.fullmatch(manifest["version"]) or not REVISION_RE.fullmatch(manifest["revision"]):
        raise SystemExit("Release manifest identity is invalid")
    if manifest["platform"] != "linux/amd64":
        raise SystemExit("Release manifest platform is invalid")
    expected = artifact_records(release_dir)
    if manifest["artifacts"] != expected:
        raise SystemExit("Release artifact hashes do not match the manifest")
    if {record["file"] for record in expected} != expected_artifact_names(manifest["version"], manifest["revision"]):
        raise SystemExit("Release manifest artifact set is incomplete")
    if set(manifest["images"]) != {"application", "metadata", "opencode"}:
        raise SystemExit("Release manifest image set is incomplete")
    expected_references = {
        "application": f"schemii:{manifest['version']}-{manifest['revision']}",
        "metadata": f"schemii-metadata-postgres:{manifest['version']}-{manifest['revision']}",
        "opencode": f"schemii-opencode:{manifest['version']}-{manifest['revision']}",
    }
    for component, image in manifest["images"].items():
        if set(image) != {"reference", "id"} or not re.fullmatch(r"sha256:[0-9a-f]{64}", image["id"]):
            raise SystemExit("Release manifest contains invalid image identity")
        if image["reference"] != expected_references[component]:
            raise SystemExit("Release manifest contains an unexpected image reference")
    if len({image["id"] for image in manifest["images"].values()}) != 3:
        raise SystemExit("Release manifest image IDs must be distinct")
    checksum_path = release_dir / "SHA256SUMS"
    if not checksum_path.is_file():
        raise SystemExit("Release checksum file is missing")
    checksums = {}
    for line in checksum_path.read_text(encoding="ascii").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._-]+)", line)
        if not match or match.group(2) in checksums:
            raise SystemExit("Release checksum file is invalid")
        checksums[match.group(2)] = match.group(1)
    expected_checksum_names = expected_artifact_names(manifest["version"], manifest["revision"]) | {MANIFEST_NAME}
    if set(checksums) != expected_checksum_names:
        raise SystemExit("Release checksum file does not cover the exact candidate")
    for name, expected_digest in checksums.items():
        if digest(release_dir / name) != expected_digest:
            raise SystemExit(f"Release checksum does not match: {name}")
    if args.version and manifest["version"] != args.version:
        raise SystemExit("Release manifest version does not match promotion input")
    if args.revision and manifest["revision"] != args.revision:
        raise SystemExit("Release manifest revision does not match promotion input")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    commands = result.add_subparsers(dest="command", required=True)
    create_parser = commands.add_parser("create")
    create_parser.add_argument("--release-dir", type=Path, required=True)
    create_parser.add_argument("--version", required=True)
    create_parser.add_argument("--revision", required=True)
    create_parser.add_argument("--platform", required=True)
    create_parser.add_argument("--application-image", required=True)
    create_parser.add_argument("--metadata-image", required=True)
    create_parser.add_argument("--opencode-image", required=True)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--release-dir", type=Path, required=True)
    verify_parser.add_argument("--version")
    verify_parser.add_argument("--revision")
    return result


if __name__ == "__main__":
    arguments = parser().parse_args()
    create(arguments) if arguments.command == "create" else verify(arguments)
