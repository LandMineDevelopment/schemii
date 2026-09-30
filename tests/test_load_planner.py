"""Faithful load fixture/header checks belong after Python dependency setup."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from schemii.schemoo.models import ExploreState, ModelDefinition
from schemii.schemoo.service import plan_query
from testing.load.compiled_columns import compiled_columns


ROOT = Path(__file__).resolve().parents[1]
SPEC = json.loads((ROOT / "testing/load/fixture-spec.json").read_text())
CATALOG = {
    "namespace": "fixture",
    "fingerprint": "fixture",
    "tables": [{"name": "orders", "columns": [{"name": "id", "dataType": "bigint"}]}],
    "relationships": [],
}


@pytest.mark.parametrize("label", ["Orders", "Order details", 'Orders "quoted"'])
def test_actual_fixture_planner_outputs_feed_exact_header_parser(label: str) -> None:
    definition = deepcopy(SPEC["definition"])
    definition["nodes"][0]["label"] = label
    plan = plan_query(
        CATALOG,
        ModelDefinition.model_validate(definition),
        ExploreState.model_validate(SPEC["explore"]),
        _bounded=False,
    )
    assert compiled_columns(plan["sql"]) == [f"{label}.id"]
    assert compiled_columns(plan["sql"]) != ["id"]


def test_production_parser_cli_consumes_real_plan_and_returns_default_header() -> None:
    plan = plan_query(
        CATALOG,
        ModelDefinition.model_validate(SPEC["definition"]),
        ExploreState.model_validate(SPEC["explore"]),
        _bounded=False,
    )
    result = subprocess.run(
        [sys.executable, str(ROOT / "testing/load/compiled_columns.py")],
        input=json.dumps(plan["sql"]),
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    assert json.loads(result.stdout) == ["Orders.id"]
    assert result.stderr == ""


@pytest.mark.parametrize(
    "sql",
    [
        'SELECT 1 AS "id"; SELECT 2 AS "id";',
        "DELETE FROM orders;",
        'SELECT 1 AS "duplicate", 2 AS "duplicate";',
    ],
)
def test_invalid_or_ambiguous_projection_cannot_become_a_fixture_header(
    sql: str,
) -> None:
    with pytest.raises(ValueError):
        compiled_columns(sql)


def test_parser_failure_never_echoes_private_sql() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "testing/load/compiled_columns.py")],
        input=json.dumps("DELETE FROM private_source;"),
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "invalid_compiled_plan\n"
    assert "private_source" not in result.stderr
