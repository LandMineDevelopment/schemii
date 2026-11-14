"""Synthetic protocol/ownership controls; no deployment or device execution."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import urllib.request
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "runtime_api_smoke", ROOT / "scripts/smoke-runtime-api.py"
)
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def fixture():
    return {
        "format": "schemii-runtime-api-smoke-v1",
        "disposable": True,
        "origin": "https://fixture.invalid",
        "credentials": {"username": "owned-smoke", "password": "synthetic-secret"},
        "user_id": "user_" + "1" * 32,
        "connection_id": "pg_" + "2" * 32,
        "workspace_id": "ws_" + "3" * 32,
        "model_id": "model_" + "4" * 32,
        "dashboard_id": "dashboard_" + "5" * 32,
        "database": "disposable",
        "namespace": "owned",
    }


class FakeClient:
    login = smoke.qa.Client.login
    logout = smoke.qa.Client.logout

    def __init__(self, value):
        self.fixture = value
        self.calls = []
        self.fail = None
        self.logout_failure = False
        self.signed_in = False
        identity = {
            "user": {
                "id": value["user_id"],
                "username": value["credentials"]["username"],
            },
            "capabilities": sorted(smoke.CAPABILITIES),
        }
        root = "/api/v1/schemii/workspaces/" + value["workspace_id"]
        self.responses: dict[str, Any] = {
            "/api/v1/readiness": {
                "ready": True,
                "persistence": "durable",
                "metadata": "postgresql",
            },
            "/api/v1/auth/status": {"enabled": True, "setup_required": False},
            "/api/v1/auth/login": identity,
            "/api/v1/auth/me": identity,
            "/api/v1/session": {
                "userId": value["user_id"],
                "authenticationSource": "session",
                "ephemeral": False,
            },
            "/api/v1/connections": {
                "connections": [
                    {
                        "id": value["connection_id"],
                        "ownerId": value["user_id"],
                        "ownership": "user",
                        "database": value["database"],
                    }
                ]
            },
            f"/api/v1/connections/{value['connection_id']}/test": {
                "ok": True,
                "database": value["database"],
            },
            root: {
                "id": value["workspace_id"],
                "connectionId": value["connection_id"],
                "database": value["database"],
                "namespace": value["namespace"],
            },
            root + "/relations?pageSize=250": {
                "workspaceId": value["workspace_id"],
                "relations": [
                    {"name": "orders", "kind": "table", "ref": "rel_" + "a" * 16}
                ],
            },
            root + "/relations/rel_" + "a" * 16 + "/rows?pageSize=5": {
                "workspaceId": value["workspace_id"],
                "relationRef": "rel_" + "a" * 16,
                "rows": [[1]],
            },
            "/api/v1/schemoo/catalog?connection_id="
            + value["connection_id"]
            + "&namespace="
            + value["namespace"]: {
                "database": value["database"],
                "namespace": value["namespace"],
                "tables": [{"name": "orders"}],
            },
            f"/api/v1/schemoo/models/{value['model_id']}": {
                "id": value["model_id"],
                "ownerId": value["user_id"],
                "connectionId": value["connection_id"],
                "database": value["database"],
                "namespace": value["namespace"],
                "revision": 1,
            },
            f"/api/v1/schemoo/models/{value['model_id']}/plan": {
                "sql": "SELECT id FROM orders LIMIT 100"
            },
            f"/api/v1/schemer/dashboards/{value['dashboard_id']}": {
                "id": value["dashboard_id"],
                "ownerId": value["user_id"],
                "modelId": value["model_id"],
                "tiles": [{"id": "orders"}],
            },
            "/api/v1/schemer/query": {"columns": [{"name": "id"}], "rows": [[1]]},
        }

    def call(self, method, path, payload=None, expected=200):
        self.calls.append((method, path, copy.deepcopy(payload), expected))
        if path == "/api/v1/auth/logout":
            if self.logout_failure:
                raise RuntimeError("synthetic-secret")
            self.signed_in = False
            return {}
        if path == "/api/v1/auth/login":
            self.signed_in = True
        if path == "/api/v1/auth/me" and expected == 401:
            assert not self.signed_in
            return None
        if path == self.fail:
            raise RuntimeError("synthetic-failure")
        return self.responses[path]


def test_common_entrypoint_exercises_real_shapes_and_shared_fixture_without_domain_mutations():
    value = fixture()
    client = FakeClient(value)
    result = smoke.smoke(client, value)
    assert result["passed"] and result["session_cleanup"] == "verified"
    assert result["device_lifecycle_acceptance"] == "pending"
    assert [call[1] for call in client.calls][-2:] == [
        "/api/v1/auth/logout",
        "/api/v1/auth/me",
    ]
    writes = [call for call in client.calls if call[0] != "GET"]
    assert all(call[0] == "POST" for call in writes)
    assert [call[1] for call in writes] == [
        "/api/v1/auth/login",
        f"/api/v1/connections/{value['connection_id']}/test",
        f"/api/v1/schemoo/models/{value['model_id']}/plan",
        "/api/v1/schemer/query",
        "/api/v1/auth/logout",
    ]
    assert (
        writes[-2][2]["explore"]
        == json.loads((ROOT / "testing/load/fixture-spec.json").read_text())["explore"]
    )


@pytest.mark.parametrize(
    "target,field,replacement",
    [
        ("/api/v1/auth/login", "user", {"id": "another-owner"}),
        ("/api/v1/session", "ephemeral", True),
        ("/api/v1/connections", "connections", []),
        ("/api/v1/schemer/query", "rows", []),
        ("/api/v1/schemer/query", "rows", [[1]] * 101),
        ("model", "ownerId", "another-owner"),
        ("model", "connectionId", "pg_" + "f" * 32),
        ("dashboard", "modelId", "model_" + "f" * 32),
    ],
)
def test_failed_identity_source_or_query_still_logs_out(target, field, replacement):
    value = fixture()
    client = FakeClient(value)
    target = {
        "model": f"/api/v1/schemoo/models/{value['model_id']}",
        "dashboard": f"/api/v1/schemer/dashboards/{value['dashboard_id']}",
    }.get(target, target)
    client.responses[target][field] = replacement
    with pytest.raises(smoke.SmokeError):
        smoke.smoke(client, value)
    assert client.calls[-2][1] == "/api/v1/auth/logout"
    assert client.calls[-1][3] == 401
    assert not client.signed_in


def test_transport_failure_retains_failure_and_revokes_session():
    value = fixture()
    client = FakeClient(value)
    client.fail = "/api/v1/schemer/query"
    with pytest.raises(RuntimeError, match="synthetic-failure"):
        smoke.smoke(client, value)
    assert not client.signed_in


def test_cleanup_failure_is_a_failure_even_if_all_reads_pass():
    value = fixture()
    client = FakeClient(value)
    client.logout_failure = True
    with pytest.raises(smoke.SmokeError, match="session cleanup failed") as error:
        smoke.smoke(client, value)
    assert "synthetic-secret" not in str(error.value)


@pytest.mark.parametrize(
    "origin",
    [
        "http://fixture.invalid",
        "https://user:secret@fixture.invalid",
        "https://fixture.invalid/path",
        "https://fixture.invalid?query",
        "https://fixture.invalid#fragment",
        "https://fixture.invalid\\bad",
        "https://fixture.invalid:bad",
        "https://fixture.invalid\n",
        "https://",
    ],
)
def test_origin_rejects_insecure_ambiguous_or_credential_destinations(origin):
    with pytest.raises(smoke.SmokeError):
        smoke.https_origin(origin)


def test_transport_reuses_existing_client_with_ca_cookie_isolation_and_no_redirect(
    monkeypatch,
):
    contexts = []

    def context(*, cafile):
        contexts.append(cafile)
        return object()

    monkeypatch.setattr(smoke.ssl, "create_default_context", context)
    handlers = []
    monkeypatch.setattr(urllib.request, "HTTPSHandler", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        urllib.request, "build_opener", lambda *args: handlers.append(args)
    )
    first = smoke.Client("https://phone-fixture.invalid", ca_file="owned-ca.crt")
    second = smoke.Client("https://desktop-fixture.invalid")
    assert isinstance(first, smoke.qa.Client) and isinstance(second, smoke.qa.Client)
    assert contexts == ["owned-ca.crt", None]
    assert handlers[0][0].cookiejar is not handlers[1][0].cookiejar
    assert isinstance(handlers[0][2], smoke.demo.NoRedirect)
    with pytest.raises(smoke.demo.DemoError):
        handlers[0][2].redirect_request(
            None, None, 302, "redirect", {}, "https://other.invalid"
        )


def test_client_sends_same_origin_without_leaking_errors():
    client = smoke.Client("https://fixture.invalid")
    requests = []

    class Opener:
        def open(self, request, timeout):
            requests.append((request, timeout))
            raise RuntimeError("private-response-and-password")

    client.opener = Opener()
    with pytest.raises(smoke.SmokeError) as error:
        client.login({"username": "owned", "password": "private-password"})
    request, timeout = requests[0]
    assert request.full_url == "https://fixture.invalid/api/v1/auth/login"
    assert request.headers["Origin"] == "https://fixture.invalid" and timeout == 30
    assert "private" not in str(error.value)


def test_private_fixture_bound_to_origin_and_disposable_consent(tmp_path):
    value = fixture()
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    assert smoke.fixture_document(path, value["origin"]) == value
    for key, replacement in (
        ("disposable", False),
        ("origin", "https://another.invalid"),
        ("connection_id", "../../private"),
        ("credentials", {"username": "no-password"}),
    ):
        changed = {**value, key: replacement}
        path.write_text(json.dumps(changed))
        with pytest.raises(smoke.SmokeError):
            smoke.fixture_document(path, value["origin"])
    path.write_text(json.dumps(value))
    path.chmod(0o644)
    with pytest.raises(smoke.demo.DemoError):
        smoke.fixture_document(path, value["origin"])


def test_cli_missing_disposable_consent_stops_before_transport(tmp_path):
    with pytest.raises(SystemExit) as error:
        smoke.main(
            [
                "--origin",
                "https://fixture.invalid",
                "--fixture",
                str(tmp_path / "absent"),
            ]
        )
    assert error.value.code == 2


def test_cli_sanitizes_unexpected_errors_and_reports_fixed_prerequisite_failures(
    monkeypatch, capsys, tmp_path
):
    args = [
        "--origin",
        "https://fixture.invalid",
        "--fixture",
        str(tmp_path / "fixture"),
        "--disposable",
    ]

    def private_failure(*args):
        raise ValueError("secret-from-private-fixture")

    monkeypatch.setattr(smoke, "fixture_document", private_failure)
    assert smoke.main(args) == 1
    assert "secret-from-private-fixture" not in capsys.readouterr().err
    args[1] = "http://fixture.invalid"
    assert smoke.main(args) == 1
    assert "exact HTTPS origin" in capsys.readouterr().err
