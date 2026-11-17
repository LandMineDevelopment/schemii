"""Offline queue controls and the original guarded PostgreSQL receipt contract."""

from contextlib import redirect_stdout
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import psycopg
from psycopg import sql
import pytest

from scripts.devops import cli, importer, receipt_queue as queue, storage
from tests.devops.test_importer import DB_ID, NAME, fixture_input, historical_fixture


def run(work, action, *args, process=False):
    argv = ["--spool", str(work), action, *(str(a) for a in args)]
    if process:
        run = subprocess.run(
            [sys.executable, "-m", "scripts.devops.receipt_queue", *argv],
            capture_output=True,
            text=True,
            check=False,
        )
        assert run.stderr == ""
        code, output = run.returncode, run.stdout
    else:
        # Exercise the same CLI boundary and check its sanitized JSON contract.
        with redirect_stdout(StringIO()) as stream:
            code = queue.main(argv)
        output = stream.getvalue()
    assert "SECRET_SENTINEL" not in output
    result = json.loads(output)
    assert result["schema"] == 1 and result["exit_code"] == code
    return result


def destination(identity=DB_ID):
    return ("--database-name", NAME, "--database-id", identity)


def private_file(path, content=""):
    path.touch(mode=0o600)
    path.write_text(content)
    return path


def make_spool(tmp_path, identity=DB_ID):
    work = tmp_path / "spool"
    work.mkdir(mode=0o700)
    lock = private_file(work / "maintenance")
    assert run(work, "init", *destination(identity), "--maintenance-lock", lock)["ok"]
    return work, lock


def source(tmp_path, name="input", **receipt):
    root = tmp_path / name
    root.mkdir(mode=0o700)
    fixture_input(root, **receipt)
    return root


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def enqueue(work, root, *, manifest="manifest.json", expected=None, process=False):
    return run(
        work,
        "enqueue",
        "--input-root",
        root,
        "--manifest",
        manifest,
        "--expect-manifest",
        expected or digest(root / manifest),
        process=process,
    )


def drain(work, profiles, *, limit=1, identity=DB_ID, process=False):
    return run(
        work,
        "drain",
        *destination(identity),
        "--limit",
        limit,
        "--profile",
        profiles / "migrator.json",
        "--collector-profile",
        profiles / "collector.json",
        process=process,
    )


def profile_pair(root, host):
    private_file(root / "passfile")
    profile = dict(
        host=str(host),
        port=5432,
        dbname=NAME,
        user="unused",
        passfile=str(root / "passfile"),
    )
    for name in ("migrator", "collector"):
        private_file(root / (name + ".json"), json.dumps(profile))


def events(work, sequence):
    with queue.spool(work) as conn:
        return [
            tuple(row)
            for row in conn.execute(
                "SELECT state, reason FROM history WHERE seq=? ORDER BY event",
                (sequence,),
            )
        ]


def test_offline_enqueue_restart_duplicate_and_original_manifest(tmp_path, monkeypatch):
    work, _ = make_spool(tmp_path)
    first = source(tmp_path)
    receipt_path = first / "run/receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["reason"] = "SECRET_SENTINEL"
    receipt_path.write_text(json.dumps(receipt))
    # A fully separate collector process can enqueue without any DB profile.
    recorded = enqueue(work, first, process=True)
    assert recorded["state"] == "queued" and not recorded["published"]
    assert run(work, "backlog", process=True)["pending"] == 1
    monkeypatch.setattr(
        importer.psycopg, "connect", lambda **kw: pytest.fail("offline connected")
    )
    manifest = private_file(
        tmp_path / "external.json", (first / "manifest.json").read_text()
    )
    duplicate = enqueue(work, first, manifest=manifest)
    assert duplicate["state"] == "duplicate"
    assert duplicate["operation_id"] == recorded["operation_id"]
    second = source(
        tmp_path,
        "continued-collection",
        status="failed",
        attempts=("failed",),
        terminal="failed",
    )
    assert enqueue(work, second)["state"] == "queued"
    pending = run(work, "backlog", process=True)
    assert pending["total"] == pending["pending"] == 2
    assert pending["oldest_pending"]["seq"] == 1
    assert pending["oldest_pending"]["last_event"] is None
    assert (second / "run/receipt.json").is_file()
    assert list((work / "operations").iterdir()) == []
    assert b"SECRET_SENTINEL" not in (work / "queue.sqlite3").read_bytes()


