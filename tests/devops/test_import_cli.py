"""Offline/human/agent parity and real commit-reconciliation controls."""

import json
import subprocess
import sys
import uuid

import psycopg
import pytest

from scripts.devops import cli, importer
from tests.devops.test_importer import DB_ID, NAME, fixture_input

pytest_plugins = ["tests.devops.test_importer"]


def arguments(root, action="plan"):
    return [
        action,
        "--input-root",
        str(root),
        "--manifest",
        "manifest.json",
        "--database-name",
        NAME,
        "--database-id",
        DB_ID,
    ]


def test_offline_and_human_json_parity_no_writes(tmp_path, capsys, monkeypatch):
    fixture_input(tmp_path)
    monkeypatch.setattr(
        importer.psycopg, "connect", lambda **kw: pytest.fail("offline connected")
    )
    before = sorted(str(v.relative_to(tmp_path)) for v in tmp_path.rglob("*"))
    assert cli.main(arguments(tmp_path) + ["--json"]) == 0
    machine = json.loads(capsys.readouterr().out)
    assert cli.main(arguments(tmp_path)) == 0
    human = dict(line.split(": ", 1) for line in capsys.readouterr().out.splitlines())
    assert {k: json.loads(v) for k, v in human.items()} == machine
    for action in ("schema", "metrics"):
        assert cli.main([action, "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["reason"] == "offline"
    for action in ("dry-run", "explain", "export"):
        assert cli.main(arguments(tmp_path, action) + ["--json"]) == 0
        assert json.loads(capsys.readouterr().out)["plan_id"] == machine["plan_id"]
    assert before == sorted(str(v.relative_to(tmp_path)) for v in tmp_path.rglob("*"))


def test_help_and_arguments_are_private(capsys):
    assert cli.main(["import", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "explicit-target-required"
    for args in (["SECRET_SENTINEL", "--json"], ["--json"]):
        with pytest.raises(SystemExit):
            cli.main(args)
        assert "SECRET_SENTINEL" not in capsys.readouterr().out
    run = subprocess.run(
        [sys.executable, "scripts/devops/cli.py", "--help"],
        capture_output=True,
        text=True,
    )
    assert (
        run.returncode == 0
        and "resume" in run.stdout
        and "--collector-profile" in run.stdout
    )


def test_known_private_fields_never_persist_or_affect_public_digest(tmp_path, capsys):
    _, r, _ = fixture_input(tmp_path)
    before = importer.plan(tmp_path, "manifest.json", NAME, DB_ID)["plan_id"]
    r["reason"] = "SECRET_SENTINEL postgres://private /home/private/file"
    r["commands"][0]["executed_command"] = ["SECRET_SENTINEL"]
    r["classification"]["title"] = "SECRET_SENTINEL"
    (tmp_path / "run/receipt.json").write_text(json.dumps(r))
    assert cli.main(arguments(tmp_path, "export") + ["--json"]) == 0
    output = capsys.readouterr().out
    assert (
        "SECRET_SENTINEL" not in output
        and "postgres://" not in output
        and "/home/private" not in output
    )
    assert json.loads(output)["plan_id"] == before


def import_arguments(root, state, real, operation, plan_id, action="import"):
    return arguments(root, action) + [
        "--state-dir",
        str(state),
        "--operation-id",
        operation,
        "--expect-plan",
        plan_id,
        "--profile",
        str(real / "migrator.json"),
        "--collector-profile",
        str(real / "collector.json"),
        "--json",
    ]


@pytest.mark.parametrize("after_commit", [False, True])
def test_real_uncertain_commit_exact_resume(
    real, tmp_path, monkeypatch, capsys, after_commit
):
    source = tmp_path / "input"
    source.mkdir()
    fixture_input(source)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    plan_id = importer.plan(source, "manifest.json", NAME, DB_ID)["plan_id"]
    operation = str(uuid.uuid4())
    args = import_arguments(source, state, real, operation, plan_id)
    original = importer.apply

    def ambiguous(*args):
        if after_commit:
            original(*args)
        raise psycopg.OperationalError("SECRET_SENTINEL acknowledgement lost")

    monkeypatch.setattr(importer, "apply", ambiguous)
    assert cli.main(args) == 3
    failed = json.loads(capsys.readouterr().out)
    assert failed["commit_state"] == "unknown" and "SECRET_SENTINEL" not in json.dumps(
        failed
    )
    monkeypatch.setattr(importer, "apply", original)
    status_args = ["status", *args[1:]]
    assert cli.main(status_args) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["cleanup_pending"] is True
    assert (status["operation_batch"] is not None) is after_commit
    assert cli.main(["resume", *args[1:]]) == 0
    completed = json.loads(capsys.readouterr().out)
    assert completed["state"] == ("replayed" if after_commit else "committed")
    assert cli.main(status_args) == 0
    assert json.loads(capsys.readouterr().out)["cleanup_pending"] is False
    assert "SECRET_SENTINEL" not in (state / (operation + ".jsonl")).read_text()


def test_actual_cli_partial_status_stale_and_plan_refusal(real, tmp_path, capsys):
    source = tmp_path / "input"
    source.mkdir()
    fixture_input(source, status="failed", saved_summary=False)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    plan_id = importer.plan(source, "manifest.json", NAME, DB_ID)["plan_id"]
    args = import_arguments(source, state, real, str(uuid.uuid4()), plan_id)
    assert cli.main(args) == 0
    assert json.loads(capsys.readouterr().out)["gaps"]
    assert cli.main(["status", *args[1:]]) == 0
    status = json.loads(capsys.readouterr().out)
    assert (
        status["last_complete_success"] is None
        and status["last_published_batch"]["status"] == "partial"
    )
    receipt = source / "run/receipt.json"
    r = json.loads(receipt.read_text())
    r["duration_ms"] = 23
    receipt.write_text(json.dumps(r))
    assert cli.main(["status", *args[1:]]) == 0
    assert json.loads(capsys.readouterr().out)["stale"] is True
    assert cli.main(["resume", *args[1:]]) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "changed-plan"


def test_actual_transaction_interruption_before_commit(
    real, tmp_path, monkeypatch, capsys
):
    source = tmp_path / "input"
    source.mkdir()
    fixture_input(source)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    plan_id = importer.plan(source, "manifest.json", NAME, DB_ID)["plan_id"]
    args = import_arguments(source, state, real, str(uuid.uuid4()), plan_id)
    original = importer.Inputs.revalidate
    calls = []

    def interrupted(self):
        calls.append(True)
        if len(calls) == 2:
            raise KeyboardInterrupt()
        return original(self)

    monkeypatch.setattr(importer.Inputs, "revalidate", interrupted)
    assert cli.main(args) == 3
    assert json.loads(capsys.readouterr().out)["commit_state"] == "unknown"
    monkeypatch.setattr(importer.Inputs, "revalidate", original)
    assert cli.main(["status", *args[1:]]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["operation_batch"] is None and status["last_published_batch"] is None
    assert cli.main(["resume", *args[1:]]) == 0
    assert json.loads(capsys.readouterr().out)["accepted_count"] == 3


def test_torn_journal_tail_reconciles_without_erasure(real, tmp_path, capsys):
    source = tmp_path / "input"
    source.mkdir()
    fixture_input(source)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    plan_id = importer.plan(source, "manifest.json", NAME, DB_ID)["plan_id"]
    operation = str(uuid.uuid4())
    args = import_arguments(source, state, real, operation, plan_id)
    assert cli.main(args) == 0
    capsys.readouterr()
    journal = state / (operation + ".jsonl")
    with journal.open("ab") as stream:
        stream.write(b'{"kind":"result","sta')
    original = journal.read_bytes()
    assert cli.main(["status", *args[1:]]) == 0
    assert json.loads(capsys.readouterr().out)["journal_loss"] is True
    assert cli.main(["resume", *args[1:]]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "replayed"
    assert journal.read_bytes().startswith(original)
    assert cli.main(["status", *args[1:]]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["cleanup_pending"] is True and status["journal_loss"] is True


def test_journal_busy_private_and_offline_schema(tmp_path, capsys):
    operation = str(uuid.uuid4())
    tmp_path.chmod(0o700)
    with cli.journal(tmp_path, operation, {"plan_id": "a" * 64}, resume=False):
        with pytest.raises(importer.storage.Refusal) as error:
            with cli.journal(tmp_path, operation, {"plan_id": "a" * 64}, resume=True):
                pass
        assert error.value.reason == "busy-operation"
    assert cli.main(["schema", "--json"]) == 0
    columns = json.loads(capsys.readouterr().out)["columns"]
    assert columns["devops.ci_runs"]["outcome"].startswith("text CHECK")
    assert columns["devops.ci_runs"]["acceptance"] == "boolean"
