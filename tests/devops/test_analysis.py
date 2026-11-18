"""Read-only analysis controls with synthetic facts in an opt-in private cluster."""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import shlex
import subprocess
from tempfile import TemporaryDirectory
from typing import TypedDict
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from scripts.devops import analysis, storage
from schemii.common.connections.models import ResolvedPostgresConnection
from schemii.common.postgres.console.models import ConsoleExecutionCreate
from schemii.common.postgres.gateway import PsycopgPostgresGateway
from schemii.schemii.ai.tools import normalize_tool_call

START = "2026-01-01T00:00:00Z"
END = "2026-02-01T00:00:00Z"
RUN_TIME = "2026-01-07T00:00:00Z"
OBSERVED = "2026-01-10T00:00:00Z"
REVISED = "2026-01-11T00:00:00Z"


class FixtureConnection(TypedDict):
    host: str
    port: int
    dbname: str
    passfile: str
    connect_timeout: int


def emit(capsys, recipe, repository, installation, *, command=None, format="sql"):
    args = [
        "sql",
        recipe,
        "--repository-id",
        str(repository),
        "--installation-id",
        str(installation),
        "--from",
        START,
        "--until",
        END,
        "--format",
        format,
    ]
    if command:
        args += ["--command-id", str(command)]
    assert analysis.main(args) == 0
    return capsys.readouterr().out


def test_catalog_and_sql_scope(capsys):
    assert analysis.main(["catalog", "--json"]) == 0
    catalog = json.loads(capsys.readouterr().out)
    assert catalog["definition_version"] == 1
    assert catalog["minimum_schema_version"] == 2
    assert catalog["agent_tool"] == "schemii_read_query"
    assert set(catalog["recipes"]) == set(analysis.RECIPES)
    repo, install = uuid4(), uuid4()
    query = emit(capsys, "verification", repo, install)
    assert f"'{repo}'::uuid" in query
    assert f"'{install}'::uuid" in query
    assert (
        "d.run_started_at >= s.window_start AND d.run_started_at < s.window_end"
        in query
    )
    assert analysis.instant("2026-01-01T01:00:00+01:00") == analysis.instant(START)


@pytest.mark.parametrize(
    ("recipe", "repo", "start", "end", "extra"),
    [
        ("health", "bad'::uuid", START, END, []),
        ("health", str(uuid4()), "2026-01-01", END, []),
        ("health", str(uuid4()), END, START, []),
        ("health", str(uuid4()), START, END, ["--command-id", str(uuid4())]),
        ("attempt-contributors", str(uuid4()), START, END, []),
    ],
)
def test_refuses_unsafe_or_ambiguous_scope(capsys, recipe, repo, start, end, extra):
    with pytest.raises(SystemExit) as exc:
        analysis.main(
            [
                "sql",
                recipe,
                "--repository-id",
                repo,
                "--installation-id",
                str(uuid4()),
                "--from",
                start,
                "--until",
                end,
                *extra,
            ]
        )
    assert exc.value.code == 2
    assert capsys.readouterr().out == ""