def test_manifests_require_approved_digest_and_typed_local_ci(tmp_path):
    work, _ = make_spool(tmp_path)
    root = source(tmp_path)
    assert enqueue(work, root, expected="0" * 64)["reason"] == "changed-manifest"
    path = root / "manifest.json"
    value = json.loads(path.read_text())
    value["source_kind"] = "t3-lifecycle-v1"
    path.write_text(json.dumps(value))
    assert not enqueue(work, root)["ok"]
    assert run(work, "backlog")["pending"] == 0


def test_maintenance_pin_and_serial_writer_leave_collection_free(tmp_path, monkeypatch):
    work, gate = make_spool(tmp_path)
    assert enqueue(work, source(tmp_path))["ok"]
    alternate = private_file(work / "alternate")
    monkeypatch.setattr(
        cli, "execute", lambda args: pytest.fail("guarded target accessed")
    )
    assert (
        run(work, "init", *destination(), "--maintenance-lock", alternate)["reason"]
        == "wrong-maintenance-lock"
    )
    assert drain(work, work, identity=str(uuid.uuid4()))["reason"] == "wrong-target"
    assert (
        drain(work, work, limit=queue.MAX_BATCH + 1)["reason"] == "invalid-batch-limit"
    )
    hold = gate.with_name(gate.name + ".hold")
    hold.symlink_to("missing-marker")
    assert drain(work, work)["reason"] == "maintenance-hold"
    hold.unlink()
    with queue.exclusive(gate, "test-lock"):
        assert drain(work, work)["reason"] == "maintenance-or-upload-active"
    with queue.exclusive(work / "operations", "test-lock", directory=True):
        assert drain(work, work, process=True)["reason"] == "busy-drain"
        assert enqueue(work, source(tmp_path, "next"), process=True)["ok"]
    gate.unlink()
    assert not drain(work, work)["ok"]
    assert run(work, "backlog")["pending"] == 2
    assert not events(work, 1)


def test_private_transport_and_offline_backlog_keep_first_failure(
    tmp_path, monkeypatch
):
    work, _ = make_spool(tmp_path)
    first = source(tmp_path)
    entry = enqueue(work, first)
    profiles = tmp_path / "profiles"
    profiles.mkdir(mode=0o700)
    profile_pair(profiles, "public.example.invalid")
    with monkeypatch.context() as isolated:
        isolated.setattr(
            importer, "connect", lambda *_: pytest.fail("public target attempted")
        )
        assert drain(work, profiles)["reason"] == "private-transport-required"
    socket = tmp_path / "offline-socket"
    socket.mkdir(mode=0o700)
    profile_pair(profiles, socket)
    blocked = drain(work, profiles, process=True)
    assert blocked["reason"] == "database-unavailable-or-interrupted"
    assert blocked["processed"] == 0 and blocked["pending"] == 1
    first_failure = blocked["oldest_pending"]["first_failure"]
    assert first_failure["reason"] == "private-transport-required"
    assert list((work / "operations").iterdir()) == []
    assert enqueue(work, first, process=True)["operation_id"] == entry["operation_id"]
    assert enqueue(work, source(tmp_path, "during-outage"))["ok"]
    assert run(work, "backlog")["pending"] == 2


@pytest.mark.parametrize("change", ["manifest", "receipt"])
def test_changed_seal_or_projection_blocks_without_publication(
    tmp_path, monkeypatch, change
):
    work, _ = make_spool(tmp_path)
    root = source(tmp_path)
    assert enqueue(work, root)["ok"]
    profile_pair(work, work)
    if change == "manifest":
        path = root / "manifest.json"
        path.write_text(path.read_text() + " ")
    else:
        path = root / "run/receipt.json"
        receipt = json.loads(path.read_text())
        receipt["duration_ms"] += 1
        path.write_text(json.dumps(receipt))

    def ready(args):
        assert args.action == "doctor"
        return {"state": "ready"}

    monkeypatch.setattr(cli, "execute", ready)
    result = drain(work, work)
    assert result["reason"] == (
        "changed-manifest" if change == "manifest" else "changed-plan"
    )
    assert result["pending"] == 1 and list((work / "operations").iterdir()) == []


