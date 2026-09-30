"""Opt-in pytest plugin: fixed public fields, never failure/fixture serialization."""

import hashlib
import json
import os
from pathlib import Path
import time


def _hash(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


class Timing:
    def __init__(self, config):
        self.config = config
        self.started = time.perf_counter()
        self.file = Path(os.environ["CI_TELEMETRY_FILE"])
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text("")
        self.items = {}
        self.phases = {}
        self.completed = set()
        self.collection_failed = False
        self.interrupted = False
        sha = os.environ.get("CI_TELEMETRY_SHA", "")
        lane = os.environ.get("CI_TELEMETRY_LANE", "python")
        if lane not in {"python", "postgres"}:
            raise ValueError("Invalid timing lane")
        self.meta = {
            "schema": 1,
            "source_sha": sha
            if len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)
            else None,
            "run_id": self.numeric("CI_TELEMETRY_RUN_ID"),
            "run_attempt": self.numeric("CI_TELEMETRY_RUN_ATTEMPT"),
            "lane": lane,
            "project": "none",
            "shard": 0,
        }

    @staticmethod
    def numeric(name):
        value = os.environ.get(name, "")
        return (
            int(value)
            if value.isascii() and value.isdigit() and len(value) <= 20
            else 0
        )

    def write(self, record):
        with self.file.open("a") as stream:
            stream.write(
                json.dumps({**self.meta, **record}, separators=(",", ":")) + "\n"
            )

    def pytest_collection_finish(self, session):
        self.items = {item.nodeid: item for item in session.items}
        self.write({"kind": "start", "planned": len(self.items)})
        for nodeid in self.items:
            self.write({"kind": "plan", "test_id": _hash(nodeid)})

    def pytest_collectreport(self, report):
        # Pytest uses exit status 2 for both collection errors and interruption.
        # The collection lifecycle owns the distinction; never infer it from
        # exception messages or publish the collection failure object.
        if report.failed:
            self.collection_failed = True

    def pytest_keyboard_interrupt(self, excinfo):
        self.interrupted = True

    def emit(self, nodeid, missing=None):
        reports = self.phases.get(nodeid, {})
        if missing:
            outcome = missing
        elif any(report.failed for report in reports.values()):
            outcome = "failed"
        elif any(report.skipped for report in reports.values()):
            outcome = "skipped"
        elif "execution" in reports:
            outcome = "passed"
        else:
            outcome = "not-run"
        item = self.items.get(nodeid)
        source, line = item.location[:2] if item is not None else ("unknown", 0)
        self.write(
            {
                "kind": "attempt",
                "test_id": _hash(nodeid),
                "source_id": _hash(source),
                "source_line": line + 1,
                "attempt": 0,
                "outcome": outcome,
                "skip": "declared-or-runtime" if outcome == "skipped" else "none",
                **{
                    f"{phase}_ms": round(reports[phase].duration * 1000, 3)
                    if phase in reports
                    else 0
                    for phase in ("setup", "execution", "teardown")
                },
            }
        )
        # Pytest calls its test-body phase "call".
        self.completed.add(nodeid)

    def pytest_runtest_logreport(self, report):
        phase = "execution" if report.when == "call" else report.when
        self.phases.setdefault(report.nodeid, {})[phase] = report
        if report.when == "teardown":
            self.emit(report.nodeid)

    def pytest_sessionfinish(self, session, exitstatus):
        if self.collection_failed:
            outcome = "collection-error"
        elif self.interrupted:
            outcome = "cancelled"
        else:
            outcome = {0: "passed", 1: "failed", 2: "cancelled"}.get(
                int(exitstatus), "error"
            )
        for nodeid in self.items:
            if nodeid not in self.completed:
                self.emit(nodeid, "cancelled" if outcome == "cancelled" else "not-run")
        self.write(
            {
                "kind": "end",
                "outcome": outcome,
                "wall_ms": round((time.perf_counter() - self.started) * 1000, 3),
            }
        )


def pytest_configure(config):
    if os.environ.get("CI_TELEMETRY_FILE"):
        config.pluginmanager.register(Timing(config), "schemii-public-timing")
