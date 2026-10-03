"""Bounded, opt-in launcher receipts. No command, log or environment payloads.

Only start.sh calls the writer. Consumers may use validate_records before exposing
the JSONL. Local launcher_id fingerprints these two instrumentation source files;
it does not claim to identify an immutable application checkout.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

MAX_BYTES = 8192
PHASES = ("preparation", "build", "replacement", "readiness", "post-start")
OUTCOMES = {"passed", "failed", "cancelled"}
META = {
    "schema",
    "source_kind",
    "source_sha",
    "launcher_id",
    "run_id",
    "run_attempt",
    "project",
    "shard",
}
FIELDS = {
    "launcher-start": {"kind"},
    "launcher-phase": {"kind", "phase", "outcome", "duration_ms"},
    "launcher-end": {"kind", "outcome", "wall_ms"},
}


def _number(value: object, *, integer: bool = False) -> bool:
    if not isinstance(value, (int, float)):
        return False
    return (
        type(value) in ({int} if integer else {int, float})
        and 0 <= value <= 10**15
        and math.isfinite(value)
    )


def validate_records(data: bytes) -> list[dict]:
    """Validate complete or partial records; partial receipts never imply success."""
    if not data or len(data) > MAX_BYTES or not data.endswith(b"\n"):
        raise ValueError("Invalid launcher timing receipt")
    records = [json.loads(line) for line in data.splitlines()]
    meta = None
    phases: list[dict] = []
    ended = False
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError("Invalid launcher timing receipt")
        kind = record.get("kind")
        if (
            not isinstance(kind, str)
            or kind not in FIELDS
            or set(record) != META | FIELDS[kind]
        ):
            raise ValueError("Invalid launcher timing receipt")
        current = {key: record[key] for key in META}
        if meta is None:
            meta = current
        if current != meta or ended:
            raise ValueError("Invalid launcher timing receipt")
        if (
            type(record["schema"]) is not int
            or record["schema"] != 1
            or record["source_kind"] not in ("local", "hosted")
            or not isinstance(record["launcher_id"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", record["launcher_id"])
            or not all(
                _number(record[key], integer=True)
                for key in ("run_id", "run_attempt", "shard")
            )
            or record["project"] not in ("none", "desktop-chromium", "android-chromium")
            or record["shard"] > 6
            or (record["project"] == "none" and record["shard"] != 0)
        ):
            raise ValueError("Invalid launcher timing receipt")
        if record["source_kind"] == "hosted":
            if (
                not isinstance(record["source_sha"], str)
                or not re.fullmatch(r"[0-9a-f]{40}", record["source_sha"])
                or not record["run_id"]
                or not record["run_attempt"]
            ):
                raise ValueError("Invalid launcher timing receipt")
        elif (
            record["source_sha"] is not None
            or record["run_id"] != 0
            or record["run_attempt"] != 0
        ):
            raise ValueError("Invalid launcher timing receipt")
        if kind == "launcher-start":
            if index != 0:
                raise ValueError("Invalid launcher timing receipt")
        elif not index or records[0]["kind"] != "launcher-start":
            raise ValueError("Invalid launcher timing receipt")
        elif kind == "launcher-phase":
            if (
                len(phases) >= len(PHASES)
                or record["phase"] != PHASES[len(phases)]
                or not isinstance(record["outcome"], str)
                or record["outcome"] not in OUTCOMES
                or not _number(record["duration_ms"])
                or (
                    any(phase["outcome"] != "passed" for phase in phases)
                    and not (
                        phases[-1]["phase"] == "readiness"
                        and record["phase"] == "post-start"
                        and record["outcome"] != "passed"
                    )
                )
            ):
                raise ValueError("Invalid launcher timing receipt")
            phases.append(record)
        else:
            if (
                not isinstance(record["outcome"], str)
                or record["outcome"] not in OUTCOMES
                or not _number(record["wall_ms"])
                or not phases
                or phases[-1]["outcome"] != record["outcome"]
                or (record["outcome"] == "passed" and len(phases) != len(PHASES))
                or abs(
                    sum(phase["duration_ms"] for phase in phases) - record["wall_ms"]
                )
                > 0.01
            ):
                raise ValueError("Invalid launcher timing receipt")
            ended = True
    return records


def validate_startup(value: object) -> dict:
    """Return the closed consolidated schema, or raise ValueError.

    Exactly five ordered phase objects contain phase/outcome/duration_ms. Missing
    observations and a missing terminal are null, never fabricated zero timings.
    preflight is always 'unmeasured': measurement starts after runtime access and
    its possible stale-group re-exec. A hosted SHA identifies the clean checkout;
    a local launcher_id fingerprints only start.sh and this helper.
    """
    if (
        not isinstance(value, dict)
        or set(value) != META | {"preflight", "phases", "outcome", "wall_ms"}
        or value["preflight"] != "unmeasured"
        or not isinstance(value["phases"], list)
        or len(value["phases"]) != len(PHASES)
    ):
        raise ValueError("Invalid consolidated launcher timing")
    meta = {key: value[key] for key in META}
    records = [{**meta, "kind": "launcher-start"}]
    missing = False
    phases = []
    for name, phase in zip(PHASES, value["phases"], strict=True):
        if (
            not isinstance(phase, dict)
            or set(phase) != {"phase", "outcome", "duration_ms"}
            or phase["phase"] != name
        ):
            raise ValueError("Invalid consolidated launcher timing")
        if phase["outcome"] is None:
            if phase["duration_ms"] is not None:
                raise ValueError("Invalid consolidated launcher timing")
            missing = True
        else:
            if missing:
                raise ValueError("Invalid consolidated launcher timing")
            records.append({**meta, "kind": "launcher-phase", **phase})
        phases.append({key: phase[key] for key in ("phase", "outcome", "duration_ms")})
    if value["outcome"] is None:
        if value["wall_ms"] is not None:
            raise ValueError("Invalid consolidated launcher timing")
    else:
        records.append(
            {
                **meta,
                "kind": "launcher-end",
                "outcome": value["outcome"],
                "wall_ms": value["wall_ms"],
            }
        )
    try:
        data = b"".join(
            json.dumps(record, allow_nan=False).encode() + b"\n" for record in records
        )
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Invalid consolidated launcher timing") from None
    validate_records(data)
    return {
        **meta,
        "preflight": "unmeasured",
        "phases": phases,
        "outcome": value["outcome"],
        "wall_ms": value["wall_ms"],
    }


def load_startup(path: str | Path) -> dict:
    """Read bounded, nonsymlink JSONL and return validate_startup's schema."""
    parent, name = _open_parent(os.path.abspath(os.fspath(path)))
    try:
        descriptor = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
        )
    finally:
        os.close(parent)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Invalid launcher timing receipt")
        records = validate_records(os.read(descriptor, MAX_BYTES + 1))
    finally:
        os.close(descriptor)
    meta = {key: records[0][key] for key in META}
    observed = {
        record["phase"]: record
        for record in records
        if record["kind"] == "launcher-phase"
    }
    end = records[-1] if records[-1]["kind"] == "launcher-end" else {}
    return validate_startup(
        {
            **meta,
            "preflight": "unmeasured",
            "phases": [
                {
                    "phase": name,
                    "outcome": observed.get(name, {}).get("outcome"),
                    "duration_ms": observed.get(name, {}).get("duration_ms"),
                }
                for name in PHASES
            ],
            "outcome": end.get("outcome"),
            "wall_ms": end.get("wall_ms"),
        }
    )


