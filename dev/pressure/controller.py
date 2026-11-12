"""Launcher-owned disposable HTTPS qualification; never a general runtime API.

Only start.sh may invoke main: canonical exclusive descriptors are mandatory.
Intent is durable before any container write. Recovery inspects unknown outcomes;
it never repeats creation, stops another owner, prunes or adopts retained data.
"""

from __future__ import annotations

import argparse
from contextlib import suppress
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import select
import signal
import socket
import ssl
import stat
import subprocess
import sys
import tarfile
import time
from typing import Any
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)
import uuid

LOCAL = "https://localhost:8001"
PREVIEW = "https://omarchy.taile4f57f.ts.net"
POSTGRES = "postgres:17-alpine@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73"
INGRESS = "nginx:1.29.1-alpine@sha256:42a516af16b852e33b7682d5ef8acbd5d13fe08fecadc7ed98605ba5e3b26ab8"
RUN_LABEL = "io.schemii.pressure.run"
REQUIRED_SOURCE_WITNESS_SHA256 = (
    "0f4e218ee4c2ecfb2c56758d16e9219537b6fa1efa5e0ae3e917417d3e823075"
)
# Integration must pin the independently reviewed constrained #135 consumer tree.
# Empty intentionally denies attachment; it cannot authorize older detached engines.
REQUIRED_CONSUMER_TREE_SHA256 = ""
SERVICES = {"metadata", "source", "observer-init", "schemii", "ingress"}
ASSETS = (
    "start.sh",
    "dev/pressure/launcher.sh",
    "dev/pressure/controller.py",
    "dev/pressure/probe.py",
    "dev/pressure/app.py",
    "dev/pressure/compose.yaml",
    "dev/ingress/nginx.conf",
    "dev/schemii.toml",
)
INSPECT = '{"id":{{json .Id}},"created":{{json .Created}},"name":{{json .Name}},"image":{{json .Image}},"state":{{json .State.Status}},"exitCode":{{json .State.ExitCode}},"oomKilled":{{json .State.OOMKilled}},"startedAt":{{json .State.StartedAt}},"finishedAt":{{json .State.FinishedAt}},"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}},"labels":{{json .Config.Labels}},"ports":{{json .HostConfig.PortBindings}},"mounts":{{json .Mounts}},"networks":{{json .NetworkSettings.Networks}},"memory":{{json .HostConfig.Memory}},"nanoCpus":{{json .HostConfig.NanoCpus}},"pids":{{json .HostConfig.PidsLimit}}}'
# No environment, command arguments, database rows or runtime error messages.
PACKAGE_SCRIPT = """import hashlib,importlib.util,json
from pathlib import Path
root=Path(importlib.util.find_spec('schemii').origin).parent
h=hashlib.sha256();n=0
for p in sorted(root.rglob('*')):
 if p.is_file() and p.suffix in {'.py','.sql'}:
  h.update(p.relative_to(root).as_posix().encode()+b'\\0'+hashlib.sha256(p.read_bytes()).digest());n+=1
print(json.dumps({'package':h.hexdigest(),'files':n,'config':hashlib.sha256(Path('/etc/schemii/schemii.toml').read_bytes()).hexdigest()}))
"""


