#!/usr/bin/env python3
"""Authenticated API smoke for an explicitly disposable Linux or Android fixture."""

from __future__ import annotations

import argparse
import http.cookiejar
import importlib.util
import json
from pathlib import Path
import re
import ssl
import sys
from typing import TYPE_CHECKING
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def _source_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Smoke source helper is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Reuse the existing request/login/logout and private-file/redirect boundaries.
# No demo provisioning, account setup or retained operational state is invoked.
qa = _source_module("runtime_smoke_qa", "testing/provision.py")
demo = _source_module("runtime_smoke_demo", "dev/demo/prepare.py")
if TYPE_CHECKING:
    from testing.provision import Client as QAClient
else:
    QAClient = qa.Client

EXPLORE = json.loads((ROOT / "testing/load/fixture-spec.json").read_text())["explore"]
CAPABILITIES = {"schemii:access", "schemoo:access", "schemer:access", "schemer:author"}


class SmokeError(RuntimeError):
    """A fixed diagnostic which never contains server content or fixture secrets."""


def require(condition, message):
    if not condition:
        raise SmokeError(message)


def https_origin(value):
    try:
        parsed = urllib.parse.urlsplit(value)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname
            and parsed.netloc
            and not parsed.username
            and not parsed.password
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
            and not re.search(r"[\s\\]", value)
        )
        _ = parsed.port
    except ValueError:
        valid = False
    require(valid, "Supply an exact HTTPS origin without a path or credentials")
    return value


def fixture_document(path, origin):
    value = json.loads(demo.read_private(path))
    require(isinstance(value, dict), "The smoke fixture must be an object")
    require(
        value.get("format") == "schemii-runtime-api-smoke-v1"
        and value.get("disposable") is True
        and value.get("origin") == origin,
        "A disposable smoke fixture bound to this exact origin is required",
    )
    for key, prefix in (
        ("connection_id", "pg"),
        ("workspace_id", "ws"),
        ("model_id", "model"),
        ("dashboard_id", "dashboard"),
    ):
        require(
            isinstance(value.get(key), str)
            and re.fullmatch(prefix + r"_[0-9a-f]{32}", value[key]),
            "The smoke fixture contains an invalid resource identity",
        )
    for key in ("user_id", "database", "namespace"):
        require(
            isinstance(value.get(key), str) and value[key],
            "The smoke fixture identity is incomplete",
        )
    credentials = value.get("credentials")
    require(
        isinstance(credentials, dict)
        and set(credentials) == {"username", "password"}
        and all(isinstance(item, str) and item for item in credentials.values()),
        "The smoke fixture needs explicit test-account credentials",
    )
    return value


class Client(QAClient):
    def __init__(self, origin, *, ca_file=None):
        self.base = https_origin(origin)
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            urllib.request.HTTPSHandler(
                context=ssl.create_default_context(cafile=ca_file)
            ),
            demo.NoRedirect(),
        )

    def call(self, method, path, payload=None, expected=200):
        # All paths below are fixed or constructed from validated resource IDs.
        try:
            return super().call(method, path, payload, expected)
        except Exception:
            raise SmokeError("An authenticated smoke HTTPS request failed") from None


