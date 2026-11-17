"""Readback gates and opt-in cross-release PostgreSQL 17 / ASGI rehearsal."""

import base64
import json
from hashlib import sha256
import os
from pathlib import Path
import secrets
import subprocess
import sys
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from scripts.ci import summary
from scripts.devops import importer, phone_upgrade_check as check, storage
from tests.devops.test_importer import DB_ID, NAME, WHEN, fixture_input, selected
from tests.test_android_portable_restore import (
    private_cluster,
    postgres_bin as postgres_bin,
)


ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "https://localhost:8001"
ROLE = "fixture_reader"


def write(path, value):
    check.write_receipt(path, value)


def make_plan(tmp_path):
    def release(number, commit):
        manifest = {
            "schema": 1,
            "tag": f"android-v0.1.0-preview.{number}",
            "commit": commit,
            "experimental": True,
            "runtime": {"postgresql": "17"},
        }
        path = tmp_path / f"release-{number}.json"
        write(path, manifest)
        return {key: manifest[key] for key in ("tag", "commit")} | {
            "manifest_sha256": sha256(path.read_bytes()).hexdigest(),
        }

    return {
        "schema": 1,
        "operation_id": str(uuid4()),
        "origin": ORIGIN,
        "account_id": "fixture_reader",
        "connection_id": "pg_" + "a" * 32,
        "workspace_id": "ws_" + "b" * 32,
        "target": {
            "database": NAME,
            "database_id": DB_ID,
            "username": ROLE,
            "host": "127.0.0.1",
            "port": 5434,
            "ssl_mode": "disable",
            "postgres_major": 17,
            "migration_sha256": storage.migration_files()[0][1],
        },
        "before": release(13, "a" * 40),
        "after": release(14, "b" * 40),
        "counts": dict(zip(check.VIEWS, (1, 1, 1, 1), strict=True)),
    }


def stage(tmp_path, plan, phase, status):
    root = tmp_path / phase
    root.mkdir(mode=0o700)
    write(
        root / "hold.json",
        {
            "schema": 1,
            "operation_id": plan["operation_id"],
            "phase": phase,
            "state": "hold",
            "in_flight": 0,
        },
    )
    write(
        root / "runtime.json",
        {
            "ready": True,
            "state": "ready",
            "supervisor_alive": True,
            "services": {name: {"running": True} for name in check.SERVICES},
        },
    )
    release = tmp_path / (
        "release-14.json" if phase == "readback" else "release-13.json"
    )
    (root / "release.json").write_bytes(release.read_bytes())
    (root / "release.json").chmod(0o600)
    write(root / "foundation.json", status)
    return root


def status_for(plan):
    return {
        "schema": 1,
        "action": "status",
        "ok": True,
        "reason": "ready",
        "exit_code": 0,
        "database_id": plan["target"]["database_id"],
        "contract_version": 1,
        "schema_version": 1,
        "pending_versions": [],
        "applied_versions": [],
        "server_version": 170012,
        "migration_checksums": {"1": plan["target"]["migration_sha256"]},
    }


@pytest.mark.parametrize(
    "file,field,replacement,reason",
    [
        ("hold", "in_flight", 1, "maintenance-hold-required"),
        ("runtime", "supervisor_alive", False, "android-runtime-not-ready"),
        ("foundation", "database_id", "wrong", "target-foundation-mismatch"),
        ("foundation", "schema_version", 2, "target-foundation-mismatch"),
        ("release", "commit", "f" * 40, "wrong-release-manifest"),
    ],
)
def test_refuses_stage_before_calling_api(tmp_path, file, field, replacement, reason):
    plan = make_plan(tmp_path)
    root = stage(tmp_path, plan, "baseline", status_for(plan))
    path = root / f"{file}.json"
    value = check.decode(path.read_bytes())
    value[field] = replacement
    path.unlink()
    write(path, value)
    with pytest.raises(check.Refused, match=reason):
        check.capture(
            "baseline", plan, root, lambda _: pytest.fail("unexpected API call")
        )


