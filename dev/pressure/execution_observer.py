"""Bounded, private receipt correlation for the disposable pressure wrapper only.

The nonce supplies correlation, never application authority. No source inspection,
SQL, result contents or public execution-read permission is involved here.
"""

from contextvars import ContextVar
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import stat
from threading import RLock
import time

MAXIMUM_BYTES = 131072
MAXIMUM_OPERATIONS = 128
STATES = {
    "reserved",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "partial_committed",
}
HEADER = b"x-schemii-fixture-operation"
_context = ContextVar("fixture_execution_operation", default=None)


def now():
    return datetime.now(timezone.utc).isoformat()


def scalar(value):
    return (
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) is not None
    )


def nonce(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def declaration(value):
    if type(value) is not dict or set(value) != {
        "operation",
        "actor",
        "resource",
        "method",
        "path",
        "kind",
    }:
        raise ValueError("execution_declaration")
    if (
        not nonce(value["operation"])
        or not scalar(value["actor"])
        or not scalar(value["resource"])
    ):
        raise ValueError("execution_declaration_identity")
    resource = re.escape(value["resource"])
    path = value["path"]
    valid = (
        (
            value["kind"] == "dashboard-stream"
            and path
            == f"/api/v1/schemer/dashboards/{value['resource']}/executions/stream"
        )
        or (
            value["kind"] == "tile-csv"
            and isinstance(path, str)
            and re.fullmatch(
                rf"/api/v1/schemer/dashboards/{resource}/tiles/[A-Za-z0-9_-]{{1,128}}/export",
                path,
            )
        )
        or (
            value["kind"] in {"model-stream", "model-csv"}
            and path
            == "/api/v1/schemer/query/"
            + ("export" if value["kind"] == "model-csv" else "stream")
        )
    )
    if value["method"] != "POST" or not valid:
        raise ValueError("execution_declaration_route")
    return dict(value)


def read_commands(path, run, process):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o640
            or info.st_gid not in {*os.getgroups(), os.getgid()}
            or info.st_size > MAXIMUM_BYTES
        ):
            raise ValueError("execution_commands_file")
        raw = stream.read(MAXIMUM_BYTES + 1)
    if len(raw) > MAXIMUM_BYTES:
        raise ValueError("execution_commands_budget")
    value = json.loads(raw)
    if (
        set(value)
        != {"version", "run", "process", "command", "operations", "acknowledge"}
        or value["version"] != 1
        or value["run"] != run
        or value["process"] != process
        or not nonce(value["command"])
        or type(value["operations"]) is not list
        or len(value["operations"]) > MAXIMUM_OPERATIONS
        or type(value["acknowledge"]) is not list
        or len(value["acknowledge"]) > MAXIMUM_OPERATIONS
        or not all(nonce(item) for item in value["acknowledge"])
    ):
        raise ValueError("execution_commands_identity")
    operations = [declaration(item) for item in value["operations"]]
    if len({item["operation"] for item in operations}) != len(operations):
        raise ValueError("execution_commands_duplicate")
    return value


