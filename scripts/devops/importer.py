"""Bounded original local-CI ingestion. Never starts tests or discovers artifacts."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from scripts.ci import summary, test_selection
from scripts.devops import storage

MAX_FILE = 20 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
TABLES = ("ci_runs", "commands", "test_attempts")
STATES = {
    "running": "pending",
    "pending": "pending",
    "passed": "complete",
    "feedback-passed": "complete",
    "failed": "partial",
    "cancelled": "cancelled",
    "incomplete": "partial",
    "source-changed": "partial",
}
NEXT = "Inspect explain/status with the same operation ID; preserve original evidence."


def require(condition: Any, reason: str = "invalid-evidence") -> None:
    if not condition:
        storage.refuse(reason, NEXT)


def digest(value: Any) -> str:
    return hashlib.sha256(encode(value)).hexdigest()


def encode(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str
    ).encode()


def fields(value: Any, required: set[str], optional: set[str] | None = None) -> None:
    require(
        isinstance(value, dict)
        and required <= value.keys()
        and not value.keys() - required - (optional or set())
    )


def identifier(value: Any) -> str:
    require(isinstance(value, str) and str(uuid.UUID(value)) == value)
    return value


def stamp(value: Any) -> str | None:
    if value is None:
        return None
    require(isinstance(value, str) and len(value) <= 40)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None)
    return parsed.astimezone(timezone.utc).isoformat()


def measured(value: Any) -> Any:
    require(value is None or summary.number(value))
    if value is not None:
        numeric = Decimal(str(value))
        require(numeric < 10**15 and numeric == numeric.quantize(Decimal("0.001")))
    return value


def phase_measurements(attempt: dict, gaps: set[str]) -> dict:
    """Project schema-1 reporter observations, not their placeholder zeros."""
    phases = {
        "setup_ms": measured(attempt["setup_ms"]),
        "call_ms": measured(attempt["execution_ms"]),
        "teardown_ms": measured(attempt["teardown_ms"]),
    }
    # Node never measures setup/teardown. Pytest can omit phase reports after
    # setup failure/skip; schema 1 flattens those missing values to zero. A
    # passing pytest attempt proves all three normal lifecycle phases existed.
    unknown = {"setup_ms", "teardown_ms"} if attempt["lane"] == "node" else set()
    if (
        attempt["lane"] in {"node", "python", "postgres"}
        and attempt["outcome"] != "passed"
    ):
        unknown.update(key for key, value in phases.items() if value == 0)
    for key in unknown:
        phases[key] = None
    if unknown:
        gaps.add("unmeasured-phase-timing")
    return phases


@dataclass
class Inputs:
    root: Path
    snapshots: dict[str, tuple[int, int, int, int, str]]
    total: int = 0

    def read(self, relative: str) -> bytes:
        require(isinstance(relative, str) and 0 < len(relative) <= 240, "unsafe-path")
        path = Path(relative)
        require(
            not path.is_absolute() and all(p not in {".", ".."} for p in path.parts),
            "unsafe-path",
        )
        # Walk via openat + O_NOFOLLOW, so ancestor replacement cannot escape.
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in self.root.parts[1:]:
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                )
                os.close(fd)
                fd = child
        except BaseException:
            os.close(fd)
            raise
        try:
            for part in path.parts[:-1]:
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                )
                os.close(fd)
                fd = child
            file = os.open(
                path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
            )
            try:
                before = os.fstat(file)
                require(
                    stat.S_ISREG(before.st_mode) and before.st_size <= MAX_FILE,
                    "input-budget",
                )
                raw = bytearray()
                while len(raw) <= MAX_FILE:
                    chunk = os.read(file, min(65536, MAX_FILE + 1 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                after = os.fstat(file)
                signature = (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                    hashlib.sha256(raw).hexdigest(),
                )
                require(
                    (
                        before.st_dev,
                        before.st_ino,
                        before.st_size,
                        before.st_mtime_ns,
                        before.st_ctime_ns,
                    )
                    == (
                        after.st_dev,
                        after.st_ino,
                        after.st_size,
                        after.st_mtime_ns,
                        after.st_ctime_ns,
                    )
                    and len(raw) <= MAX_FILE,
                    "changed-input",
                )
                prior = self.snapshots.get(relative)
                if prior is not None:
                    require(prior == signature, "changed-input")
                else:
                    self.total += len(raw)
                    require(
                        self.total <= MAX_TOTAL and len(self.snapshots) < 512,
                        "input-budget",
                    )
                    self.snapshots[relative] = signature
                return bytes(raw)
            finally:
                os.close(file)
        finally:
            os.close(fd)

    def revalidate(self) -> None:
        for relative in list(self.snapshots):
            self.read(relative)


def json_value(raw: bytes) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "malformed-input")
            result[key] = value
        return result

    return json.loads(
        raw,
        object_pairs_hook=pairs,
        parse_constant=lambda _: storage.refuse("malformed-input", NEXT),
    )


def safe_root(root: Path) -> Path:
    require(
        root.is_absolute() and not any(p.is_symlink() for p in (root, *root.parents)),
        "unsafe-path",
    )
    require(root.is_dir(), "missing-input")
    return root


def registry(profile: str, base: str) -> list[list[str]]:
    return test_selection.commands(profile, base)


def command_identity(argv: list[str]) -> tuple[str, str | None, int | None]:
    if argv == ["npm", "test"] or argv[:2] == ["node", "--test"]:
        return "node", "none", 0
    if len(argv) > 1 and argv[1] == "scripts/ci/python-tests.py":
        return "python", "none", 0
    if len(argv) > 2 and argv[1:3] == ["-m", "pytest"]:
        return "postgres", "none", 0
    if len(argv) > 1 and argv[1] == "scripts/ci/run-browser-shard.mjs":
        return (
            "browser",
            argv[2].split("=", 1)[1],
            int(argv[3].split("=", 1)[1].split("/")[0]),
        )
    return "static", None, None


def registered_key(argv: list[str]) -> str:
    """Execution lane is not command identity (npm test != node --test)."""
    if argv == ["npm", "test"]:
        return "node-unit"
    if argv[:2] == ["node", "--test"]:
        return "node-browser-infrastructure"
    if argv == ["npm", "ci"]:
        return "browser-dependencies"
    if argv[0] == "./start.sh":
        if len(argv) == 1:
            return "stack-start"
        return "backup-create" if argv[1] == "--backup" else "backup-verify"
    script = argv[1]
    if script == "scripts/ci/validate_reports.py":
        return (
            "native-documents" if "--native-documents" in argv else "report-validation"
        )
    if script == "-m":
        return "python-compile" if argv[2] == "compileall" else "postgres-metadata"
    return {
        "scripts/check_runtime_parity.py": "runtime-parity",
        "scripts/check_python_quality.py": "python-quality",
        "scripts/ci/python-tests.py": "python-behavior",
        "scripts/ci/run-browser-shard.mjs": "browser-shard",
    }[script]


def plan(root: Path, manifest: str, database_name: str, database_id: str) -> dict:
    require(
        isinstance(database_name, str)
        and re.fullmatch(r"devops_analytics_[a-z0-9_]{1,48}", database_name),
        "wrong-target",
    )
    inputs = Inputs(safe_root(root), {})
    m = json_value(inputs.read(manifest))
    fields(m, {"schema", "repository", "installation_id", "runs"}, {"reuse"})
    require(type(m["schema"]) is int and m["schema"] == 1, "unsupported-schema")
    fields(m["repository"], {"id", "provider", "repository_key", "display_name"})
    repo = m["repository"]
    identifier(repo["id"])
    identifier(m["installation_id"])
    # Curated public registry identity, never inferred from a receipt or Git remote.
    require(repo["provider"] in {"github", "local"})
    require(
        isinstance(repo["repository_key"], str)
        and re.fullmatch(r"[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)?", repo["repository_key"])
        and len(repo["repository_key"]) <= 160
    )
    require(
        isinstance(repo["display_name"], str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", repo["display_name"])
    )
    require(isinstance(m["runs"], list) and 0 < len(m["runs"]) <= 64)
    rows: dict[str, list[dict]] = {table: [] for table in TABLES}
    gaps: set[str] = set()
    selections: dict[str, list[dict]] = {}
    run_keys = set()
    receipt_keys = set()
    for selection in m["runs"]:
        fields(
            selection,
            {
                "invocation_id",
                "receipt",
                "telemetry",
                "summaries",
                "revision",
                "change_kind",
                "observed_at",
            },
            {"revisions"},
        )
        invocation = identifier(selection["invocation_id"])
        require(invocation not in run_keys, "identity-conflict")
        run_keys.add(invocation)
        require(type(selection["revision"]) is int and selection["revision"] > 0)
        require(
            selection["change_kind"]
            in (
                {"initial"}
                if selection["revision"] == 1
                else {"correction", "retraction"}
            )
        )
        observed = stamp(selection["observed_at"])
        require(observed is not None)
        require(isinstance(selection.get("revisions", {}), dict))
        revision_keys: set[str] = set()
        common = dict(
            repository_id=repo["id"],
            installation_id=m["installation_id"],
            revision=selection["revision"],
            change_kind=selection["change_kind"],
            observed_at=observed,
        )

        def fact(table, key, values):
            identity = str(
                uuid.uuid5(
                    uuid.UUID(m["installation_id"]),
                    repo["id"] + ":" + table + ":" + key,
                )
            )
            override = selection.get("revisions", {}).get(key, {})
            revision_keys.add(key)
            fields(override, set(), {"revision", "change_kind"})
            row = (
                common
                | override
                | dict(id=identity, source_key=key, evidence_ref=identity)
                | values
            )
            require(
                type(row["revision"]) is int
                and row["revision"] > 0
                and row["change_kind"]
                in (
                    {"initial"}
                    if row["revision"] == 1
                    else {"correction", "retraction"}
                )
            )
            if row.get("started_at") is not None and row.get("finished_at") is not None:
                require(
                    datetime.fromisoformat(row["finished_at"])
                    >= datetime.fromisoformat(row["started_at"])
                )
            row["evidence_sha256"] = digest(row)
            rows[table].append(row)
            return row

        r = json_value(inputs.read(selection["receipt"]))
        receipt_identity = inputs.snapshots[selection["receipt"]][:2]
        require(receipt_identity not in receipt_keys, "identity-conflict")
        receipt_keys.add(receipt_identity)
        fields(
            r,
            {
                "schema",
                "run_id",
                "started_at",
                "base_ref",
                "classification",
                "mode",
                "profile",
                "source",
                "status",
                "commands",
                "pending_layers",
                "pending_commands",
            },
            {
                "acceptance",
                "finished_at",
                "duration_ms",
                "reason",
                "markdown_validation",
                "deployment",
                "browser_credentials",
            },
        )
        require(type(r["schema"]) is int and r["schema"] == 1, "unsupported-schema")
        require(summary.number(r["run_id"], True))
        require(
            isinstance(r["status"], str) and r["status"] in STATES, "unsupported-status"
        )
        require(
            r["mode"] in {"selected", "full", "feedback"}
            and r["profile"] in test_selection.PROFILES
        )
        require(isinstance(r["base_ref"], str) and len(r["base_ref"]) <= 256)
        require(
            isinstance(r["classification"], dict)
            and r["classification"].get("profile") == r["profile"]
            and type(r["classification"].get("valid")) is bool
        )
        expected = registry(r["profile"], r["base_ref"])
        cmds = r["commands"]
        require(isinstance(cmds, list) and 0 < len(cmds) <= 64)
        selected = [c.get("command") for c in cmds if isinstance(c, dict)]
        native = [
            "python3",
            "scripts/ci/validate_reports.py",
            "--native-documents",
            "--output",
            ".schemii/test-selection/native-documents.json",
        ]
        prefixes = [
            [native[:3] + list(paths) + native[3:]]
            for paths in (
                [test_selection.NATIVE_SKILL],
                ["testing/agents/README.md"],
                [test_selection.NATIVE_SKILL, "testing/agents/README.md"],
            )
        ]
        require(
            selected == expected
            or any(selected == prefix + expected for prefix in prefixes),
            "unsupported-command",
        )
        source = r["source"]
        if source is not None:
            fields(source, {"commit", "fingerprint", "index_fingerprint", "dirty"})
            require(
                isinstance(source["commit"], str)
                and re.fullmatch(r"[0-9a-f]{40}", source["commit"])
            )
            require(
                all(
                    isinstance(source[k], str)
                    and re.fullmatch(r"[0-9a-f]{64}", source[k])
                    for k in ("fingerprint", "index_fingerprint")
                )
            )
            require(type(source["dirty"]) is bool)
        else:
            gaps.add("missing-source")
        pending = [i for i, c in enumerate(cmds) if c["status"] != "passed"]
        require(
            isinstance(r["pending_commands"], list) and r["pending_commands"] == pending
        )
        require(
            isinstance(r["pending_layers"], list)
            and all(
                isinstance(v, str)
                and v in {"static", "python", "node", "postgres", "browser"}
                for v in r["pending_layers"]
            )
        )
        acceptance = r.get("acceptance")
        require(acceptance is None or type(acceptance) is bool)
        complete = STATES[r["status"]]
        if r["status"] == "feedback-passed" and (pending or r["pending_layers"]):
            complete = "partial"
        if complete == "complete":
            require(
                source is not None
                and r["classification"]["valid"]
                and not pending
                and not r["pending_layers"]
            )
        require(
            acceptance is not True
            or r["status"] == "passed"
            and complete == "complete"
            and r["mode"] != "feedback"
        )
        run = fact(
            "ci_runs",
            invocation,
            dict(
                head_sha=source["commit"] if source else None,
                source_fingerprint=source["fingerprint"] if source else None,
                index_fingerprint=source["index_fingerprint"] if source else None,
                dirty=source["dirty"] if source else None,
                base_sha=None,
                policy_sha256=None,
                inventory_sha256=None,
                cohort_key=None,
                profile=r["profile"],
                mode=r["mode"],
                started_at=stamp(r["started_at"]),
                finished_at=stamp(r.get("finished_at")),
                duration_ms=measured(r.get("duration_ms")),
                outcome=r["status"],
                completeness=complete,
                acceptance=acceptance,
            ),
        )
        run["evidence_sha256"] = digest({"fact": run, "producer_run_id": r["run_id"]})
        if complete != "complete":
            gaps.add("incomplete-source")
        require(
            isinstance(selection["telemetry"], list)
            and isinstance(selection["summaries"], list)
        )
        telemetry: dict[int, str] = {}
        summaries: dict[int, str] = {}
        for name, target in (("telemetry", telemetry), ("summaries", summaries)):
            for item in selection[name]:
                fields(item, {"slot", "file"})
                require(
                    type(item["slot"]) is int
                    and 0 <= item["slot"] < len(cmds)
                    and item["slot"] not in target
                )
                target[item["slot"]] = item["file"]
        selections[invocation] = []
        for i, entry in enumerate(cmds):
            fields(
                entry,
                {"command", "status", "duration_ms"},
                {"executed_command", "exit_code", "timing_evidence", "reason"},
            )
            require(
                entry["status"]
                in {
                    "pending",
                    "running",
                    "passed",
                    "failed",
                    "cancelled",
                    "incomplete",
                    "source-changed",
                },
                "unsupported-status",
            )
            command_key, project, shard = command_identity(entry["command"])
            disposition = "pending" if entry["status"] == "pending" else "executed"
            c = fact(
                "commands",
                invocation + "." + str(i),
                dict(
                    run_id=run["id"],
                    run_revision=run["revision"],
                    slot_key=str(i),
                    command_key=registered_key(entry["command"]),
                    project=project,
                    shard=shard,
                    disposition=disposition,
                    donor_command_id=None,
                    donor_command_revision=None,
                    expected_cases=None,
                    inventory_sha256=None,
                    started_at=None,
                    finished_at=None,
                    duration_ms=measured(entry["duration_ms"])
                    if disposition == "executed"
                    else None,
                    outcome=entry["status"] if disposition == "executed" else None,
                ),
            )
            selections[invocation].append(c)
            timing = entry.get("timing_evidence")
            if timing is None:
                require(i not in telemetry and i not in summaries)
                if project is not None and disposition == "executed":
                    gaps.add("missing-telemetry")
                    require(complete != "complete")
                continue
            require(timing == f"command-{i:02d}/timing.jsonl")
            require(i in telemetry, "unselected-telemetry")
            expected_path = str(Path(selection["receipt"]).parent / timing)
            require(telemetry[i] == expected_path, "unbound-telemetry")
            records = [
                json_value(line)
                for line in inputs.read(telemetry[i]).splitlines()
                if line
            ]
            require(all(isinstance(v, dict) for v in records))
            measured_summary = summary.summarize(records)
            require(disposition == "executed" and source is not None)
            require(
                all(
                    measured_summary[k] == v
                    for k, v in dict(
                        schema=1,
                        run_id=r["run_id"],
                        run_attempt=1,
                        source_sha=source["commit"],
                        lane=command_key,
                        project=project,
                        shard=shard,
                    ).items()
                ),
                "identity-conflict",
            )
            c["expected_cases"] = next(
                v["planned"] for v in records if v["kind"] == "start"
            )
            c["inventory_sha256"] = digest(
                sorted(v["test_id"] for v in records if v["kind"] == "plan")
            )
            if i in summaries:
                require(
                    summaries[i]
                    == str(Path(expected_path).with_suffix(".summary.json")),
                    "unbound-summary",
                )
                require(json_value(inputs.read(summaries[i])) == measured_summary)
            else:
                gaps.add("missing-summary")
            if not measured_summary["complete"]:
                gaps.add("missing-terminal-or-cases")
            if entry["status"] == "passed":
                # Repeat the original evidence predicates without executing its runner.
                require(
                    measured_summary["complete"]
                    and measured_summary["outcome"] == "passed"
                    and measured_summary["first_attempt_eligible"]
                    and not measured_summary["first_attempt_failures"]
                    and not measured_summary["retry_recovered"]
                )
                require(
                    command_key != "postgres"
                    or not measured_summary["attempt_outcomes"]["skipped"]
                )
                scope = dict(
                    profile=r["profile"],
                    planned=sorted(
                        v["test_id"] for v in records if v["kind"] == "plan"
                    ),
                    observed=[
                        {
                            k: v[k]
                            for k in ("test_id", "source_id", "outcome", "attempt")
                        }
                        for v in records
                        if v["kind"] == "attempt"
                    ],
                )
                require(
                    test_selection.valid_scope(
                        r["profile"], (command_key, project, shard), scope
                    )
                )
            for attempt in records:
                if attempt["kind"] != "attempt":
                    continue
                # These reporters also emit cancellation/not-run placeholders for
                # unstarted cases. There is no observation bit: don't invent one.
                if attempt["outcome"] in {"not-run", "cancelled"}:
                    gaps.add("unobserved-placeholder")
                    continue
                fact(
                    "test_attempts",
                    invocation
                    + "."
                    + str(i)
                    + "."
                    + attempt["test_id"]
                    + "."
                    + str(attempt["attempt"]),
                    dict(
                        command_id=c["id"],
                        command_revision=c["revision"],
                        case_key=attempt["test_id"],
                        attempt_number=attempt["attempt"],
                        outcome=attempt["outcome"],
                        **phase_measurements(attempt, gaps),
                    ),
                )
            c["evidence_sha256"] = digest(
                {k: v for k, v in c.items() if k != "evidence_sha256"}
            )
        require(not selection.get("revisions", {}).keys() - revision_keys)
    # A run tombstone hides children through the foundation's named views.
    # Keep their original executed parent revisions; don't manufacture child
    # retractions pointing at a retracted command (which SQL correctly refuses).
    retracted_runs = {
        r["id"] for r in rows["ci_runs"] if r["change_kind"] == "retraction"
    }
    retracted_commands = {
        c["id"] for c in rows["commands"] if c["run_id"] in retracted_runs
    }
    rows["commands"] = [
        c for c in rows["commands"] if c["id"] not in retracted_commands
    ]
    rows["test_attempts"] = [
        a for a in rows["test_attempts"] if a["command_id"] not in retracted_commands
    ]
    require(isinstance(m.get("reuse", []), list) and len(m.get("reuse", [])) <= 64)
    for edge in m.get("reuse", []):
        fields(
            edge, {"target_invocation", "target_slot", "donor_invocation", "donor_slot"}
        )
        for side in ("target", "donor"):
            require(
                edge[side + "_invocation"] in selections
                and type(edge[side + "_slot"]) is int
                and 0
                <= edge[side + "_slot"]
                < len(selections[edge[side + "_invocation"]]),
                "invalid-reuse",
            )
        target = selections[edge["target_invocation"]][edge["target_slot"]]
        donor = selections[edge["donor_invocation"]][edge["donor_slot"]]
        require(
            target["run_id"] not in retracted_runs
            and donor["run_id"] not in retracted_runs
            and target["disposition"] == "pending"
            and donor["disposition"] == "executed",
            "invalid-reuse",
        )
        require(
            all(target[k] == donor[k] for k in ("command_key", "project", "shard")),
            "invalid-reuse",
        )
        target.update(
            disposition="reused",
            donor_command_id=donor["id"],
            donor_command_revision=donor["revision"],
            inventory_sha256=donor["inventory_sha256"],
            expected_cases=donor["expected_cases"],
        )
        target["evidence_sha256"] = digest(
            {k: v for k, v in target.items() if k != "evidence_sha256"}
        )
    body = dict(
        schema=1,
        repository=repo,
        installation_id=m["installation_id"],
        database_name=database_name,
        database_id=identifier(database_id),
        rows=rows,
        gaps=sorted(gaps),
    )
    # Input byte digests remain transient, never persisted/exported (raw receipts
    # may contain private argv/diagnostics). Persist only the allowlisted projection.
    return dict(body=body, plan_id=digest(body), inputs=inputs)


@contextmanager
def connect(config):
    ambient = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("PG")}
    try:
        conn = psycopg.connect(**config, autocommit=True, row_factory=dict_row)
    finally:
        os.environ.update(ambient)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def target(profile: Path, collector_profile: Path, name: str, identity: str):
    validation = storage.connection_config(profile)
    collector = storage.connection_config(collector_profile)
    require(validation["dbname"] == collector["dbname"] == name, "wrong-target")
    require(
        all(
            validation[k] == collector[k] for k in ("host", "port", "dbname", "sslmode")
        ),
        "wrong-target",
    )
    # Keep the foundation migration lock for the entire collection transaction.
    # The authority connection never writes facts; writes use only collector.
    with connect(validation) as guard:
        with guard.transaction():
            guard.row_factory = psycopg.rows.tuple_row
            checked = storage.operate(guard, "status", name, uuid.UUID(identity))
            require(not checked["pending_versions"], "migration-pending")
            with connect(collector) as conn:
                actor = conn.execute(
                    "SELECT current_database() AS name, session_user AS actor, current_user AS effective_actor, rolcanlogin, rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication AS dangerous, pg_has_role(session_user,'devops_collector','USAGE') AS effective_access, has_database_privilege(session_user,current_database(),'CREATE') OR has_database_privilege(session_user,current_database(),'TEMP') OR has_schema_privilege(session_user,'public','CREATE') OR has_schema_privilege(session_user,'public','USAGE') AS extra_privileges FROM pg_roles WHERE rolname=session_user"
                ).fetchone()
                # Follow actual grants, not superuser's implicit pg_has_role
                # membership. This validates the selected connection itself,
                # including privileged logins with no collector grant edges.
                memberships = {
                    row["rolname"]
                    for row in conn.execute(
                        "WITH RECURSIVE ancestors(oid) AS (SELECT roleid FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=session_user) UNION SELECT m.roleid FROM pg_auth_members m JOIN ancestors a ON m.member=a.oid) SELECT r.rolname FROM ancestors a JOIN pg_roles r ON r.oid=a.oid"
                    )
                }
                require(
                    actor
                    and actor["name"] == name
                    and actor["actor"] == collector["user"] == actor["effective_actor"]
                    and actor["rolcanlogin"]
                    and not actor["dangerous"]
                    and not actor["extra_privileges"]
                    and actor["effective_access"]
                    and "devops_collector" in memberships
                    and not memberships - {"devops_collector", "devops_reader"},
                    "unsafe-collector",
                )
                yield conn


def insert(conn, table: str, row: dict) -> int:
    query = sql.SQL("INSERT INTO devops.{} ({}) VALUES ({})").format(
        sql.Identifier(table),
        sql.SQL(",").join(map(sql.Identifier, row)),
        sql.SQL(",").join(sql.Placeholder() for _ in row),
    )
    return conn.execute(query, list(row.values())).rowcount


def existing(conn, table, row):
    return conn.execute(
        sql.SQL(
            "SELECT * FROM devops.{} WHERE repository_id=%s AND installation_id=%s AND id=%s AND revision=%s"
        ).format(sql.Identifier(table)),
        [row[k] for k in ("repository_id", "installation_id", "id", "revision")],
    ).fetchone()


def apply(conn, selected: dict, operation_id: str, started_at: str) -> dict:
    body = selected["body"]
    repo = body["repository"]
    batch_id = identifier(operation_id)
    with conn.transaction():
        locked = conn.execute(
            "SELECT pg_try_advisory_xact_lock(279, hashtext(%s)) AS locked",
            [repo["id"] + body["installation_id"]],
        ).fetchone()
        require(locked and locked["locked"], "busy-stream")
        selected["inputs"].revalidate()
        prior = conn.execute(
            "SELECT * FROM devops.import_batches WHERE repository_id=%s AND installation_id=%s AND id=%s ORDER BY revision DESC LIMIT 1",
            [repo["id"], body["installation_id"], batch_id],
        ).fetchone()
        if prior:
            require(
                prior["evidence_sha256"] == selected["plan_id"],
                "immutable-digest-mismatch",
            )
            return dict(
                state="replayed",
                accepted_count=prior["accepted_count"],
                commit_state="reconciled",
            )
        insert(conn, "repositories", repo)
        # Count and compare immutable normalized facts before batch publication.
        new = []
        for table in TABLES:
            for row in body["rows"][table]:
                old = existing(conn, table, row)
                if old:
                    require(
                        old["evidence_sha256"] == row["evidence_sha256"],
                        "immutable-digest-mismatch",
                    )
                else:
                    new.append((table, row))
        finish = datetime.now(timezone.utc).isoformat()
        insert(
            conn,
            "import_batches",
            dict(
                repository_id=repo["id"],
                installation_id=body["installation_id"],
                id=batch_id,
                source_key=batch_id,
                revision=1,
                change_kind="initial",
                observed_at=finish,
                evidence_sha256=selected["plan_id"],
                evidence_ref=batch_id,
                source_kind="local-ci-v1",
                started_at=started_at,
                finished_at=finish,
                status="partial" if body["gaps"] else "complete",
                accepted_count=len(new),
                rejected_count=0,
                reason_code="incomplete-source" if body["gaps"] else "none",
            ),
        )
        # All executed donors precede reused slots. No attempts minted for reuse.
        new.sort(
            key=lambda pair: (
                TABLES.index(pair[0]),
                pair[1].get("disposition") == "reused",
            )
        )
        for table, row in new:
            insert(
                conn,
                table,
                row | dict(import_batch_id=batch_id, import_batch_revision=1),
            )
        selected["inputs"].revalidate()
    return dict(state="committed", accepted_count=len(new), commit_state="acknowledged")
