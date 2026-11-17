"""Queue explicitly approved local-CI manifests and drain one guarded batch."""

from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
import fcntl
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import uuid

import psycopg

from scripts.devops import cli, importer, storage

MAX_BATCH = 64
SCHEMA = """
CREATE TABLE IF NOT EXISTS target (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    name TEXT NOT NULL,
    identity TEXT NOT NULL,
    maintenance_lock TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS queue (
    seq INTEGER PRIMARY KEY,
    operation TEXT NOT NULL UNIQUE,
    plan TEXT NOT NULL UNIQUE,
    root TEXT NOT NULL,
    manifest TEXT NOT NULL,
    manifest_sha TEXT NOT NULL,
    enqueued TEXT NOT NULL,
    published TEXT
);
CREATE INDEX IF NOT EXISTS pending_fifo ON queue(seq) WHERE published IS NULL;
CREATE TABLE IF NOT EXISTS history (
    event INTEGER PRIMARY KEY,
    seq INTEGER NOT NULL REFERENCES queue(seq),
    at TEXT NOT NULL,
    state TEXT NOT NULL,
    reason TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS history_by_item ON history(seq, event);
"""


def private_dir(path: Path) -> Path:
    importer.safe_root(path)
    info = path.stat()
    importer.require(
        info.st_uid == os.getuid() and not info.st_mode & 0o077, "unsafe-directory"
    )
    return path


def private_file(info: os.stat_result) -> None:
    importer.require(
        stat.S_ISREG(info.st_mode)
        and info.st_uid == os.getuid()
        and not info.st_mode & 0o077
        and info.st_nlink == 1,
        "unsafe-file",
    )


@contextmanager
def exclusive(path: Path, reason: str, *, directory: bool = False):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    if directory:
        flags |= os.O_DIRECTORY
    fd = os.open(path, flags)
    try:
        if not directory:
            private_file(os.fstat(fd))
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            storage.refuse(reason, importer.NEXT)
        yield
    finally:
        os.close(fd)