def test_refuses_stale_evidence_and_unsafe_files(tmp_path):
    plan = make_plan(tmp_path)
    root = stage(tmp_path, plan, "baseline", status_for(plan))
    runtime = root / "runtime.json"
    os.utime(runtime, (1, 1))
    with pytest.raises(check.Refused, match="stale-stage"):
        check.stage_evidence(plan, "baseline", root)

    secret = tmp_path / "session"
    secret.symlink_to(root / "hold.json")
    with pytest.raises(OSError):
        check.private_bytes(secret)
    with pytest.raises(FileExistsError):
        write(root / "hold.json", {})
    assert check.decode((root / "hold.json").read_bytes())["state"] == "hold"


def test_view_pages_compare_as_a_set_and_reject_a_duplicate(monkeypatch):
    monkeypatch.setattr(check, "PAGE_ROWS", 1)
    relation = {"ref": "rel_" + "a" * 32, "fingerprint": "1" * 64}
    ids = [str(uuid4()), str(uuid4())]

    def api(order):
        def get(path):
            last = "cursor=" in path
            return {
                "workspaceId": "workspace",
                "relationRef": relation["ref"],
                "relationFingerprint": relation["fingerprint"],
                "nextCursor": None if last else "continuation",
                "truncated": not last,
                "columns": [
                    {"name": name}
                    for name in (
                        "repository_id",
                        "installation_id",
                        "id",
                        "definition_version",
                    )
                ],
                "rows": [[ids[0], ids[1], order[last], 1]],
            }

        return get

    before = check.read_view("/relations", "workspace", relation, 2, api(ids))
    assert before == check.read_view(
        "/relations", "workspace", relation, 2, api(ids[::-1])
    )
    with pytest.raises(check.Refused, match="duplicate-view-row"):
        check.read_view("/relations", "workspace", relation, 2, api([ids[0], ids[0]]))
    with pytest.raises(check.Refused, match="incomplete-view-page"):
        check.read_view("/relations", "workspace", relation, 1, api(ids))


def connection(socket, user, password, database=NAME, **kwargs):
    return psycopg.connect(
        host=socket,
        user=user,
        password=password,
        dbname=database,
        autocommit=True,
        **kwargs,
    )


def seed_target(socket, psql, password, source, *, actor_password):
    psql(
        "psql",
        "-X",
        "-v",
        "db_name=" + NAME,
        "-v",
        "database_id=" + DB_ID,
        "-f",
        ROOT / "scripts/devops/provision.sql",
    )
    with connection(socket, "fixture_admin", password) as authority:
        for login, role in (
            ("fixture_migrator", "devops_migrator"),
            ("fixture_collector", "devops_collector"),
            (ROLE, "devops_reader"),
        ):
            authority.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(login), sql.Literal(actor_password)
                )
            )
            authority.execute(
                sql.SQL("GRANT {} TO {}").format(
                    sql.Identifier(role), sql.Identifier(login)
                )
            )
    with connection(socket, "fixture_migrator", actor_password) as conn:
        storage.operate(conn, "migrate", NAME, UUID(DB_ID))
    with connection(
        socket, "fixture_collector", actor_password, row_factory=dict_row
    ) as conn:
        _, _, (start, case, attempt, end) = fixture_input(source)
        # Exercise two real API pages with a single synthetic CI run.
        size = check.PAGE_ROWS + 1
        identifiers = [f"{i:064x}" for i in range(size)]
        records = [start | {"planned": size}]
        records += [case | {"test_id": key} for key in identifiers]
        records += [attempt | {"test_id": key} for key in identifiers]
        records.append(end)
        timing = source / "run/command-00/timing.jsonl"
        timing.write_text("".join(json.dumps(value) + "\n" for value in records))
        (timing.parent / "timing.summary.json").write_text(
            json.dumps(summary.summarize(records))
        )
        importer.apply(conn, selected(source), str(uuid4()), WHEN)


