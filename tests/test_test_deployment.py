import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _service(compose: str, name: str) -> str:
    lines = compose.splitlines()
    start = lines.index(f"  {name}:")
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("  ") and not lines[index].startswith("    ")
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_compose_keeps_postgres_private_and_never_mounts_docker_socket() -> None:
    compose = (ROOT / "compose.test.yaml").read_text(encoding="utf-8")
    schemii = _service(compose, "schemii")
    ingress = _service(compose, "ingress")
    metadata_postgres = _service(compose, "metadata-postgres")
    demo_postgres = _service(compose, "demo-postgres")
    metadata_bootstrap = _service(compose, "metadata-bootstrap")
    demo_bootstrap = _service(compose, "demo-bootstrap")
    networks = compose.split("\nnetworks:\n", 1)[1]

    assert "/docker.sock" not in compose
    assert "ports:" not in schemii
    assert "ports:" not in metadata_postgres
    assert "ports:" not in demo_postgres
    assert '"127.0.0.1:${SCHEMII_TEST_APP_PORT:-8001}:8443"' in ingress
    assert "/etc/nginx/tls/localhost.crt:ro" in ingress
    assert "/etc/nginx/tls/localhost.key:ro" in ingress
    assert '"${SCHEMII_TLS_READER_GID:-101}"' in ingress
    assert '"https://127.0.0.1:8443/api/v1/readiness"' in ingress
    assert "  database:\n    internal: true" in networks
    assert "  app-ingress:\n    internal: true" in networks
    assert "postgres:17-alpine@sha256:" in compose
    assert 'SCHEMII_DEVELOPER_INSPECTION: "1"' in schemii
    assert "SCHEMII_DEPLOYMENT_MODE: authenticated" in schemii
    assert 'SCHEMII_AUTH_ENABLED: "1"' in schemii
    assert (
        "SCHEMII_TARGET_EGRESS_MODE: ${SCHEMII_TARGET_EGRESS_MODE:-internal-only}"
        in schemii
    )
    assert (
        "SCHEMII_ALLOWED_TARGET_HOSTS: ${SCHEMII_ALLOWED_TARGET_HOSTS:-demo-postgres,postgres}"
        in schemii
    )
    assert "metadata-postgres" not in next(
        line for line in schemii.splitlines() if "SCHEMII_ALLOWED_TARGET_HOSTS" in line
    )
    assert "SCHEMII_STORAGE_MODE: postgresql" in schemii
    assert "SCHEMII_METADATA_DSN:" in schemii
    assert "host=metadata-postgres" in schemii
    assert (
        "SCHEMII_METADATA_PASSWORD_FILE: /run/secrets/metadata_app_password" in schemii
    )
    assert (
        "SCHEMII_METADATA_ENCRYPTION_KEY_FILE: /run/secrets/metadata_encryption_key"
        in schemii
    )
    assert "/run/secrets/metadata_app_password:ro" in schemii
    assert "/run/secrets/metadata_encryption_key:ro" in schemii
    assert "SCHEMII_METADATA_TARGET_HOST_ALIASES: metadata-postgres" in schemii
    assert '"${SCHEMII_SECRET_READER_GID:-10001}"' in schemii
    assert compose.count("ports:") == 1
    assert "condition: service_completed_successfully" in compose
    assert "condition: service_healthy\n        restart: true" in ingress
    assert "      - database\n      - app-ingress" in schemii
    assert "      - app-ingress\n      - loopback" in ingress
    assert "POSTGRES_USER: ${SCHEMII_TEST_POSTGRES_USER:-schemii}" in metadata_postgres
    assert "POSTGRES_USER: schemii_demo_admin" in demo_postgres
    assert "SCHEMII_METADATA_APP_USER:" in metadata_bootstrap
    assert "metadata_app_password" in metadata_bootstrap
    assert "condition: service_completed_successfully" in _service(
        compose, "postgres-seed"
    )
    assert "demo_target_password" in demo_bootstrap
    assert "schemii-test-postgres:/var/lib/postgresql/data" in metadata_postgres
    assert "schemii-test-demo-postgres:/var/lib/postgresql/data" in demo_postgres

    demo_fixture = _service(compose, "demo-fixture")
    assert (
        "SCHEMII_ALLOWED_TARGET_HOSTS: ${SCHEMII_ALLOWED_TARGET_HOSTS:-demo-postgres,postgres}"
        in demo_fixture
    )


