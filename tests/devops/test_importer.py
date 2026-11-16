"""Original producer-shaped fixtures and explicit disposable PostgreSQL controls."""

from copy import deepcopy
import json
import os
from pathlib import Path
import uuid
from typing import Any

import psycopg
import pytest

from scripts.ci import summary, test_selection
from scripts.devops import cli, importer, storage

DB_ID = "b0b3ae63-0626-481c-af8f-151ff939b53b"
NAME = "devops_analytics_import279"
WHEN = "2026-11-16T00:00:00+00:00"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def fixture_input(
    root,
    *,
    status="passed",
    attempts=("passed",),
    terminal="passed",
    saved_summary=True,
    profile="frontend-tests",
):
    invocation = str(uuid.uuid4())
    entries: list[dict[str, Any]] = [
        {"command": argv, "status": "passed", "duration_ms": 12.5}
        for argv in test_selection.commands(profile, "origin/main")
    ]
    key = importer.command_identity(entries[0]["command"])
    meta = dict(
        schema=1,
        run_id=1,
        run_attempt=1,
        source_sha="1" * 40,
        lane=key[0],
        project=key[1],
        shard=key[2],
    )
    records = [
        meta | dict(kind="start", planned=1),
        meta | dict(kind="plan", test_id="a" * 64),
    ]
    for i, outcome in enumerate(attempts):
        records.append(
            meta
            | dict(
                kind="attempt",
                test_id="a" * 64,
                source_id="b" * 64,
                source_line=1,
                attempt=i,
                outcome=outcome,
                skip="declared-or-runtime" if outcome == "skipped" else "none",
                setup_ms=0,
                execution_ms=1.5,
                teardown_ms=0,
            )
        )
    if terminal:
        records.append(meta | dict(kind="end", outcome=terminal, wall_ms=12.5))
    entries[0].update(
        timing_evidence="command-00/timing.jsonl",
        status="passed" if status in {"passed", "feedback-passed"} else status,
    )
    receipt = dict(
        schema=1,
        run_id=1,
        started_at=WHEN,
        finished_at=WHEN,
        duration_ms=12.5,
        base_ref="origin/main",
        classification=dict(profile=profile, valid=True),
        mode="feedback" if status == "feedback-passed" else "selected",
        profile=profile,
        source=dict(
            commit="1" * 40,
            fingerprint="2" * 64,
            index_fingerprint="3" * 64,
            dirty=True,
        ),
        status=status,
        acceptance=status == "passed",
        commands=entries,
        pending_layers=[] if status in {"passed", "feedback-passed"} else ["node"],
        pending_commands=[i for i, v in enumerate(entries) if v["status"] != "passed"],
    )
    selection = dict(
        invocation_id=invocation,
        receipt="run/receipt.json",
        telemetry=[dict(slot=0, file="run/command-00/timing.jsonl")],
        summaries=[dict(slot=0, file="run/command-00/timing.summary.json")]
        if saved_summary
        else [],
        revision=1,
        change_kind="initial",
        observed_at=WHEN,
    )
    manifest = dict(
        schema=1,
        repository=dict(
            id=str(uuid.uuid4()),
            provider="local",
            repository_key="synthetic279-" + uuid.uuid4().hex,
            display_name="synthetic-only",
        ),
        installation_id=str(uuid.uuid4()),
        runs=[selection],
    )
    write(root / "run/receipt.json", receipt)
    timing = root / "run/command-00/timing.jsonl"
    timing.parent.mkdir(parents=True, exist_ok=True)
    timing.write_text("".join(json.dumps(v) + "\n" for v in records))
    if saved_summary:
        write(root / "run/command-00/timing.summary.json", summary.summarize(records))
    write(root / "manifest.json", manifest)
    return manifest, receipt, records


def selected(root):
    return importer.plan(root, "manifest.json", NAME, DB_ID)


