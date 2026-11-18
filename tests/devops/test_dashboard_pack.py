"""Focused templates and opt-in authenticated ASGI integration on disposable PG."""

from collections import Counter, defaultdict
from decimal import Decimal
import importlib
import json
import os
from pathlib import Path
import secrets
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from scripts.devops import analysis, dashboard_pack as pack
from schemii.common.api.runtime import RuntimeConfig

REPOSITORY = UUID("172659a3-02e0-482b-af6a-9a15b1f09083")
INSTALLATION = UUID("94932396-f2a8-44fa-8d27-07aef3b9edd3")
CONNECTION = "pg_" + "a" * 32


def scope(repository=REPOSITORY, installation=INSTALLATION):
    return pack.Scope(
        repository,
        installation,
        analysis.instant("2026-11-12T00:00:00Z"),
        analysis.instant("2026-11-18T00:00:00Z"),
    )


def test_scope_is_fixed_and_grains_and_evidence_are_separate():
    target = scope()
    model = pack.model_template(target, CONNECTION, "devops_analytics_example")
    rules = model.definition
    assert rules.root == "slots" and rules.edges == []
    assert {node.id: node.table for node in rules.nodes} == {
        "slots": "v1_ci_verification",
        "runs": "v1_ci_run_coverage",
    }
    assert rules.exposedFields is not None
    assert not {field.column for field in rules.exposedFields} & {
        "repository_id",
        "installation_id",
        "id",
        "run_id",
        "evidence_ref",
        "evidence_sha256",
        "import_batch_id",
        "first_failure_fraction",
    }
    (window,) = rules.scopes
    assert (window.kind, window.requirement, window.rowBehavior) == (
        "conditional",
        "required",
        "require_matching",
    )
    (fixed,) = window.alternatives
    assert fixed.inputs == []
    assert [(c.operator, c.value) for c in fixed.conditions if c.table == "slots"] == [
        ("eq", str(REPOSITORY)),
        ("eq", str(INSTALLATION)),
        ("gte", target.bounds[0]),
        ("lt", target.bounds[1]),
    ]
    corrected = scope(repository=UUID(int=2))
    assert pack.model_template(corrected, CONNECTION, model.database).name != model.name
    assert pack.run_overview().root == "runs"
    dashboard = pack.dashboard_template(
        dict(name=model.name, id="model_" + "a" * 32, revision=3)
    )
    assert dashboard.model_revision == 3 and dashboard.selections == {}
    assert all(
        field.aggregate not in {"avg", "count_distinct"}
        for tile in dashboard.tiles
        for field in tile.measures
    )


def test_refuses_empty_or_unqualified_window():
    good = scope()
    with pytest.raises(ValueError, match="increasing"):
        pack.Scope(REPOSITORY, INSTALLATION, good.end, good.start)
    with pytest.raises(ValueError, match="offset"):
        pack.Scope(REPOSITORY, INSTALLATION, good.start.replace(tzinfo=None), good.end)


def fixture_private(path):
    assert path.resolve() == path and os.stat(path).st_uid == os.getuid()
    assert os.stat(path).st_mode & 0o077 == 0