@pytest.fixture
def guarded_fixture():
    """Use a separately owned clone of the disposable #279 fixture, if supplied."""
    marker = os.getenv("SCHEMII_DEVOPS307_FIXTURE", "")
    if not marker:
        pytest.skip("explicit owned disposable PostgreSQL fixture required")
    path = Path(marker)
    queue.private_dir(path.parent)
    queue.private_file(path.lstat())
    selected = json.loads(path.read_text())
    assert selected["owner"] == "devops307-disposable"
    pid = selected["pid"]
    assert (
        Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
        == selected["birth"]
    )
    # Check the explicit disposable socket before connecting any test authority.
    socket = queue.private_dir(Path(selected["socket"]))
    for role in ("admin", "migrator", "collector"):
        config = storage.connection_config(path.parent / f"{role}.json")
        assert config["host"] == str(socket) and config["dbname"] == NAME
    with importer.connect(
        storage.connection_config(path.parent / "admin.json")
    ) as admin:
        assert admin.info.server_version == 180006
        assert (
            admin.execute("SHOW listen_addresses").fetchone()["listen_addresses"] == ""
        )
        assert (
            admin.execute("SHOW data_directory").fetchone()["data_directory"]
            == selected["data"]
        )
        assert admin.execute("SHOW unix_socket_directories").fetchone()[
            "unix_socket_directories"
        ] == str(socket)
    return path.parent


def guarded_rows(profiles, root, operation):
    body = importer.plan(root, "manifest.json", NAME, DB_ID)["body"]
    with importer.target(
        profiles / "migrator.json", profiles / "collector.json", NAME, DB_ID
    ) as conn:
        scope = [body["repository"]["id"], body["installation_id"]]
        batches = conn.execute(
            "SELECT id,status,accepted_count,source_kind FROM devops.import_batches "
            "WHERE repository_id=%s AND installation_id=%s",
            scope,
        ).fetchall()
        if not batches:
            return None
        assert len(batches) == 1 and str(batches[0]["id"]) == operation
        expected = {
            "ci_runs": ["outcome", "acceptance", "source_fingerprint"],
            "commands": ["slot_key", "outcome"],
            "test_attempts": ["outcome", "attempt_number"],
        }
        for table, extra in expected.items():
            columns = ["id", "source_key", "revision", "evidence_sha256", *extra]
            stmt = sql.SQL(
                "SELECT {}, import_batch_id FROM devops.{} "
                "WHERE repository_id=%s AND installation_id=%s"
            ).format(
                sql.SQL(", ").join(map(sql.Identifier, columns)),
                sql.Identifier(table),
            )
            stored = conn.execute(stmt, scope).fetchall()
            assert all(str(row["import_batch_id"]) == operation for row in stored)
            actual = {
                str(row["id"]): tuple(row[key] for key in columns[1:]) for row in stored
            }
            assert actual == {
                fact["id"]: tuple(fact[key] for key in columns[1:])
                for fact in body["rows"][table]
            }
        assert batches[0]["accepted_count"] == sum(map(len, body["rows"].values()))
        assert batches[0]["source_kind"] == "local-ci-v1"
        return batches[0]["status"]