def smoke(client, fixture):
    """Borrow pre-provisioned owned resources; only create/revoke this login session."""
    ready = client.call("GET", "/api/v1/readiness")
    require(
        ready.get("ready") is True
        and ready.get("persistence") == "durable"
        and ready.get("metadata") == "postgresql",
        "Durable PostgreSQL readiness failed",
    )
    status = client.call("GET", "/api/v1/auth/status")
    require(
        status.get("enabled") is True and status.get("setup_required") is False,
        "A pre-provisioned authenticated disposable fixture is required",
    )
    client.call("GET", "/api/v1/auth/me", expected=401)
    # Always attempt logout, including a login response whose identity is wrong.
    # The inherited cookie jar may already have accepted its session cookie.
    failure = None
    try:
        identity = client.login(fixture["credentials"])
        require(
            identity.get("user", {}).get("id") == fixture["user_id"]
            and identity.get("user", {}).get("username")
            == fixture["credentials"]["username"]
            and CAPABILITIES <= set(identity.get("capabilities", [])),
            "The smoke account identity or access changed",
        )
        current = client.call("GET", "/api/v1/auth/me")
        require(
            current.get("user", {}).get("id") == fixture["user_id"],
            "The authenticated session identity changed",
        )
        session = client.call("GET", "/api/v1/session")
        require(
            session.get("userId") == fixture["user_id"]
            and session.get("authenticationSource") == "session"
            and session.get("ephemeral") is False,
            "The authenticated durable session is unavailable",
        )
        connections = client.call("GET", "/api/v1/connections")["connections"]
        source: dict = next(
            (
                item
                for item in connections
                if item.get("id") == fixture["connection_id"]
            ),
            {},
        )
        require(
            source.get("ownerId") == fixture["user_id"]
            and source.get("ownership") == "user"
            and source.get("database") == fixture["database"],
            "The smoke target connection is not owned by this account",
        )
        tested = client.call(
            "POST", f"/api/v1/connections/{fixture['connection_id']}/test", {}
        )
        require(
            tested.get("ok") is True and tested.get("database") == fixture["database"],
            "Target PostgreSQL readiness failed",
        )
        root = f"/api/v1/schemii/workspaces/{fixture['workspace_id']}"
        workspace = client.call("GET", root)
        require(
            workspace.get("id") == fixture["workspace_id"]
            and workspace.get("connectionId") == fixture["connection_id"]
            and workspace.get("database") == fixture["database"]
            and workspace.get("namespace") == fixture["namespace"],
            "The smoke workspace target changed",
        )
        listing = client.call("GET", root + "/relations?pageSize=250")
        relation: dict = next(
            (
                item
                for item in listing.get("relations", [])
                if item.get("name") == "orders" and item.get("kind") == "table"
            ),
            {},
        )
        ref = relation.get("ref", "")
        require(
            listing.get("workspaceId") == fixture["workspace_id"]
            and isinstance(ref, str)
            and re.fullmatch(r"rel_[A-Za-z0-9_-]{16,256}", ref),
            "The smoke orders catalog is unavailable",
        )
        page = client.call("GET", root + "/relations/" + ref + "/rows?pageSize=5")
        require(
            page.get("workspaceId") == fixture["workspace_id"]
            and page.get("relationRef") == ref
            and bool(page.get("rows")),
            "The smoke orders fixture has no readable rows",
        )
        catalog = client.call(
            "GET",
            "/api/v1/schemoo/catalog?"
            + urllib.parse.urlencode(
                {
                    "connection_id": fixture["connection_id"],
                    "namespace": fixture["namespace"],
                }
            ),
        )
        require(
            catalog.get("database") == fixture["database"]
            and catalog.get("namespace") == fixture["namespace"]
            and any(item.get("name") == "orders" for item in catalog.get("tables", [])),
            "The Schemoo catalog target changed",
        )
        model = client.call("GET", f"/api/v1/schemoo/models/{fixture['model_id']}")
        require(
            model.get("id") == fixture["model_id"]
            and model.get("ownerId") == fixture["user_id"]
            and model.get("connectionId") == fixture["connection_id"]
            and model.get("namespace") == fixture["namespace"]
            and model.get("database") == fixture["database"]
            and isinstance(model.get("revision"), int),
            "The smoke model ownership or source changed",
        )
        body = {"expectedRevision": model["revision"], "explore": EXPLORE}
        plan = client.call(
            "POST", f"/api/v1/schemoo/models/{fixture['model_id']}/plan", body
        )
        require(
            isinstance(plan.get("sql"), str) and plan["sql"],
            "The Schemoo model plan is unavailable",
        )
        dashboard = client.call(
            "GET", f"/api/v1/schemer/dashboards/{fixture['dashboard_id']}"
        )
        require(
            dashboard.get("id") == fixture["dashboard_id"]
            and dashboard.get("ownerId") == fixture["user_id"]
            and dashboard.get("modelId") == fixture["model_id"]
            and bool(dashboard.get("tiles")),
            "The smoke dashboard ownership or model changed",
        )
        result = client.call(
            "POST", "/api/v1/schemer/query", {"modelId": fixture["model_id"], **body}
        )
        require(
            bool(result.get("columns"))
            and isinstance(result.get("rows"), list)
            and bool(result["rows"])
            and len(result["rows"]) <= EXPLORE["limit"],
            "The bounded Schemer query returned no valid fixture rows",
        )
    except Exception as error:
        failure = error
    finally:
        try:
            client.logout()
            client.call("GET", "/api/v1/auth/me", expected=401)
        except Exception:
            raise SmokeError(
                "Smoke session cleanup failed; inspect the private disposable fixture"
            ) from None
    if failure is not None:
        raise failure
    return {
        "passed": True,
        "scope": "authenticated-runtime-api",
        "families": ["auth", "common", "schemii", "schemoo", "schemer"],
        "session_cleanup": "verified",
        "device_lifecycle_acceptance": "pending",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument(
        "--fixture",
        type=Path,
        required=True,
        help="Private mode-600 disposable fixture manifest",
    )
    parser.add_argument(
        "--ca-file",
        type=Path,
        help="CA for an owned self-signed HTTPS fixture; otherwise system trust",
    )
    parser.add_argument(
        "--disposable",
        action="store_true",
        required=True,
        help="Confirm this deployment and account are test-owned/disposable",
    )
    args = parser.parse_args(argv)
    try:
        origin = https_origin(args.origin)
        fixture = fixture_document(args.fixture, origin)
        print(json.dumps(smoke(Client(origin, ca_file=args.ca_file), fixture)))
        return 0
    except SmokeError as error:
        print("Runtime API smoke: " + str(error), file=sys.stderr)
        return 1
    except Exception:
        print(
            "Runtime API smoke failed; check the disposable fixture prerequisites and private evidence",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
