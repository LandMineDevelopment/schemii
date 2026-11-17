"""Check held CI analytics through the Android preview's existing read APIs."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import stat
import time
from typing import Any, TypeGuard
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import (
    HTTPSHandler,
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)
from uuid import UUID


VIEWS = ("v1_ci_runs", "v1_command_runs", "v1_test_attempts", "v1_collection_health")
SERVICES = ("metadata", "target", "pi", "app", "ingress", "tailnet")
PAGE_ROWS = 250
MAX_VIEW_ROWS = 10_000
MAX_BYTES = 2 * 1024 * 1024
FRESH_SECONDS = 15 * 60
SHA256 = r"[0-9a-f]{64}"
Get = Callable[[str], dict[str, Any]]


class Refused(Exception):
    """A fixed diagnostic safe to print without private inputs."""


def require(condition: object, reason: str) -> None:
    if not condition:
        raise Refused(reason)


def encode(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest(value: Any) -> str:
    return sha(encode(value))


def matches(value: Any, pattern: str) -> bool:
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def positive_int(value: Any) -> TypeGuard[int]:
    return type(value) is int and value > 0


def object_with_keys(value: Any, keys: set[str], reason: str) -> dict[str, Any]:
    require(isinstance(value, dict) and value.keys() == keys, reason)
    return value


def private_directory(path: Path) -> None:
    info = path.lstat()
    require(
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.getuid()
        and stat.S_IMODE(info.st_mode) == 0o700,
        "private-directory-required",
    )


def private_bytes(path: Path, *, fresh: bool = False, limit: int = MAX_BYTES) -> bytes:
    private_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_size <= limit,
            "private-file-required",
        )
        if fresh:
            require(
                0 <= time.time() - info.st_mtime <= FRESH_SECONDS,
                "stale-stage-evidence",
            )
        raw = stream.read(limit + 1)
        require(len(raw) <= limit, "input-too-large")
    return raw


def decode(raw: bytes) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value = dict(pairs)
        require(len(value) == len(pairs), "duplicate-json-field")
        return value

    value = json.loads(raw, object_pairs_hook=unique)
    require(isinstance(value, dict), "invalid-json-object")
    return value


def validate_plan(plan: dict[str, Any]) -> None:
    reason = "invalid-plan"
    object_with_keys(
        plan,
        {
            "schema",
            "operation_id",
            "origin",
            "account_id",
            "connection_id",
            "workspace_id",
            "target",
            "before",
            "after",
            "counts",
        },
        reason,
    )
    require(type(plan["schema"]) is int and plan["schema"] == 1, reason)
    operation_id = plan["operation_id"]
    require(
        isinstance(operation_id, str) and str(UUID(operation_id)) == operation_id,
        reason,
    )
    # An HTTPS origin only. Reject userinfo, paths, and embedded credentials.
    require(
        matches(
            plan["origin"],
            r"https://[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[1-9][0-9]{0,4})?",
        ),
        reason,
    )
    require(matches(plan["account_id"], r"[A-Za-z0-9_-]{1,128}"), reason)
    require(matches(plan["connection_id"], r"pg_[0-9a-f]{32}"), reason)
    require(matches(plan["workspace_id"], r"ws_[0-9a-f]{32}"), reason)
    target = object_with_keys(
        plan["target"],
        {
            "host",
            "port",
            "database",
            "database_id",
            "username",
            "ssl_mode",
            "postgres_major",
            "migration_sha256",
        },
        reason,
    )
    require(matches(target["host"], r"[A-Za-z0-9._:-]{1,253}"), reason)
    require(type(target["port"]) is int and 1 <= target["port"] <= 65535, reason)
    require(matches(target["database"], r"devops_analytics_[a-z0-9_]{1,48}"), reason)
    database_id = target["database_id"]
    require(
        isinstance(database_id, str) and str(UUID(database_id)) == database_id, reason
    )
    require(matches(target["username"], r"[a-z_][a-z0-9_]{0,62}"), reason)
    require(
        target["ssl_mode"]
        in ("disable", "allow", "prefer", "require", "verify-ca", "verify-full"),
        reason,
    )
    require(
        type(target["postgres_major"]) is int and target["postgres_major"] == 17, reason
    )
    require(matches(target["migration_sha256"], SHA256), reason)
    for key in ("before", "after"):
        release = object_with_keys(
            plan[key], {"tag", "commit", "manifest_sha256"}, reason
        )
        require(
            matches(
                release["tag"], r"android-v[0-9]+\.[0-9]+\.[0-9]+-preview\.[1-9][0-9]*"
            ),
            reason,
        )
        require(matches(release["commit"], r"[0-9a-f]{40}"), reason)
        require(matches(release["manifest_sha256"], SHA256), reason)
    require(
        plan["before"]["tag"] != plan["after"]["tag"]
        and plan["before"]["commit"] != plan["after"]["commit"],
        reason,
    )
    counts = object_with_keys(plan["counts"], set(VIEWS), reason)
    require(
        all(type(n) is int and 0 <= n <= MAX_VIEW_ROWS for n in counts.values()), reason
    )
    require(counts["v1_ci_runs"] > 0 and counts["v1_collection_health"] > 0, reason)


def stage_evidence(plan: dict[str, Any], phase: str, root: Path) -> dict[str, Any]:
    hold = decode(private_bytes(root / "hold.json", fresh=True))
    require(
        hold
        == {
            "schema": 1,
            "operation_id": plan["operation_id"],
            "phase": phase,
            "state": "hold",
            "in_flight": 0,
        }
        and type(hold["in_flight"]) is int
        and type(hold["schema"]) is int,
        "maintenance-hold-required",
    )

    release = plan["after" if phase == "readback" else "before"]
    raw = private_bytes(root / "release.json")
    require(sha(raw) == release["manifest_sha256"], "wrong-release-manifest")
    manifest = decode(raw)
    require(
        type(manifest.get("schema")) is int
        and manifest["schema"] == 1
        and manifest.get("tag") == release["tag"]
        and manifest.get("commit") == release["commit"]
        and manifest.get("experimental") is True
        and manifest.get("runtime", {}).get("postgresql")
        == str(plan["target"]["postgres_major"]),
        "wrong-release-source",
    )

    runtime = decode(private_bytes(root / "runtime.json", fresh=True))
    services = runtime.get("services", {})
    require(
        runtime.get("state") == "ready"
        and runtime.get("ready") is True
        and runtime.get("supervisor_alive") is True
        and all(
            services.get(service, {}).get("running") is True for service in SERVICES
        ),
        "android-runtime-not-ready",
    )

    foundation = decode(private_bytes(root / "foundation.json", fresh=True))
    target = plan["target"]
    server_version = foundation.get("server_version")
    require(
        foundation.get("schema") == 1
        and foundation.get("action") == "status"
        and foundation.get("ok") is True
        and foundation.get("reason") == "ready"
        and foundation.get("exit_code") == 0
        and foundation.get("database_id") == target["database_id"]
        and foundation.get("contract_version") == 1
        and foundation.get("schema_version") == 1
        and foundation.get("pending_versions") == []
        and foundation.get("applied_versions") == []
        and foundation.get("migration_checksums") == {"1": target["migration_sha256"]}
        and positive_int(server_version)
        and server_version // 10000 == target["postgres_major"],
        "target-foundation-mismatch",
    )
    return {
        "release": release,
        "foundation_sha256": digest(foundation),
        "runtime_sha256": digest(runtime),
        "postgres_version": server_version,
    }


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Refused("https-redirect-refused")


def https_reader(origin: str, token: str) -> Get:
    # Ignore ambient proxies; validate the certificate with the platform trust store.
    opener = build_opener(
        ProxyHandler({}),
        HTTPSHandler(context=ssl.create_default_context()),
        NoRedirect(),
    )

    def get(path: str) -> dict[str, Any]:
        request = Request(
            origin + path,
            headers={
                "Accept": "application/json",
                "Cache-Control": "no-cache",
                "Cookie": f"schemii_session={token}",
            },
        )
        try:
            with opener.open(request, timeout=20) as response:
                require(
                    response.status == 200
                    and response.headers.get_content_type() == "application/json",
                    "unexpected-api-response",
                )
                raw = response.read(MAX_BYTES + 1)
        except URLError:
            raise Refused("https-api-unavailable") from None
        require(len(raw) <= MAX_BYTES, "api-response-too-large")
        return decode(raw)

    return get


def saved_identity(plan: dict[str, Any], get: Get) -> dict[str, str]:
    ready = get("/api/v1/readiness")
    require(
        ready.get("ready") is True
        and ready.get("metadata") == "postgresql"
        and ready.get("persistence") == "durable",
        "metadata-not-ready",
    )
    session = get("/api/v1/session")
    require(
        session.get("userId") == plan["account_id"]
        and session.get("authenticationSource") == "session"
        and session.get("ephemeral") is False,
        "wrong-reader-account",
    )
    connection = get(f"/api/v1/connections/{plan['connection_id']}")
    target = plan["target"]
    expected = {
        "id": plan["connection_id"],
        "host": target["host"],
        "port": target["port"],
        "database": target["database"],
        "username": target["username"],
        "sslMode": target["ssl_mode"],
        "credentialStored": True,
    }
    require(
        all(connection.get(key) == value for key, value in expected.items())
        and connection["credentialStored"] is True
        and positive_int(connection.get("revision")),
        "saved-connection-mismatch",
    )
    workspace = get(f"/api/v1/schemii/workspaces/{plan['workspace_id']}")
    expected_workspace = {
        "id": plan["workspace_id"],
        "connectionId": plan["connection_id"],
        "database": target["database"],
        "namespace": "devops",
    }
    require(
        all(workspace.get(key) == value for key, value in expected_workspace.items())
        and positive_int(workspace.get("revision")),
        "saved-workspace-mismatch",
    )
    namespaces = get(f"/api/v1/connections/{plan['connection_id']}/namespaces")
    names = {value["name"] for value in namespaces["namespaces"]}
    require(
        namespaces.get("connectionId") == plan["connection_id"]
        and namespaces.get("connectionRevision") == connection["revision"]
        and "devops" in names
        and "devops_control" not in names,
        "reader-namespace-mismatch",
    )
    return {
        "account_sha256": digest(session["userId"]),
        "connection_sha256": digest(
            {
                key: connection[key]
                for key in (*expected, "revision", "name", "ownerId", "ownership")
            }
        ),
        "workspace_sha256": digest(
            {key: workspace[key] for key in (*expected_workspace, "revision", "name")}
        ),
    }


def read_view(
    path: str,
    workspace_id: str,
    relation: dict[str, Any],
    count: int,
    get: Get,
) -> dict[str, Any]:
    ref, fingerprint = relation["ref"], relation["fingerprint"]
    require(matches(ref, r"rel_[A-Za-z0-9_-]{16,256}$"), "invalid-relation-ref")
    require(matches(fingerprint, SHA256), "invalid-relation-fingerprint")
    params: dict[str, Any] = {"pageSize": PAGE_ROWS}
    keys: set[tuple[str, ...]] = set()
    hashes: list[str] = []
    columns = None
    cursors: set[str] = set()
    # Every nonterminal page must be full. Bound requests from the approved count.
    for _ in range(max(1, (count + PAGE_ROWS - 1) // PAGE_ROWS)):
        page = get(f"{path}/{ref}/rows?{urlencode(params)}")
        require(
            page.get("workspaceId") == workspace_id
            and page.get("relationRef") == ref
            and page.get("relationFingerprint") == fingerprint,
            "relation-changed-during-read",
        )
        if columns is None:
            columns = page["columns"]
        require(page["columns"] == columns, "unexpected-view-shape")
        names = [column["name"] for column in columns]
        id_fields = ("repository_id", "installation_id", "id")
        require(
            all(names.count(name) == 1 for name in (*id_fields, "definition_version")),
            "unexpected-view-shape",
        )
        id_indexes = [names.index(name) for name in id_fields]
        version_index = names.index("definition_version")
        rows = page["rows"]
        require(
            isinstance(rows, list)
            and len(rows) <= PAGE_ROWS
            and len(hashes) + len(rows) <= count,
            "view-count-mismatch",
        )
        for row in rows:
            require(
                isinstance(row, list)
                and len(row) == len(names)
                and type(row[version_index]) is int
                and row[version_index] == 1,
                "unexpected-view-version",
            )
            key = tuple(row[index] for index in id_indexes)
            require(
                all(matches(part, r"[0-9a-f-]{36}") for part in key),
                "invalid-view-row-id",
            )
            require(key not in keys, "duplicate-view-row")
            keys.add(key)
            hashes.append(digest(row))

        cursor = page["nextCursor"]
        if cursor is None:
            require(page.get("truncated") is False, "incomplete-view-page")
            require(len(hashes) == count, "view-count-mismatch")
            return {"count": count, "sha256": digest([columns, sorted(hashes)])}
        require(
            page.get("truncated") is True
            and len(rows) == PAGE_ROWS
            and len(hashes) < count
            and matches(cursor, r"[A-Za-z0-9_-]{1,512}")
            and cursor not in cursors,
            "incomplete-view-page",
        )
        cursors.add(cursor)
        params["cursor"] = cursor
    raise Refused("incomplete-view-page")


def read_views(plan: dict[str, Any], get: Get) -> dict[str, Any]:
    workspace_id = plan["workspace_id"]
    path = f"/api/v1/schemii/workspaces/{workspace_id}/relations"
    catalog = get(f"{path}?pageSize={PAGE_ROWS}")
    require(
        catalog.get("workspaceId") == workspace_id and catalog["nextCursor"] is None,
        "incomplete-relation-catalog",
    )
    views = {}
    for name in VIEWS:
        candidates = [
            entry for entry in catalog["relations"] if entry.get("name") == name
        ]
        require(
            len(candidates) == 1 and candidates[0]["kind"] == "view",
            "required-view-missing",
        )
        views[name] = read_view(
            path, workspace_id, candidates[0], plan["counts"][name], get
        )
    return views


def capture(
    phase: str,
    plan: dict[str, Any],
    stage: Path,
    get: Get,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validate_plan(plan)
    binding = {
        "schema": 1,
        "plan_sha256": digest(plan),
        "helper_sha256": sha(Path(__file__).read_bytes()),
    }
    if phase != "baseline":
        require(
            baseline is not None
            and baseline.get("phase") == "baseline"
            and baseline.get("continuity") == "captured"
            and baseline.get("uploader") == "hold"
            and all(baseline.get(key) == value for key, value in binding.items())
            and baseline.get("stage", {}).get("release") == plan["before"],
            "baseline-binding-mismatch",
        )
    evidence = stage_evidence(plan, phase, stage)
    identity = saved_identity(plan, get)
    views = read_views(plan, get)
    require(saved_identity(plan, get) == identity, "identity-changed-during-read")
    observed = {"identity": identity, "views": views}
    if baseline is not None:
        require(baseline.get("observed") == observed, "continuity-mismatch")
    return {
        **binding,
        "phase": phase,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "stage": evidence,
        "observed": observed,
        "baseline_sha256": digest(baseline) if baseline is not None else None,
        "continuity": "captured" if phase == "baseline" else "matched",
        "uploader": "hold",
    }


def write_receipt(path: Path, value: dict[str, Any]) -> None:
    private_directory(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(encode(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("baseline", "readback", "rollback"))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--session-file", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        require(
            (args.baseline is None) == (args.phase == "baseline"),
            "baseline-argument-mismatch",
        )
        private_directory(args.output.parent)
        require(not os.path.lexists(args.output), "output-already-exists")
        plan = decode(private_bytes(args.plan))
        validate_plan(plan)
        baseline = (
            decode(private_bytes(args.baseline)) if args.baseline is not None else None
        )
        token = (
            private_bytes(args.session_file, limit=512)
            .decode("ascii")
            .removesuffix("\n")
        )
        require(matches(token, r"[A-Za-z0-9_-]{32,256}"), "invalid-session-file")
        receipt = capture(
            args.phase, plan, args.stage, https_reader(plan["origin"], token), baseline
        )
        write_receipt(args.output, receipt)
    except Refused as error:
        print(f"hold: {error}")
        return 2
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        print("hold: invalid-or-unavailable-input")
        return 2

    print(f"Continuity {receipt['continuity']}. Uploader remains on hold.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
