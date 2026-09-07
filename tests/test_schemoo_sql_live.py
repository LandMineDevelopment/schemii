"""Opt-in, read-only PostgreSQL execution of synthetic semantic fixtures.

Run after ./start.sh: SCHEMOO_LIVE_TESTS=1 .venv/bin/python -m pytest
tests/test_schemoo_sql_live.py. Uses existing organization workspace credentials;
all rows are typed VALUES CTEs, never warehouse records or persistent objects.
"""

import json
import os
import ssl
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

import pytest

from schemii.schemoo.prototype import compile_preview


pytestmark = pytest.mark.skipif(os.getenv("SCHEMOO_LIVE_TESTS") != "1", reason="Opt in with SCHEMOO_LIVE_TESTS=1 against the launcher-managed HTTPS stack.")
ORIGIN = "https://localhost:8001"


def api(path, body=None):
    request = Request(ORIGIN + path, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json", "Origin": ORIGIN})
    try:
        with urlopen(request, context=ssl._create_unverified_context(), timeout=20) as response:
            return json.load(response)
    except HTTPError as error:
        raise AssertionError(f"HTTP {error.code}: {error.read().decode()}") from error


@pytest.fixture(scope="module")
def execute():
    workspaces = api("/api/v1/schemii/workspaces")["workspaces"]
    workspace = next((item for item in workspaces if item.get("database") == "organization" and item.get("namespace") == "public"), None)
    assert workspace, "The existing organization.public workspace must be available."
    settings = api("/api/v1/schemii/console/settings")
    path = f"/api/v1/schemii/workspaces/{workspace['id']}/console/executions"

    def run(sql):
        created = api(path, {"consoleId": "con_" + uuid4().hex, "expectedWorkspaceRevision": workspace["revision"], "expectedSettingsRevision": settings["revision"], "mode": "managed_read", "statements": [sql]})
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            receipt = api(f"{path}/{created['id']}")
            if receipt["status"] in ("succeeded", "failed", "cancelled", "expired"):
                break
            time.sleep(0.1)
        assert receipt["status"] == "succeeded", receipt
        assert len(receipt["results"]) == 1
        page = api(f"{path}/{created['id']}/results/{receipt['results'][0]['id']}")
        assert page["nextCursor"] is None, "Synthetic fixture must fit a single page."
        return page["rows"]

    return run


CATALOG = {
    "tables": [{"name": name, "columns": [{"name": field} for field in columns]} for name, columns in [
        ("people", ["id", "name"]),
        ("assignment", ["person_id", "org_id", "start", "end"]),
        ("org", ["id", "name"]),
        ("cert", ["person_id", "name"]),
    ]],
    "relationships": [
        {"id": "person_fk", "sourceTable": "assignment", "sourceColumn": "person_id", "targetTable": "people", "targetColumn": "id"},
        {"id": "org_fk", "sourceTable": "assignment", "sourceColumn": "org_id", "targetTable": "org", "targetColumn": "id"},
        {"id": "cert_fk", "sourceTable": "cert", "sourceColumn": "person_id", "targetTable": "people", "targetColumn": "id"},
    ],
}
FIXTURES = """WITH
people(id, name) AS (VALUES (1, 'Ada'::text), (2, 'Bea'), (3, 'Cy')),
assignment(person_id, org_id, start, "end") AS (
    VALUES (1, 10, DATE '2020-01-01', NULL::date),
           (1, 20, DATE '2020-01-01', NULL::date),
           (2, 20, DATE '2010-01-01', DATE '2015-01-01')
),
org(id, name) AS (VALUES (10, 'A'::text), (20, 'B')),
cert(person_id, name) AS (VALUES (1, 'A'::text), (1, 'B'), (2, 'B'))
"""


def compiled(**changes):
    request = {"root": "people", "relationships": ["person_fk", "org_fk", "cert_fk"], "fields": [{"table": "people", "column": "name"}], **changes}
    sql = compile_preview(CATALOG, request)["sql"]
    for table in ("people", "assignment", "org", "cert"):
        sql = sql.replace(f'"public"."{table}"', f'"{table}"')
    return FIXTURES + sql


def required_org():
    return {"id": "scope", "kind": "required", "alternatives": [{"id": "a", "conditions": [{"table": "org", "column": "id", "operator": "eq", "value": 10}]}]}


def test_required_scope_person_rows_do_not_fan_out(execute):
    assert execute(compiled(scopes=[required_org()])) == [["Ada"]]


def test_required_scope_limits_returned_assignment_not_just_person(execute):
    fields = [{"table": "people", "column": "name"}, {"table": "assignment", "column": "org_id"}]
    assert execute(compiled(fields=fields, scopes=[required_org()])) == [["Ada", 10]]


def test_conditional_period_preserves_people_without_current_assignment(execute):
    temporal = {"id": "time", "kind": "conditional", "alternatives": [{"id": "current", "inputs": [{"id": "asof", "type": "date", "defaultValue": "2026-09-05"}], "conditions": [
        {"table": "assignment", "column": "start", "operator": "lte", "parameterId": "asof"},
        {"table": "assignment", "column": "end", "operator": "gte", "parameterId": "asof", "allowNull": True},
    ]}]}
    fields = [{"table": "people", "column": "name"}, {"table": "assignment", "column": "org_id"}]
    rows = execute(compiled(fields=fields, scopes=[temporal]))
    assert sorted(rows, key=lambda row: (row[0], row[1] or 0)) == [["Ada", 10], ["Ada", 20], ["Bea", None], ["Cy", None]]


def test_existence_filter_retains_all_returned_certifications(execute):
    fields = [{"table": "people", "column": "name"}, {"table": "cert", "column": "name"}]
    condition = {"table": "cert", "column": "name", "operator": "eq", "value": "A"}
    rows = execute(compiled(fields=fields, reportFilters=[{"mode": "exists", "conditions": [condition]}]))
    assert sorted(rows) == [["Ada", "A"], ["Ada", "B"]]
    rows = execute(compiled(fields=fields, reportFilters=[{"mode": "rows", "conditions": [condition]}]))
    assert rows == [["Ada", "A"]]


def test_not_exists_returns_people_without_matching_certification(execute):
    condition = {"table": "cert", "column": "name", "operator": "eq", "value": "A"}
    assert sorted(execute(compiled(reportFilters=[{"mode": "not_exists", "conditions": [condition]}]))) == [["Bea"], ["Cy"]]