@pytest.fixture
def integration_target(monkeypatch):
    marker = os.environ.get("SCHEMII_DEVOPS280_DASHBOARD_TARGET")
    if not marker:
        pytest.skip("explicit owned disposable dashboard target is required")
    assert not any(key.startswith("PG") for key in os.environ)
    path = Path(marker)
    top = path.parent
    assert path.is_absolute() and top.parent == Path("/tmp")
    assert top.name.startswith("sc280-pack-")
    fixture_private(top)
    fixture_private(path)
    target = json.loads(path.read_text())
    assert target["owner"] == "devops280-dashboard-v1"
    sockets = [Path(target[key]["host"]) for key in ("source", "metadata")]
    assert sockets == [top / "source-socket", top / "metadata-socket"]
    for key, socket in zip(("source", "metadata"), sockets, strict=True):
        fixture_private(socket)
        data = top / (key + "-data")
        fixture_private(data)
        lines = (data / "postmaster.pid").read_text().splitlines()
        assert lines[1] == str(data) and lines[4:6] == [str(socket), ""]
        assert int(lines[3]) == target[key]["port"]
        proc = Path(f"/proc/{lines[0]}")
        assert proc.stat().st_uid == os.getuid()
        assert proc.joinpath("cmdline").read_bytes().split(b"\0")[1:3] == [
            b"-D",
            os.fsencode(data),
        ]
        assert (
            proc.joinpath("stat").read_text().rsplit(")", 1)[1].split()[19]
            == target[key]["birth"]
        )
        fixture_private(top / (key + ".password"))
    fixture_private(top / "metadata.key")
    source = target["source"]
    metadata = target["metadata"]
    # Import the app under the ordinary test runtime before selecting the owned databases.
    application = importlib.import_module("schemii.main")
    setup_token = secrets.token_hex(24)
    for key, value in {
        "SCHEMII_STORAGE_MODE": "postgresql",
        "SCHEMII_METADATA_DSN": (
            f"host={metadata['host']} port={metadata['port']} dbname={metadata['database']} user={metadata['username']}"
        ),
        "SCHEMII_METADATA_PASSWORD_FILE": str(top / "metadata.password"),
        "SCHEMII_METADATA_ENCRYPTION_KEY_FILE": str(top / "metadata.key"),
        "SCHEMII_DEPLOYMENT_MODE": "authenticated",
        "SCHEMII_AUTH_ENABLED": "1",
        "SCHEMII_SETUP_TOKEN": setup_token,
        "SCHEMII_ALLOWED_TARGET_HOSTS": source["host"],
        "SCHEMII_TARGET_EGRESS_MODE": "internal-only",
        "SCHEMII_DEVELOPER_INSPECTION": "0",
    }.items():
        monkeypatch.setenv(key, value)

    app = application.create_app(runtime_config=RuntimeConfig.from_env())
    origin = "https://localhost:8001"
    with TestClient(app, base_url=origin, headers={"Origin": origin}) as client:
        assert client.get(pack.MODEL_API).status_code == 401
        response = client.post(
            "/api/v1/auth/setup",
            json={
                "username": "disposable-ci-pack",
                "display_name": "CI pack integration",
                "password": secrets.token_hex(20),
                "setup_token": setup_token,
            },
        )
        assert response.status_code == 200
        assert app.state.services.metadata.durable
        yield client, target, SecretStr((top / "source.password").read_text().strip())
        assert client.post("/api/v1/auth/logout").status_code == 200


def json_ok(response):
    response.raise_for_status()
    return response.json()


def canonical(target, password, selected):
    source = target["source"]
    with psycopg.connect(
        host=source["host"],
        port=source["port"],
        dbname=source["database"],
        user=source["username"],
        password=password,
        sslmode="disable",
        row_factory=dict_row,
        options="-c default_transaction_read_only=on",
    ) as reader:
        assert reader.execute(
            "SELECT inet_server_addr() AS addr, current_user AS actor"
        ).fetchone() == {
            "addr": None,
            "actor": source["username"],
        }
        with pytest.raises(psycopg.errors.InsufficientPrivilege), reader.transaction():
            reader.execute("SELECT * FROM devops.ci_runs")
        rows = []
        for recipe in ("verification", "run-coverage", "slow-commands"):
            stmt = analysis.recipe_sql(
                recipe,
                selected.repository,
                selected.installation,
                selected.start,
                selected.end,
                None,
            )
            rows.append(reader.execute(stmt).fetchall())
        return rows