@pytest.mark.parametrize("after_commit", [False, True])
def test_guarded_fifo_partial_progress_and_exact_reconciliation(
    tmp_path, monkeypatch, guarded_fixture, after_commit
):
    work, _ = make_spool(tmp_path)
    first = source(tmp_path, "first")
    historic = tmp_path / "historical"
    historic.mkdir(mode=0o700)
    historical_fixture(historic)
    failed = source(
        tmp_path,
        "failed",
        status="failed",
        attempts=("failed",),
        terminal="failed",
        saved_summary=False,
    )
    first_id, historical_id, failed_id = [
        enqueue(work, root)["operation_id"] for root in (first, historic, failed)
    ]
    apply = importer.apply

    def interrupted(conn, selected, operation, started_at):
        if operation != historical_id or after_commit:
            outcome = apply(conn, selected, operation, started_at)
            if operation != historical_id:
                return outcome
        raise psycopg.OperationalError("SECRET_SENTINEL lost acknowledgement")

    with monkeypatch.context() as isolated:
        isolated.setattr(importer, "apply", interrupted)
        batch = drain(work, guarded_fixture, limit=2)
    assert (
        batch["state"] == "blocked"
        and batch["processed"] == 1
        and batch["pending"] == 2
    )
    assert batch["oldest_pending"]["seq"] == 2
    assert guarded_rows(guarded_fixture, first, first_id) == "partial"
    assert guarded_rows(guarded_fixture, historic, historical_id) == (
        "partial" if after_commit else None
    )
    assert guarded_rows(guarded_fixture, failed, failed_id) is None

    seen = []
    execute = cli.execute

    def observe(args):
        value = execute(args)
        if args.action in {"status", "resume"}:
            seen.append((args.action, args.operation_id, value["state"]))
        return value

    with monkeypatch.context() as isolated:
        isolated.setattr(cli, "execute", observe)
        resumed = drain(work, guarded_fixture)
    assert resumed["processed"] == 1 and resumed["pending"] == 1
    assert seen == [
        ("status", historical_id, "reconciled" if after_commit else "unpublished"),
        ("resume", historical_id, "replayed" if after_commit else "committed"),
    ]
    assert events(work, 2) == [
        ("started", "importer-resume"),
        ("blocked", "database-unavailable-or-interrupted"),
        ("started", "importer-resume"),
        ("published", "replayed" if after_commit else "committed"),
    ]
    assert guarded_rows(guarded_fixture, historic, historical_id) == "partial"
    assert guarded_rows(guarded_fixture, failed, failed_id) is None
    assert drain(work, guarded_fixture, process=True)["pending"] == 0
    duplicate = enqueue(work, historic, process=True)
    assert duplicate["operation_id"] == historical_id and duplicate["published"]
    assert drain(work, guarded_fixture)["state"] == "empty"
    assert guarded_rows(guarded_fixture, historic, historical_id) == "partial"
    assert guarded_rows(guarded_fixture, failed, failed_id) == "partial"


def test_guard_refuses_same_database_name_with_wrong_identity(
    tmp_path, guarded_fixture
):
    identity = str(uuid.uuid4())
    work, _ = make_spool(tmp_path, identity)
    root = source(tmp_path)
    enqueued = enqueue(work, root)
    refused = drain(work, guarded_fixture, identity=identity)
    assert refused["reason"] == "wrong-target"
    assert refused["pending"] == 1 and refused["processed"] == 0
    assert list((work / "operations").iterdir()) == []
    assert guarded_rows(guarded_fixture, root, enqueued["operation_id"]) is None


def test_collector_process_exit_after_commit_reconciles_on_restart(
    tmp_path, guarded_fixture
):
    work, _ = make_spool(tmp_path)
    root = source(tmp_path)
    operation = enqueue(work, root, process=True)["operation_id"]
    inject_exit = """
import os
from scripts.devops import receipt_queue as queue
original = queue.record
def record(conn, item, state, reason):
    if state == "published":
        os._exit(71)
    original(conn, item, state, reason)
queue.record = record
queue.main()
"""
    crashed = subprocess.run(
        [
            sys.executable,
            "-c",
            inject_exit,
            "--spool",
            str(work),
            "drain",
            *destination(),
            "--limit",
            "1",
            "--profile",
            str(guarded_fixture / "migrator.json"),
            "--collector-profile",
            str(guarded_fixture / "collector.json"),
        ],
        capture_output=True,
        check=False,
    )
    assert crashed.returncode == 71 and not crashed.stdout and not crashed.stderr
    pending = run(work, "backlog", process=True)["oldest_pending"]
    assert pending["operation_id"] == operation
    assert pending["last_event"]["state"] == "started"
    assert guarded_rows(guarded_fixture, root, operation) == "partial"
    assert drain(work, guarded_fixture, process=True)["pending"] == 0
    assert events(work, 1)[-1] == ("published", "replayed")
    assert guarded_rows(guarded_fixture, root, operation) == "partial"


def test_missing_attempt_journal_refuses_instead_of_minting_new_operation(
    tmp_path, monkeypatch
):
    work, _ = make_spool(tmp_path)
    root = source(tmp_path)
    entry = enqueue(work, root)
    profile_pair(work, work)
    execute = cli.execute

    def interrupted(args):
        if args.action == "doctor":
            return {"state": "ready"}
        if args.action == "resume":
            raise KeyboardInterrupt
        return execute(args)

    monkeypatch.setattr(cli, "execute", interrupted)
    assert drain(work, work)["pending"] == 1
    path = work / "operations" / (entry["operation_id"] + ".jsonl")
    path.rename(path.with_suffix(".preserved"))
    refused = drain(work, work)
    assert not refused["ok"] and refused["pending"] == 1
    assert not path.exists()
    assert enqueue(work, root)["operation_id"] == entry["operation_id"]
