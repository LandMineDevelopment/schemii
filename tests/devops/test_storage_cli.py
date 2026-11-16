"""Cheap input/privacy controls, with no database connection."""

import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import psycopg
import pytest

from scripts.devops import storage


def test_profile_requires_explicit_private_fields(tmp_path):
    path = tmp_path / "connection.json"
    path.write_text(
        json.dumps(
            {
                "host": "localhost",
                "port": 5432,
                "dbname": "schemii",
                "user": "someone",
                "passfile": "/does-not-exist",
            }
        )
    )
    path.chmod(0o600)
    with pytest.raises(storage.Refusal) as error:
        storage.connection_config(path)
    assert error.value.reason == "wrong-target"
    path.chmod(0o644)
    with pytest.raises(storage.Refusal) as error:
        storage.connection_config(path)
    assert error.value.reason == "unsafe-profile"
    alias = tmp_path / "alias.json"
    alias.symlink_to(path)
    with pytest.raises(storage.Refusal):
        storage.connection_config(alias)


def test_unsupported_profile_and_private_error_output(tmp_path, capsys):
    path = tmp_path / "connection.json"
    path.write_text('{"password":"SECRET_SENTINEL"}')
    path.chmod(0o600)
    code = storage.main(
        [
            "migrate",
            "--profile",
            str(path),
            "--database-name",
            "devops_analytics_test",
            "--database-id",
            "b33696dc-4a2a-4438-82bb-76bd1ec37231",
            "--json",
        ]
    )
    result = capsys.readouterr()
    assert code == 2 and "SECRET_SENTINEL" not in result.out + result.err
    assert json.loads(result.out)["reason"] == "invalid-profile"
    assert json.loads(result.out)["next_action"]


def test_documented_help_and_no_default_connection():
    script = Path(storage.__file__)
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if not k.startswith("PG")},
    )
    assert result.returncode == 0
    assert "--database-id" in result.stdout and "--profile" in result.stdout
    result = subprocess.run(
        [sys.executable, str(script), "migrate", "--json"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["reason"] == "invalid-arguments"
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("failure", [psycopg.OperationalError, OSError, ValueError])
def test_commit_acknowledgement_loss_requires_reconciliation(
    monkeypatch, capsys, failure
):
    completed = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            raise failure("SECRET_SENTINEL")

    def operated(*_args):
        completed.append(True)
        return {"pending_versions": []}

    monkeypatch.setenv("PGAPPNAME", "retained-test-context")
    monkeypatch.setattr(
        storage, "connection_config", lambda _path: {"dbname": "devops_analytics_test"}
    )
    monkeypatch.setattr(storage.psycopg, "connect", lambda **_kw: Connection())
    monkeypatch.setattr(storage, "operate", operated)
    assert (
        storage.main(
            [
                "migrate",
                "--profile",
                "unused-test-profile",
                "--database-name",
                "devops_analytics_test",
                "--database-id",
                "b33696dc-4a2a-4438-82bb-76bd1ec37231",
                "--json",
            ]
        )
        == 3
    )
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert completed == [True]
    assert result["reason"] == "database-error" and not result["ok"]
    assert result["commit_state"] == "unknown"
    assert "Run status" in result["next_action"]
    assert "same explicit database name and UUID" in result["next_action"]
    assert "rolled back" not in captured.out
    assert "SECRET_SENTINEL" not in captured.out + captured.err
    assert os.environ["PGAPPNAME"] == "retained-test-context"


def test_local_ci_run_states_have_lossless_documented_storage_mapping():
    root = Path(storage.__file__).resolve().parents[2]
    tree = ast.parse((root / "scripts/ci/local_ci.py").read_text())
    emitted = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "status":
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        emitted.add(value.value)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "receipt"
            and node.func.attr == "update"
        ):
            for item in node.keywords:
                if item.arg == "status":
                    values = (
                        [item.value.body, item.value.orelse]
                        if isinstance(item.value, ast.IfExp)
                        else [item.value]
                    )
                    emitted.update(
                        value.value
                        for value in values
                        if isinstance(value, ast.Constant)
                        and isinstance(value.value, str)
                    )
    sql = (storage.MIGRATIONS / "001_foundation.sql").read_text()
    run_table = sql.split("CREATE TABLE devops.ci_runs (", 1)[1].split(
        "CREATE UNIQUE INDEX ci_runs_source", 1
    )[0]
    vocabulary = re.search(r"outcome text CHECK \(outcome IN \((.*?)\)\)", run_table)
    assert vocabulary is not None
    assert emitted == set(re.findall(r"'([^']+)'", vocabulary.group(1)))
    docs = (root / "docs/devops-metrics.md").read_text()
    assert {"feedback-passed", "source-changed"} <= emitted
    for status in emitted:
        assert f"| `{status}` |" in docs