@pytest.mark.parametrize(
    "status,attempts,terminal,count",
    [
        ("passed", ("passed",), "passed", 1),
        ("failed", ("failed",), "failed", 1),
        ("incomplete", ("failed", "passed"), "passed", 2),
        ("feedback-passed", ("passed",), "passed", 1),
        ("cancelled", ("cancelled",), "cancelled", 0),
        ("incomplete", ("not-run",), None, 0),
        ("source-changed", ("passed",), "passed", 1),
    ],
)
def test_original_states(tmp_path, status, attempts, terminal, count):
    fixture_input(
        tmp_path,
        status=status,
        attempts=attempts,
        terminal=terminal,
        saved_summary=False if status != "passed" else True,
    )
    p = selected(tmp_path)
    run = p["body"]["rows"]["ci_runs"][0]
    assert run["outcome"] == status and run["dirty"] is True
    assert run["base_sha"] is run["policy_sha256"] is run["cohort_key"] is None
    assert len(p["body"]["rows"]["test_attempts"]) == count
    assert run["acceptance"] is (status == "passed")
    if status == "feedback-passed":
        assert run["completeness"] == "complete" and not run["acceptance"]


def test_missing_summary_and_original_unstarted(tmp_path):
    m, r, _ = fixture_input(tmp_path, status="failed", saved_summary=False)
    assert "missing-summary" in selected(tmp_path)["body"]["gaps"]
    r["status"] = "pending"
    r["source"] = None
    r.pop("acceptance")
    r["commands"][0] = dict(command=["npm", "test"], status="pending", duration_ms=None)
    r["pending_commands"] = [0]
    m["runs"][0].update(telemetry=[], summaries=[])
    write(tmp_path / "run/receipt.json", r)
    write(tmp_path / "manifest.json", m)
    p = selected(tmp_path)
    assert p["body"]["rows"]["ci_runs"][0]["acceptance"] is None
    assert p["body"]["rows"]["commands"][0]["disposition"] == "pending"
    assert not p["body"]["rows"]["test_attempts"]