class Refused(RuntimeError):
    """A fixed, nonsecret failure reason; never serialize subprocess exceptions."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check(condition: bool, reason: str) -> None:
    if not condition:
        raise Refused(reason)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha(path: Path) -> str:
    check(path.is_file() and not path.is_symlink(), "regular_source_asset_required")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def private_read(path: Path) -> dict:
    before = path.lstat()
    check(
        stat.S_ISREG(before.st_mode)
        and stat.S_IMODE(before.st_mode) == 0o600
        and before.st_uid == os.getuid(),
        "journal_permissions",
    )
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        check(
            stat.S_ISREG(info.st_mode)
            and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_uid == os.getuid(),
            "journal_permissions",
        )
        check(
            (info.st_dev, info.st_ino) == (before.st_dev, before.st_ino),
            "journal_identity_changed",
        )
        raw = stream.read(262145)
    check(len(raw) <= 262144, "journal_size")
    result = json.loads(raw)
    check(type(result) is dict, "journal_schema")
    return result


def private_write(path: Path, value: dict) -> None:
    encoded = (json.dumps(value, separators=(",", ":")) + "\n").encode()
    check(len(encoded) <= 262144, "journal_size")
    if path.exists() or path.is_symlink():
        private_read(path)
    temporary = path.with_name(path.name + "." + secrets.token_hex(8))
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def command(
    arguments: list[str],
    *,
    environment: dict | None = None,
    timeout: int = 30,
    limit: int = 1048576,
    binary: bool = False,
) -> str | bytes:
    """Bound output/time and suppress all CLI errors, including secret-bearing ones."""
    process = subprocess.Popen(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=environment,
        start_new_session=True,
        pass_fds=(3, 4)
        if environment is not None
        and environment.get("SCHEMII_QA_LEASE_MODE") == "exclusive"
        else (),
    )
    try:
        deadline = time.monotonic() + timeout
        result = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            check(
                remaining > 0
                and bool(select.select([process.stdout], [], [], remaining)[0]),
                "command_timeout",
            )
            part = os.read(process.stdout.fileno(), min(65536, limit + 1 - len(result)))
            if not part:
                break
            result.extend(part)
            check(len(result) <= limit, "command_output_bound")
        check(
            process.wait(timeout=max(0.01, deadline - time.monotonic())) == 0,
            "command_failed",
        )
        return bytes(result) if binary else result.decode()
    finally:
        if process.poll() is None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        process.stdout.close()


def verify_leases(common: Path) -> None:
    for number, name in ((4, "qa-startup.lock"), (3, "qa-deployment.lock")):
        path = common / name
        info = path.lstat()
        descriptor = os.fstat(number)
        check(
            stat.S_ISREG(info.st_mode)
            and (info.st_dev, info.st_ino) == (descriptor.st_dev, descriptor.st_ino),
            "canonical_lease_identity",
        )
        fcntl.flock(number, fcntl.LOCK_EX | fcntl.LOCK_NB)
    check(
        os.environ.get("SCHEMII_QA_LEASE_MODE") == "exclusive",
        "exclusive_lease_required",
    )


def checkout_authority(root: Path, primary: Path, common: Path) -> None:
    resolved = command(
        [
            "git",
            "-C",
            str(root),
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ]
    ).strip()
    check(
        resolved == str(common) and common.is_absolute(),
        "canonical_git_common_directory",
    )
    listed = command(
        ["git", "--git-dir=" + resolved, "worktree", "list", "--porcelain", "-z"]
    )
    check(
        listed.split("\0", 1)[0] == "worktree " + str(primary),
        "canonical_primary_checkout",
    )


def package_identity(root: Path) -> dict:
    digest = hashlib.sha256()
    count = 0
    for path in sorted((root / "src/schemii").rglob("*")):
        if path.is_file() and path.suffix in {".py", ".sql"}:
            check(not path.is_symlink(), "source_symlink")
            digest.update(
                path.relative_to(root / "src/schemii").as_posix().encode()
                + b"\0"
                + hashlib.sha256(path.read_bytes()).digest()
            )
            count += 1
    return {
        "package": digest.hexdigest(),
        "files": count,
        "config": sha(root / "dev/schemii.toml"),
    }


def fixed_identity(record: dict) -> dict:
    selected = {
        key: record[key]
        for key in (
            "id",
            "created",
            "name",
            "image",
            "labels",
            "ports",
            "mounts",
            "memory",
            "nanoCpus",
            "pids",
        )
    }
    selected["networkIdentities"] = {
        name: item["NetworkID"] for name, item in record["networks"].items()
    }
    return selected


def valid_container(record: dict) -> None:
    check(
        type(record) is dict
        and bool(re.fullmatch(r"[0-9a-f]{64}", record.get("id", ""))),
        "container_identity",
    )
    check(
        type(record.get("labels")) is dict and type(record.get("mounts")) is list,
        "container_metadata",
    )
    check(
        type(record.get("networks")) is dict
        and all(
            type(value) is dict
            and bool(re.fullmatch(r"[0-9a-f]{64}", value.get("NetworkID", "")))
            for value in record["networks"].values()
        ),
        "container_network_identity",
    )
    check(
        bool(re.fullmatch(r"sha256:[0-9a-f]{64}", record.get("image", ""))),
        "image_identity",
    )


def instant(value: str) -> datetime:
    check(type(value) is str and len(value) <= 40, "observation_timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    check(parsed.tzinfo is not None, "observation_timestamp")
    return parsed


def validate_observation(value: dict, run: str, native: dict, *, fresh: bool) -> None:
    check(
        type(value) is dict
        and value.get("version") == 1
        and value.get("run") == run
        and value.get("startupComplete") is True
        and value.get("observationFailed") is False,
        "observer_receipt_identity",
    )
    process = value.get("process", {})
    check(
        type(process.get("pid")) is int
        and process["pid"] > 0
        and process.get("uid") == 10001
        and re.fullmatch(r"[0-9]+", process.get("startTicks", "")) is not None
        and re.fullmatch(r"[0-9a-f]{32}", process.get("incarnation", "")) is not None,
        "observer_process_identity",
    )
    at = instant(value["at"])
    check(
        instant(native["startedAt"]) <= instant(process["startedAt"]) <= at,
        "observer_native_incarnation",
    )
    if fresh:
        age = (datetime.now(timezone.utc) - at).total_seconds()
        check(
            value.get("stage") == "running" and 0 <= age <= 2,
            "observer_fresh_running_sample",
        )
    for name in ("normal", "readiness"):
        owner = value.get(name, {})
        counts = owner.get("counts", {})
        check(
            owner.get("status") == "observed"
            and type(counts.get("closed")) is bool
            and all(
                type(counts.get(key)) is int and counts[key] >= 0
                for key in ("maximum", "active", "peak_active")
            )
            and 0 <= counts["active"] <= counts["peak_active"] <= counts["maximum"],
            "observer_owner_unknown",
        )
    check(
        value["normal"]["counts"]["maximum"] == 2
        and value["readiness"]["counts"]["maximum"] == 1,
        "observer_fixed_owner_limits",
    )


def validate_shutdown(before: dict, after: dict, native: dict) -> None:
    check(
        before["containerId"] == after["containerId"] == native["id"]
        and before["containerStartedAt"]
        == after["containerStartedAt"]
        == native["startedAt"]
        and before["observation"]["process"] == after["observation"]["process"],
        "lifespan_incarnation_changed",
    )
    value = after["observation"]
    check(
        value["stage"] == "shutdown-complete"
        and instant(before["observation"]["at"])
        <= instant(value["at"])
        <= instant(native["finishedAt"]),
        "stock_lifespan_completion_missing",
    )
    check(
        all(
            value[name]["counts"]["closed"] is True
            and value[name]["counts"]["active"] == 0
            for name in ("normal", "readiness")
        ),
        "lifespan_accounting_not_closed",
    )


def process_identity(pid: int) -> dict:
    check(type(pid) is int and pid > 1, "consumer_pid")
    base = Path(f"/proc/{pid}")
    check(base.stat().st_uid == os.getuid(), "consumer_uid")
    raw = (base / "stat").read_text()
    fields = raw.rsplit(") ", 1)[1].split()
    check(
        fields[0] != "Z" and int(fields[2]) == pid and int(fields[3]) == pid,
        "consumer_dedicated_group",
    )
    with (base / "cmdline").open("rb") as stream:
        argv = stream.read(4097)
    check(0 < len(argv) <= 4096, "consumer_argv_bound")
    return {
        "pid": pid,
        "start": fields[19],
        "uid": os.getuid(),
        "argvSha256": hashlib.sha256(argv).hexdigest(),
    }


def consumer_identity(pid: int, root: Path) -> dict:
    identity = process_identity(pid)
    base = Path(f"/proc/{pid}")
    with (base / "cmdline").open("rb") as stream:
        raw = stream.read(4097)
    check(0 < len(raw) <= 4096, "consumer_argv_bound")
    argv = raw.rstrip(b"\0").decode().split("\0")
    check(
        len(argv) == 3
        and Path(os.readlink(base / "exe")).name == "node"
        and re.fullmatch(r"load-[a-z0-9-]{6,80}", argv[2]) is not None,
        "consumer_fixed_entrypoint",
    )
    script = Path(argv[1])
    check(
        script.is_absolute()
        and script == script.resolve()
        and script.parts[-3:] == ("testing", "load", "controller.mjs"),
        "consumer_script",
    )
    checkout = script.parents[2]
    expected = command(
        [
            "git",
            "-C",
            str(root),
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ]
    ).strip()
    actual = command(
        [
            "git",
            "-C",
            str(checkout),
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ]
    ).strip()
    check(expected == actual, "consumer_git_common")
    command(
        [
            "git",
            "-C",
            str(checkout),
            "ls-files",
            "--error-unmatch",
            "testing/load/controller.mjs",
        ]
    )
    command(
        [
            "git",
            "-C",
            str(checkout),
            "diff",
            "--quiet",
            "HEAD",
            "--",
            "testing/load/controller.mjs",
        ]
    )
    sources = sorted(
        path
        for path in (checkout / "testing/load").rglob("*")
        if path.suffix in {".mjs", ".py"} and path.is_file()
    )
    check(0 < len(sources) <= 128, "consumer_source_inventory")
    fingerprint = hashlib.sha256()
    for path in sources:
        check(
            not path.is_symlink() and path.stat().st_size <= 262144,
            "consumer_source_leaf",
        )
        fingerprint.update(
            path.relative_to(checkout).as_posix().encode()
            + b"\0"
            + bytes.fromhex(sha(path))
        )
    check(
        REQUIRED_CONSUMER_TREE_SHA256
        and fingerprint.hexdigest() == REQUIRED_CONSUMER_TREE_SHA256,
        "reviewed_consumer_source_required",
    )
    command(
        ["git", "-C", str(checkout), "diff", "--quiet", "HEAD", "--", "testing/load"]
    )
    return identity


def group_members(pid: int) -> list[dict]:
    members = []
    for item in Path("/proc").iterdir():
        if not item.name.isdigit():
            continue
        try:
            fields = (item / "stat").read_text().rsplit(") ", 1)[1].split()
            if int(fields[2]) == pid and fields[0] != "Z":
                check(
                    item.stat().st_uid == os.getuid() and int(fields[3]) == pid,
                    "consumer_group_owner",
                )
                members.append({"pid": int(item.name), "start": fields[19]})
        except FileNotFoundError:
            continue
    check(len(members) <= 128, "consumer_group_bound")
    return members


def join_consumer(identity: dict) -> None:
    """Terminate only the still-exact opted-in session; never chase a reused group."""
    pid = identity["pid"]
    members = group_members(pid)
    if not members:
        return
    check(process_identity(pid) == identity, "consumer_join_identity_unknown")
    os.killpg(pid, signal.SIGTERM)
    deadline = time.monotonic() + 3
    while group_members(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    members = group_members(pid)
    if members:
        # A dead/replaced leader prevents attributing newly observed group members.
        check(process_identity(pid) == identity, "consumer_force_identity_unknown")
        os.killpg(pid, signal.SIGKILL)
        deadline = time.monotonic() + 2
        while group_members(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
    check(not group_members(pid), "consumer_group_not_joined")


def attachment_request(
    root: Path, primary: Path, directory: Path, action: str, argument: str
) -> dict:
    """Metadata IPC only: no Docker access or acquisition of the holder's leases."""
    check(
        directory.parent == primary / ".schemii/metadata-pressure"
        and re.fullmatch(r"pressure_[0-9a-f]{32}", directory.name) is not None
        and not directory.is_symlink()
        and stat.S_IMODE(directory.stat().st_mode) == 0o700
        and directory.stat().st_uid == os.getuid(),
        "attachment_directory",
    )
    journal = private_read(directory / "journal.json")
    check(
        journal.get("directory") == str(directory)
        and journal.get("root") == str(root)
        and journal.get("primary") == str(primary)
        and journal.get("run") == directory.name
        and journal.get("assets") == {name: sha(root / name) for name in ASSETS},
        "attachment_journal",
    )
    authority = private_read(directory / "authority.json")
    check(
        authority.get("kind") == "launcher_owned_disposable_pressure"
        and authority.get("status") == "held"
        and authority.get("run") == journal["run"]
        and authority.get("nonce") == journal.get("hold", {}).get("nonce")
        and instant(authority["expiresAt"]) > datetime.now(timezone.utc)
        and 0
        <= (
            datetime.now(timezone.utc) - instant(authority["heartbeatAt"])
        ).total_seconds()
        <= 3,
        "holder_authority_expired",
    )
    owner = journal["owner"]
    fields = Path(f"/proc/{owner['pid']}/stat").read_text().rsplit(") ", 1)[1].split()
    check(fields[19] == owner["start"] and fields[0] != "Z", "holder_owner_gone")
    request = {
        "version": 1,
        "run": journal["run"],
        "nonce": authority["nonce"],
        "requestId": secrets.token_hex(16),
        "action": action,
    }
    if action == "attach":
        check(argument.isdecimal(), "consumer_pid")
        request["pid"] = int(argument)
        consumer_identity(request["pid"], root)
    else:
        check(Path(argument) == directory / "attachment.json", "attachment_fixed_path")
        attachment = private_read(Path(argument))
        check(
            attachment.get("run") == journal["run"]
            and attachment.get("nonce") == authority["nonce"]
            and attachment.get("status") == "attached",
            "attachment_file_identity",
        )
        request["token"] = attachment["token"]
    lock_path = directory / "ipc.lock"
    descriptor = os.open(
        lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
    )
    try:
        info = os.fstat(descriptor)
        check(
            stat.S_ISREG(info.st_mode)
            and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_uid == os.getuid(),
            "attachment_lock",
        )
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        private_write(directory / "request.json", request)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = directory / "response.json"
            if response.exists():
                value = private_read(response)
                if value.get("requestId") == request["requestId"]:
                    check(
                        value.get("status") in {"attached", "detaching"},
                        "holder_request_rejected",
                    )
                    return value
            time.sleep(0.05)
        raise Refused("holder_response_unknown_no_replay")
    finally:
        os.close(descriptor)