def app_phase(root, phase):
    # Child imports are deliberately resolved from the selected release's src.
    from fastapi.testclient import TestClient
    from schemii.main import app

    assert Path(sys.modules["schemii.main"].__file__).is_relative_to(
        Path(os.environ["PYTHONPATH"])
    )
    app.state.services.postgres._connect_factory = lambda **kw: psycopg.connect(
        **(
            kw
            | {
                "host": os.environ["FIXTURE_TARGET_SOCKET"],
                "port": 5432,
                "row_factory": dict_row,
            }
        )
    )
    plan_file = root / "plan.json"
    plan = check.decode(check.private_bytes(plan_file))
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:

        def post(path, body):
            reply = client.post(path, json=body)
            assert reply.status_code in (200, 201), (path, reply.status_code)
            return reply.json()

        if phase == "baseline":
            credentials = {
                "username": "fixture_owner",
                "password": secrets.token_urlsafe(32),
            }
            post(
                "/api/v1/auth/setup",
                credentials
                | {
                    "display_name": "Fixture administrator",
                    "setup_token": Path(
                        os.environ["SCHEMII_SETUP_TOKEN_FILE"]
                    ).read_text(),
                },
            )
            password = secrets.token_urlsafe(32)
            post(
                "/api/v1/admin/accounts",
                {
                    "username": "fixture_app_reader",
                    "display_name": "Fixture app reader",
                    "password": password,
                    "direct_access": {"capabilities": ["schemii:access"]},
                },
            )
            account = post(
                "/api/v1/auth/login",
                {
                    "username": "fixture_app_reader",
                    "password": password,
                },
            )
            profile = post(
                "/api/v1/connections",
                {
                    "name": "Fixture target",
                    "host": "127.0.0.1",
                    "port": 5434,
                    "database": NAME,
                    "username": ROLE,
                    "password": (root / "target-password").read_text(),
                    "sslMode": "disable",
                },
            )
            workspace = post(
                "/api/v1/schemii/workspaces/postgres",
                {
                    "connectionId": profile["id"],
                    "namespace": "devops",
                },
            )
            plan.update(
                account_id=account["user"]["id"],
                connection_id=profile["id"],
                workspace_id=workspace["workspace"]["id"],
            )
            plan_file.unlink()
            write(plan_file, plan)
            session_file = root / "session"
            session_file.touch(mode=0o600, exist_ok=False)
            session_file.write_text(client.cookies["schemii_session"])

        def get(path):
            token = check.private_bytes(root / "session").decode()
            reply = client.get(path, headers={"Cookie": f"schemii_session={token}"})
            assert reply.status_code == 200, (path, reply.status_code)
            return reply.json()

        baseline = (
            None
            if phase == "baseline"
            else check.decode(check.private_bytes(root / "baseline.json"))
        )
        receipt = check.capture(phase, plan, root / phase, get, baseline)
        assert receipt["uploader"] == "hold"
        write(root / f"{phase}.json", receipt)