@pytest.mark.parametrize(
    "change",
    [
        "schema",
        "command",
        "secret",
        "mixed",
        "oversized",
        "malformed",
        "symlink",
        "parent",
        "contradiction",
    ],
)
def test_refusals_and_no_private_diagnostics(tmp_path, change, capsys):
    m, r, records = fixture_input(tmp_path)
    if change == "schema":
        r["schema"] = 99
    elif change == "command":
        r["commands"][0]["command"] = ["sh", "SECRET_SENTINEL"]
    elif change == "secret":
        r["secret"] = "SECRET_SENTINEL"
    elif change == "mixed":
        records[-1]["run_id"] = 99
        (tmp_path / "run/command-00/timing.jsonl").write_text(
            "\n".join(json.dumps(v) for v in records)
        )
    elif change == "parent":
        m["runs"][0]["receipt"] = "absent/receipt.json"
    elif change == "contradiction":
        r["status"] = "failed"
    write(tmp_path / "run/receipt.json", r)
    write(tmp_path / "manifest.json", m)
    if change == "symlink":
        (tmp_path / "alias").symlink_to(tmp_path / "run", target_is_directory=True)
        m["runs"][0]["receipt"] = "alias/receipt.json"
        write(tmp_path / "manifest.json", m)
    elif change == "oversized":
        with (tmp_path / "run/receipt.json").open("wb") as stream:
            stream.truncate(importer.MAX_FILE + 1)
    elif change == "malformed":
        (tmp_path / "run/receipt.json").write_text('{"SECRET_SENTINEL":')
    code = cli.main(
        [
            "plan",
            "--input-root",
            str(tmp_path),
            "--manifest",
            "manifest.json",
            "--database-name",
            NAME,
            "--database-id",
            DB_ID,
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert code == 2 and "SECRET_SENTINEL" not in captured.out + captured.err


def test_changed_bytes_and_symlink_swap_after_plan(tmp_path):
    fixture_input(tmp_path)
    p = selected(tmp_path)
    with (tmp_path / "run/receipt.json").open("a") as stream:
        stream.write(" ")
    with pytest.raises(storage.Refusal) as failure:
        p["inputs"].revalidate()
    assert failure.value.reason == "changed-input"


def test_identity_and_reuse_without_attempts(tmp_path):
    m, r, _ = fixture_input(tmp_path)
    first = selected(tmp_path)
    old = m["runs"][0]
    other = deepcopy(old)
    other.update(
        invocation_id=str(uuid.uuid4()),
        receipt="other/receipt.json",
        telemetry=[],
        summaries=[],
    )
    pending = deepcopy(r)
    pending.update(
        status="pending",
        acceptance=False,
        pending_commands=[0],
        pending_layers=["node"],
    )
    pending["commands"][0] = dict(
        command=["npm", "test"], status="pending", duration_ms=None
    )
    write(tmp_path / "other/receipt.json", pending)
    m["runs"].append(other)
    m["reuse"] = [
        dict(
            target_invocation=other["invocation_id"],
            target_slot=0,
            donor_invocation=old["invocation_id"],
            donor_slot=0,
        )
    ]
    write(tmp_path / "manifest.json", m)
    body = selected(tmp_path)["body"]
    assert len(body["rows"]["test_attempts"]) == 1
    donor, reused = body["rows"]["commands"]
    assert reused["donor_command_id"] == donor["id"] and reused["outcome"] is None
    assert len({v["id"] for v in body["rows"]["ci_runs"]}) == 2
    m["installation_id"] = str(uuid.uuid4())
    write(tmp_path / "manifest.json", m)
    assert (
        first["body"]["rows"]["ci_runs"][0]["id"]
        != selected(tmp_path)["body"]["rows"]["ci_runs"][0]["id"]
    )
    m["repository"]["id"] = str(uuid.uuid4())
    write(tmp_path / "manifest.json", m)
    assert donor["id"] != selected(tmp_path)["body"]["rows"]["commands"][0]["id"]


@pytest.fixture
def real():
    marker = os.environ.get("SCHEMII_DEVOPS279_FIXTURE")
    if not marker:
        pytest.skip("explicit own disposable devops279 fixture required")
    path = Path(marker)
    root = Path("/home/kasey/.cache/schemii-devops279")
    assert not path.is_symlink() and path.resolve().is_relative_to(root)
    m = json.loads(path.read_text())
    assert m["owner"] == "devops279-original-local-ci-importer"
    assert (
        Path(f"/proc/{m['pid']}/stat").read_text().rsplit(")", 1)[1].split()[19]
        == m["birth"]
    )
    admin = storage.connection_config(path.parent / "admin.json")
    with psycopg.connect(**admin, autocommit=True) as conn:
        assert conn.execute("SHOW listen_addresses").fetchall()[0][0] == ""
        assert conn.execute("SHOW data_directory").fetchall()[0][0] == m["data"]
        assert (
            conn.execute("SHOW unix_socket_directories").fetchall()[0][0] == m["socket"]
        )
        assert conn.info.server_version == 180006
    return path.parent


def target(real):
    return importer.target(real / "migrator.json", real / "collector.json", NAME, DB_ID)


def test_pg_import_replay_conflict_and_revision(real, tmp_path):
    m, r, _ = fixture_input(tmp_path)
    p = selected(tmp_path)
    operation = str(uuid.uuid4())
    with target(real) as conn:
        assert importer.apply(conn, p, operation, WHEN)["accepted_count"] == 3
        assert importer.apply(conn, p, operation, WHEN)["state"] == "replayed"
        second = importer.apply(conn, p, str(uuid.uuid4()), WHEN)
        assert second["accepted_count"] == 0
        row = conn.execute(
            "SELECT count(*) AS n FROM devops.test_attempts WHERE repository_id=%s",
            [m["repository"]["id"]],
        ).fetchone()
        assert row["n"] == 1
    r["duration_ms"] = 99
    write(tmp_path / "run/receipt.json", r)
    with target(real) as conn, pytest.raises(storage.Refusal) as error:
        importer.apply(conn, selected(tmp_path), str(uuid.uuid4()), WHEN)
    assert error.value.reason == "immutable-digest-mismatch"
    m["runs"][0].update(revision=2, change_kind="retraction")
    write(tmp_path / "manifest.json", m)
    with target(real) as conn:
        assert (
            importer.apply(conn, selected(tmp_path), str(uuid.uuid4()), WHEN)[
                "accepted_count"
            ]
            == 1
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM devops.v1_ci_runs WHERE repository_id=%s",
                [m["repository"]["id"]],
            ).fetchone()["n"]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM devops.ci_runs WHERE repository_id=%s",
                [m["repository"]["id"]],
            ).fetchone()["n"]
            == 2
        )