def ready_origins() -> None:
    for origin in (LOCAL, PREVIEW):
        context = (
            ssl._create_unverified_context()
            if origin == LOCAL
            else ssl.create_default_context()
        )
        opener = build_opener(
            ProxyHandler({}), NoRedirect(), HTTPSHandler(context=context)
        )
        with opener.open(Request(origin + "/api/v1/readiness"), timeout=5) as response:
            raw = response.read(4097)
            check(
                response.status == 200
                and len(raw) <= 4096
                and json.loads(raw).get("ready") is True,
                "https_readiness",
            )
            check(response.url == origin + "/api/v1/readiness", "https_redirect")


def free_canonical_port() -> None:
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", 8001))


def restored_readiness() -> None:
    deadline = time.monotonic() + 30
    while True:
        try:
            ready_origins()
            return
        except Exception:
            check(time.monotonic() < deadline, "restored_https_deadline")
            time.sleep(0.2)


class Fixture:
    def __init__(
        self, root: Path, primary: Path, directory: Path, state: dict, runtime=command
    ):
        self.root, self.primary, self.directory, self.state, self.runtime = (
            root,
            primary,
            directory,
            state,
            runtime,
        )
        self.journal = directory / "journal.json"
        self.environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(
                ("SCHEMII_", "COMPOSE_", "DOCKER_", "PRESSURE_", "PG")
            )
        }
        self.environment.update(
            SCHEMII_QA_GATE_FD="4",
            SCHEMII_QA_LEASE_FD="3",
            SCHEMII_QA_LEASE_MODE="exclusive",
            PRESSURE_RUN=state["run"],
            PRESSURE_PROJECT=state["project"],
            PRESSURE_ROOT=str(root),
            PRESSURE_DIRECTORY=str(directory),
            PRESSURE_GID=str(os.getgid()),
            PRESSURE_APPLICATION_IMAGE=state["applicationImage"],
        )
        self.compose = [
            "compose",
            "--env-file",
            "/dev/null",
            "--project-name",
            state["project"],
            "--project-directory",
            str(root),
            "--file",
            str(root / "dev/pressure/compose.yaml"),
        ]

    def save(self, stage: str, **changes: Any) -> None:
        self.state.update(changes, stage=stage, updated=now())
        private_write(self.journal, self.state)

    def docker(self, arguments: list[str], *, timeout: int = 30) -> str:
        return self.runtime(
            ["docker", *arguments], environment=self.environment, timeout=timeout
        )

    def inspect(self, identifier: str) -> dict:
        check(bool(re.fullmatch(r"[0-9a-f]{64}", identifier)), "exact_container_id")
        value = json.loads(
            self.docker(["container", "inspect", "--format", INSPECT, identifier])
        )
        valid_container(value)
        check(value["id"] == identifier, "exact_container_match")
        return value

    def containers(self, project: str) -> list[dict]:
        ids = self.docker(
            [
                "container",
                "ls",
                "--all",
                "--no-trunc",
                "--quiet",
                "--filter",
                "label=com.docker.compose.project=" + project,
            ]
        ).splitlines()
        check(len(ids) <= 32 and len(ids) == len(set(ids)), "project_inventory_bound")
        return [self.inspect(identifier) for identifier in ids]

    def owned(self) -> list[dict]:
        records = self.containers(self.state["project"])
        for record in records:
            service = record["labels"].get("com.docker.compose.service")
            check(
                service in SERVICES
                and record["labels"].get(RUN_LABEL) == self.state["run"],
                "disposable_container_owner",
            )
            check(
                record["name"] == f"/{self.state['project']}-{service}-1",
                "disposable_container_name",
            )
            expected = (
                self.state["applicationImage"]
                if service in {"schemii", "observer-init"}
                else self.state["images"][
                    "ingress" if service == "ingress" else "database"
                ]
            )
            check(record["image"] == expected, "disposable_image")
            limits = {
                "metadata": (268435456, 500000000, 64),
                "source": (268435456, 500000000, 64),
                "schemii": (536870912, 1000000000, 128),
                "ingress": (67108864, 250000000, 32),
                "observer-init": (67108864, 250000000, 16),
            }
            check(
                (record["memory"], record["nanoCpus"], record["pids"])
                == limits[service],
                "disposable_runtime_limits",
            )
            old = self.state.get("containers", {}).get(service)
            check(
                old is None or fixed_identity(old) == fixed_identity(record),
                "disposable_identity_drift",
            )
            for mount in record["mounts"]:
                if mount["Type"] == "volume":
                    check(
                        mount["Name"]
                        in {
                            self.state["project"] + "-metadata",
                            self.state["project"] + "-source",
                            self.state["project"] + "-observer",
                        },
                        "disposable_volume_mount",
                    )
                elif mount["Type"] == "bind":
                    check(mount["Source"] in self.bind_paths(), "disposable_bind_mount")
                elif mount["Type"] == "tmpfs":
                    check(
                        mount.get("Destination") in {"/tmp", "/tls"}, "disposable_tmpfs"
                    )
                else:
                    raise Refused("unexpected_mount")
            if service == "ingress":
                check(
                    record["ports"]
                    == {"8443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8001"}]},
                    "disposable_canonical_port",
                )
            else:
                check(not record["ports"], "disposable_no_other_ports")
        check(
            len({r["labels"]["com.docker.compose.service"] for r in records})
            == len(records),
            "duplicate_service",
        )
        return records

    def bind_paths(self) -> set[str]:
        return {
            str(self.directory / name)
            for name in (
                "admin_password",
                "source_admin_password",
                "app_password",
                "encryption_key",
                "setup_token",
                "database.key",
                "database.crt",
                "ingress.key",
                "ingress.crt",
            )
        } | {
            str(self.root / "dev/pressure/app.py"),
            str(self.root / "dev/schemii.toml"),
            str(self.root / "dev/ingress/nginx.conf"),
        }

    def resource(self, kind: str, name: str) -> dict:
        check(kind in {"volume", "network"}, "resource_kind")
        value = json.loads(self.docker([kind, "inspect", name]))
        check(type(value) is list and len(value) == 1, "resource_inventory")
        result = value[0]
        check(
            result["Name"] == name
            and result.get("Labels", {}).get(RUN_LABEL) == self.state["run"]
            and result["Labels"].get("com.docker.compose.project")
            == self.state["project"],
            "resource_owner",
        )
        selected = {
            key: result.get(key)
            for key in ("Name", "Id", "CreatedAt", "Created", "Driver", "Labels")
        }
        previous = self.state.get("resources", {}).get(name)
        check(previous is None or previous == selected, "resource_identity_drift")
        return selected

    def inventory(self) -> None:
        records = self.owned()
        named_ids = self.docker(
            [
                "container",
                "ls",
                "--all",
                "--no-trunc",
                "--quiet",
                "--filter",
                "name=" + self.state["project"],
            ]
        ).splitlines()
        check(
            set(named_ids) == {record["id"] for record in records},
            "unlabeled_or_foreign_container",
        )
        # Reconcile a completed or uncertain Compose create by predeclared exact
        # run/project/service/image/volume identity, never by age or name alone.
        self.state["containers"] = {
            r["labels"]["com.docker.compose.service"]: r for r in records
        }
        resources = dict(self.state.get("resources", {}))
        for kind, suffixes in (
            ("volume", ("metadata", "source", "observer")),
            ("network", ("database", "ingress", "loopback")),
        ):
            names = self.docker(
                [
                    kind,
                    "ls",
                    "--format",
                    "{{.Name}}",
                    "--filter",
                    "label=com.docker.compose.project=" + self.state["project"],
                ]
            ).splitlines()
            expected = {self.state["project"] + "-" + suffix for suffix in suffixes}
            named = self.docker(
                [
                    kind,
                    "ls",
                    "--format",
                    "{{.Name}}",
                    "--filter",
                    "name=" + self.state["project"],
                ]
            ).splitlines()
            check(set(named) == set(names), "unlabeled_or_foreign_resource")
            check(
                set(names) <= expected and len(names) == len(set(names)),
                "unexpected_project_resource",
            )
            for name in names:
                resources[name] = self.resource(kind, name)
        self.save("inventory", resources=resources)

    def intent(self, operation: str) -> None:
        self.save("operation-intent", operation=operation)

    def context(self) -> dict:
        name = self.docker(["context", "show"]).strip()
        check(
            bool(re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name)), "runtime_context_name"
        )
        endpoint = json.loads(
            self.docker(
                [
                    "context",
                    "inspect",
                    name,
                    "--format",
                    "{{json .Endpoints.docker.Host}}",
                ]
            )
        )
        check(
            type(endpoint) is str
            and endpoint.startswith("unix:///")
            and "\n" not in endpoint,
            "local_runtime_context_required",
        )
        daemon = json.loads(self.docker(["info", "--format", "{{json .ID}}"]))
        check(
            type(daemon) is str and bool(daemon) and len(daemon) <= 256,
            "runtime_daemon_identity",
        )
        return {"name": name, "endpoint": endpoint, "daemon": daemon}

    def up(self, services: list[str]) -> None:
        check(set(services) <= SERVICES, "service_selection")
        initializer = services == ["observer-init"]
        check(
            "observer-init" not in services or initializer,
            "initializer_service_selection",
        )
        self.intent("up:" + ",".join(services))
        try:
            self.docker(
                [
                    *self.compose,
                    "up",
                    "--detach",
                    "--no-deps",
                    *([] if initializer else ["--wait", "--wait-timeout", "60"]),
                    *services,
                ],
                timeout=90,
            )
        finally:
            self.inventory()
        if initializer:
            baseline = self.state["containers"]["observer-init"]
            deadline = time.monotonic() + 10
            while baseline["state"] == "running":
                check(time.monotonic() < deadline, "observer_initializer_timeout")
                time.sleep(0.05)
                current = self.inspect(baseline["id"])
                check(
                    fixed_identity(current) == fixed_identity(baseline),
                    "observer_initializer_changed",
                )
                baseline = current
            self.state["containers"]["observer-init"] = baseline
            self.save("observer-initializer-observed")

    def exact_state(self, baseline: dict, target: str) -> dict:
        current = self.inspect(baseline["id"])
        check(
            fixed_identity(current) == fixed_identity(baseline), "exact_lifecycle_owner"
        )
        self.intent(target + ":" + baseline["id"])
        command_outcome = "completed"
        try:
            output = self.docker(
                ["container", "stop", "--time", "10", baseline["id"]]
                if target == "exited"
                else ["container", "start", baseline["id"]],
                timeout=20,
            )
            if output.strip() != baseline["id"]:
                command_outcome = "unexpected-output"
        except Exception:
            command_outcome = "uncertain-command"
        self.save(
            "exact-command-observed",
            lastLifecycleCommand={
                "containerId": baseline["id"],
                "target": target,
                "commandOutcome": command_outcome,
            },
        )
        # Resolve actual state even after a CLI exception; never replay a stop.
        current = self.inspect(baseline["id"])
        check(
            fixed_identity(current) == fixed_identity(baseline)
            and current["state"] == target,
            "lifecycle_state_unconfirmed",
        )

        return current

    def observation(self, *, stopped: bool = False) -> dict:
        """Copy one fixed small private file from the exact journal-owned app only."""
        self.inventory()
        owned = self.state["containers"]["schemii"]
        check(
            owned["state"] == ("exited" if stopped else "running"),
            "observer_native_state",
        )
        raw = self.runtime(
            [
                "docker",
                "container",
                "cp",
                owned["id"] + ":/qualification-state/observation.json",
                "-",
            ],
            environment=self.environment,
            timeout=5,
            limit=32768,
            binary=True,
        )
        check(type(raw) is bytes and len(raw) <= 32768, "observer_archive_bound")
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            members = archive.getmembers()
            check(len(members) == 1, "observer_archive_members")
            member = members[0]
            check(
                member.name == "observation.json"
                and member.isfile()
                and member.size <= 16384
                and member.uid == 10001
                and member.mode == 0o600,
                "observer_archive_owner",
            )
            value = json.loads(archive.extractfile(member).read(16385))
        current = self.inspect(owned["id"])
        check(
            fixed_identity(current) == fixed_identity(owned)
            and current["state"] == owned["state"]
            and current.get("startedAt") == owned.get("startedAt"),
            "observer_native_changed_during_copy",
        )
        validate_observation(value, self.state["run"], owned, fresh=not stopped)
        return {
            "containerId": owned["id"],
            "containerStartedAt": owned["startedAt"],
            "observation": value,
        }

    def stop_application(self) -> dict:
        before = self.observation()
        owned = self.state["containers"]["schemii"]
        started = time.monotonic()
        after = self.exact_state(owned, "exited")
        native = {
            key: after.get(key)
            for key in (
                "id",
                "state",
                "exitCode",
                "oomKilled",
                "startedAt",
                "finishedAt",
            )
        }
        native["elapsedSeconds"] = time.monotonic() - started
        native["commandOutcome"] = self.state["lastLifecycleCommand"]["commandOutcome"]
        native["classification"] = (
            "clean-exit"
            if after.get("exitCode") == 0 and after.get("oomKilled") is False
            else "forced-crashed-or-unknown"
        )
        # Keep the original native outcome even when a following proof fails.
        self.save("application-stop-observed", applicationStop=native)
        check(
            after.get("exitCode") == 0
            and type(after.get("exitCode")) is int
            and after.get("oomKilled") is False
            and after.get("startedAt") == owned.get("startedAt"),
            "application_stop_not_graceful",
        )
        observed = self.observation(stopped=True)
        validate_shutdown(before, observed, after)
        evidence = {
            "status": "observed-stock-lifespan-drained",
            "native": native,
            **observed,
        }
        self.save("application-lifespan-confirmed", applicationLifespan=evidence)
        return evidence

    def stop_retained(self) -> None:
        retained = self.state["retainedIngress"]
        current = self.inspect(retained["id"])
        check(
            fixed_identity(current) == fixed_identity(retained)
            and current["state"] == "running",
            "retained_ingress_changed",
        )
        self.exact_state(retained, "exited")
        self.save("retained-ingress-stopped", retainedStopIssued=True)
        free_canonical_port()

    def probe(self, phase: str) -> dict:
        interpreter = self.primary / ".venv/bin/python"
        check(interpreter.is_file(), "prepared_probe_interpreter_required")
        self.intent("probe:" + phase)
        process = subprocess.Popen(
            [
                str(interpreter),
                str(self.root / "dev/pressure/probe.py"),
                "--directory",
                str(self.directory),
                "--phase",
                phase,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=self.environment,
            pass_fds=(3, 4),
        )
        total = 0
        deadline = time.monotonic() + 180
        result = None
        transitions = []
        samples = 0
        try:
            self.save(
                "probe-start-intent", probeOwner={"pid": process.pid, "phase": phase}
            )
            process.stdin.write(b'{"authorized":true}\n')
            process.stdin.flush()
            while True:
                remaining = deadline - time.monotonic()
                check(
                    remaining > 0
                    and bool(select.select([process.stdout], [], [], remaining)[0]),
                    "probe_timeout",
                )
                line = self.protocol_line(process.stdout.fileno(), deadline)
                if not line:
                    break
                total += len(line)
                check(len(line) <= 65536 and total <= 262144, "probe_output_bound")
                message = json.loads(line)
                if message == {"operation": "sample-owned-app"}:
                    samples += 1
                    check(phase == "qualify" and samples <= 8, "probe_sample_bound")
                    reply = {"confirmed": True, "sample": self.observation()}
                    process.stdin.write((json.dumps(reply) + "\n").encode())
                    process.stdin.flush()
                elif message.get("operation") in {"stop-owned-app", "start-owned-app"}:
                    check(phase == "qualify", "probe_phase_operation")
                    expected = (
                        "stop-owned-app" if not transitions else "start-owned-app"
                    )
                    check(
                        message == {"operation": expected} and len(transitions) < 2,
                        "probe_operation_order",
                    )
                    self.inventory()
                    owned = self.state["containers"]["schemii"]
                    reply = {"confirmed": True}
                    if not transitions:
                        reply["lifespan"] = self.stop_application()
                    else:
                        self.exact_state(owned, "running")
                    transitions.append(expected)
                    process.stdin.write((json.dumps(reply) + "\n").encode())
                    process.stdin.flush()
                else:
                    check(
                        result is None and set(message) == {"result"},
                        "probe_result_schema",
                    )
                    result = message["result"]
            check(process.wait(timeout=2) == 0 and type(result) is dict, "probe_failed")
            if phase == "qualify":
                check(
                    transitions == ["stop-owned-app", "start-owned-app"],
                    "lifespan_probe_missing",
                )
            return result
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            process.stdout.close()
            process.stdin.close()

    @staticmethod
    def protocol_line(descriptor: int, deadline: float) -> bytes:
        # Read one byte at a time: this tiny fixed protocol never blocks on a
        # partial readline and never consumes bytes belonging to the next reply.
        result = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            check(
                remaining > 0
                and bool(select.select([descriptor], [], [], remaining)[0]),
                "probe_timeout",
            )
            part = os.read(descriptor, 1)
            if not part:
                check(not result, "probe_partial_reply")
                return b""
            result.extend(part)
            check(len(result) <= 65536, "probe_output_bound")
            if part == b"\n":
                return bytes(result)

    def secrets(self) -> None:
        self.intent("private-fixture-files")
        for name in (
            "admin_password",
            "source_admin_password",
            "app_password",
            "monitor_password",
            "blocker_password",
            "source_password",
            "setup_token",
            "account_password",
        ):
            value = secrets.token_urlsafe(32) + "\n"
            descriptor = os.open(
                self.directory / name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o640,
            )
            with os.fdopen(descriptor, "w") as stream:
                stream.write(value)
        descriptor = os.open(
            self.directory / "encryption_key",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o640,
        )
        with os.fdopen(descriptor, "w") as stream:
            import base64

            stream.write(base64.urlsafe_b64encode(os.urandom(32)).decode() + "\n")
        for name in ("database", "ingress"):
            command(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-sha256",
                    "-nodes",
                    "-days",
                    "1",
                    "-subj",
                    "/CN=localhost",
                    "-addext",
                    "subjectAltName=DNS:localhost,IP:127.0.0.1",
                    "-keyout",
                    str(self.directory / (name + ".key")),
                    "-out",
                    str(self.directory / (name + ".crt")),
                ],
                timeout=15,
            )
            for suffix in (".key", ".crt"):
                os.chmod(self.directory / (name + suffix), 0o640)

    def prepare_stack(self) -> None:
        self.secrets()
        self.up(["metadata", "source"])
        endpoints = {}
        for name in ("metadata", "source"):
            network = self.state["containers"][name]["networks"]
            selected = network.get(self.state["project"] + "-database")
            check(
                type(selected) is dict
                and bool(
                    re.fullmatch(r"\d+\.\d+\.\d+\.\d+", selected.get("IPAddress", ""))
                ),
                "private_database_endpoint",
            )
            endpoints[name] = selected["IPAddress"]
        self.save("databases-ready", endpoints=endpoints)
        prepared = self.probe("prepare")
        check(
            prepared.get("phase") == "prepared"
            and prepared.get("sourceBaseline", {}).get("ownedBackends") == 0
            and prepared["sourceBaseline"]["releaseVerified"] is False,
            "preparation_baseline_missing",
        )
        private_write(self.directory / "preparation.json", prepared)
        self.up(["observer-init"])
        initialized = self.state["containers"]["observer-init"]
        check(
            initialized["state"] == "exited"
            and initialized.get("exitCode") == 0
            and initialized.get("oomKilled") is False,
            "observer_volume_initialization",
        )
        self.up(["schemii"])
        self.stop_retained()
        self.up(["ingress"])
        restored_readiness()

    def run(self) -> dict:
        outcome = "failed"
        try:
            self.prepare_stack()
            result = self.probe("qualify")
            private_write(self.directory / "qualification.json", result)
            check(
                result.get("acceptance") is True
                and result.get("capacityAcceptance") is False,
                "qualification_incomplete",
            )
            outcome = "qualified"
            return result
        finally:
            self.save("qualification-finished", qualification=outcome)
            self.cleanup()

    def authority(self, status: str) -> dict:
        hold = self.state["hold"]
        value = {
            "version": 1,
            "kind": "launcher_owned_disposable_pressure",
            "status": status,
            "run": self.state["run"],
            "nonce": hold["nonce"],
            "directory": str(self.directory),
            "owner": self.state["owner"],
            "heartbeatAt": now(),
            "expiresAt": hold["expiresAt"],
            "canonicalOrigin": LOCAL,
            "previewOrigin": PREVIEW,
            "launcherPath": str(self.root / "start.sh"),
            "sourceWitnessConfig": str(self.directory / "source-monitor-config.json"),
            "credentialsFile": str(self.directory / "consumer-credentials.json"),
            "principal": private_read(self.directory / "principal.json"),
            "application": fixed_identity(self.state["containers"]["schemii"]),
            "source": fixed_identity(self.state["containers"]["source"]),
            "instrumentation": "fixture_only_stock_asgi_lifespan_and_owner_sampler",
            "stockCapacityEligible": False,
            "attachment": hold.get("attachment"),
        }
        private_write(self.directory / "authority.json", value)
        return value

    def hold_request(self) -> None:
        request_path = self.directory / "request.json"
        if not request_path.exists():
            return
        request = private_read(request_path)
        hold = self.state["hold"]
        if request.get("requestId") == hold.get("lastRequest"):
            return
        check(hold.get("requests", 0) < 1024, "holder_request_bound")
        check(
            request.get("version") == 1
            and request.get("run") == self.state["run"]
            and request.get("nonce") == hold["nonce"]
            and re.fullmatch(r"[0-9a-f]{32}", request.get("requestId", "")) is not None,
            "holder_request_identity",
        )
        action = request.get("action")
        attachment = hold.get("attachment")
        reply = {"version": 1, "requestId": request["requestId"], "status": "rejected"}
        try:
            if action == "attach":
                check(attachment is None, "fixture_already_attached")
                consumer = consumer_identity(request.get("pid"), self.root)
                token = secrets.token_hex(32)
                attachment = {
                    "version": 1,
                    "run": self.state["run"],
                    "nonce": hold["nonce"],
                    "token": token,
                    "consumer": consumer,
                    "sourceFingerprint": REQUIRED_CONSUMER_TREE_SHA256,
                    "heartbeatAt": now(),
                    "status": "attached",
                }
                private_write(self.directory / "attachment.json", attachment)
                hold["attachment"] = attachment
                reply.update(
                    status="attached",
                    attachmentFile=str(self.directory / "attachment.json"),
                )
            else:
                check(
                    action in {"heartbeat", "detach"}
                    and type(attachment) is dict
                    and secrets.compare_digest(
                        request.get("token", ""), attachment["token"]
                    ),
                    "attachment_authority",
                )
                # Revalidate argv/PID incarnation on every continuing attachment.
                check(
                    consumer_identity(attachment["consumer"]["pid"], self.root)
                    == attachment["consumer"],
                    "consumer_identity_changed",
                )
                if action == "heartbeat":
                    attachment["heartbeatAt"] = now()
                    private_write(self.directory / "attachment.json", attachment)
                    reply["status"] = "attached"
                else:
                    # A live controller may relinquish only after stopping API/source work;
                    # join the dedicated entire group before releasing restoration authority.
                    attachment["status"] = "detaching"
                    hold["attachment"] = attachment
                    attachment["detachAt"] = now()
                    self.save("consumer-detach-intent")
                    private_write(self.directory / "attachment.json", attachment)
                    reply["status"] = "detaching"
        except Exception:
            reply["status"] = "rejected"
        hold.update(
            lastRequest=request["requestId"], requests=hold.get("requests", 0) + 1
        )
        self.save("holder-request-observed")
        private_write(self.directory / "response.json", reply)

    def finish_consumers(self) -> None:
        attachment = self.state.get("hold", {}).get("attachment")
        if attachment:
            self.save("consumer-join-intent")
            join_consumer(attachment["consumer"])
            self.state["hold"]["attachment"] = None
            attachment["status"] = "joined"
            private_write(self.directory / "attachment.json", attachment)
            self.save("consumer-joined")

    def hold(self, seconds: int) -> None:
        check(type(seconds) is int and 60 <= seconds <= 3600, "hold_duration_bound")
        try:
            self.prepare_stack()
            result = self.probe("bootstrap")
            check(result.get("phase") == "bootstrapped", "hold_bootstrap_missing")
            credentials = {
                "version": 1,
                "run": self.state["run"],
                "admin": result["principal"],
                "sourceAdminRole": "pressure_admin",
                "sourceAdminConnection": {
                    "host": self.state["endpoints"]["source"],
                    "port": 5432,
                    "dbname": "pressure_source",
                    "user": "pressure_admin",
                    "sslmode": "require",
                    "passwordFile": str(self.directory / "source_admin_password"),
                },
                "appSourceConnection": {
                    "host": "source",
                    "port": 5432,
                    "database": "pressure_source",
                    "username": "pressure_source",
                    "sslMode": "require",
                    "connectTimeout": 3,
                    "passwordFile": str(self.directory / "source_password"),
                },
            }
            private_write(self.directory / "consumer-credentials.json", credentials)
            deadline = time.monotonic() + seconds
            self.save(
                "held",
                hold={
                    "nonce": secrets.token_hex(32),
                    "expiresAt": datetime.fromtimestamp(
                        time.time() + seconds, timezone.utc
                    ).isoformat(),
                    "attachment": None,
                    "requests": 0,
                },
            )
            self.authority("held")
            print(
                json.dumps(
                    {
                        "status": "held",
                        "authorityFile": str(self.directory / "authority.json"),
                    }
                ),
                flush=True,
            )
            while time.monotonic() < deadline:
                self.hold_request()
                attachment = self.state["hold"].get("attachment")
                if attachment and attachment.get("status") == "detaching":
                    # Let the requesting callback observe the ack and exit, then
                    # join the whole opted-in group before future attachments.
                    if (
                        datetime.now(timezone.utc) - instant(attachment["detachAt"])
                    ).total_seconds() >= 1:
                        self.finish_consumers()
                elif (
                    attachment
                    and (
                        datetime.now(timezone.utc) - instant(attachment["heartbeatAt"])
                    ).total_seconds()
                    > 30
                ):
                    self.finish_consumers()
                    raise Refused("consumer_heartbeat_expired")
                self.authority("held")
                time.sleep(0.2)
        finally:
            if "hold" in self.state:
                self.authority("closing")
                # Unknown/reused consumer identity blocks all restore/delete actions;
                # the durable journal remains explicit, never quietly borrowed.
                self.finish_consumers()
            self.cleanup()

    def cleanup(self) -> None:
        self.finish_consumers()
        cleaned = False
        try:
            # No bulk deletion against unknown or drifted project resources.
            self.inventory()
            self.intent("cleanup-owned-project")
            self.docker(
                [*self.compose, "down", "--volumes", "--timeout", "10"], timeout=45
            )
            check(not self.containers(self.state["project"]), "owned_containers_remain")
            for kind in ("volume", "network"):
                check(
                    not self.docker(
                        [
                            kind,
                            "ls",
                            "--format",
                            "{{.Name}}",
                            "--filter",
                            "label=com.docker.compose.project=" + self.state["project"],
                        ]
                    ).strip(),
                    "owned_resources_remain",
                )
            cleaned = True
        finally:
            if not cleaned:
                # Quarantine unknown resources. If and only if the recorded
                # disposable ingress still has its exact identity, stop that
                # owned port consumer so the retained service can be restored.
                ingress = self.state.get("containers", {}).get("ingress")
                if ingress is not None:
                    current = self.inspect(ingress["id"])
                    check(
                        fixed_identity(current) == fixed_identity(ingress),
                        "quarantine_ingress_changed",
                    )
                    if current["state"] == "running":
                        self.exact_state(ingress, "exited")
            retained = self.state["retainedIngress"]
            current = self.inspect(retained["id"])
            check(
                fixed_identity(current) == fixed_identity(retained),
                "retained_restore_owner_changed",
            )
            application = self.inspect(self.state["retainedApplication"]["id"])
            check(
                fixed_identity(application) == self.state["retainedApplication"]
                and application["state"] == "running"
                and application["health"] == "healthy",
                "retained_application_changed",
            )
            if current["state"] != "running":
                check(current["state"] == "exited", "retained_restore_state_unknown")
                free_canonical_port()
                self.exact_state(retained, "running")
            ready_origins()
            self.save(
                "restored" if cleaned else "recovery-required",
                restored=True,
                cleanup=cleaned,
                recoveryRequired=not cleaned,
            )