@contextmanager
def spool(path: Path, *, create: bool = False):
    private_dir(path)
    db = path / "queue.sqlite3"
    if create:
        fd = os.open(
            db, os.O_CREAT | os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
        )
        os.close(fd)
    private_file(db.lstat())
    with closing(sqlite3.connect(db.as_uri() + "?mode=rw", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous=EXTRA")
        conn.execute("PRAGMA foreign_keys=ON")
        if not create:
            importer.require(
                conn.execute("PRAGMA user_version").fetchone()[0] == 1,
                "uninitialized-spool",
            )
        yield conn


def initialize(conn, directory: Path, name: str, identity: str, gate: Path) -> None:
    importer.require(
        re.fullmatch(r"devops_analytics_[a-z0-9_]{1,48}", name), "wrong-target"
    )
    importer.identifier(identity)
    private_dir(gate.parent)
    operations = directory / "operations"
    operations.mkdir(mode=0o700, exist_ok=True)
    private_dir(operations)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    importer.require(version in (0, 1), "unsupported-spool")
    if version == 0:
        conn.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO target VALUES (1, ?, ?, ?)",
                (name, identity, str(gate)),
            )
            conn.execute("PRAGMA user_version=1")
    importer.require(target(conn) == (name, identity), "wrong-target")
    importer.require(maintenance_lock(conn) == gate, "wrong-maintenance-lock")
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def target(conn) -> tuple[str, str]:
    row = conn.execute("SELECT name, identity FROM target WHERE singleton=1").fetchone()
    return row["name"], row["identity"]


def maintenance_lock(conn) -> Path:
    return Path(conn.execute("SELECT maintenance_lock FROM target").fetchone()[0])


def checked_plan(root: Path, manifest: str, sha: str, destination: tuple[str, str]):
    private_dir(root)
    manifest_path = Path(manifest)
    if manifest_path.is_absolute():
        private_dir(manifest_path.parent)
    selected = importer.plan(root, manifest, *destination)
    inputs = selected["inputs"]
    manifest_inputs = inputs.manifest_input
    actual = (
        manifest_inputs.snapshots[manifest_path.name]
        if manifest_inputs
        else inputs.snapshots[manifest]
    )[-1]
    importer.require(actual == sha, "changed-manifest")
    inputs.revalidate()
    return selected


def enqueue(conn, root: Path, manifest: str, sha: str) -> dict:
    selected = checked_plan(root, manifest, sha, target(conn))
    plan = selected["plan_id"]
    with conn:
        added = conn.execute(
            "INSERT INTO queue(operation, plan, root, manifest, manifest_sha, enqueued) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(plan) DO NOTHING",
            (str(uuid.uuid4()), plan, str(root), manifest, sha, cli.now()),
        ).rowcount
        entry = conn.execute(
            "SELECT operation, published FROM queue WHERE plan=?", (plan,)
        ).fetchone()
    return report(
        "queued" if added else "duplicate",
        operation_id=entry["operation"],
        published=entry["published"] is not None,
    )


def backlog(conn) -> dict:
    # Short snapshot only, so concurrent enqueue cannot split counts and head.
    with conn:
        conn.execute("BEGIN")
        return backlog_snapshot(conn)


def backlog_snapshot(conn) -> dict:
    counts = conn.execute(
        "SELECT count(*) AS total, count(*) - count(published) AS pending FROM queue"
    ).fetchone()
    head = conn.execute(
        "SELECT seq, operation AS operation_id, enqueued "
        "FROM queue WHERE published IS NULL ORDER BY seq LIMIT 1"
    ).fetchone()
    oldest = None
    if head:
        last = conn.execute(
            "SELECT state, reason, at FROM history WHERE seq=? ORDER BY event DESC LIMIT 1",
            (head["seq"],),
        ).fetchone()
        first_failure = conn.execute(
            "SELECT reason, at FROM history WHERE seq=? AND state='blocked' ORDER BY event LIMIT 1",
            (head["seq"],),
        ).fetchone()
        oldest = dict(head) | {
            "last_event": dict(last) if last else None,
            "first_failure": dict(first_failure) if first_failure else None,
        }
    return dict(counts) | {"oldest_pending": oldest}


def record(conn, item, state: str, reason: str) -> None:
    at = cli.now()
    with conn:
        conn.execute(
            "INSERT INTO history(seq, at, state, reason) VALUES (?, ?, ?, ?)",
            (item["seq"], at, state, reason),
        )
        if state == "published":
            conn.execute("UPDATE queue SET published=? WHERE seq=?", (at, item["seq"]))


def verify_transport(args) -> None:
    for path in (args.profile, args.collector_profile):
        importer.require(path.is_absolute(), "unsafe-profile")
        private_dir(path.parent)
        config = storage.connection_config(path)
        host = config["host"]
        if host.startswith("/"):
            private_dir(Path(host))
        else:
            # Only an already-established local forward. The importer retains
            # authenticate-and-verify-full semantics for TCP.
            importer.require(host == "127.0.0.1", "private-transport-required")


def upload(conn, item, args, destination, operations: Path) -> str:
    root, manifest = Path(item["root"]), item["manifest"]
    selected = checked_plan(root, manifest, item["manifest_sha"], destination)
    importer.require(selected["plan_id"] == item["plan"], "changed-plan")
    operation = item["operation"]
    request = argparse.Namespace(
        database_name=destination[0],
        database_id=destination[1],
        profile=args.profile,
        collector_profile=args.collector_profile,
        input_root=root,
        manifest=manifest,
        state_dir=operations,
        operation_id=operation,
        expect_plan=item["plan"],
    )
    started = conn.execute(
        "SELECT 1 FROM history WHERE seq=? AND state='started' LIMIT 1", (item["seq"],)
    ).fetchone()
    if started:
        request.action = "status"
        status = cli.execute(request)
        importer.require(
            status["state"] in {"unpublished", "reconciled"}, "changed-plan"
        )
        if status["journal_loss"]:
            record(conn, item, "notice", "operation-journal-loss")
    else:
        # A durable importer header precedes the durable queue attempt. A crash
        # between those commits cannot have published anything to PostgreSQL.
        with cli.journal(
            operations, operation, cli.binding(selected, operation), resume=False
        ):
            pass
    record(conn, item, "started", "importer-resume")
    request.action = "resume"
    outcome = cli.execute(request)
    importer.require(outcome["state"] in {"committed", "replayed"}, "unknown-outcome")
    return outcome["state"]


def error_result(error: BaseException) -> tuple[str, int]:
    if isinstance(error, storage.Refusal):
        return error.reason, 2
    if isinstance(error, (psycopg.Error, KeyboardInterrupt)):
        return "database-unavailable-or-interrupted", 3
    if isinstance(error, (sqlite3.Error, OSError)):
        return "unavailable-local-state-or-input", 3
    return "invalid-queue-input", 2


FAILURES = (
    storage.Refusal,
    psycopg.Error,
    KeyboardInterrupt,
    OSError,
    sqlite3.Error,
    ValueError,
    TypeError,
    KeyError,
    IndexError,
    OverflowError,
)


def drain(conn, args) -> dict:
    destination = target(conn)
    importer.require(
        destination == (args.database_name, args.database_id), "wrong-target"
    )
    importer.require(1 <= args.limit <= MAX_BATCH, "invalid-batch-limit")
    operations = private_dir(args.spool / "operations")
    gate = maintenance_lock(conn)
    private_dir(gate.parent)
    completed = 0
    with exclusive(operations, "busy-drain", directory=True):
        items = conn.execute(
            "SELECT * FROM queue WHERE published IS NULL ORDER BY seq LIMIT ?",
            (args.limit,),
        ).fetchall()
        if not items:
            return report("empty", processed=0, **backlog(conn))
        with exclusive(gate, "maintenance-or-upload-active"):
            try:
                gate.with_name(gate.name + ".hold").lstat()
            except FileNotFoundError:
                pass
            else:
                storage.refuse("maintenance-hold", importer.NEXT)
            try:
                verify_transport(args)
                ready = cli.execute(
                    argparse.Namespace(**(vars(args) | {"action": "doctor"}))
                )
                importer.require(ready["state"] == "ready", "target-not-ready")
                for item in items:
                    state = upload(conn, item, args, destination, operations)
                    record(conn, item, "published", state)
                    completed += 1
            except FAILURES as error:
                reason, code = error_result(error)
                record(conn, items[completed], "blocked", reason)
                return report(
                    "blocked", reason, code, processed=completed, **backlog(conn)
                )
    return report("drained", processed=completed, **backlog(conn))


def report(state: str, reason: str = "ok", code: int = 0, **fields) -> dict:
    return cli.result(state, reason, code, **fields) | {
        "next_action": (
            "Inspect the private backlog and resolve the oldest blocker before draining again."
            if code
            else "Use backlog to inspect pending work."
        )
    }


def execute(args) -> dict:
    with spool(args.spool, create=args.action == "init") as conn:
        if args.action == "init":
            initialize(
                conn,
                args.spool,
                args.database_name,
                args.database_id,
                args.maintenance_lock,
            )
            return report("initialized")
        if args.action == "enqueue":
            return enqueue(conn, args.input_root, args.manifest, args.expect_manifest)
        if args.action == "backlog":
            return report("backlog", **backlog(conn))
        try:
            return drain(conn, args)
        except FAILURES as error:
            reason, code = error_result(error)
            return report("blocked", reason, code, **backlog(conn))


class QueueParser(cli.Parser):
    json_errors = True


def main(argv: list[str] | None = None) -> int:
    parser = QueueParser(description=__doc__)
    parser.add_argument("--spool", type=Path, required=True)
    actions = parser.add_subparsers(dest="action", required=True)
    init = actions.add_parser("init")
    init.add_argument("--database-name", required=True)
    init.add_argument("--database-id", required=True)
    init.add_argument("--maintenance-lock", type=Path, required=True)
    submit = actions.add_parser("enqueue")
    submit.add_argument("--input-root", type=Path, required=True)
    submit.add_argument("--manifest", required=True)
    submit.add_argument("--expect-manifest", required=True)
    actions.add_parser("backlog")
    batch = actions.add_parser("drain")
    batch.add_argument("--database-name", required=True)
    batch.add_argument("--database-id", required=True)
    batch.add_argument("--profile", type=Path, required=True)
    batch.add_argument("--collector-profile", type=Path, required=True)
    batch.add_argument("--limit", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        value = execute(args)
    except FAILURES as error:
        reason, code = error_result(error)
        value = report("refused", reason, code)
    cli.emit(value, True)
    return value["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