@pytest.fixture(scope="module")
def database():
    if os.environ.get("SCHEMII_DEVOPS280_POSTGRES") != "1":
        pytest.skip(
            "opt in to a private synthetic PostgreSQL cluster with SCHEMII_DEVOPS280_POSTGRES=1"
        )

    # Use a fresh short socket path, exclude ambient libpq target settings, and
    # authenticate with a throwaway password whose files remain in this directory.
    name, identity = "devops_analytics_issue280", uuid4()
    with (
        TemporaryDirectory(prefix="sc280-") as directory,
        pytest.MonkeyPatch.context() as environment,
    ):
        for key in list(os.environ):
            if key.startswith("PG"):
                environment.delenv(key)
        root = Path(directory)
        data = root / "data"
        password = secrets.token_hex(24)
        pwfile, passfile = root / "init-password", root / "pgpass"
        pwfile.touch(mode=0o600)
        pwfile.write_text(password + "\n")
        passfile.touch(mode=0o600)
        passfile.write_text(f"*:*:*:*:{password}\n")
        env = dict(os.environ, PGPASSFILE=str(passfile))
        subprocess.run(
            [
                "initdb",
                "-D",
                str(data),
                "-U",
                "fixture_admin",
                "--auth=scram-sha-256",
                f"--pwfile={pwfile}",
                "--no-locale",
                "--encoding=UTF8",
            ],
            check=True,
            env=env,
            timeout=45,
        )
        control = ["pg_ctl", "-D", str(data)]
        try:
            subprocess.run(
                [
                    *control,
                    "-w",
                    "-t",
                    "15",
                    "-l",
                    str(root / "postgres.log"),
                    "-o",
                    f"-c listen_addresses='' -k {shlex.quote(str(root))}",
                    "start",
                ],
                check=True,
                env=env,
                timeout=20,
            )
            subprocess.run(
                [
                    "psql",
                    "-X",
                    "-w",
                    "-h",
                    str(root),
                    "-p",
                    "5432",
                    "-U",
                    "fixture_admin",
                    "-d",
                    "postgres",
                    "-v",
                    f"db_name={name}",
                    "-v",
                    f"database_id={identity}",
                    "-f",
                    str(Path(storage.__file__).with_name("provision.sql")),
                ],
                check=True,
                env=env,
                timeout=20,
            )
            config = FixtureConnection(
                host=str(root),
                port=5432,
                dbname=name,
                passfile=str(passfile),
                connect_timeout=5,
            )
            with psycopg.connect(
                **config, user="fixture_admin", autocommit=True
            ) as admin:
                assert admin.execute("SHOW listen_addresses").fetchall() == [("",)]
                for role in ("migrator", "collector", "reader"):
                    admin.execute(
                        sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                            sql.Identifier(f"fixture_{role}"),
                            sql.Literal(password),
                        )
                    )
                    admin.execute(
                        sql.SQL("GRANT {} TO {}").format(
                            sql.Identifier(f"devops_{role}"),
                            sql.Identifier(f"fixture_{role}"),
                        )
                    )
            with psycopg.connect(
                **config, user="fixture_migrator", autocommit=True
            ) as migrator:
                original = storage.migration_files()
                with pytest.MonkeyPatch.context() as patch:
                    patch.setattr(storage, "migration_files", lambda: original[:1])
                    assert storage.operate(migrator, "migrate", name, identity)[
                        "applied_versions"
                    ] == [1]
                with pytest.MonkeyPatch.context() as patch:
                    patch.setattr(storage, "migration_files", lambda: original[:2])
                    assert storage.operate(migrator, "plan", name, identity)[
                        "pending_versions"
                    ] == [2]
                    assert storage.operate(migrator, "migrate", name, identity)[
                        "applied_versions"
                    ] == [2]
                    assert (
                        storage.operate(migrator, "migrate", name, identity)[
                            "applied_versions"
                        ]
                        == []
                    )
            yield config
        finally:
            if (data / "postmaster.pid").exists():
                subprocess.run(
                    [*control, "-w", "-t", "15", "-m", "fast", "stop"],
                    check=True,
                    env=env,
                    timeout=20,
                )


def insert(conn, table, row):
    conn.execute(
        sql.SQL("INSERT INTO devops.{} ({}) VALUES ({})").format(
            sql.Identifier(table),
            sql.SQL(", ").join(map(sql.Identifier, row)),
            sql.SQL(", ").join(sql.Placeholder() for _ in row),
        ),
        list(row.values()),
    )
    return row