def begin(
    root: Path, primary: Path, directory: Path | None, runtime=command
) -> Fixture:
    run = directory.name if directory is not None else "pressure_" + uuid.uuid4().hex
    check(bool(re.fullmatch(r"pressure_[0-9a-f]{32}", run)), "run_directory_name")
    base = primary / ".schemii/metadata-pressure"
    directory = directory or base / run
    check(
        directory.parent == base and directory.name == run,
        "new_run_directory_is_generated",
    )
    check(not directory.exists() and not directory.is_symlink(), "new_run_required")
    # Inspect and verify the existing deployment before creating even a fixture
    # intent. An empty retained installation is refused, never auto-bootstrapped.
    minimal = {
        "run": run,
        "project": "schemii-pressure-" + run[9:],
        "applicationImage": "",
        "images": {},
    }
    fixture = Fixture(root, primary, directory, minimal, runtime)
    runtime_context = fixture.context()
    retained = fixture.containers("schemii-test")
    selected = {r["labels"].get("com.docker.compose.service"): r for r in retained}
    check(
        "ingress" in selected and "schemii" in selected, "retained_deployment_required"
    )
    check(
        all(
            sum(
                r["labels"].get("com.docker.compose.service") == service
                for r in retained
            )
            == 1
            for service in ("ingress", "schemii")
        ),
        "retained_service_ambiguous",
    )
    ingress, application = selected["ingress"], selected["schemii"]
    check(
        ingress["state"] == application["state"] == "running"
        and ingress["health"] == application["health"] == "healthy",
        "retained_deployment_healthy",
    )
    check(
        ingress["name"] == "/schemii-test-ingress-1"
        and application["name"] == "/schemii-test-schemii-1",
        "retained_service_identity",
    )
    check(
        ingress["ports"] == {"8443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8001"}]},
        "retained_canonical_port",
    )
    check(
        json.loads(
            fixture.docker(
                ["container", "exec", application["id"], "python", "-c", PACKAGE_SCRIPT]
            )
        )
        == package_identity(root),
        "retained_source_or_config_mismatch",
    )
    expected_command = [
        "uvicorn",
        "schemii.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--no-access-log",
    ]
    check(
        json.loads(
            fixture.docker(
                [
                    "image",
                    "inspect",
                    "--format",
                    "{{json .Config.Entrypoint}}",
                    application["image"],
                ]
            )
        )
        in (None, []),
        "application_image_entrypoint",
    )
    check(
        json.loads(
            fixture.docker(
                [
                    "image",
                    "inspect",
                    "--format",
                    "{{json .Config.Cmd}}",
                    application["image"],
                ]
            )
        )
        == expected_command,
        "application_image_command",
    )
    ready_origins()
    images = {}
    for key, image in (("database", POSTGRES), ("ingress", INGRESS)):
        value = json.loads(
            fixture.docker(["image", "inspect", "--format", "{{json .Id}}", image])
        )
        check(
            bool(re.fullmatch(r"sha256:[0-9a-f]{64}", value)),
            "required_pinned_image_missing",
        )
        images[key] = value
    check(ingress["image"] == images["ingress"], "retained_ingress_image")
    interpreter = primary / ".venv/bin/python"
    check(interpreter.is_file(), "prepared_probe_interpreter_required")
    command([str(interpreter), "-c", "import psycopg,httpx"], timeout=5)
    check(
        (root / "testing/load/source_monitor.py").is_file(),
        "source_monitor_dependency_missing",
    )
    check(
        sha(root / "testing/load/source_monitor.py") == REQUIRED_SOURCE_WITNESS_SHA256,
        "source_witness_fingerprint",
    )
    check(
        command(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "--error-unmatch",
                "testing/load/source_monitor.py",
            ]
        ).strip()
        == "testing/load/source_monitor.py",
        "committed_source_witness_required",
    )
    command(
        [
            "git",
            "-C",
            str(root),
            "diff",
            "--quiet",
            "HEAD",
            "--",
            "testing/load/source_monitor.py",
        ]
    )
    for kind in ("container", "volume", "network"):
        check(
            not fixture.docker(
                [
                    kind,
                    "ls",
                    "--quiet",
                    "--filter",
                    "label=com.docker.compose.project=" + minimal["project"],
                ]
            ).strip(),
            "fresh_project_required",
        )
        # Random names are not absence proof. Foreign/unlabeled resources with
        # these new names must be rejected before Compose can attach to them.
        listing = [
            kind,
            "ls",
            *(["--all"] if kind == "container" else []),
            "--format",
            "{{.Names}}" if kind == "container" else "{{.Name}}",
            "--filter",
            "name=" + minimal["project"],
        ]
        check(not fixture.docker(listing).strip(), "fresh_resource_names_required")
    for ancestor in (primary / ".schemii", base):
        check(not ancestor.is_symlink(), "private_directory_symlink")
        ancestor.mkdir(mode=0o700, exist_ok=True)
    directory.mkdir(mode=0o700)
    fixture.state.update(
        version=1,
        root=str(root),
        primary=str(primary),
        directory=str(directory),
        created=now(),
        applicationImage=application["image"],
        images=images,
        retainedIngress=ingress,
        retainedApplication=fixed_identity(application),
        source=package_identity(root),
        assets={name: sha(root / name) for name in ASSETS},
        sourceWitness=sha(root / "testing/load/source_monitor.py"),
        containers={},
        resources={},
        runtimeContext=runtime_context,
        owner={
            "pid": os.getpid(),
            "start": Path(f"/proc/{os.getpid()}/stat").read_text().split()[21],
        },
        bootstrapConsent="explicit-disposable-fixture-only",
        restored=False,
        cleanup=False,
    )
    fixture.environment["PRESSURE_APPLICATION_IMAGE"] = application["image"]
    fixture.save("intent-before-create")
    return fixture


