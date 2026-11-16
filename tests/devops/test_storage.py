"""Opt-in real PostgreSQL controls; never discover or start an existing service."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

import psycopg
from psycopg import sql
import pytest

from scripts.devops import storage


@pytest.fixture(scope="module")
def fixture():
    marker = os.environ.get("SCHEMII_DEVOPS278_FIXTURE")
    if not marker:
        pytest.skip("explicit owned devops278 fixture marker not configured")
    path = Path(marker)
    root = Path("/home/kasey/.cache/schemii-devops278")
    assert path.resolve().is_relative_to(root) and not path.is_symlink()
    record = json.loads(path.read_text())
    assert record["owner"] == "devops278-storage-foundation-v1"
    assert Path(record["root"]).is_relative_to(root)
    assert (
        Path(f"/proc/{record['pid']}/stat").read_text().split()[21]
        == record["start_ticks"]
    )
    profiles = {
        r: storage.connection_config(Path(record["root"]) / f"{r}.json")
        for r in ("admin", "migrator", "collector", "reader", "maintenance")
    }
    with psycopg.connect(**profiles["admin"], autocommit=True) as conn:
        assert conn.execute("SHOW data_directory").fetchall()[0][0] == record["data"]
        assert conn.execute("SHOW listen_addresses").fetchall()[0][0] == ""
        assert (
            conn.execute("SHOW unix_socket_directories").fetchall()[0][0]
            == record["socket"]
        )
    return record, profiles


def operate(fixture, action="status", *, database_id=None, database_name=None):
    record, profiles = fixture
    with psycopg.connect(**profiles["migrator"], autocommit=True) as conn:
        return storage.operate(
            conn,
            action,
            database_name or record["database"],
            uuid.UUID(database_id or record["database_id"]),
        )


@pytest.fixture
def collector(fixture):
    with psycopg.connect(**fixture[1]["collector"], autocommit=True) as conn:
        with conn.transaction(force_rollback=True):
            yield conn


def insert(conn, table, row):
    query = sql.SQL("INSERT INTO devops.{} ({}) VALUES ({})").format(
        sql.Identifier(table),
        sql.SQL(",").join(map(sql.Identifier, row)),
        sql.SQL(",").join(sql.Placeholder() for _ in row),
    )
    return conn.execute(query, list(row.values())).rowcount


def seed(conn):
    repo, installation, batch, run, command = [uuid.uuid4() for _ in range(5)]
    insert(
        conn,
        "repositories",
        {
            "id": repo,
            "provider": "local",
            "repository_key": "fixture-" + repo.hex,
            "display_name": "fixture-only",
        },
    )
    common = {
        "repository_id": repo,
        "installation_id": installation,
        "revision": 1,
        "change_kind": "initial",
        "observed_at": "2026-11-16T00:00:00Z",
        "evidence_sha256": "a" * 64,
        "evidence_ref": "fixture-original",
    }
    b = {
        **common,
        "id": batch,
        "source_key": "batch-one",
        "source_kind": "local-ci-v1",
        "started_at": "2026-11-16T00:00:00Z",
        "finished_at": "2026-11-16T00:01:00Z",
        "status": "complete",
        "accepted_count": 2,
        "rejected_count": 0,
        "reason_code": "none",
    }
    insert(conn, "import_batches", b)
    common.update(import_batch_id=batch, import_batch_revision=1)
    r = {
        **common,
        "id": run,
        "source_key": "invocation-one",
        "head_sha": "1" * 40,
        "dirty": True,
        "source_fingerprint": "b" * 64,
        "index_fingerprint": "c" * 64,
        "completeness": "partial",
        "outcome": "failed",
    }
    insert(conn, "ci_runs", r)
    c = {
        **common,
        "id": command,
        "source_key": "command-one",
        "run_id": run,
        "run_revision": 1,
        "slot_key": "python-0",
        "command_key": "python",
        "disposition": "executed",
        "expected_cases": 1,
        "inventory_sha256": "d" * 64,
        "outcome": "failed",
    }
    insert(conn, "commands", c)
    a = {
        **common,
        "id": uuid.uuid4(),
        "source_key": "attempt-zero",
        "command_id": command,
        "command_revision": 1,
        "case_key": "e" * 64,
        "attempt_number": 0,
        "outcome": "failed",
        "setup_ms": 0,
        "call_ms": 12.5,
    }
    insert(conn, "test_attempts", a)
    return b, r, c, a


def denied(conn, query, parameters=None, error=psycopg.Error):
    with pytest.raises(error), conn.transaction():
        conn.execute(query, parameters)


def test_revision_replay_original_failures_and_retraction(collector):
    _, run, command, attempt = seed(collector)
    assert insert(collector, "test_attempts", attempt) == 0
    with pytest.raises(psycopg.errors.UniqueViolation), collector.transaction():
        insert(collector, "test_attempts", {**attempt, "outcome": "passed"})
    # A real retry is a different observed attempt, never overwrites attempt zero.
    retry = {
        **attempt,
        "id": uuid.uuid4(),
        "source_key": "attempt-one",
        "attempt_number": 1,
        "outcome": "passed",
    }
    insert(collector, "test_attempts", retry)
    assert collector.execute(
        "SELECT outcome FROM devops.test_attempts WHERE command_id=%s ORDER BY attempt_number",
        (command["id"],),
    ).fetchall() == [("failed",), ("passed",)]
    for change in (
        {"revision": 3, "change_kind": "correction"},
        {"revision": 2, "change_kind": "correction", "case_key": "f" * 64},
    ):
        with pytest.raises(psycopg.errors.CheckViolation), collector.transaction():
            insert(collector, "test_attempts", {**attempt, **change})
    corrected = {
        **attempt,
        "revision": 2,
        "change_kind": "correction",
        "call_ms": 15,
        "evidence_ref": "fixture-correction",
    }
    insert(collector, "test_attempts", corrected)
    insert(
        collector,
        "test_attempts",
        {**corrected, "revision": 3, "change_kind": "retraction"},
    )
    assert (
        collector.execute(
            "SELECT revision FROM devops.v1_test_attempts WHERE id=%s",
            (attempt["id"],),
        ).fetchall()
        == []
    )
    assert (
        collector.execute(
            "SELECT count(*) FROM devops.test_attempts WHERE id=%s", (attempt["id"],)
        ).fetchall()[0][0]
        == 3
    )
    insert(collector, "ci_runs", {**run, "revision": 2, "change_kind": "retraction"})
    # Underlying child originals retained; no current child dataset after parent retraction.
    assert (
        collector.execute(
            "SELECT count(*) FROM devops.v1_test_attempts WHERE command_id=%s",
            (command["id"],),
        ).fetchall()[0][0]
        == 0
    )


def test_identity_constraints_and_real_attempt_boundary(collector):
    batch, run, command, attempt = seed(collector)
    for table, row in [
        (
            "ci_runs",
            {
                **run,
                "id": uuid.uuid4(),
                "source_key": "other",
                "repository_id": uuid.uuid4(),
            },
        ),
        (
            "commands",
            {
                **command,
                "id": uuid.uuid4(),
                "source_key": "other",
                "installation_id": uuid.uuid4(),
            },
        ),
        ("commands", {**command, "id": uuid.uuid4(), "source_key": "alias"}),
        ("test_attempts", {**attempt, "id": uuid.uuid4(), "source_key": "alias"}),
        (
            "test_attempts",
            {
                **attempt,
                "id": uuid.uuid4(),
                "source_key": "not-run",
                "attempt_number": 2,
                "outcome": "not-run",
            },
        ),
        (
            "test_attempts",
            {
                **attempt,
                "id": uuid.uuid4(),
                "source_key": "negative",
                "attempt_number": 2,
                "call_ms": -1,
            },
        ),
        (
            "import_batches",
            {**batch, "id": uuid.uuid4(), "source_key": "https://private/token"},
        ),
    ]:
        with pytest.raises(psycopg.IntegrityError), collector.transaction():
            insert(collector, table, row)
    telemetry = {
        **batch,
        "id": uuid.uuid4(),
        "source_key": "telemetry",
        "source_kind": "test-telemetry-v1",
    }
    insert(collector, "import_batches", telemetry)
    with pytest.raises(psycopg.errors.CheckViolation), collector.transaction():
        insert(
            collector,
            "ci_runs",
            {
                **run,
                "id": uuid.uuid4(),
                "source_key": "wrong-kind",
                "import_batch_id": telemetry["id"],
            },
        )
    reused = {
        **command,
        "id": uuid.uuid4(),
        "source_key": "reused",
        "slot_key": "python-1",
        "disposition": "reused",
        "outcome": None,
        "donor_command_id": command["id"],
        "donor_command_revision": 1,
    }
    insert(collector, "commands", reused)
    with pytest.raises(psycopg.errors.CheckViolation), collector.transaction():
        insert(
            collector,
            "test_attempts",
            {
                **attempt,
                "id": uuid.uuid4(),
                "source_key": "fake",
                "command_id": reused["id"],
            },
        )
    for change in ({"donor_command_id": reused["id"]}, {"command_key": "different"}):
        with pytest.raises(psycopg.errors.CheckViolation), collector.transaction():
            insert(
                collector,
                "commands",
                {
                    **reused,
                    "id": uuid.uuid4(),
                    "source_key": "bad-donor",
                    "slot_key": "python-2",
                    **change,
                },
            )
    # NULL remains unknown; zero duration remains an actual zero.
    assert collector.execute(
        "SELECT base_sha,policy_sha256,inventory_sha256,cohort_key FROM devops.ci_runs WHERE id=%s",
        (run["id"],),
    ).fetchone() == (None, None, None, None)


def test_runtime_roles_cannot_escalate_or_mutate(fixture, collector):
    seed(collector)
    for role in ("collector", "reader"):
        with psycopg.connect(**fixture[1][role], autocommit=True) as conn:
            assert conn.execute(
                "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
            ).fetchone() == (False, False)
            for query in (
                "SET ROLE devops_owner",
                "SET ROLE devops_migrator",
                "SET ROLE devops_maintenance",
                "CREATE TABLE public.unauthorized(id int)",
                "CREATE SCHEMA unauthorized",
                "CREATE TEMP TABLE unauthorized(id int)",
                "DELETE FROM devops.test_attempts",
                "TRUNCATE devops.ci_runs CASCADE",
                "UPDATE devops.ci_runs SET dirty=false",
            ):
                denied(conn, query, error=psycopg.errors.InsufficientPrivilege)
            assert (
                conn.execute("SELECT count(*) FROM devops.v1_ci_runs").fetchall()[0][0]
                >= 0
            )
            if role == "reader":
                for query in (
                    "SELECT * FROM devops.ci_runs",
                    "SELECT * FROM devops.current_ci_runs",
                    "SELECT * FROM devops_control.identity",
                    "INSERT INTO devops.repositories VALUES (gen_random_uuid(),'local','bad','bad')",
                ):
                    denied(conn, query, error=psycopg.errors.InsufficientPrivilege)
    with psycopg.connect(**fixture[1]["maintenance"], autocommit=True) as conn:
        conn.execute("SELECT * FROM devops_control.identity").fetchall()
        denied(
            conn,
            "DELETE FROM devops.test_attempts",
            error=psycopg.errors.InsufficientPrivilege,
        )


@pytest.mark.parametrize("role", ["collector", "reader"])
@pytest.mark.parametrize("privilege", ["CREATE", "USAGE"])
def test_direct_runtime_public_schema_grants_refuse_before_writes(
    fixture, role, privilege
):
    actor = fixture[1][role]["user"]
    with psycopg.connect(**fixture[1]["admin"], autocommit=True) as admin:
        before = admin.execute(
            "SELECT * FROM devops_control.migrations ORDER BY version"
        ).fetchall()
        admin.execute(
            sql.SQL("GRANT {} ON SCHEMA public TO {}").format(
                sql.SQL(privilege), sql.Identifier(actor)
            )
        )
        try:
            assert admin.execute(
                "SELECT has_schema_privilege(%s,'public',%s)", (actor, privilege)
            ).fetchone() == (True,)
            for action in ("plan", "status", "migrate"):
                with pytest.raises(storage.Refusal) as error:
                    operate(fixture, action)
                assert error.value.reason == "unsafe-runtime-actor"
            assert (
                admin.execute(
                    "SELECT * FROM devops_control.migrations ORDER BY version"
                ).fetchall()
                == before
            )
        finally:
            admin.execute(
                sql.SQL("REVOKE {} ON SCHEMA public FROM {}").format(
                    sql.SQL(privilege), sql.Identifier(actor)
                )
            )
    assert operate(fixture)["pending_versions"] == []


def test_run_status_and_acceptance_preserve_producer_assertions(collector):
    _batch, run, command, _attempt = seed(collector)
    states = {
        "running": "pending",
        "pending": "pending",
        "passed": "complete",
        "feedback-passed": "partial",
        "failed": "partial",
        "cancelled": "cancelled",
        "incomplete": "partial",
        "source-changed": "partial",
    }
    for index, (outcome, completeness) in enumerate(states.items()):
        row = run | {
            "id": uuid.uuid4(),
            "source_key": f"producer-state-{index}",
            "mode": "feedback" if outcome == "feedback-passed" else "selected",
            "outcome": outcome,
            "completeness": completeness,
            "acceptance": outcome == "passed",
        }
        insert(collector, "ci_runs", row)
        assert collector.execute(
            "SELECT outcome,completeness,acceptance FROM devops.v1_ci_runs WHERE id=%s",
            (row["id"],),
        ).fetchone() == (outcome, completeness, row["acceptance"])
    assert collector.execute(
        "SELECT acceptance FROM devops.v1_ci_runs WHERE id=%s", (run["id"],)
    ).fetchone() == (None,)
    changed = command | {
        "id": uuid.uuid4(),
        "source_key": "source-changed-command",
        "slot_key": "python-changed",
        "outcome": "source-changed",
    }
    insert(collector, "commands", changed)
    assert collector.execute(
        "SELECT outcome FROM devops.v1_command_runs WHERE id=%s", (changed["id"],)
    ).fetchone() == ("source-changed",)
    for invalid in (
        {"outcome": "unsupported-state"},
        {"outcome": "feedback-passed", "acceptance": True},
        {"outcome": "source-changed", "acceptance": True},
        {"outcome": "passed", "completeness": "partial", "acceptance": True},
        {
            "outcome": "passed",
            "completeness": "complete",
            "mode": "feedback",
            "acceptance": True,
        },
        {"outcome": None, "acceptance": True},
    ):
        with pytest.raises(psycopg.errors.CheckViolation), collector.transaction():
            insert(
                collector,
                "ci_runs",
                run | {"id": uuid.uuid4(), "source_key": "rejected-state"} | invalid,
            )


def test_migration_reapply_guards_and_rollback(fixture, monkeypatch):
    assert operate(fixture, "migrate")["applied_versions"] == []
    with psycopg.connect(**fixture[1]["admin"], autocommit=True) as admin:
        before = storage.catalog_digest(admin)
        for name, ident in [
            ("devops_analytics_wrong", None),
            (None, str(uuid.uuid4())),
        ]:
            with pytest.raises(storage.Refusal) as exc:
                operate(fixture, "migrate", database_name=name, database_id=ident)
            assert exc.value.reason == "wrong-target"
            assert storage.catalog_digest(admin) == before
        for column, value, reason in [
            ("version", "2", "divergent-version"),
            ("checksum", "'" + "0" * 64 + "'", "divergent-version"),
            ("schema_digest", "'" + "0" * 64 + "'", "divergent-schema"),
        ]:
            old = admin.execute(
                sql.SQL("SELECT {} FROM devops_control.migrations").format(
                    sql.Identifier(column)
                )
            ).fetchall()[0][0]
            admin.execute(
                sql.SQL("UPDATE devops_control.migrations SET {}=" + value).format(
                    sql.Identifier(column)
                )
            )
            try:
                with pytest.raises(storage.Refusal) as exc:
                    operate(fixture, "migrate")
                assert exc.value.reason == reason
            finally:
                admin.execute(
                    sql.SQL("UPDATE devops_control.migrations SET {}=%s").format(
                        sql.Identifier(column)
                    ),
                    (old,),
                )
        admin.execute("ALTER TABLE devops.ci_runs ADD COLUMN drift integer")
        try:
            with pytest.raises(storage.Refusal) as exc:
                operate(fixture, "migrate")
            assert exc.value.reason == "divergent-schema"
        finally:
            admin.execute("ALTER TABLE devops.ci_runs DROP COLUMN drift")
        assert storage.catalog_digest(admin) == before
        saved_id = admin.execute(
            "SELECT database_id FROM devops_control.identity"
        ).fetchall()[0][0]
        admin.execute(
            "UPDATE devops_control.identity SET database_id=%s", (uuid.uuid4(),)
        )
        try:
            with pytest.raises(storage.Refusal) as exc:
                operate(fixture, "migrate")
            assert exc.value.reason == "wrong-schema-identity"
        finally:
            admin.execute(
                "UPDATE devops_control.identity SET database_id=%s", (saved_id,)
            )
        admin.execute("GRANT devops_owner TO devops_collector")
        try:
            with pytest.raises(storage.Refusal) as exc:
                operate(fixture, "migrate")
            assert exc.value.reason == "unsafe-roles"
        finally:
            admin.execute("REVOKE devops_owner FROM devops_collector")
        admin.execute("ALTER TABLE devops.test_attempts DISABLE TRIGGER a_revision")
        try:
            with pytest.raises(storage.Refusal) as exc:
                operate(fixture, "migrate")
            assert exc.value.reason == "divergent-schema"
        finally:
            admin.execute("ALTER TABLE devops.test_attempts ENABLE TRIGGER a_revision")
        original = storage.migration_files()
        broken = "CREATE TABLE devops.rollback_probe(id integer); SELECT missing_column FROM devops.rollback_probe;"
        monkeypatch.setattr(
            storage,
            "migration_files",
            lambda: (
                original + [(2, hashlib.sha256(broken.encode()).hexdigest(), broken)]
            ),
        )
        with pytest.raises(psycopg.errors.UndefinedColumn):
            operate(fixture, "migrate")
        assert (
            admin.execute("SELECT to_regclass('devops.rollback_probe')").fetchall()[0][
                0
            ]
            is None
        )
        assert (
            admin.execute("SELECT count(*) FROM devops_control.migrations").fetchall()[
                0
            ][0]
            == 1
        )
        assert storage.catalog_digest(admin) == before


def new_database(fixture, suffix):
    record, profiles = fixture
    name = "devops_analytics_" + suffix + uuid.uuid4().hex[:10]
    intent = Path(record["root"]) / ("target-intent-" + name + ".json")
    intent.write_text(
        json.dumps(
            {
                "database": name,
                "database_id": record["database_id"],
                "fixture_pid": record["pid"],
                "fixture_start_ticks": record["start_ticks"],
                "synthetic_only": True,
            }
        )
    )
    with psycopg.connect(**profiles["admin"], autocommit=True) as admin:
        admin.execute(
            sql.SQL(
                "CREATE DATABASE {} OWNER devops_maintenance TEMPLATE template0"
            ).format(sql.Identifier(name))
        )
        admin.execute(
            sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(name)
            )
        )
        admin.execute(
            sql.SQL(
                "GRANT CONNECT ON DATABASE {} TO devops_migrator,devops_collector,devops_reader"
            ).format(sql.Identifier(name))
        )
        admin.execute(
            sql.SQL("GRANT CREATE ON DATABASE {} TO devops_owner").format(
                sql.Identifier(name)
            )
        )
        admin.execute(
            sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                sql.Identifier(name),
                sql.Literal("schemii-devops-v1:" + record["database_id"]),
            )
        )
    with psycopg.connect(
        **(profiles["admin"] | {"dbname": name}), autocommit=True
    ) as admin:
        admin.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    return name


def test_first_apply_failure_and_empty_target(fixture, monkeypatch):
    name = new_database(fixture, "apply_")
    with psycopg.connect(
        **(fixture[1]["migrator"] | {"dbname": name}), autocommit=True
    ) as conn:
        assert storage.operate(
            conn, "plan", name, uuid.UUID(fixture[0]["database_id"])
        )["pending_versions"] == [1]
        original = storage.migration_files()
        broken = original[0][2] + "SELECT nonexistent FROM devops.ci_runs;"
        monkeypatch.setattr(
            storage,
            "migration_files",
            lambda: [(1, hashlib.sha256(broken.encode()).hexdigest(), broken)],
        )
        with pytest.raises(psycopg.errors.UndefinedColumn):
            storage.operate(conn, "migrate", name, uuid.UUID(fixture[0]["database_id"]))
        assert conn.execute("SELECT to_regnamespace('devops')").fetchall()[0][0] is None
        monkeypatch.setattr(storage, "migration_files", lambda: original)
        assert storage.operate(
            conn, "migrate", name, uuid.UUID(fixture[0]["database_id"])
        )["applied_versions"] == [1]
        assert (
            storage.operate(
                conn, "migrate", name, uuid.UUID(fixture[0]["database_id"])
            )["applied_versions"]
            == []
        )


def test_private_dump_restore_preserves_history_and_lineage(fixture):
    record, profiles = fixture
    # Explicit committed synthetic data for the backup control only. No natural sample.
    with psycopg.connect(**profiles["collector"]) as conn:
        _, _, command, attempt = seed(conn)
        insert(
            conn,
            "test_attempts",
            {**attempt, "revision": 2, "change_kind": "retraction"},
        )
    dump = Path(record["root"]) / ("backup-" + uuid.uuid4().hex + ".dump")
    fd = os.open(dump, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    env["PGPASSFILE"] = profiles["maintenance"]["passfile"]
    with os.fdopen(fd, "wb") as stream:
        result = subprocess.run(
            [
                "pg_dump",
                "-Fc",
                "-h",
                record["socket"],
                "-p",
                str(profiles["maintenance"]["port"]),
                "-U",
                profiles["maintenance"]["user"],
                "-d",
                record["database"],
                "-N",
                "public",
            ],
            stdout=stream,
            stderr=subprocess.PIPE,
            env=env,
        )
    assert result.returncode == 0, result.stderr.decode()
    assert dump.stat().st_mode & 0o077 == 0
    name = new_database(fixture, "restore_")
    result = subprocess.run(
        [
            "pg_restore",
            "--exit-on-error",
            "--role=devops_owner",
            "-h",
            record["socket"],
            "-p",
            str(profiles["migrator"]["port"]),
            "-U",
            profiles["migrator"]["user"],
            "-d",
            name,
            str(dump),
        ],
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr.decode()
    with psycopg.connect(
        **(profiles["migrator"] | {"dbname": name}), autocommit=True
    ) as restored:
        assert (
            storage.operate(restored, "status", name, uuid.UUID(record["database_id"]))[
                "schema_version"
            ]
            == 1
        )
    with (
        psycopg.connect(
            **(profiles["maintenance"] | {"dbname": name}), autocommit=True
        ) as restored,
        psycopg.connect(**profiles["maintenance"], autocommit=True) as source,
    ):
        for table in (
            "repositories",
            "import_batches",
            "ci_runs",
            "commands",
            "test_attempts",
        ):
            query = sql.SQL(
                "SELECT to_jsonb(t) FROM devops.{} t ORDER BY to_jsonb(t)::text"
            ).format(sql.Identifier(table))
            assert (
                restored.execute(query).fetchall() == source.execute(query).fetchall()
            )
        assert (
            restored.execute(
                "SELECT count(*) FROM devops.current_test_attempts WHERE id=%s",
                (attempt["id"],),
            ).fetchall()[0][0]
            == 0
        )
    with psycopg.connect(
        **(profiles["reader"] | {"dbname": name}), autocommit=True
    ) as reader:
        reader.execute("SELECT * FROM devops.v1_ci_runs").fetchall()
        denied(
            reader,
            "SELECT * FROM devops.test_attempts",
            error=psycopg.errors.InsufficientPrivilege,
        )
        denied(
            reader,
            "DELETE FROM devops.test_attempts",
            error=psycopg.errors.InsufficientPrivilege,
        )
    # Owned private backup receipt; data/dump retained, no encryption claim.
    (Path(record["root"]) / (dump.stem + ".receipt.json")).write_text(
        json.dumps(
            {
                "dump_sha256": hashlib.sha256(dump.read_bytes()).hexdigest(),
                "restored_database": name,
                "database_id": record["database_id"],
                "protection": "0600 dump in 0700 fixture directory; not encrypted",
                "synthetic_only": True,
            }
        )
    )


def test_concurrent_exact_replay_is_one_original(fixture):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    common = {
        "id": uuid.uuid4(),
        "provider": "local",
        "repository_key": "concurrent-" + uuid.uuid4().hex,
        "display_name": "fixture-only",
    }
    first_inserted, second_started = Event(), Event()

    def first():
        with psycopg.connect(**fixture[1]["collector"]) as conn:
            result = insert(conn, "repositories", common)
            first_inserted.set()
            assert second_started.wait(5)
            return result

    def second():
        assert first_inserted.wait(5)
        with psycopg.connect(**fixture[1]["collector"]) as conn:
            second_started.set()
            return insert(conn, "repositories", common)

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(first)
        b = pool.submit(second)
        assert (a.result(timeout=10), b.result(timeout=10)) == (1, 0)
    with psycopg.connect(**fixture[1]["collector"], autocommit=True) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM devops.repositories WHERE id=%s", (common["id"],)
            ).fetchall()[0][0]
            == 1
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            insert(conn, "repositories", {**common, "display_name": "conflict"})


def test_provision_refuses_reserved_target_before_writes(fixture):
    record, profiles = fixture
    with psycopg.connect(**profiles["admin"], autocommit=True) as admin:
        query = "SELECT datname,shobj_description(oid,'pg_database') FROM pg_database ORDER BY datname"
        before = admin.execute(query).fetchall()
        env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
        env["PGPASSFILE"] = profiles["admin"]["passfile"]
        result = subprocess.run(
            [
                "psql",
                "-X",
                "-h",
                record["socket"],
                "-p",
                str(profiles["admin"]["port"]),
                "-U",
                profiles["admin"]["user"],
                "-d",
                record["database"],
                "-v",
                "ON_ERROR_STOP=1",
                "-v",
                "db_name=" + record["database"],
                "-v",
                "database_id=" + record["database_id"],
                "-f",
                str(Path(storage.__file__).with_name("provision.sql")),
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 3
        assert "Refused" in result.stderr
        assert admin.execute(query).fetchall() == before