def create_facts(conn, *, copy_from=None):
    repository = copy_from["repository"] if copy_from else uuid4()
    if copy_from is None:
        insert(
            conn,
            "repositories",
            dict(
                id=repository,
                provider="local",
                repository_key=f"synthetic-{repository.hex}",
                display_name="synthetic",
            ),
        )
    installation = uuid4()
    facts = dict(repository=repository, installation=installation)
    common = dict(
        repository_id=repository,
        installation_id=installation,
        revision=1,
        change_kind="initial",
        observed_at=OBSERVED,
        evidence_ref="fixture-original",
        evidence_sha256="a" * 64,
    )

    def row(table, key, **fields):
        id_ = copy_from[key]["id"] if copy_from else uuid4()
        facts[key] = insert(conn, table, dict(common, id=id_, source_key=key) | fields)
        return facts[key]

    batch = row(
        "import_batches",
        "batch",
        source_kind="local-ci-v1",
        started_at=OBSERVED,
        finished_at=OBSERVED,
        status="partial",
        accepted_count=7,
        rejected_count=0,
    )
    common.update(import_batch_id=batch["id"], import_batch_revision=1)

    def run(key, **fields):
        return row(
            "ci_runs",
            key,
            **(
                dict(
                    started_at=RUN_TIME,
                    finished_at="2026-01-07T00:20:00Z",
                    profile="full",
                    mode="selected",
                    duration_ms=6000,
                    outcome="failed",
                    completeness="partial",
                    acceptance=False,
                )
                | fields
            ),
        )

    def command(parent, key, slot, **fields):
        return row(
            "commands",
            key,
            **(
                dict(
                    run_id=parent["id"],
                    run_revision=1,
                    slot_key=slot,
                    command_key="python",
                    disposition="executed",
                    expected_cases=5,
                    outcome="failed",
                    duration_ms=1200,
                )
                | fields
            ),
        )

    first = run("run")
    second = run("run_other", duration_ms=7000)
    boundary = run("run_boundary", started_at=END, finished_at=END)
    run("run_no_clock", started_at=None, finished_at=None, duration_ms=None)
    run("feedback", mode="feedback", outcome="feedback-passed")
    unstable = run("run_unstable", outcome="source-changed")
    producer = command(first, "command", "1")
    shorter = command(second, "command_other", "1", duration_ms=250)
    command(
        second,
        "reused",
        "2",
        disposition="reused",
        outcome=None,
        duration_ms=None,
        donor_command_id=producer["id"],
        donor_command_revision=1,
    )
    command(
        first,
        "pending",
        "2",
        disposition="pending",
        duration_ms=None,
        expected_cases=None,
        outcome=None,
    )
    command(
        first,
        "excluded",
        "3",
        disposition="excluded",
        duration_ms=None,
        expected_cases=None,
        outcome=None,
    )
    command(
        first,
        "unknown",
        "4",
        command_key="static",
        duration_ms=None,
        expected_cases=None,
        outcome=None,
    )
    command(
        first,
        "open_slot",
        "5",
        command_key="static",
        duration_ms=10,
        expected_cases=None,
        outcome="running",
    )
    command(
        first,
        "zero",
        "6",
        command_key="static",
        duration_ms=0,
        expected_cases=0,
        outcome="passed",
    )
    command(unstable, "source_changed", "1", duration_ms=9000)
    command(boundary, "command_boundary", "1", duration_ms=99999)

    def attempt(key, parent, case, ordinal, outcome):
        return row(
            "test_attempts",
            key,
            command_id=parent["id"],
            command_revision=1,
            case_key=case * 64,
            attempt_number=ordinal,
            outcome=outcome,
        )

    attempt("first_failure", producer, "b", 0, "failed")
    attempt("retry_failure", producer, "b", 1, "failed")
    attempt("retry_pass", producer, "b", 2, "passed")
    attempt("first_pass", producer, "c", 0, "passed")
    attempt("first_skip", producer, "d", 0, "skipped")
    attempt("missing_zero", producer, "e", 1, "passed")
    attempt("timed_out", shorter, "b", 0, "timed-out")
    attempt("other_pass", shorter, "c", 0, "passed")

    return facts


def query(reader, capsys, recipe, facts, *, command=None):
    return reader.execute(
        emit(
            capsys, recipe, facts["repository"], facts["installation"], command=command
        )
    ).fetchall()


@pytest.fixture
def session(database):
    with (
        psycopg.connect(
            **database, user="fixture_collector", autocommit=True
        ) as collector,
        psycopg.connect(
            **database, user="fixture_reader", autocommit=True, row_factory=dict_row
        ) as reader,
    ):
        reader.execute("SET default_transaction_read_only = on")
        yield collector, reader