def recover(root: Path, primary: Path, directory: Path, runtime=command) -> Fixture:
    check(
        directory.parent == primary / ".schemii/metadata-pressure"
        and bool(re.fullmatch(r"pressure_[0-9a-f]{32}", directory.name)),
        "recovery_directory",
    )
    check(
        not directory.is_symlink() and stat.S_IMODE(directory.stat().st_mode) == 0o700,
        "recovery_directory_permissions",
    )
    state = private_read(directory / "journal.json")
    check(
        state.get("version") == 1
        and state.get("directory") == str(directory)
        and state.get("root") == str(root)
        and state.get("primary") == str(primary),
        "recovery_journal_identity",
    )
    check(
        state["run"] == directory.name
        and state["project"] == "schemii-pressure-" + directory.name[9:],
        "recovery_run_identity",
    )
    check(
        state["assets"] == {name: sha(root / name) for name in ASSETS},
        "recovery_source_drift",
    )
    check(
        state["sourceWitness"] == sha(root / "testing/load/source_monitor.py"),
        "recovery_source_witness_drift",
    )
    owner = state["owner"]
    process = Path(f"/proc/{owner['pid']}/stat")
    if process.exists():
        check(
            process.read_text().split()[21] != owner["start"],
            "recorded_controller_still_active",
        )
    check(state.get("stage") != "restored", "already_restored")
    fixture = Fixture(root, primary, directory, state, runtime)
    check(
        fixture.context() == state["runtimeContext"], "recovery_runtime_context_drift"
    )
    return fixture


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--primary", type=Path, required=True)
    parser.add_argument("--common", type=Path, required=True)
    parser.add_argument(
        "action",
        choices=(
            "--qualify-metadata-pressure",
            "--recover-metadata-pressure",
            "--hold-metadata-pressure",
            "--metadata-pressure-attach",
            "--metadata-pressure-heartbeat",
            "--metadata-pressure-detach",
        ),
    )
    parser.add_argument("directory", nargs="?", type=Path)
    parser.add_argument("argument", nargs="?")
    # Action begins with --; the shell passes -- before it.
    args = parser.parse_args()
    fixture = None
    try:
        check(
            args.root == Path(__file__).resolve().parents[2],
            "controller_checkout_identity",
        )
        checkout_authority(args.root, args.primary, args.common)
        if args.action.startswith("--metadata-pressure-"):
            check(
                args.directory is not None and args.argument is not None,
                "attachment_arguments",
            )
            result = attachment_request(
                args.root,
                args.primary,
                args.directory,
                args.action.removeprefix("--metadata-pressure-"),
                args.argument,
            )
            print(json.dumps(result), flush=True)
            return 0
        verify_leases(args.common)
        if args.action == "--recover-metadata-pressure":
            check(args.directory is not None, "recovery_directory_required")
            fixture = recover(args.root, args.primary, args.directory)
            fixture.cleanup()
        else:
            seconds = None
            if args.action == "--hold-metadata-pressure":
                check(
                    args.directory is not None
                    and args.argument is None
                    and str(args.directory).isdecimal(),
                    "hold_seconds_required",
                )
                seconds = int(str(args.directory))
                check(60 <= seconds <= 3600, "hold_duration_bound")
            fixture = begin(
                args.root, args.primary, None if seconds is not None else args.directory
            )
            print(
                json.dumps(
                    {"journal": str(fixture.journal), "status": "intent-recorded"}
                ),
                flush=True,
            )
            fixture.hold(seconds) if seconds is not None else fixture.run()
        return 0
    except (Exception, KeyboardInterrupt):
        if fixture is not None and not (
            fixture.state.get("restored") and fixture.state.get("cleanup")
        ):
            with suppress(Exception):
                fixture.save("recovery-required", recoveryRequired=True)
        print(
            "Schemii pressure qualification failed; retain its private journal and use explicit recovery after ownership review",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":

    def cancelled(_signal, _frame):
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, cancelled)
    signal.signal(signal.SIGHUP, cancelled)
    raise SystemExit(main())