def test_containerized_application_is_non_root_and_read_only() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "compose.test.yaml").read_text(encoding="utf-8")
    schemii = _service(compose, "schemii")
    ingress = _service(compose, "ingress")
    seed = _service(compose, "postgres-seed")
    metadata_bootstrap = _service(compose, "metadata-bootstrap")
    demo_bootstrap = _service(compose, "demo-bootstrap")

    assert "USER 10001:10001" in dockerfile
    assert "schemii.main:app" in dockerfile
    assert "--constraint constraints.docker.txt" in dockerfile
    assert "read_only: true" in schemii
    assert 'user: "101:101"' in ingress
    assert "read_only: true" in ingress
    assert "user: postgres" in seed
    assert "user: postgres" in metadata_bootstrap
    assert "user: postgres" in demo_bootstrap
    assert "no-new-privileges:true" in schemii
    assert "no-new-privileges:true" in ingress
    assert "no-new-privileges:true" in seed
    assert "no-new-privileges:true" in metadata_bootstrap
    assert "no-new-privileges:true" in demo_bootstrap


def test_pi_sidecar_is_default_and_has_no_chat_data_volume() -> None:
    compose = (ROOT / "compose.test.yaml").read_text(encoding="utf-8")
    runtime = _service(compose, "ai-prototype-runtime")
    app = _service(compose, "schemii")
    assert "  opencode:" not in compose
    assert "profiles:" not in runtime
    assert "read_only: true" in runtime
    assert "no-new-privileges:true" in runtime
    assert "ports:" not in runtime
    assert "/opencode/data" not in runtime
    assert "/var/run/docker.sock" not in compose
    assert "SCHEMII_PI_PROTOTYPE_URL: http://ai-prototype-runtime:4097" in app
    assert "ai-prototype-runtime:" in app


def test_database_roles_are_split_by_control_plane_and_demo_responsibility() -> None:
    compose = (ROOT / "compose.test.yaml").read_text(encoding="utf-8")
    metadata_bootstrap = (
        ROOT / "dev" / "postgres" / "metadata-bootstrap.sh"
    ).read_text(encoding="utf-8")
    demo_bootstrap = (ROOT / "dev" / "postgres" / "demo-bootstrap.sh").read_text(
        encoding="utf-8"
    )
    seed = (ROOT / "dev" / "postgres" / "seed.sh").read_text(encoding="utf-8")

    assert "metadata-postgres:" in compose
    assert "demo-postgres:" in compose
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE" in metadata_bootstrap
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE" in demo_bootstrap
    assert "ALTER ROLE %I LOGIN" in demo_bootstrap
    assert "ALTER DATABASE %I OWNER TO %I" in demo_bootstrap
    assert "ALTER SCHEMA metadata OWNER TO %I" in metadata_bootstrap
    assert "pg_get_function_identity_arguments" in metadata_bootstrap
    assert "object.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')" in metadata_bootstrap
    assert "dependency.deptype IN ('a', 'i')" in metadata_bootstrap
    assert "GRANT USAGE, CREATE ON SCHEMA metadata TO %I" in metadata_bootstrap
    assert 'dropdb --username "$SCHEMII_DEMO_ADMIN_USER"' in seed
    assert 'createdb --username "$SCHEMII_DEMO_ADMIN_USER" --owner "$PGUSER"' in seed