def test_reports_exact_coverage_timing_and_observed_retry_denominators(session, capsys):
    collector, reader = session
    facts = create_facts(collector)
    create_facts(collector, copy_from=facts)
    slots = {r["command_id"]: r for r in query(reader, capsys, "verification", facts)}
    command_id = facts["command"]["id"]
    main = slots[command_id]
    assert len(slots) == 9
    assert main["definition_version"] == 1 and main["as_of"] is not None
    assert main["revision"] == main["current_run_revision"] == main["run_revision"] == 1
    assert main["observed_cases"] == 4 and main["expected_cases"] == 5
    assert (main["missing_case_observations"], main["case_coverage_state"]) == (
        1,
        "missing-observations",
    )
    assert (
        main["first_failures"]
        == main["first_passes"]
        == main["recovered_first_failures"]
        == 1
    )
    assert main["eligible_first_attempt_cases"] == 2
    assert (
        main["excluded_first_attempt_cases"] == main["unknown_first_attempt_cases"] == 1
    )
    assert main["cases_with_retries"] == 2
    assert (
        main["first_failure_fraction"]
        == main["first_pass_fraction"]
        == main["retry_recovery_fraction"]
        == 0.5
    )
    assert main["recovery_given_failure_fraction"] == 1
    assert (
        main["producer_acceptance"] is False
        and main["pinned_import_status"] == "partial"
    )
    reused = slots[facts["reused"]["id"]]
    assert (
        reused["donor_command_id"] == command_id
        and reused["donor_command_revision"] == 1
    )
    assert reused["observed_cases"] is None and reused["duration_ms"] is None
    assert (
        reused["eligible_first_attempt_cases"] == 0
        and reused["first_failure_fraction"] is None
    )
    assert slots[facts["pending"]["id"]]["verification_state"] == "pending"
    assert slots[facts["excluded"]["id"]]["verification_state"] == "excluded"
    assert slots[facts["unknown"]["id"]]["case_coverage_state"] == "unknown-inventory"
    assert slots[facts["zero"]["id"]]["case_coverage_state"] == "empty-inventory"
    assert (
        slots[facts["source_changed"]["id"]]["verification_state"] == "invalid-source"
    )

    runs = {r["id"]: r for r in query(reader, capsys, "run-coverage", facts)}
    assert len(runs) == 4
    first = runs[facts["run"]["id"]]
    assert (
        first["observed_slots"],
        first["executed_slots"],
        first["pending_slots"],
        first["excluded_slots"],
    ) == (6, 4, 1, 1)
    assert runs[facts["run_other"]["id"]]["reused_slots"] == 1
    feedback = runs[facts["feedback"]["id"]]
    assert (
        feedback["slot_coverage_state"] == "no-visible-slots"
        and feedback["acceptance"] is False
    )

    slow = query(reader, capsys, "slow-commands", facts)
    python = next(r for r in slow if r["outcome"] == "failed")
    assert (
        python["observations"],
        python["duration_samples"],
        python["excluded_durations"],
    ) == (3, 2, 1)
    assert (python["p50_ms"], python["p90_ms"], python["max_ms"]) == (250, 1200, 1200)
    assert python["cohort_key"] is None
    assert next(r for r in slow if r["outcome"] == "running")["open_executions"] == 1
    assert next(r for r in slow if r["outcome"] == "passed")["p90_ms"] == 0
    run_times = query(reader, capsys, "run-elapsed", facts)
    assert (
        next(r for r in run_times if r["outcome"] == "failed")["duration_samples"] == 2
    )
    assert (
        next(r for r in run_times if r["outcome"] == "source-changed")["max_ms"] is None
    )

    top = query(reader, capsys, "case-hotspots", facts)[0]
    assert top["case_key"] == "b" * 64
    assert top["eligible_first_attempts"] == top["first_failures"] == 2
    assert top["first_failure_fraction"] == 1
    assert (
        top["recovered_first_failures"] == 1
        and top["recovery_given_failure_fraction"] == 0.5
    )
    contributors = query(
        reader, capsys, "attempt-contributors", facts, command=command_id
    )
    assert len(contributors) == 6
    assert [r["outcome"] for r in contributors if r["case_key"] == top["case_key"]] == [
        "failed",
        "failed",
        "passed",
    ]
    assert (
        contributors[0]["evidence_ref"] == "fixture-original"
        and contributors[0]["command_revision"] == 1
    )