def _numeric(name: str) -> int:
    value = os.environ.get(name, "")
    return int(value) if re.fullmatch(r"[0-9]{1,15}", value) else 0


def _metadata(root: Path) -> dict:
    sha = os.environ.get("CI_TELEMETRY_SHA", "")
    hosted = False
    run_id = _numeric("CI_TELEMETRY_RUN_ID")
    run_attempt = _numeric("CI_TELEMETRY_RUN_ATTEMPT")
    if (
        os.environ.get("GITHUB_ACTIONS") == "true"
        and re.fullmatch(r"[0-9a-f]{40}", sha)
        and run_id
        and run_attempt
    ):
        try:
            head = (
                subprocess.run(
                    ["git", "-C", str(root), "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    timeout=10,
                )
                .stdout.strip()
                .decode("ascii")
            )
            clean = (
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(root),
                        "status",
                        "--porcelain",
                        "--untracked-files=normal",
                    ],
                    check=True,
                    capture_output=True,
                    timeout=10,
                ).stdout
                == b""
            )
            hosted = head == sha and clean
        except (OSError, subprocess.SubprocessError, UnicodeError):
            pass
    digest = hashlib.sha256()
    for name in ("start.sh", "scripts/ci/startup_timing.py"):
        source = (root / name).read_bytes()
        digest.update(len(source).to_bytes(8, "big"))
        digest.update(source)
    project = os.environ.get("CI_TELEMETRY_PROJECT", "none")
    if project not in ("none", "desktop-chromium", "android-chromium"):
        project = "none"
    shard = _numeric("CI_TELEMETRY_SHARD")
    return {
        "schema": 1,
        "source_kind": "hosted" if hosted else "local",
        "source_sha": sha if hosted else None,
        "launcher_id": digest.hexdigest(),
        "run_id": run_id if hosted else 0,
        "run_attempt": run_attempt if hosted else 0,
        "project": project,
        "shard": shard if project != "none" and shard <= 6 else 0,
    }