def validate_snapshot(value, run, process):
    """Closed wire projection; the holder never exports arbitrary app JSON."""
    if (
        type(value) is not dict
        or set(value)
        != {
            "version",
            "run",
            "process",
            "at",
            "command",
            "losses",
            "acknowledged",
            "operations",
            "instrumentation",
        }
        or value["version"] != 1
        or value["run"] != run
        or value["process"] != process
    ):
        raise ValueError("execution_observation_identity")
    if (
        not isinstance(value["at"], str)
        or len(value["at"]) > 40
        or value["command"] is not None
        and not nonce(value["command"])
    ):
        raise ValueError("execution_observation_time")
    for key in ("losses", "acknowledged"):
        if type(value[key]) is not int or not 0 <= value[key] <= 2147483647:
            raise ValueError("execution_observation_count")
    operations = value["operations"]
    if type(operations) is not list or len(operations) > MAXIMUM_OPERATIONS:
        raise ValueError("execution_observation_operations")
    seen = set()
    for item in operations:
        if type(item) is not dict or set(item) != {
            "operation",
            "actor",
            "resource",
            "method",
            "path",
            "kind",
            "request",
            "unknown",
            "receipts",
        }:
            raise ValueError("execution_observation_fields")
        declaration(
            {
                key: item[key]
                for key in ("operation", "actor", "resource", "method", "path", "kind")
            }
        )
        if (
            item["operation"] in seen
            or item["request"] not in {"declared", "active", "complete", "failed"}
            or type(item["unknown"]) is not bool
        ):
            raise ValueError("execution_observation_state")
        seen.add(item["operation"])
        if type(item["receipts"]) is not list or len(item["receipts"]) > 1:
            raise ValueError("execution_observation_receipts")
        for receipt in item["receipts"]:
            if set(receipt) not in (
                {"id", "owner", "status", "observedAt"},
                {"id", "owner", "status", "revision", "observedAt"},
            ):
                raise ValueError("execution_observation_receipt_fields")
            if (
                not isinstance(receipt["id"], str)
                or re.fullmatch(r"cex_[0-9a-f]{32}", receipt["id"]) is None
                or receipt["owner"] != item["actor"]
                or receipt["status"] not in STATES | {"unknown"}
            ):
                raise ValueError("execution_observation_receipt_identity")
            if receipt["observedAt"] is not None and (
                not isinstance(receipt["observedAt"], str)
                or len(receipt["observedAt"]) > 40
            ):
                raise ValueError("execution_observation_receipt_time")
            if "revision" in receipt and (
                type(receipt["revision"]) is not int or receipt["revision"] < 1
            ):
                raise ValueError("execution_observation_revision")
    instrumentation = value["instrumentation"]
    if type(instrumentation) is not dict or set(instrumentation) != {
        "kind",
        "maximumBytes",
        "maximumOperations",
        "maximumReadsPerSample",
        "metadataReads",
        "samplingSeconds",
        "captureWrites",
        "captureSeconds",
        "overheadMeasured",
        "stockCapacityEligible",
    }:
        raise ValueError("execution_observation_instrumentation")
    if (
        instrumentation["kind"] != "fixture_only_exact_execution_receipts"
        or instrumentation["maximumBytes"] != MAXIMUM_BYTES
        or instrumentation["maximumOperations"] != MAXIMUM_OPERATIONS
        or instrumentation["maximumReadsPerSample"] != 16
        or instrumentation["overheadMeasured"] is not False
        or instrumentation["stockCapacityEligible"] is not False
        or type(instrumentation["metadataReads"]) is not int
        or instrumentation["metadataReads"] < 0
        or type(instrumentation["samplingSeconds"]) not in {int, float}
        or not 0 <= instrumentation["samplingSeconds"] < 1e9
        or type(instrumentation["captureWrites"]) is not int
        or instrumentation["captureWrites"] < 0
        or type(instrumentation["captureSeconds"]) not in {int, float}
        or not 0 <= instrumentation["captureSeconds"] < 1e9
    ):
        raise ValueError("execution_observation_instrumentation_value")
    return value