def revise(conn, table, row, **fields):
    return insert(
        conn,
        table,
        dict(
            row,
            revision=row["revision"] + 1,
            observed_at=REVISED,
            change_kind="correction",
            evidence_ref="fixture-revised",
        )
        | fields,
    )


def test_current_revisions_retractions_and_pinned_lineage(session, capsys):
    collector, reader = session
    facts = create_facts(collector)
    revise(collector, "ci_runs", facts["run"], duration_ms=700)
    command = revise(
        collector, "commands", facts["command"], run_revision=2, duration_ms=500
    )
    revised_failure = revise(
        collector,
        "test_attempts",
        facts["first_failure"],
        command_revision=2,
        outcome="timed-out",
    )
    revise(collector, "test_attempts", facts["retry_pass"], change_kind="retraction")
    rows = query(reader, capsys, "attempt-contributors", facts, command=command["id"])
    assert len(rows) == 5
    expected = {
        revised_failure["id"]: (2, 2, "timed-out"),
        facts["retry_failure"]["id"]: (1, 1, "failed"),
    }
    assert {
        a["attempt_id"]: (a["revision"], a["command_revision"], a["outcome"])
        for a in rows
        if a["case_key"] == "b" * 64
    } == expected
    result = next(
        r
        for r in query(reader, capsys, "verification", facts)
        if r["command_id"] == command["id"]
    )
    assert result["current_run_revision"] == result["run_revision"] == 2
    assert result["first_failures"] == 1 and result["recovered_first_failures"] == 0

    revise(collector, "test_attempts", revised_failure, change_kind="retraction")
    result = next(
        r
        for r in query(reader, capsys, "verification", facts)
        if r["command_id"] == command["id"]
    )
    assert result["first_failures"] == 0 and result["unknown_first_attempt_cases"] == 2
    revise(collector, "commands", command, change_kind="retraction")
    assert (
        query(reader, capsys, "attempt-contributors", facts, command=command["id"])
        == []
    )
    # Retracting a donor preserves the pinned edge without adding evidence to reuse.
    reused = next(
        r
        for r in query(reader, capsys, "verification", facts)
        if r["disposition"] == "reused"
    )
    assert (
        reused["donor_command_id"] == command["id"]
        and reused["donor_command_revision"] == 1
    )
    revise(
        collector, "ci_runs", facts["run"] | {"revision": 2}, change_kind="retraction"
    )
    assert all(
        r["run_id"] != facts["run"]["id"]
        for r in query(reader, capsys, "verification", facts)
    )


def test_collection_health_distinguishes_empty_partial_and_complete(session, capsys):
    collector, reader = session
    facts = create_facts(collector)
    absent = facts | {"installation": uuid4()}
    empty = query(reader, capsys, "health", absent)[0]
    assert empty["runs_in_window"] == empty["import_batches_in_window"] == 0
    assert (
        empty["source_window_coverage"] == "unknown"
        and empty["last_batch_status"] is None
    )
    assert query(reader, capsys, "verification", absent) == []

    health = query(reader, capsys, "health", facts)[0]
    assert health["runs_in_window"] == 4 and health["runs_without_start"] == 1
    assert (
        health["last_batch_status"] == "partial"
        and health["last_complete_import_finished_at"] is None
    )
    completed = revise(
        collector,
        "import_batches",
        facts["batch"],
        status="complete",
        finished_at=REVISED,
    )
    later = insert(
        collector,
        "import_batches",
        dict(
            facts["batch"],
            id=uuid4(),
            source_key="later",
            observed_at="2026-01-12T00:00:00Z",
        ),
    )
    health = query(reader, capsys, "health", facts)[0]
    assert health["import_batches_in_window"] == 2 and health["complete_batches"] == 1
    assert (
        health["last_batch_id"] == later["id"]
        and health["last_batch_status"] == "partial"
    )
    assert health["last_complete_import_finished_at"] == analysis.instant(REVISED)
    assert {
        r["id"]: r["revision"] for r in query(reader, capsys, "batches", facts)
    } == {completed["id"]: 2, later["id"]: 1}
    revise(collector, "import_batches", completed, change_kind="retraction")
    assert (
        query(reader, capsys, "health", facts)[0]["last_complete_import_finished_at"]
        is None
    )


