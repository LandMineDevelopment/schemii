"""One explicit human/agent interface for original local-CI evidence (v1)."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
import re
from pathlib import Path
import stat
import sys
from typing import NoReturn

# Support the documented script form as well as python -m scripts.devops.cli.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import psycopg

from scripts.devops import importer as core
from scripts.devops import storage

ACTIONS = (
    "schema",
    "metrics",
    "doctor",
    "plan",
    "dry-run",
    "import",
    "resume",
    "status",
    "explain",
    "export",
)


def result(state: str, reason: str, code: int = 0, **details) -> dict:
    return dict(
        schema=1,
        ok=code == 0,
        state=state,
        reason=reason,
        next_action="Review the explicit plan, then import with --expect-plan and a new --operation-id."
        if state == "planned"
        else core.NEXT,
        exit_code=code,
        **details,
    )


class Parser(argparse.ArgumentParser):
    json_errors = False

    def error(self, message: str) -> NoReturn:
        emit(result("refused", "invalid-arguments", 2), self.json_errors)
        raise SystemExit(2)


def emit(value: dict, machine: bool) -> None:
    if machine:
        print(json.dumps(value, sort_keys=True, default=str))
    else:
        for key, item in value.items():
            print(f"{key}: {json.dumps(item, sort_keys=True, default=str)}")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def journal(directory: Path, operation: str, binding: dict, *, resume: bool):
    core.safe_root(directory)
    info = directory.stat()
    core.require(
        info.st_uid == os.getuid() and not info.st_mode & 0o077, "unsafe-state"
    )
    path = directory / (core.identifier(operation) + ".jsonl")
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    if not resume:
        flags |= os.O_CREAT
    fd = os.open(path, flags, 0o600)
    try:
        info = os.fstat(fd)
        core.require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.getuid()
            and not info.st_mode & 0o077
            and info.st_nlink == 1
            and info.st_size <= 1024 * 1024,
            "unsafe-state",
        )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            storage.refuse("busy-operation", core.NEXT)
        raw = os.read(fd, 1024 * 1024 + 1)
        records, loss = read_events(raw)
        if records:
            core.require(records[0]["binding"] == binding, "operation-plan-mismatch")
        else:
            core.require(not resume, "missing-operation")
            event(fd, dict(kind="operation", binding=binding, started_at=now()))
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            records, loss = read_events(os.pread(fd, 1024 * 1024, 0))
        records[0]["journal_loss"] = loss
        yield fd, records
    finally:
        os.close(fd)


def read_events(raw: bytes):
    records = []
    loss = False
    for line in raw.splitlines(keepends=True):
        if not line.strip():
            continue
        if not line.endswith(b"\n"):
            loss = True
            continue
        try:
            value = core.json_value(line)
            core.require(isinstance(value, dict))
            records.append(value)
        except (ValueError, storage.Refusal):
            loss = True
    core.require(
        not raw or records and records[0].get("kind") == "operation",
        "incomplete-journal",
    )
    return records, loss


def event(fd: int, value: dict) -> None:
    core.require(os.fstat(fd).st_size < 1024 * 1024 - 4096, "journal-budget")
    os.lseek(fd, 0, os.SEEK_END)
    data = b"\n" + core.encode(value) + b"\n"
    written = os.write(fd, data)
    core.require(written == len(data), "journal-write-failed")
    os.fsync(fd)


def binding(selected, operation):
    body = selected["body"]
    return dict(
        operation_id=operation,
        plan_id=selected["plan_id"],
        database_id=body["database_id"],
        repository_id=body["repository"]["id"],
        installation_id=body["installation_id"],
    )


def health(conn, selected, operation):
    body = selected["body"]
    params = [body["repository"]["id"], body["installation_id"]]
    last = conn.execute(
        "SELECT id, status, finished_at, accepted_count, rejected_count, reason_code FROM devops.v1_collection_health WHERE repository_id=%s AND installation_id=%s ORDER BY started_at DESC,id DESC LIMIT 1",
        params,
    ).fetchone()
    complete = conn.execute(
        "SELECT id, finished_at FROM devops.v1_collection_health WHERE repository_id=%s AND installation_id=%s AND status='complete' ORDER BY finished_at DESC NULLS LAST,id DESC LIMIT 1",
        params,
    ).fetchone()
    committed = conn.execute(
        "SELECT evidence_sha256,status,accepted_count FROM devops.import_batches WHERE repository_id=%s AND installation_id=%s AND id=%s ORDER BY revision DESC LIMIT 1",
        params + [operation],
    ).fetchone()
    return dict(
        last_published_batch=last,
        last_complete_success=complete,
        operation_batch=committed,
    )


def safe_attempt(event):
    state = event.get("state")
    core.require(
        state in {None, "started", "committed", "replayed", "refused", "uncertain"},
        "invalid-journal",
    )
    return dict(state=state or "unstarted", at=core.stamp(event.get("at")))


def columns():
    result = {}
    for _, _, body in storage.migration_files():
        for name, definition in re.findall(
            r"CREATE TABLE (devops(?:_control)?\.[a-z_]+) \((.*?)\n\);", body, re.S
        ):
            result[name] = {
                key: spec.rstrip(",")
                for key, spec in re.findall(r"^    ([a-z_]+) (.+)$", definition, re.M)
            }
    return result


def execute(args) -> dict:
    if args.action in {"schema", "metrics"}:
        return result(
            "discovered",
            "offline",
            tables=["repositories", "import_batches", *core.TABLES],
            contract_version=1,
            manifest_version=1,
            supported_source="local-ci-v1",
            datasets=[
                "v1_ci_runs",
                "v1_command_runs",
                "v1_test_attempts",
                "v1_collection_health",
            ],
            metrics={
                "T02-T03": "Original first attempts; outcome counts with explicit unknown coverage.",
                "T08": "Original command durations; no execution for reuse.",
                "O01-O02": "Collection status/counts; source gaps and unknown freshness.",
            },
            unknown_fields=["base_sha", "policy_sha256", "cohort_key"],
            columns=columns() if args.action == "schema" else None,
            migration_checksums={str(v): c for v, c, _ in storage.migration_files()},
        )
    core.require(args.database_name and args.database_id, "explicit-target-required")
    core.identifier(args.database_id)
    if args.action == "doctor":
        core.require(
            args.profile and args.collector_profile, "explicit-profiles-required"
        )
        with core.target(
            args.profile, args.collector_profile, args.database_name, args.database_id
        ):
            return result("ready", "validated-target", database_id=args.database_id)
    core.require(args.input_root and args.manifest, "explicit-manifest-required")
    selected = core.plan(
        args.input_root, args.manifest, args.database_name, args.database_id
    )
    detail = dict(
        plan_id=selected["plan_id"],
        counts={table: len(rows) for table, rows in selected["body"]["rows"].items()},
        gaps=selected["body"]["gaps"],
        acceptance="producer-assertion-only",
        coverage="explicit-manifest-only",
    )
    if args.action in {"plan", "dry-run", "explain", "export"}:
        if args.action == "export":
            detail["facts"] = selected["body"]
        return result("planned", "validated-input", **detail)
    core.require(
        args.profile
        and args.collector_profile
        and args.operation_id
        and args.state_dir,
        "explicit-operation-required",
    )
    core.identifier(args.operation_id)
    expected = binding(selected, args.operation_id)
    if args.action == "status":
        # Read the exact journal only. Status never creates or stamps a checkpoint.
        inputs = core.Inputs(core.safe_root(args.state_dir), {})
        raw = inputs.read(args.operation_id + ".jsonl")
        events, loss = read_events(raw)
        core.require(bool(events), "missing-operation")
        recorded_binding = events[0]["binding"]
        core.require(
            {k: v for k, v in recorded_binding.items() if k != "plan_id"}
            == {k: v for k, v in expected.items() if k != "plan_id"},
            "wrong-operation-target",
        )
        stale = recorded_binding != expected
        with core.target(
            args.profile, args.collector_profile, args.database_name, args.database_id
        ) as conn:
            status = health(conn, selected, args.operation_id)
        batch = status["operation_batch"]
        core.require(
            batch is None
            or batch["evidence_sha256"] == events[0]["binding"]["plan_id"],
            "immutable-digest-mismatch",
        )
        # A commit may have succeeded before the acknowledgement/journal event.
        cleanup = loss or events[-1].get("state") not in {
            "committed",
            "replayed",
            "refused",
        }
        return result(
            "stale" if stale else "reconciled" if batch else "unpublished",
            "changed-plan" if stale else "status",
            last_attempt=safe_attempt(events[-1]),
            cleanup_pending=cleanup,
            journal_loss=loss,
            stale=stale,
            freshness="unknown-outside-selected-manifest",
            **status,
            **detail,
        )
    core.require(args.expect_plan == selected["plan_id"], "changed-plan")
    with journal(
        args.state_dir, args.operation_id, expected, resume=args.action == "resume"
    ) as (fd, records):
        started_at = records[0]["started_at"]
        event(fd, dict(kind="attempt", at=now(), state="started"))
        try:
            with core.target(
                args.profile,
                args.collector_profile,
                args.database_name,
                args.database_id,
            ) as conn:
                outcome = core.apply(conn, selected, args.operation_id, started_at)
        except storage.Refusal as error:
            event(
                fd, dict(kind="result", at=now(), state="refused", reason=error.reason)
            )
            raise
        except (psycopg.Error, OSError, KeyboardInterrupt):
            # COMMIT acknowledgement and close can fail after durable commit.
            event(
                fd,
                dict(
                    kind="result", at=now(), state="uncertain", reason="commit-unknown"
                ),
            )
            raise
        event(fd, dict(kind="result", at=now(), **outcome))
    return result(
        outcome["state"], "imported", **outcome_without_state(outcome), **detail
    )


def outcome_without_state(outcome):
    return {k: v for k, v in outcome.items() if k != "state"}


def main(argv: list[str] | None = None) -> int:
    parser = Parser(
        description=__doc__,
        epilog="No ambient DB, scan, test execution, schema repair or natural intake authorization. See docs/devops-import.md.",
    )
    parser.add_argument("action", choices=ACTIONS)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Versioned typed result, also used by human output",
    )
    for name in ("profile", "collector-profile", "input-root", "state-dir"):
        parser.add_argument("--" + name, type=Path)
    for name in (
        "database-name",
        "database-id",
        "manifest",
        "operation-id",
        "expect-plan",
    ):
        parser.add_argument("--" + name)
    parser.json_errors = "--json" in (sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(argv)
    try:
        value = execute(args)
    except storage.Refusal as error:
        value = result("refused", error.reason, 2)
        value["next_action"] = error.next_action
    except (psycopg.Error, KeyboardInterrupt):
        value = result(
            "uncertain", "database-or-interruption", 3, commit_state="unknown"
        )
        value["next_action"] = (
            "Run status with the same explicit target and operation ID; reconcile, then resume the exact plan. Never blindly retry or restamp."
        )
    except OSError:
        value = (
            result("uncertain", "io-uncertain", 3, commit_state="unknown")
            if args.action in {"import", "resume"}
            else result("refused", "invalid-or-unavailable-input", 2)
        )
    except (ValueError, KeyError, TypeError, IndexError, OverflowError):
        value = result("refused", "invalid-or-unavailable-input", 2)
    emit(value, args.json)
    return value["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