def test_pg_busy_and_changed_before_commit(real, tmp_path):
    m, _, _ = fixture_input(tmp_path)
    p = selected(tmp_path)
    config = storage.connection_config(real / "collector.json")
    with importer.connect(config) as other, target(real) as conn:
        with other.transaction():
            other.execute(
                "SELECT pg_advisory_xact_lock(279,hashtext(%s))",
                [m["repository"]["id"] + m["installation_id"]],
            )
            with pytest.raises(storage.Refusal) as error:
                importer.apply(conn, p, str(uuid.uuid4()), WHEN)
            assert error.value.reason == "busy-stream"
        (tmp_path / "run/receipt.json").write_text("{}")
        with pytest.raises(storage.Refusal) as error:
            importer.apply(conn, p, str(uuid.uuid4()), WHEN)
        assert error.value.reason == "changed-input"
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM devops.import_batches WHERE repository_id=%s",
                [m["repository"]["id"]],
            ).fetchone()["n"]
            == 0
        )


def test_pg_wrong_target_profile_uuid_and_absent_db(real):
    with pytest.raises(storage.Refusal):
        with importer.target(
            real / "migrator.json", real / "collector.json", NAME, str(uuid.uuid4())
        ):
            pass
    with pytest.raises(storage.Refusal):
        with importer.target(
            real / "migrator.json", real / "migrator.json", NAME, DB_ID
        ):
            pass
    # Nonexistent name fails explicit profile/name agreement before connection.
    with pytest.raises(storage.Refusal):
        with importer.target(
            real / "migrator.json",
            real / "collector.json",
            "devops_analytics_absent279",
            DB_ID,
        ):
            pass


def test_same_lane_different_commands_and_invalid_donor(tmp_path):
    m, r, _ = fixture_input(tmp_path)
    r.update(
        profile="full",
        classification=dict(profile="full", valid=True),
        status="failed",
        acceptance=False,
    )
    r["commands"] = [
        dict(command=v, status="pending", duration_ms=None)
        for v in test_selection.commands("full", "origin/main")
    ]
    donor = next(
        i for i, c in enumerate(r["commands"]) if c["command"] == ["npm", "test"]
    )
    other = next(
        i for i, c in enumerate(r["commands"]) if c["command"][:2] == ["node", "--test"]
    )
    r["commands"][donor].update(status="failed", duration_ms=4)
    r["pending_commands"] = list(range(len(r["commands"])))
    r["pending_layers"] = ["browser", "node", "postgres", "python", "static"]
    m["runs"][0].update(telemetry=[], summaries=[])
    write(tmp_path / "run/receipt.json", r)
    write(tmp_path / "manifest.json", m)
    p = selected(tmp_path)
    cmds = p["body"]["rows"]["commands"]
    assert (
        cmds[donor]["id"] != cmds[other]["id"]
        and cmds[donor]["command_key"] != cmds[other]["command_key"]
    )
    m["reuse"] = [
        dict(
            target_invocation=m["runs"][0]["invocation_id"],
            target_slot=other,
            donor_invocation=m["runs"][0]["invocation_id"],
            donor_slot=donor,
        )
    ]
    write(tmp_path / "manifest.json", m)
    with pytest.raises(storage.Refusal) as error:
        selected(tmp_path)
    assert error.value.reason == "invalid-reuse"