def test_saved_scoped_model_and_dashboard_match_canonical_views(
    integration_target, record_property
):
    client, target, credential = integration_target
    password = credential.get_secret_value()
    source = target["source"]
    selected = scope(UUID(target["repository"]), UUID(target["installation"]))
    slots, runs, elapsed = canonical(target, password, selected)
    assert len(slots) == 22 and len(runs) == 1
    profile = json_ok(
        client.post(
            "/api/v1/connections",
            json={
                "name": "Disposable CI reader",
                "host": source["host"],
                "port": source["port"],
                "database": source["database"],
                "username": source["username"],
                "password": password,
                "sslMode": "disable",
            },
        )
    )
    assert "password" not in profile
    args = dict(
        connection_id=profile["id"],
        database=profile["database"],
        connection_revision=profile["revision"],
    )
    with pytest.raises(ValueError, match="catalog"):
        pack.install_pack(client, selected, **(args | {"connection_revision": 2}))

    class LostAcknowledgement:
        get = staticmethod(client.get)

        @staticmethod
        def post(url, *, json):
            response = client.post(url, json=json)
            response.raise_for_status()
            if url == pack.DASHBOARD_API:
                raise TimeoutError("disposable lost acknowledgement after save")
            return response

    with pytest.raises(TimeoutError, match="lost acknowledgement"):
        pack.install_pack(LostAcknowledgement(), selected, **args)
    model, dashboard = pack.install_pack(client, selected, **args)
    assert pack.install_pack(client, selected, **args) == (model, dashboard)
    assert len(json_ok(client.get(pack.MODEL_API))["models"]) == 1
    assert len(json_ok(client.get(pack.DASHBOARD_API))["dashboards"]) == 1

    run_preview = pack.run_overview().model_dump(mode="json", by_alias=True)
    plan_api = f"{pack.MODEL_API}/{model['id']}"
    body = {"expectedRevision": model["revision"], "explore": run_preview}
    rebound = client.post(
        f"{plan_api}/plan",
        json=body
        | {
            "explore": run_preview
            | {
                "selections": {
                    "ci-window": {
                        "alternativeId": "fixed",
                        "values": {"repository": str(UUID(int=2))},
                    }
                }
            }
        },
    )
    planned = json_ok(client.post(f"{plan_api}/plan", json=body))
    assert json_ok(rebound)["sql"] == planned["sql"]
    assert str(UUID(int=2)) not in planned["sql"]
    hidden = client.post(
        f"{plan_api}/plan",
        json=body
        | {
            "explore": run_preview
            | {
                "reportFilters": [
                    {
                        "mode": "rows",
                        "conditions": [
                            {
                                "table": "runs",
                                "column": "evidence_ref",
                                "operator": "not_null",
                            }
                        ],
                    }
                ]
            }
        },
    )
    assert (
        hidden.status_code == 422
        and hidden.json()["error"]["code"] == "field_not_exposed"
    )
    started = json_ok(
        client.post(
            f"{plan_api}/executions",
            json=body | {"consoleId": "con_" + secrets.token_hex(16)},
        )
    )
    read_url = started["executionUrl"]
    execution = json_ok(client.get(read_url))
    assert execution["status"] == "succeeded"
    (result,) = execution["results"]
    page = json_ok(client.get(f"{read_url}/results/{result['id']}"))
    assert not page["truncated"] and page["nextCursor"] is None
    (run,) = runs
    (row,) = page["rows"]
    assert row[:4] == [run["outcome"], run["completeness"], run["acceptance"], 1]
    assert list(map(int, row[4:])) == [run["observed_slots"], run["pending_slots"]]

    stream_url = f"{pack.DASHBOARD_API}/{dashboard['id']}/executions/stream"
    stale = client.post(
        stream_url, json={"expectedRevision": dashboard["revision"] + 1}
    )
    assert stale.status_code == 409
    with client.stream(
        "POST", stream_url, json={"expectedRevision": dashboard["revision"]}
    ) as stream:
        stream.raise_for_status()
        events = [json.loads(line) for line in stream.iter_lines()]
    assert events[0]["type"] == "start" and events[0]["tileErrors"] == []
    assert events[-1]["type"] == "end" and not any(e["type"] == "error" for e in events)
    completions = {e["tileId"]: e for e in events if e["type"] == "complete"}
    assert set(completions) == {
        "verification",
        "case-coverage",
        "first-attempts",
        "command-duration",
    }
    assert not any(e["limitReached"] for e in completions.values())
    actual = defaultdict(list)
    for event in events:
        if event["type"] == "rows":
            actual[event["tileId"]].extend(event["rows"])
    verification = Counter(
        (s["disposition"], s["verification_state"], s["run_outcome"]) for s in slots
    )
    assert sorted(actual["verification"]) == sorted(
        [*group, n] for group, n in verification.items()
    )
    counts = Counter(s["case_coverage_state"] for s in slots)
    assert sorted(actual["case-coverage"]) == sorted([k, n] for k, n in counts.items())
    (first,) = actual["first-attempts"]
    assert list(map(int, first)) == [
        sum(s[col] for s in slots) for col in pack.ATTEMPT_TOTALS
    ]
    # Compare the sample count and exact maximum. Each natural command has one measured group.
    assert sorted(
        (key, outcome, sample, Decimal(ms))
        for key, outcome, sample, ms in actual["command-duration"]
    ) == sorted(
        (row["command_key"], row["outcome"], row["duration_samples"], row["max_ms"])
        for row in elapsed
        if row["duration_samples"]
    )
    for label, value in (("model", model), ("dashboard", dashboard)):
        record_property(f"{label}_id", value["id"])
        record_property(f"{label}_revision", value["revision"])
    record_property(
        "matched_dashboard_tile_rows",
        json.dumps({k: len(v) for k, v in actual.items()}, sort_keys=True),
    )