def test_reader_grants_and_private_inputs(database):
    with psycopg.connect(**database, user="fixture_reader", autocommit=True) as reader:
        assert reader.execute(
            "SELECT count(*) FROM devops.v1_ci_analysis_definitions"
        ).fetchall() == [(9,)]
        grants = reader.execute(
            "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
            "WHERE table_schema = 'devops' AND grantee = 'devops_reader' "
            "AND table_name LIKE 'v1_ci_%'"
        ).fetchall()
        assert set(grants) == {
            (view, "SELECT")
            for view in (
                "v1_ci_runs",
                "v1_ci_command_elapsed",
                "v1_ci_verification",
                "v1_ci_run_coverage",
                "v1_ci_case_first_attempts",
                "v1_ci_analysis_definitions",
            )
        }
        for statement in (
            "SELECT * FROM devops.current_commands",
            "SELECT * FROM devops.test_attempts",
            "SELECT * FROM devops_control.migrations",
            "UPDATE devops.commands SET duration_ms = 1",
            "CREATE TABLE devops.forbidden(id integer)",
            "CREATE TEMP TABLE forbidden(id integer)",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                reader.execute(statement)


def test_console_and_assistant_match_through_the_shared_managed_reader(
    database, session, capsys
):
    collector, _reader = session
    facts = create_facts(collector)
    repo, installation = facts["repository"], facts["installation"]
    gateway = PsycopgPostgresGateway(
        connect_factory=lambda **parameters: psycopg.connect(
            **(parameters | {"row_factory": dict_row, "passfile": database["passfile"]})
        )
    )
    connection = ResolvedPostgresConnection(
        id=f"pg_{uuid4().hex}",
        owner_id="synthetic-owner",
        name="Synthetic CI analytics",
        host=database["host"],
        port=database["port"],
        database=database["dbname"],
        username="fixture_reader",
        revision=1,
    )

    def result(statement):
        # Same cursor path used by Console and by the assistant's read execution.
        read = gateway.open_console_read_session(
            connection,
            "devops",
            [statement],
            on_started=lambda _: True,
            page_memory_bytes=1024 * 1024,
        )
        try:
            columns = [c.name for c in read.results[0].columns]
            rows = [dict(zip(columns, row)) for row in read.page(0, 0, 32)]
            assert len(rows) < 32 and read.page(0, len(rows), 1) == ()
            for row in rows:
                assert row.pop("as_of")
            return rows
        finally:
            read.close()

    for recipe, command in (
        ("verification", None),
        ("case-hotspots", None),
        ("attempt-contributors", facts["command"]["id"]),
    ):
        human = ConsoleExecutionCreate(
            console_id=f"con_{uuid4().hex}",
            expected_workspace_revision=1,
            expected_settings_revision=1,
            mode="managed_read",
            statements=[emit(capsys, recipe, repo, installation, command=command)],
        )
        payload = json.loads(
            emit(
                capsys, recipe, repo, installation, command=command, format="agent-json"
            )
        )
        agent = normalize_tool_call("schemii_read_query", payload)
        assert agent.action["queries"][0]["sql"] == human.statements[0]
        human_rows = result(human.statements[0])
        assert human_rows == result(agent.action["queries"][0]["sql"])
        assert (
            len(human_rows)
            == {"verification": 9, "case-hotspots": 4, "attempt-contributors": 6}[
                recipe
            ]
        )
        if recipe == "attempt-contributors":
            assert human_rows[0]["attempt_id"] == str(facts["first_failure"]["id"])
            assert human_rows[0]["evidence_ref"] == "fixture-original"
            assert human_rows[0]["evidence_sha256"] == "a" * 64