def test_ingress_terminates_local_https_and_private_keys_never_enter_the_image() -> (
    None
):
    nginx = (ROOT / "dev" / "ingress" / "nginx.conf").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert "listen 8443 ssl;" in nginx
    assert "ssl_certificate /etc/nginx/tls/localhost.crt;" in nginx
    assert "ssl_certificate_key /etc/nginx/tls/localhost.key;" in nginx
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in nginx
    assert "proxy_set_header Host localhost;" in nginx
    assert "proxy_set_header X-Forwarded-Host localhost;" in nginx
    assert "proxy_http_version 1.1;" in nginx
    assert "proxy_buffering off;" in nginx
    assert "proxy_request_buffering off;" in nginx
    assert "client_max_body_size 20m;" in nginx
    assert (
        'location ~ "^/api/v1/schemii/workspaces/[^/]+/console/sessions/[^/]+/copy/uploads/[^/]+$"'
        in nginx
    )
    assert "client_max_body_size 0;" in nginx
    copy_location = nginx.split('location ~ "^/api/v1/schemii/workspaces/', 1)[1]
    assert "client_max_body_size 0;" in copy_location
    assert "proxy_pass http://schemii_backend;" in copy_location
    assert ".schemii" in dockerignore


def test_seed_contains_archived_tutorial_and_catalog_coverage_namespaces() -> None:
    seed = (ROOT / "dev" / "postgres" / "seed.sql").read_text(encoding="utf-8")

    for expected in (
        "CREATE SCHEMA bookstore",
        "CREATE TABLE bookstore.order_items",
        "CREATE MATERIALIZED VIEW bookstore.monthly_sales",
        "CREATE SCHEMA catalog_lab",
        "PARTITION BY RANGE",
        "FOREIGN KEY (tenant_id, account_id)",
        "EXCLUDE USING gist",
        "CREATE PROCEDURE catalog_lab.remove_finished_jobs",
        "WITH NO DATA",
        "fixture=v1",
    ):
        assert expected in seed


def test_launcher_seeds_an_isolated_migration_demo_database() -> None:
    script = (ROOT / "dev" / "postgres" / "seed.sh").read_text(encoding="utf-8")
    launcher = (ROOT / "start.sh").read_text(encoding="utf-8")
    compose = (ROOT / "compose.test.yaml").read_text(encoding="utf-8")
    demo = (ROOT / "dev" / "postgres" / "migration-demo.sql").read_text(
        encoding="utf-8"
    )

    assert 'demo_database="schemii_migration_demo"' in script
    assert '--if-exists --force "$demo_database"' in script
    assert "--reset-migration-demo" in launcher
    assert "SCHEMII_RESET_MIGRATION_DEMO" in compose
    assert "CREATE TABLE public.tasks" in demo
    assert "CREATE TABLE public.task_comments" in demo
    assert "CREATE TABLE public.empty_migration_lab" in demo
    assert "CREATE INDEX tasks_assignee_due_idx" in demo


def test_demo_reset_rebuilds_only_the_reserved_target_and_its_metadata() -> None:
    launcher = (ROOT / "start.sh").read_text(encoding="utf-8")
    compose = (ROOT / "compose.test.yaml").read_text(encoding="utf-8")
    seed = (ROOT / "dev" / "postgres" / "seed.sh").read_text(encoding="utf-8")
    fixture = (ROOT / "dev" / "postgres" / "demo-fixture.py").read_text(
        encoding="utf-8"
    )

    assert "--reset-demo [SCENARIO]" in launcher
    assert "--list-demo-scenarios" in launcher
    assert "SCHEMII_DEMO_SOURCE_REVISION" in launcher
    assert '--if-exists --force "$demo_database"' in seed
    assert 'profiles: ["demo-fixture"]' in compose
    assert "python /fixture/demo-fixture.py" in compose
    assert "LOCAL_PROTOTYPE_USER_ID" in fixture
    assert "schemii.workspace_targets" in fixture
    assert "metadata.postgres_connections" in fixture
    assert "TRUNCATE" not in fixture.upper()
    assert "DROP SCHEMA" not in fixture.upper()