def test_pg_reuse_and_registry_collision(real, tmp_path):
    test_identity_and_reuse_without_attempts(tmp_path)
    p = selected(tmp_path)
    with target(real) as conn:
        importer.apply(conn, p, str(uuid.uuid4()), WHEN)
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM devops.test_attempts WHERE repository_id=%s",
                [p["body"]["repository"]["id"]],
            ).fetchone()["n"]
            == 1
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM devops.commands WHERE repository_id=%s AND disposition='reused'",
                [p["body"]["repository"]["id"]],
            ).fetchone()["n"]
            == 1
        )
    m = json.loads((tmp_path / "manifest.json").read_text())
    m["repository"]["display_name"] = "changed-registry"
    write(tmp_path / "manifest.json", m)
    with target(real) as conn, pytest.raises(psycopg.errors.UniqueViolation):
        importer.apply(conn, selected(tmp_path), str(uuid.uuid4()), WHEN)


def test_pg_actual_absent_database(real, tmp_path):
    for role in ("migrator", "collector"):
        p = json.loads((real / (role + ".json")).read_text())
        p["dbname"] = "devops_analytics_absent279"
        path = tmp_path / (role + ".json")
        write(path, p)
        path.chmod(0o600)
    with pytest.raises(psycopg.OperationalError):
        with importer.target(
            tmp_path / "migrator.json",
            tmp_path / "collector.json",
            "devops_analytics_absent279",
            DB_ID,
        ):
            pass


@pytest.mark.parametrize(
    "status,attempts,terminal",
    [
        ("failed", ("failed",), "failed"),
        ("incomplete", ("failed", "passed"), "passed"),
        ("cancelled", ("cancelled",), "cancelled"),
        ("feedback-passed", ("passed",), "passed"),
    ],
)
def test_pg_original_populations(real, tmp_path, status, attempts, terminal):
    m, _, _ = fixture_input(
        tmp_path,
        status=status,
        attempts=attempts,
        terminal=terminal,
        saved_summary=False,
    )
    with target(real) as conn:
        p = selected(tmp_path)
        importer.apply(conn, p, str(uuid.uuid4()), WHEN)
        run = conn.execute(
            "SELECT outcome,acceptance FROM devops.ci_runs WHERE repository_id=%s",
            [m["repository"]["id"]],
        ).fetchone()
        assert run["outcome"] == status and run["acceptance"] is False
        actual = conn.execute(
            "SELECT outcome,attempt_number FROM devops.test_attempts WHERE repository_id=%s ORDER BY attempt_number",
            [m["repository"]["id"]],
        ).fetchall()
        assert [v["outcome"] for v in actual] == [
            a for a in attempts if a != "cancelled"
        ]
        assert [v["attempt_number"] for v in actual] == list(range(len(actual)))
        importer.apply(conn, p, str(uuid.uuid4()), WHEN)
        assert conn.execute(
            "SELECT count(*) AS n FROM devops.test_attempts WHERE repository_id=%s",
            [m["repository"]["id"]],
        ).fetchone()["n"] == len(actual)


@pytest.mark.parametrize("invalid", [float("inf"), -1, 10**15, 0.0001])
def test_no_numeric_rounding_or_overflow(tmp_path, invalid):
    _, r, _ = fixture_input(tmp_path)
    r["duration_ms"] = invalid
    write(tmp_path / "run/receipt.json", r)
    with pytest.raises(storage.Refusal):
        selected(tmp_path)
