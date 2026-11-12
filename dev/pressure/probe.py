"""Fixed owned HTTPS/metadata pressure recipe, invoked only by start.sh's controller.

There is no arbitrary SQL, DSN, URL or account argument. Database credentials are
fresh fixture files, endpoints are the verified private project addresses, public
requests use the two canonical HTTPS origins, and observations stay private.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, suppress
import ipaddress
import json
import os
import stat
from pathlib import Path
import re
import ssl
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dev.pressure.controller import (  # noqa: E402
    LOCAL,
    PRESSURE_PROFILE,
    bound_profile,
    PREVIEW,
    check,
    command,
    checkout_authority,
    instant,
    private_read,
    private_write,
    verify_leases,
)

AUTH_LOCK = 0x53434841555448
WAIT_SECONDS = 8
SOURCE_MODULE = ROOT / "testing/load/source_monitor.py"


def secret(directory: Path, name: str) -> str:
    path = directory / name
    check(
        path.is_file()
        and not path.is_symlink()
        and path.stat().st_uid == os.getuid()
        and stat.S_IMODE(path.stat().st_mode) in {0o600, 0o640},
        "fixture_secret_owner",
    )
    with path.open("rb") as stream:
        raw = stream.read(513)
    check(len(raw) <= 512, "fixture_secret_bound")
    value = raw.decode().strip()
    check(bool(re.fullmatch(r"[A-Za-z0-9_-]{32,128}", value)), "fixture_secret_format")
    return value


def operation(name: str) -> dict:
    check(
        name in {"stop-owned-app", "start-owned-app", "sample-owned-app"},
        "fixed_lifecycle_operation",
    )
    print(json.dumps({"operation": name}), flush=True)
    response = sys.stdin.readline(32769)
    check(len(response) <= 32768, "lifecycle_not_confirmed")
    value = json.loads(response)
    check(
        type(value) is dict and value.get("confirmed") is True,
        "lifecycle_not_confirmed",
    )
    if name == "stop-owned-app":
        check(
            value.get("lifespan", {}).get("status")
            == "observed-stock-lifespan-drained",
            "stock_lifespan_evidence_missing",
        )
    elif name == "sample-owned-app":
        check(type(value.get("sample")) is dict, "owner_sample_missing")
    else:
        check(value == {"confirmed": True}, "lifecycle_not_confirmed")
    return value


def observed_peak(sample: dict, *, process_pid: int, started_at: str) -> dict:
    observation = sample.get("observation", {})
    check(
        observation.get("process", {}).get("pid") == process_pid,
        "saturated_owner_process_changed",
    )
    candidates = observation.get("ownerHistory", [])
    check(type(candidates) is list and len(candidates) <= 16, "owner_history_bound")
    for event in reversed(candidates):
        normal = event.get("normal", {})
        counts = normal.get("counts", {})
        if (
            normal.get("status") == "observed"
            and event.get("stage") == "running"
            and counts.get("active")
            == counts.get("maximum")
            == counts.get("peak_active")
            == 2
            and counts.get("closed") is False
            and instant(started_at)
            <= instant(event["at"])
            <= instant(observation["at"])
        ):
            return event
    raise RuntimeError("saturated_owner_count_not_observed")


def wait_until(action, seconds: float = WAIT_SECONDS):
    deadline = time.monotonic() + seconds
    while True:
        value = action()
        if value:
            return value
        check(time.monotonic() < deadline, "observation_deadline")
        time.sleep(0.02)


def assert_retryable(response, cookie_before: dict, client) -> str:
    check(
        response.status_code == 503 and "set-cookie" not in response.headers,
        "safe_pressure_response",
    )
    error = response.json().get("error", {})
    check(
        error.get("code")
        in {"metadata_capacity_exceeded", "metadata_storage_unavailable"}
        and error.get("retryable") is True,
        "retryable_pressure_error",
    )
    check(dict(client.cookies) == cookie_before, "session_cookie_changed")
    return error["code"]


class Probe:
    def __init__(self, directory: Path):
        self.directory = directory
        self.manifest = private_read(directory / "journal.json")
        check(
            self.manifest.get("bootstrapConsent") == "explicit-disposable-fixture-only",
            "disposable_bootstrap_consent",
        )
        check(
            re.fullmatch(r"pressure_[0-9a-f]{32}", self.manifest.get("run", ""))
            is not None,
            "fixture_run",
        )
        endpoints = self.manifest.get("endpoints", {})
        check(
            set(endpoints) == {"metadata", "source"}
            and endpoints["metadata"] != endpoints["source"],
            "independent_database_endpoints",
        )
        check(
            all(
                ipaddress.ip_address(value).is_private
                and not ipaddress.ip_address(value).is_loopback
                for value in endpoints.values()
            ),
            "private_database_addresses",
        )
        self.profile = bound_profile(self.manifest.get("profile"))
        self.run = self.manifest["run"]
        self.observations: list[dict] = []
        self.identities: list[dict] = []

    def connect(self, kind: str, role: str):
        import psycopg
        from psycopg.rows import dict_row

        roles = {
            "admin": ("pressure_admin", "admin_password"),
            "app": ("pressure_app", "app_password"),
            "monitor": ("pressure_monitor", "monitor_password"),
            "blocker": ("pressure_blocker", "blocker_password"),
            "source": ("pressure_source", "source_password"),
        }
        check(
            kind in {"metadata", "source"} and role in roles, "fixed_database_authority"
        )
        user, password = roles[role]
        if kind == "source" and role == "admin":
            password = "source_admin_password"
        connection = psycopg.connect(
            host=self.manifest["endpoints"][kind],
            port=5432,
            dbname="pressure_" + kind,
            user=user,
            password=secret(self.directory, password),
            sslmode="require",
            connect_timeout=3,
            autocommit=True,
            row_factory=dict_row,
            application_name="pressure_" + role + "_" + self.run[-16:],
            options="-c statement_timeout=5000 -c lock_timeout=1000 -c idle_in_transaction_session_timeout=60000",
        )
        identity = connection.execute(
            "SELECT current_database() AS database,session_user AS role,pg_backend_pid() AS pid,backend_start FROM pg_stat_activity WHERE pid=pg_backend_pid()"
        ).fetchone()
        check(
            identity["database"] == "pressure_" + kind and identity["role"] == user,
            "database_authority_mismatch",
        )
        self.identities.append(
            {
                "kind": kind,
                "role": user,
                "database": identity["database"],
                "pid": identity["pid"],
                "backendStart": identity["backend_start"].isoformat(),
            }
        )
        return connection

    def prepare(self) -> dict:
        from psycopg import sql

        fixture_id = hashlib.sha256((self.run + ":source-fixture").encode()).hexdigest()
        server = None
        database_settings = {}
        for kind in ("metadata", "source"):
            with self.connect(kind, "admin") as connection:
                effective = connection.execute(
                    "SELECT current_setting('max_connections')::integer AS maximum_connections,current_setting('shared_buffers') AS shared_buffers"
                ).fetchone()
                check(
                    effective["maximum_connections"] == self.profile.database_maximum
                    and effective["shared_buffers"] == self.profile.shared_buffers,
                    "fixed_database_profile_mismatch",
                )
                database_settings[kind] = {
                    "maximumConnections": effective["maximum_connections"],
                    "sharedBuffers": effective["shared_buffers"],
                }
                names = (
                    ("app", "monitor", "blocker") if kind == "metadata" else ("source",)
                )
                for role in names:
                    user = "pressure_" + role
                    password = secret(self.directory, role + "_password")
                    connection.execute(
                        sql.SQL(
                            "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {}"
                        ).format(sql.Identifier(user), sql.Literal(password))
                    )
                owner = "pressure_app" if kind == "metadata" else "pressure_source"
                connection.execute(
                    sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
                        sql.Identifier("pressure_" + kind), sql.Identifier(owner)
                    )
                )
                if kind == "metadata":
                    connection.execute("GRANT pg_monitor TO pressure_monitor")
                else:
                    connection.execute(
                        sql.SQL("COMMENT ON DATABASE pressure_source IS {}").format(
                            sql.Literal("schemii-load:" + fixture_id)
                        )
                    )
                    observed = connection.execute(
                        "SELECT inet_server_addr()::text AS address,inet_server_port() AS port,pg_postmaster_start_time() AS started,d.oid::bigint AS database_oid,shobj_description(d.oid,'pg_database') AS marker FROM pg_database d WHERE d.datname=current_database()"
                    ).fetchone()
                    check(
                        observed["marker"] == "schemii-load:" + fixture_id,
                        "source_fixture_marker",
                    )
                    server = {
                        "address": observed["address"],
                        "port": observed["port"],
                        "postmasterStart": observed["started"]
                        .astimezone(timezone.utc)
                        .isoformat(timespec="microseconds"),
                        "databaseOid": observed["database_oid"],
                        "fixtureId": fixture_id,
                    }
        # This same-role external witness is owned by #135. Do not duplicate its
        # matcher or call an in-process application registry source proof.
        check(SOURCE_MODULE.is_file(), "source_monitor_dependency_missing")
        monitor_run = "load_" + self.run[-16:]
        authority = {
            "kind": "exclusive_source_role",
            "exclusiveToRun": True,
            "database": "pressure_source",
            "role": "pressure_source",
            "applicationName": "schemii",
            "endpoint": {
                "host": self.manifest["endpoints"]["source"],
                "port": 5432,
                "sslmode": "require",
            },
            "server": server,
        }
        provision = self.directory / "source-provisioning.json"
        private_write(
            provision,
            {"version": 2, "status": "ready", "runId": monitor_run, **authority},
        )
        private_write(
            self.directory / "source-monitor-config.json",
            {
                "version": 2,
                "runId": monitor_run,
                "authority": {**authority, "provisioningReceipt": str(provision)},
                "connection": {
                    "host": self.manifest["endpoints"]["source"],
                    "port": 5432,
                    "dbname": "pressure_source",
                    "user": "pressure_source",
                    "password": secret(self.directory, "source_password"),
                    "sslmode": "require",
                },
            },
        )
        baseline = self.source_sample()
        check(
            baseline["ownedBackends"] == 0 and baseline["releaseVerified"] is False,
            "source_empty_baseline",
        )
        return {
            "phase": "prepared",
            "profile": self.profile.receipt(),
            "databaseSettings": database_settings,
            "sourceBaseline": baseline,
            "databaseAuthorities": self.identities,
        }

    def source_sample(self) -> dict:
        reply = command(
            [
                sys.executable,
                str(SOURCE_MODULE),
                "--config",
                str(self.directory / "source-monitor-config.json"),
                "--output",
                str(self.directory / "source-monitor-ledger.json"),
            ],
            timeout=5,
            limit=4096,
        )
        check(
            json.loads(reply) == {"status": "available"}, "source_monitor_unavailable"
        )
        value = private_read(self.directory / "source-monitor-ledger.json")
        check(value.get("version") == 2, "source_witness_version")
        config = private_read(self.directory / "source-monitor-config.json")
        expected = config["authority"]
        scope = value.get("authority", {})
        check(
            scope.get("runId") == config["runId"]
            and all(
                scope.get(key) == expected[key]
                for key in (
                    "kind",
                    "exclusiveToRun",
                    "database",
                    "role",
                    "applicationName",
                    "endpoint",
                    "server",
                )
            )
            and value.get("serverIdentity") == expected["server"],
            "source_monitor_server_binding",
        )
        check(
            value["authority"]["database"] == "pressure_source"
            and value["authority"]["role"] == "pressure_source",
            "source_monitor_scope",
        )
        return value

    def metadata_sample(self, monitor) -> list[dict]:
        rows = monitor.execute(
            "SELECT pid,backend_start,datname,usename,application_name,state,wait_event_type,wait_event FROM pg_stat_activity WHERE datname=%s AND usename=%s AND application_name=%s ORDER BY pid LIMIT 17",
            ("pressure_metadata", "pressure_app", "schemii-metadata"),
        ).fetchall()
        check(len(rows) <= 2, "normal_backend_bound")
        return [
            {**row, "backend_start": row["backend_start"].isoformat()} for row in rows
        ]

    def metadata_closed(self, monitor, captured: list[dict]) -> bool:
        # Recheck captured PID/start identities without filtering mutable role or
        # application_name; a renamed live backend must never become "closed".
        rows = monitor.execute(
            "SELECT pid,backend_start FROM pg_stat_activity WHERE pid=ANY(%s) ORDER BY pid LIMIT 17",
            ([item["pid"] for item in captured],),
        ).fetchall()
        check(len(rows) <= len(captured), "captured_metadata_scope")
        return not any(
            row["pid"] == item["pid"]
            and row["backend_start"].isoformat() == item["backend_start"]
            for row in rows
            for item in captured
        )

    def client(self, origin: str = LOCAL):
        import httpx

        check(origin in {LOCAL, PREVIEW}, "canonical_https_only")
        verify = ssl._create_unverified_context() if origin == LOCAL else True
        return httpx.Client(
            base_url=origin,
            verify=verify,
            follow_redirects=False,
            trust_env=False,
            timeout=8,
            headers={"Origin": origin},
        )

    def expect(self, response, status: int) -> dict:
        check(response.status_code == status, "https_response_contract")
        return response.json() if response.content else {}

    def rpc_ready(self, client) -> None:
        def ready():
            try:
                response = client.get("/api/v1/readiness")
                return (
                    response.status_code == 200 and response.json().get("ready") is True
                )
            except Exception:
                return False

        wait_until(ready, 30)

    def execute(
        self, client, workspace: dict, settings: dict, statement: str
    ) -> tuple[str, str]:
        check(
            statement
            in {"SELECT 42::bigint AS recovered", "SELECT 1::bigint FROM pg_sleep(20)"},
            "fixed_source_statement",
        )
        receipt = self.expect(
            client.post(
                f"/api/v1/schemii/workspaces/{workspace['id']}/console/executions",
                json={
                    "consoleId": "con_" + uuid.uuid4().hex,
                    "expectedWorkspaceRevision": workspace["revision"],
                    "expectedSettingsRevision": settings["revision"],
                    "mode": "managed_read",
                    "statements": [statement],
                },
            ),
            201,
        )
        path = "/api/v1/common/query-executions/" + receipt["id"]
        execution = self.expect(client.get(path), 200)
        check(
            execution["status"] == "succeeded" and len(execution["results"]) == 1,
            "execution_shape",
        )
        return path, path + "/results/" + execution["results"][0]["id"]

    def administrator(self, client) -> tuple[dict, dict]:
        account = {
            "username": self.run,
            "password": secret(self.directory, "account_password"),
        }
        principal = self.directory / "principal.json"
        if principal.exists():
            stored = private_read(principal)
            check(
                stored.get("run") == self.run
                and stored.get("username") == account["username"],
                "fixture_principal_identity",
            )
            identity = self.expect(client.post("/api/v1/auth/login", json=account), 200)
            check(
                identity["user"]["id"] == stored["userId"], "fixture_principal_changed"
            )
        else:
            identity = self.expect(
                client.post(
                    "/api/v1/auth/setup",
                    json={
                        **account,
                        "display_name": "Disposable pressure fixture",
                        "setup_token": secret(self.directory, "setup_token"),
                        "is_admin": True,
                    },
                ),
                200,
            )
            check(identity["is_admin"] is True, "fixture_bootstrap_admin")
            private_write(
                principal,
                {
                    "run": self.run,
                    "username": account["username"],
                    "userId": identity["user"]["id"],
                    "passwordFile": str(self.directory / "account_password"),
                },
            )
        return account, identity

    def bootstrap(self) -> dict:
        with self.client() as client:
            self.administrator(client)
            runtime = self.expect(client.get("/api/v1/admin/runtime-observation"), 200)
        return {
            "phase": "bootstrapped",
            "runtimeObservation": runtime,
            "run": self.run,
            "principal": private_read(self.directory / "principal.json"),
        }

    def qualify(self) -> dict:
        check(
            self.profile is PRESSURE_PROFILE, "pressure_qualification_profile_required"
        )
        with ExitStack() as cleanup:
            admin = cleanup.enter_context(self.client())
            account, identity = self.administrator(admin)
            cookie = dict(admin.cookies)
            check(bool(cookie), "fixture_cookie_required")
            preview = cleanup.enter_context(self.client(PREVIEW))
            remote = self.expect(preview.post("/api/v1/auth/login", json=account), 200)
            check(
                remote["user"]["id"] == identity["user"]["id"],
                "preview_fixture_identity",
            )
            peer_user = self.expect(
                admin.post(
                    "/api/v1/admin/accounts",
                    json={
                        "username": self.run + "_peer",
                        "display_name": "Disposable peer",
                        "password": account["password"],
                        "direct_access": {"capabilities": ["schemii:access"]},
                    },
                ),
                201,
            )
            peer = cleanup.enter_context(self.client())
            self.expect(
                peer.post(
                    "/api/v1/auth/login",
                    json={
                        "username": self.run + "_peer",
                        "password": account["password"],
                    },
                ),
                200,
            )
            profile = self.expect(
                admin.post(
                    "/api/v1/connections",
                    json={
                        "name": self.run,
                        "host": "source",
                        "port": 5432,
                        "database": "pressure_source",
                        "username": "pressure_source",
                        "password": secret(self.directory, "source_password"),
                        "sslMode": "require",
                    },
                ),
                201,
            )
            workspace = self.expect(
                admin.post(
                    "/api/v1/schemii/workspaces/postgres",
                    json={"connectionId": profile["id"], "namespace": "public"},
                ),
                201,
            )["workspace"]
            settings = self.expect(admin.get("/api/v1/schemii/console/settings"), 200)
            monitor = cleanup.enter_context(self.connect("metadata", "monitor"))
            blocker = cleanup.enter_context(self.connect("metadata", "blocker"))
            self.expect(admin.get("/api/v1/auth/me"), 200)
            initial = self.expect(admin.get("/api/v1/admin/runtime-observation"), 200)
            check(
                initial["sources"]["metadata"]["counts"]["active"] == 0,
                "metadata_empty_permits",
            )
            execution, result_path = self.execute(
                admin, workspace, settings, "SELECT 1::bigint FROM pg_sleep(20)"
            )
            with ThreadPoolExecutor(max_workers=4) as executor:
                page = executor.submit(admin.get, result_path)
                source = wait_until(
                    lambda: (
                        value
                        if (value := self.source_sample())["ownedBackends"] == 1
                        else None
                    )
                )
                check(
                    self.expect(peer.delete(execution), 404) is not None,
                    "peer_cannot_cancel",
                )
                with blocker.transaction():
                    blocker.execute("SELECT pg_advisory_xact_lock(%s)", (AUTH_LOCK,))

                    def login_waiter():
                        with self.client() as requester:
                            return requester.post("/api/v1/auth/login", json=account)

                    pressure_started_at = datetime.now(timezone.utc).isoformat()
                    waits = [executor.submit(login_waiter) for _ in range(2)]
                    native = wait_until(
                        lambda: (
                            rows
                            if len(rows := self.metadata_sample(monitor)) == 2
                            and all(row["wait_event_type"] == "Lock" for row in rows)
                            else None
                        )
                    )
                    peak = operation("sample-owned-app")["sample"]
                    event = observed_peak(
                        peak,
                        process_pid=initial["processPid"],
                        started_at=pressure_started_at,
                    )
                    started = time.monotonic()
                    error = assert_retryable(
                        admin.get("/api/v1/auth/me"), cookie, admin
                    )
                    assert_retryable(admin.delete(execution), cookie, admin)
                    self.expect(admin.get("/api/v1/readiness"), 200)
                    check(
                        time.monotonic() - started < 2,
                        "bounded_rejection_and_readiness",
                    )
                    check(not page.done(), "pressure_did_not_cancel_source")
                    for pending in waits:
                        response = pending.result(timeout=5)
                        check(
                            response.status_code == 503
                            and "set-cookie" not in response.headers,
                            "advisory_deadline_response",
                        )
                    wait_until(lambda: self.metadata_closed(monitor, native))
                    self.observations.append(
                        {
                            "case": "saturation",
                            "status": "passed",
                            "normalWaiters": native,
                            "error": error,
                            "cookieRetained": True,
                            "source": source,
                            "peakPermits": {
                                "status": "observed",
                                "sample": peak,
                                "event": event,
                            },
                        }
                    )
                self.expect(admin.delete(execution), 200)
                stopped_page = page.result(timeout=5)
                check(
                    stopped_page.json()["error"]["code"]
                    == "postgres_console_cancelled",
                    "source_cancelled",
                )
                closed = wait_until(
                    lambda: (
                        value
                        if (value := self.source_sample())["releaseVerified"]
                        else None
                    )
                )
                check(closed["ownedBackends"] == 0, "source_backend_closed")
                observed = self.expect(
                    admin.get("/api/v1/admin/runtime-observation"), 200
                )
                check(
                    observed["sources"]["metadata"]["counts"]["active"] == 0,
                    "metadata_permits_released",
                )
                self.observations.append(
                    {
                        "case": "cancel_recovery",
                        "status": "passed",
                        "source": closed,
                        "metadataActivePermits": 0,
                    }
                )
                # Exercise the real application lifespan with known blocked native
                # metadata identities. The controller alone owns service actions.
                with blocker.transaction():
                    blocker.execute("SELECT pg_advisory_xact_lock(%s)", (AUTH_LOCK,))
                    waits = [executor.submit(login_waiter) for _ in range(2)]
                    shutdown_native = wait_until(
                        lambda: (
                            rows
                            if len(rows := self.metadata_sample(monitor)) == 2
                            else None
                        )
                    )
                    lifespan = operation("stop-owned-app")["lifespan"]
                    wait_until(lambda: self.metadata_closed(monitor, shutdown_native))
                    for pending in waits:
                        with suppress(Exception):
                            pending.result(timeout=5)
                    self.observations.append(
                        {
                            "case": "lifespan_native_drain",
                            "status": "passed",
                            "observedBefore": shutdown_native,
                            "remainingNormalBackends": 0,
                            "lifespan": lifespan,
                        }
                    )
                operation("start-owned-app")
                self.rpc_ready(admin)
            self.expect(admin.get("/api/v1/auth/me"), 200)
            check(
                dict(admin.cookies) == cookie, "session_survives_pressure_and_lifespan"
            )
            fresh_execution, fresh_result = self.execute(
                admin, workspace, settings, "SELECT 42::bigint AS recovered"
            )
            check(
                self.expect(admin.get(fresh_result), 200)["rows"] == [[42]],
                "fresh_exact_select42",
            )
            self.expect(admin.delete(fresh_result), 204)
            self.expect(admin.delete(fresh_execution), 200)
            final_source = self.source_sample()
            check(
                final_source["releaseVerified"] and final_source["ownedBackends"] == 0,
                "final_source_closed",
            )
            stale = admin.post(
                f"/api/v1/schemii/workspaces/{workspace['id']}/console/executions",
                json={
                    "consoleId": "con_" + uuid.uuid4().hex,
                    "expectedWorkspaceRevision": workspace["revision"] + 1,
                    "expectedSettingsRevision": settings["revision"],
                    "mode": "managed_read",
                    "statements": ["SELECT 42::bigint AS recovered"],
                },
            )
            check(stale.status_code == 409, "revision_authority")
            self.expect(
                admin.patch(
                    "/api/v1/admin/accounts/" + peer_user["id"],
                    json={"direct_access": {"capabilities": []}},
                ),
                200,
            )
            check(
                peer.get("/api/v1/schemii/workspaces").status_code == 403,
                "current_role_revocation",
            )
            self.expect(peer.post("/api/v1/auth/logout"), 200)
            check(peer.get("/api/v1/auth/me").status_code == 401, "session_revocation")
            self.expect(admin.get("/api/v1/auth/me"), 200)
            self.observations.append(
                {
                    "case": "fresh_read_and_authority",
                    "status": "passed",
                    "value": 42,
                    "revisionConflict": True,
                    "roleRevocation": True,
                    "sessionRevocation": True,
                    "peerControl": True,
                    "source": final_source,
                }
            )
            final_runtime = self.expect(
                admin.get("/api/v1/admin/runtime-observation"), 200
            )
        # This qualifies an explicitly instrumented fixture, never stock capacity.
        return {
            "version": 1,
            "run": self.run,
            "phase": "qualify",
            "profile": self.profile.receipt(),
            "runtimeObservation": final_runtime,
            "acceptance": True,
            "capacityAcceptance": False,
            "instrumentation": "fixture_only_stock_asgi_lifespan_and_owner_sampler",
            "cases": self.observations,
            "databaseAuthorities": self.identities,
            "pending": [
                "Independent current-source qualification evidence review",
            ],
            "scope": "disposable-canonical-https-metadata-qualification",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument(
        "--phase", choices=("prepare", "qualify", "bootstrap"), required=True
    )
    args = parser.parse_args()
    try:
        authorization = sys.stdin.readline(4097)
        check(
            len(authorization) <= 4096
            and json.loads(authorization) == {"authorized": True},
            "launcher_authorization_required",
        )
        probe = Probe(args.directory)
        primary = Path(probe.manifest["primary"])
        common = Path(
            command(
                [
                    "git",
                    "-C",
                    str(ROOT),
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-common-dir",
                ]
            ).strip()
        )
        checkout_authority(ROOT, primary, common)
        verify_leases(common)
        result = {
            "prepare": probe.prepare,
            "qualify": probe.qualify,
            "bootstrap": probe.bootstrap,
        }[args.phase]()
        print(json.dumps({"result": result}, separators=(",", ":")), flush=True)
        return 0
    except Exception:
        print(
            json.dumps(
                {
                    "result": {
                        "phase": args.phase,
                        "acceptance": False,
                        "capacityAcceptance": False,
                        "status": "failed",
                        "reason": "fixed_probe_failed_or_observation_missing",
                    }
                }
            ),
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