class ExecutionObserver:
    def __init__(
        self,
        run,
        directory,
        process,
        commands=Path("/qualification-input/execution-commands.json"),
    ):
        self.run, self.directory, self.process, self.commands = (
            run,
            directory,
            process,
            commands,
        )
        self.lock = RLock()
        self.operations = {}
        self.command = None
        self.console = None
        self.losses = 0
        self.acknowledged = 0
        self.reads = 0
        self.seconds = 0.0
        self.capture_writes = 0
        self.capture_seconds = 0.0
        self.cursor = 0

    def loss(self, operation=None):
        with self.lock:
            self.losses = min(2147483647, self.losses + 1)
            if operation in self.operations:
                self.operations[operation]["unknown"] = True

    def install(self, console):
        if self.console is console:
            return
        if self.console is not None:
            self.loss()
            return
        original = console.reserve_read_target

        def reserve(*args, **kwargs):
            owner = args[0] if args else kwargs.get("owner_id")
            context = _context.get()
            operation = context[1] if context and context[0] is self else None
            try:
                result = original(*args, **kwargs)
            except BaseException:
                self.loss(operation)
                raise
            # This runs in the actual synchronous worker, before its successful
            # return, including when the awaiting request has been cancelled.
            started = time.monotonic()
            try:
                access = kwargs.get("connection_access")
                with self.lock:
                    entry = self.operations.get(operation)
                    if (
                        entry is None
                        or entry["actor"] != owner
                        or getattr(access, "actor", None) != owner
                        or getattr(access, "resource_id", None) != entry["resource"]
                        or not re.fullmatch(r"cex_[0-9a-f]{32}", result.id)
                        or entry["receipts"]
                    ):
                        self.loss(operation)
                    else:
                        entry["receipts"].append(
                            {
                                "id": result.id,
                                "owner": owner,
                                "status": "unknown",
                                "observedAt": None,
                            }
                        )
                    self.capture_writes += 1
                    self.write()
            except BaseException:
                # Observation may never replace the real reserve return or
                # interrupt the application's cancel -> join -> close cleanup.
                self.loss(operation)
            finally:
                self.capture_seconds += time.monotonic() - started
            return result

        console.reserve_read_target = reserve
        self.console = console

    def reload(self):
        value = read_commands(self.commands, self.run, self.process)
        with self.lock:
            if self.command == value["command"]:
                return
            for operation in value["acknowledge"]:
                entry = self.operations.get(operation)
                if entry is not None:
                    if (
                        entry["request"] not in {"complete", "failed"}
                        or entry["unknown"]
                        or len(entry["receipts"]) != 1
                        or entry["receipts"][0]["status"]
                        not in STATES - {"reserved", "running"}
                    ):
                        self.loss(operation)
                    else:
                        del self.operations[operation]
                        self.acknowledged += 1
            for item in value["operations"]:
                key = item["operation"]
                if key in self.operations:
                    if any(self.operations[key][name] != item[name] for name in item):
                        self.loss(key)
                elif len(self.operations) >= MAXIMUM_OPERATIONS:
                    self.loss()
                else:
                    self.operations[key] = {
                        **item,
                        "request": "declared",
                        "unknown": False,
                        "receipts": [],
                    }
            self.command = value["command"]

    def begin(self, scope):
        headers = [
            value for key, value in scope.get("headers", []) if key.lower() == HEADER
        ]
        if not headers:
            return None, None
        operation = None
        try:
            if len(headers) != 1:
                raise ValueError("execution_header")
            operation = headers[0].decode("ascii")
            self.reload()
            with self.lock:
                entry = self.operations.get(operation)
                if (
                    entry is None
                    or entry["request"] != "declared"
                    or entry["path"] != scope.get("path")
                    or entry["method"] != scope.get("method")
                ):
                    raise ValueError("execution_request_binding")
                entry["request"] = "active"
            return operation, _context.set((self, operation))
        except BaseException:
            self.loss(operation)
            return None, None

    def finish(self, operation, token, failed):
        try:
            if token is not None:
                _context.reset(token)
            if operation is not None:
                with self.lock:
                    entry = self.operations[operation]
                    entry["request"] = "failed" if failed else "complete"
                    self.write()
        except BaseException:
            self.loss(operation)

    def sample(self):
        started = time.monotonic()
        try:
            if self.commands.exists():
                self.reload()
            with self.lock:
                selected = [
                    (key, receipt["owner"], receipt["id"])
                    for key, entry in self.operations.items()
                    for receipt in entry["receipts"]
                ]
                count = min(16, len(selected))
                selected = [
                    selected[(self.cursor + i) % len(selected)] for i in range(count)
                ]
                self.cursor += count
            for key, owner, identifier in selected:
                try:
                    self.reads += 1
                    result = self.console.get_owned(owner, identifier)
                    if (
                        result.id != identifier
                        or result.status not in STATES
                        or type(result.revision) is not int
                        or result.revision < 1
                    ):
                        raise ValueError("execution_status_binding")
                    projected = {
                        "id": identifier,
                        "owner": owner,
                        "status": result.status,
                        "revision": result.revision,
                        "observedAt": now(),
                    }
                except BaseException:
                    projected = {
                        "id": identifier,
                        "owner": owner,
                        "status": "unknown",
                        "observedAt": now(),
                    }
                with self.lock:
                    if key in self.operations:
                        self.operations[key]["receipts"] = [projected]
            with self.lock:
                self.write()
        except BaseException:
            self.loss()
        finally:
            self.seconds += time.monotonic() - started

    def snapshot(self):
        return {
            "version": 1,
            "run": self.run,
            "process": self.process,
            "at": now(),
            "command": self.command,
            "losses": self.losses,
            "acknowledged": self.acknowledged,
            "operations": list(self.operations.values()),
            "instrumentation": {
                "kind": "fixture_only_exact_execution_receipts",
                "maximumBytes": MAXIMUM_BYTES,
                "maximumOperations": MAXIMUM_OPERATIONS,
                "maximumReadsPerSample": 16,
                "metadataReads": self.reads,
                "samplingSeconds": self.seconds,
                "captureWrites": self.capture_writes,
                "captureSeconds": self.capture_seconds,
                "overheadMeasured": False,
                "stockCapacityEligible": False,
            },
        }

    def write(self):
        raw = (json.dumps(self.snapshot(), separators=(",", ":")) + "\n").encode()
        if len(raw) > MAXIMUM_BYTES:
            raise ValueError("execution_observation_budget")
        temporary = self.directory / ("execution-" + secrets.token_hex(8) + ".tmp")
        try:
            fd = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.directory / "execution-observation.json")
            descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)