def _open_parent(path: str) -> tuple[int, str]:
    # Traverse with directory descriptors, refusing symlinks at every component.
    parts = path.split("/")
    if not path.startswith("/") or any(part in ("", ".", "..") for part in parts[1:]):
        raise ValueError("Unsafe launcher timing path")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[1:-1]:
            following = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = following
        return descriptor, parts[-1]
    except BaseException:
        os.close(descriptor)
        raise


def _append(descriptor: int, records: list[dict], previous_size: int) -> None:
    data = b"".join(
        json.dumps(record, separators=(",", ":"), allow_nan=False).encode() + b"\n"
        for record in records
    )
    if previous_size + len(data) > MAX_BYTES:
        raise ValueError("Launcher timing receipt exceeds bound")
    if os.write(descriptor, data) != len(data):
        raise OSError("Incomplete launcher timing write")
    os.fsync(descriptor)


def begin(path: str, root: Path) -> str:
    started = time.monotonic_ns()
    meta = _metadata(root)
    parent, name = _open_parent(path)
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
    finally:
        os.close(parent)
    try:
        info = os.fstat(descriptor)
        _append(descriptor, [{**meta, "kind": "launcher-start"}], 0)
        return f"{info.st_dev}:{info.st_ino}:{started}"
    finally:
        os.close(descriptor)


def update(
    path: str,
    token: str,
    phase: str,
    outcome: str,
    phase_start: str,
    *,
    finish: bool = False,
) -> int:
    if not re.fullmatch(r"[0-9]+:[0-9]+:[0-9]+", token):
        raise ValueError("Invalid launcher timing owner")
    device, inode, started = map(int, token.split(":"))
    parent, name = _open_parent(path)
    try:
        descriptor = os.open(
            name, os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
        )
    finally:
        os.close(parent)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        info = os.fstat(descriptor)
        if (
            (info.st_dev, info.st_ino) != (device, inode)
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > MAX_BYTES
        ):
            raise ValueError("Unsafe launcher timing output")
        data = os.read(descriptor, MAX_BYTES + 1)
        records = validate_records(data)
        if records[-1]["kind"] == "launcher-end":
            raise ValueError("Launcher timing already ended")
        if (
            phase not in PHASES
            or outcome not in OUTCOMES
            or not re.fullmatch(r"[0-9]+", phase_start)
        ):
            raise ValueError("Invalid launcher timing phase")
        now = time.monotonic_ns()
        previous = int(phase_start)
        if not started <= previous <= now:
            raise ValueError("Invalid launcher timing clock")
        meta = {key: records[0][key] for key in META}
        additions = [
            {
                **meta,
                "kind": "launcher-phase",
                "phase": phase,
                "outcome": outcome,
                "duration_ms": round((now - previous) / 1_000_000, 3),
            }
        ]
        if finish:
            additions.append(
                {
                    **meta,
                    "kind": "launcher-end",
                    "outcome": outcome,
                    "wall_ms": round((now - started) / 1_000_000, 3),
                }
            )
        candidate = data + b"".join(
            json.dumps(record).encode() + b"\n" for record in additions
        )
        validate_records(candidate)
        _append(descriptor, additions, len(data))
        return now
    finally:
        os.close(descriptor)


def main() -> int:
    try:
        action, path, *arguments = sys.argv[1:]
        if action == "begin" and len(arguments) == 1:
            print(begin(path, Path(arguments[0])))
        elif action in ("phase", "finish") and len(arguments) == 4:
            token, phase, outcome, phase_start = arguments
            print(
                update(
                    path, token, phase, outcome, phase_start, finish=action == "finish"
                )
            )
        else:
            raise ValueError("Invalid launcher timing invocation")
        return 0
    except (OSError, ValueError, TypeError, OverflowError, subprocess.SubprocessError):
        # Never publish a path, argv, environment, exception or command output.
        print(
            "Schemii launcher timing error: receipt operation failed", file=sys.stderr
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