def test_demo_scenarios_are_saved_and_runtime_provenanced() -> None:
    scenarios = ROOT / "dev" / "postgres" / "demo-scenarios"
    expected = {
        "baseline",
        "column-migrations",
        "column-order",
        "compatible-drift",
        "conflicting-drift",
        "live-browser",
        "sql-console",
        "type-conversions",
        "undo-redo",
    }
    assert {path.parent.name for path in scenarios.glob("*/manifest.json")} == expected
    for scenario in expected:
        manifest = __import__("json").loads(
            (scenarios / scenario / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["id"] == scenario
        assert manifest["sourceRevision"] == "runtime"
        assert (scenarios / scenario / manifest["targetAlteration"]).is_file()
        for alteration in manifest["designAlterations"]:
            assert (scenarios / scenario / alteration).is_file()
        for query in manifest["consoleQueries"]:
            assert set(query) == {"name", "file"}
            assert (scenarios / scenario / query["file"]).is_file()

    compatible = (scenarios / "compatible-drift" / "target.sql").read_text(
        encoding="utf-8"
    )
    conflicting = (scenarios / "conflicting-drift" / "target.sql").read_text(
        encoding="utf-8"
    )
    assert "ADD COLUMN external_reference" in compatible
    assert "ADD COLUMN migration_note" in conflicting

    console_manifest = __import__("json").loads(
        (scenarios / "sql-console" / "manifest.json").read_text(encoding="utf-8")
    )
    assert len(console_manifest["consoleQueries"]) == 7
    assert console_manifest["consoleQueries"][0]["name"] == "00 · Test guide"

    column_migrations = __import__("json").loads(
        (scenarios / "column-migrations" / "design-01.json").read_text(encoding="utf-8")
    )
    assert column_migrations["changes"] == [
        {
            "operation": "addColumn",
            "table": "empty_migration_lab",
            "column": "required_code",
            "dataType": "text",
            "nullable": False,
        },
        {
            "operation": "setColumnType",
            "table": "teams",
            "column": "slug",
            "dataType": "character varying(160)",
        },
    ]

    undo_redo = __import__("json").loads(
        (scenarios / "undo-redo" / "manifest.json").read_text(encoding="utf-8")
    )
    view_change = __import__("json").loads(
        (scenarios / "undo-redo" / "design-03.json").read_text(encoding="utf-8")
    )
    assert undo_redo["designAlterations"][-1] == "design-03.json"
    assert view_change["changes"][0]["operation"] == "addView"
    assert view_change["changes"][0]["name"] == "project_workload"

    live_browser = __import__("json").loads(
        (scenarios / "live-browser" / "manifest.json").read_text(encoding="utf-8")
    )
    live_browser_sql = (scenarios / "live-browser" / "target.sql").read_text(
        encoding="utf-8"
    )
    assert "workspaceMode" not in live_browser
    assert "CREATE VIEW public.team_delivery_health" in live_browser_sql
    assert "CREATE MATERIALIZED VIEW public.priority_queue" in live_browser_sql


def _named_step(job: str, name: str) -> str:
    return job.split(f"      - name: {name}\n", 1)[1].split("\n      - ", 1)[0]


def _step_run(step: str) -> str:
    value = re.search(r"^        run: (.+)$", step, re.MULTILINE)
    assert value is not None, "The required CI step must execute a command"
    if value[1] == "|":
        return "\n".join(
            line[10:]
            for line in step[value.end() :].splitlines()
            if line.startswith("          ")
        )
    return value[1]


@pytest.mark.parametrize("include_load", [False, True])
def test_ci_executes_unit_browser_and_real_postgres_behavior(
    tmp_path: Path, include_load: bool
) -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    unit = _service(workflow, "test")
    node_step = _named_step(unit, "Fast frontend and harness feedback")
    output_value = re.search(
        r"^          CI_TELEMETRY_FILE: (.+)$", node_step, re.MULTILINE
    )
    assert output_value is not None, "CI must instrument the canonical Node command"
    output = tmp_path / output_value[1]

    # Execute the actual CI command in a tiny checkout containing one test from
    # each intended family. Product/E2E and load-execution files deliberately
    # fail if discovered. This catches a reduced CI alias or lost load glob,
    # rather than accepting an arbitrary mention of `npm test` in the workflow.
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    (tmp_path / "package.json").write_text(
        json.dumps({"type": "module", "scripts": package["scripts"]})
    )
    families = {
        "tests/frontend": "frontend",
        "testing/harness": "harness",
        "scripts/ci": "telemetry",
    }
    if include_load:
        families["testing/load"] = "load"
    expected_sources = set()
    for directory, name in families.items():
        target = tmp_path / directory
        target.mkdir(parents=True)
        filename = "sentinel.test.js" if name == "frontend" else "sentinel.test.mjs"
        source = f"{directory}/{filename}"
        expected_sources.add(hashlib.sha256(source.encode()).hexdigest())
        (target / filename).write_text(
            f"import {{ test }} from 'node:test'; test('{name}', () => {{}});\n"
        )
    for filename in ("node-tests.mjs", "node-reporter.mjs", "timing.mjs"):
        shutil.copyfile(
            ROOT / "scripts/ci" / filename, tmp_path / "scripts/ci" / filename
        )
    excluded = tmp_path / "tests/e2e/should-not-run.spec.js"
    excluded.parent.mkdir(parents=True)
    excluded.write_text("throw new Error('Browser acceptance must remain opt-in');\n")
    if include_load:
        (tmp_path / "testing/load/stress.mjs").write_text(
            "throw new Error('Load execution must remain opt-in');\n"
        )
    env = {
        **os.environ,
        "CI_TELEMETRY_SHA": "a" * 40,
        "CI_TELEMETRY_RUN_ID": "1",
        "CI_TELEMETRY_RUN_ATTEMPT": "1",
        "CI_TELEMETRY_FILE": output_value[1],
    }
    ordinary_env = {
        key: value for key, value in env.items() if key != "CI_TELEMETRY_FILE"
    }
    ordinary = subprocess.run(
        ["npm", "test"],
        cwd=tmp_path,
        env=ordinary_env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert ordinary.returncode == 0 and not output.exists()
    assert re.findall(r"^(?:#|ℹ) tests (\d+)\s*$", ordinary.stdout, re.MULTILINE) == [
        str(len(families))
    ]
    instrumented = subprocess.run(
        ["bash", "-e", "-c", _step_run(node_step)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert instrumented.returncode == 0
    from scripts.ci.summary import load, summarize

    records = load(output)
    summary = summarize(records)
    attempts = [record for record in records if record["kind"] == "attempt"]
    assert {record["source_id"] for record in attempts} == expected_sources
    assert (
        summary["collected"]
        == summary["attempts"]
        == summary["attempt_outcomes"]["passed"]
        == len(families)
    )
    assert summary["complete"] is True
    for name in families.values():
        assert name in ordinary.stdout and name in instrumented.stdout

    (tmp_path / "tests/frontend/sentinel.test.js").write_text(
        "import { test } from 'node:test'; test('frontend', () => { throw new Error('planted failure'); });\n"
    )
    failed = subprocess.run(
        ["bash", "-e", "-c", _step_run(node_step)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert failed.returncode == 1
    failed_summary = summarize(load(output))
    assert failed_summary["outcome"] == "failed" and failed_summary["complete"] is True
    assert failed_summary["attempt_outcomes"]["failed"] == 1

    python_step = _named_step(unit, "Deterministic Python behavior")
    python_args = shlex.split(_step_run(python_step))
    assert python_args[:3] == ["python", "-m", "pytest"]
    assert not any(
        argument.split("=", 1)[0]
        in {"-k", "-m", "--collect-only", "--ignore", "--deselect"}
        for argument in python_args[3:]
    )

    postgres = _service(workflow, "postgres-integration")
    postgres_step = _named_step(
        postgres,
        "Exercise metadata migrations, credentials, isolation, and atomic imports",
    )
    postgres_args = shlex.split(_step_run(postgres_step))
    assert postgres_args[:3] == ["python", "-m", "pytest"]
    assert "tests/integration" in postgres_args[3:]
    assert not any(
        argument.split("=", 1)[0]
        in {"-k", "-m", "--collect-only", "--ignore", "--deselect"}
        for argument in postgres_args[3:]
    )
    assert (
        "postgres:17-alpine@sha256:" in postgres
        and "SCHEMII_TEST_METADATA_DSN:" in postgres_step
    )
    browser = _service(workflow, "browser-smoke")
    assert shlex.split(
        _step_run(_named_step(browser, "Start the canonical application stack"))
    ) == ["./start.sh"]
    assert shlex.split(_step_run(_named_step(browser, "Exercise browser flows")))[
        :3
    ] == ["npm", "run", "test:e2e"]
    assert shlex.split(package["scripts"]["test:e2e"]) == ["playwright", "test"]
