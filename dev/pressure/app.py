"""Fixture-only observation of the unchanged application and its stock lifespan.

No HTTP route, database query or admission permit is added. The sampler calls
existing owning snapshots directly; an original shutdown.complete plus closed
zero-active owners is required separately from the native process exit. This
instrumented topology is explicitly unsuitable as an uninstrumented capacity run.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
from threading import Event, RLock, Thread

INTERVAL = 0.1
MAXIMUM_BYTES = 16384
FIELDS = {
    "maximum_connections",
    "active",
    "peak_active",
    "admitted",
    "rejected",
    "connection_failures",
    "connection_seconds",
    "closed",
}


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def owner_snapshot(owner):
    try:
        counts = asdict(owner.admission_snapshot())
        if set(counts) != FIELDS or type(counts["closed"]) is not bool:
            raise ValueError()
        for name in FIELDS - {"closed", "connection_seconds"}:
            if type(counts[name]) is not int or counts[name] < 0:
                raise ValueError()
        seconds = counts["connection_seconds"]
        if (
            type(seconds) not in {int, float}
            or not math.isfinite(seconds)
            or seconds < 0
        ):
            raise ValueError()
        if (
            counts["maximum_connections"] < 1
            or not 0
            <= counts["active"]
            <= counts["peak_active"]
            <= counts["maximum_connections"]
        ):
            raise ValueError()
        # Adapt the existing production owner contract to the fixed private DTO.
        # Schema drift stays unknown instead of inventing or defaulting counters.
        counts["maximum"] = counts.pop("maximum_connections")
        return {"status": "observed", "counts": counts}
    except Exception:
        return {"status": "unknown", "reason": "owner_snapshot_failed"}


class ObservedApplication:
    def __init__(self, application, run, directory):
        if re.fullmatch(r"pressure_[0-9a-f]{32}", run) is None:
            raise ValueError("invalid fixture observer run")
        info = directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_uid != os.getuid()
        ):
            raise ValueError("observer directory is not private and owned")
        self.application, self.run, self.directory = application, run, directory
        self.stop = Event()
        self.lock = RLock()
        self.thread = None
        self.stage = "startup-pending"
        self.startup_complete = False
        self.failure = False
        self.samples = 0
        self.history = []
        self.process = {
            "pid": os.getpid(),
            "uid": os.getuid(),
            "startTicks": Path("/proc/self/stat")
            .read_text()
            .rsplit(") ", 1)[1]
            .split()[19],
            "incarnation": secrets.token_hex(16),
            "startedAt": timestamp(),
        }

    def snapshot(self):
        metadata = self.application.state.services.metadata
        readiness = getattr(metadata.readiness_probe, "_connection_factory", None)
        return {
            "version": 1,
            "run": self.run,
            "process": self.process,
            "at": timestamp(),
            "stage": self.stage,
            "startupComplete": self.startup_complete,
            "normal": owner_snapshot(metadata.connection_factory),
            "readiness": owner_snapshot(readiness),
            "observationFailed": self.failure,
            "instrumentation": {
                "kind": "fixture_only_stock_asgi_lifespan_and_owner_sampler",
                "intervalSeconds": INTERVAL,
                "maximumBytes": MAXIMUM_BYTES,
                "samples": self.samples,
                "overheadMeasured": False,
                "stockCapacityEligible": False,
            },
        }

    def write(self):
        self.samples += 1
        value = self.snapshot()
        event = {key: value[key] for key in ("at", "stage", "normal", "readiness")}
        previous = self.history[-1] if self.history else None
        if previous is None or any(
            previous[key] != event[key] for key in ("stage", "normal", "readiness")
        ):
            self.history = [*self.history[-15:], event]
        value["ownerHistory"] = self.history
        encoded = (
            json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n"
        ).encode()
        if len(encoded) > MAXIMUM_BYTES:
            raise ValueError("observer receipt too large")
        target = self.directory / "observation.json"
        temporary = self.directory / (".observation-" + secrets.token_hex(8))
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            if temporary.exists():
                temporary.unlink()

    def sample(self):
        while not self.stop.wait(INTERVAL):
            with self.lock:
                if self.stop.is_set():
                    return
                try:
                    self.write()
                except Exception:
                    self.failure = True

    async def __call__(self, scope, receive, send):
        if scope["type"] != "lifespan":
            return await self.application(scope, receive, send)

        async def incoming():
            message = await receive()
            if message["type"] == "lifespan.shutdown":
                with self.lock:
                    self.stage = "shutdown-requested"
                    try:
                        self.write()
                    except Exception:
                        self.failure = True
            return message

        async def outgoing(message):
            terminal = message["type"] in {
                "lifespan.shutdown.complete",
                "lifespan.shutdown.failed",
                "lifespan.startup.failed",
            }
            if terminal:
                self.stop.set()
                if self.thread is not None:
                    self.thread.join(timeout=1)
                    if self.thread.is_alive():
                        self.failure = True
            with self.lock:
                if message["type"] == "lifespan.startup.complete":
                    self.startup_complete = True
                    self.stage = "running"
                    try:
                        self.write()
                    except Exception:
                        self.failure = True
                    self.thread = Thread(
                        target=self.sample, name="fixture-owner-observer", daemon=True
                    )
                    self.thread.start()
                elif terminal:
                    self.stage = (
                        "shutdown-complete"
                        if message["type"] == "lifespan.shutdown.complete"
                        else "lifespan-failed"
                    )
                    try:
                        self.write()
                    except Exception:
                        self.failure = True
            # Forward exactly the original stock lifespan message; never synthesize success.
            await send(message)

        try:
            await self.application(scope, incoming, outgoing)
        except BaseException:
            with self.lock:
                self.failure = True
                self.stage = "lifespan-failed"
                try:
                    self.write()
                except Exception:
                    pass
            raise
        finally:
            self.stop.set()
            if self.thread is not None:
                self.thread.join(timeout=1)
                if self.thread.is_alive():
                    self.failure = True


def observed_application():
    from schemii.main import app

    return ObservedApplication(
        app, os.environ["PRESSURE_OBSERVER_RUN"], Path("/qualification-state")
    )