def test_postgres17_retained_state_cross_release_and_refusal(
    postgres_bin, tmp_path, request
):
    before = os.environ.get("SCHEMII_308_BEFORE_SRC")
    after = os.environ.get("SCHEMII_308_AFTER_SRC")
    if not before or not after:
        pytest.skip("select the exact previous and candidate source directories")
    plan = make_plan(tmp_path)
    for phase, source, number in (("before", before, 13), ("after", after, 14)):
        package = Path(source).parent
        manifest_bytes = (package / "ANDROID_RELEASE.json").read_bytes()
        manifest = check.decode(manifest_bytes)
        for name, entry in manifest["files"].items():
            if name.startswith("src/"):
                assert (
                    sha256((package / name).read_bytes()).hexdigest() == entry["sha256"]
                )
        release = tmp_path / f"release-{number}.json"
        release.write_bytes(manifest_bytes)
        plan[phase] = {key: manifest[key] for key in ("tag", "commit")} | {
            "manifest_sha256": sha256(manifest_bytes).hexdigest()
        }
    plan["counts"]["v1_test_attempts"] = check.PAGE_ROWS + 1
    write(tmp_path / "plan.json", plan)
    request.addfinalizer(lambda: (tmp_path / "session").unlink(missing_ok=True))
    metadata_password, admin_password, actor_password = (
        secrets.token_urlsafe(32) for _ in range(3)
    )

    def secret_file(name, value):
        path = tmp_path / name
        path.touch(mode=0o600, exist_ok=False)
        path.write_text(value)
        request.addfinalizer(lambda: path.unlink(missing_ok=True))
        return str(path)

    with (
        private_cluster(postgres_bin, "fixture_metadata", metadata_password) as (
            metadata,
            metadata_tools,
        ),
        private_cluster(postgres_bin, "fixture_admin", admin_password) as (
            target,
            psql,
        ),
    ):
        seed_target(
            target,
            psql,
            admin_password,
            tmp_path / "intake",
            actor_password=actor_password,
        )
        env = {
            k: v for k, v in os.environ.items() if not k.startswith(("SCHEMII_", "PG"))
        }
        env.update(
            PYTHONDONTWRITEBYTECODE="1",
            SCHEMII_STORAGE_MODE="postgresql",
            SCHEMII_METADATA_DSN=f"host={metadata} dbname=postgres user=fixture_metadata",
            SCHEMII_METADATA_PASSWORD_FILE=secret_file(
                "metadata-password", metadata_password
            ),
            SCHEMII_METADATA_ENCRYPTION_KEY_FILE=secret_file(
                "metadata-key", base64.b64encode(secrets.token_bytes(32)).decode()
            ),
            SCHEMII_AUTH_ENABLED="1",
            SCHEMII_DEPLOYMENT_MODE="authenticated",
            SCHEMII_TARGET_EGRESS_MODE="internal-only",
            SCHEMII_ALLOWED_TARGET_HOSTS="127.0.0.1",
            SCHEMII_SETUP_TOKEN_FILE=secret_file(
                "setup-token", secrets.token_urlsafe(32)
            ),
            FIXTURE_TARGET_SOCKET=target,
        )
        secret_file("target-password", actor_password)

        def run(source, phase):
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from tests.devops.test_phone_upgrade_check import app_phase, Path; "
                    "import sys; app_phase(Path(sys.argv[1]), sys.argv[2])",
                    str(tmp_path),
                    phase,
                ],
                cwd=ROOT,
                env=env | {"PYTHONPATH": source},
                text=True,
                capture_output=True,
                timeout=60,
            )
            assert result.returncode == 0, result.stderr

        def foundation():
            with connection(target, "fixture_migrator", actor_password) as conn:
                return status_for(plan) | storage.operate(
                    conn, "status", NAME, UUID(DB_ID)
                )

        stage(tmp_path, plan, "baseline", foundation())
        run(before, "baseline")
        for socket, tools in ((metadata, metadata_tools), (target, psql)):
            root = Path(socket)
            tools(
                "pg_ctl",
                "-D",
                root / "data",
                "-l",
                root / "log",
                "-w",
                "-t",
                "20",
                "-m",
                "fast",
                "restart",
            )
        stage(tmp_path, plan, "readback", foundation())
        run(after, "readback")
        receipt = check.decode(check.private_bytes(tmp_path / "readback.json"))
        assert receipt["continuity"] == "matched"
        assert {
            name: view["count"] for name, view in receipt["observed"]["views"].items()
        } == plan["counts"]

        # An interrupted candidate cannot issue a matching receipt even if HTTPS answers.
        readback = tmp_path / "readback"
        runtime_path = readback / "runtime.json"
        runtime_path.unlink()
        write(runtime_path, {"state": "starting", "ready": False})
        with pytest.raises(check.Refused, match="android-runtime-not-ready"):
            check.stage_evidence(plan, "readback", readback)

        stage(tmp_path, plan, "rollback", foundation())
        run(before, "rollback")
        assert (
            check.decode(check.private_bytes(tmp_path / "rollback.json"))["continuity"]
            == "matched"
        )
        with connection(
            metadata, "fixture_metadata", metadata_password, "postgres"
        ) as conn:
            assert conn.execute(
                "SELECT count(*) FROM schemii.console_executions"
            ).fetchone() == (0,)
            assert conn.execute(
                "SELECT count(*) FROM schemii.console_query_history"
            ).fetchone() == (0,)
