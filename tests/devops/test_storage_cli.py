"""Cheap input/privacy controls, with no database connection."""

import json
import os
from pathlib import Path
import subprocess
import sys

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
