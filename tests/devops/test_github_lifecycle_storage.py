"""Sealed offline oracle and opt-in, socket-only PostgreSQL publication control."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import shlex
import subprocess
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
import pytest

from scripts.devops import github_lifecycle as lifecycle, importer, storage
from scripts.devops import github_lifecycle_storage as writer
from test_github_lifecycle import (
    BEGIN,
    CUTOFF,
    HEAD,
    SCOPE,
    SECRET,
    Fixture,
    collect,
    event,
    pr,
    review,
)

# Private decision packet's exact synthetic envelope seals; regenerated below.
SEALS = {
    "P": "6225cfdff1e801a3334b4e16223a5dea3866a358d236a1875bacb00202603b27",
    "C1": "9f78b223c0cf2c1525b7cc27aa76bbdc155cf67d6bfccecf627f3044614b9930",
    "C2": "9b14210b5900829742571a3fe5eec60a2f43a74efdfcfe16fc8537bf591ba13b",
    "STALE": "a0a2cdface9933c18050ac96375ba77bcd27fd4c638e97c90c13b6c089afb5a9",
}


def seal(receipt):
    def point(checkpoint):
        return dict(
            through=checkpoint.through.isoformat(),
            attempt_id=str(checkpoint.attempt_id) if checkpoint.attempt_id else None,
            evidence_sha256=checkpoint.evidence_sha256,
        )

    scope = receipt.before.scope
    return dict(
        format_version=1,
        scope=dict(
            repository_id=str(scope.repository_id),
            installation_id=str(scope.installation_id),
            repository_key=scope.repository_key,
            provider_repository_id=scope.provider_repository_id,
            window_start=scope.window_start.isoformat(),
            overlap_us=scope.overlap // timedelta(microseconds=1),
        ),
        before=point(receipt.before),
        proposal=point(receipt.proposal) if receipt.proposal else None,
        report=receipt.report,
    )


@pytest.fixture(scope="module")
def oracle():
    old, new = HEAD, "b" * 40
    transport = Fixture(
        [
            pr(1, draft=True),
            pr(2, updated=9, state="MERGED"),
            pr(3, updated=6, state="CLOSED"),
        ],
        timeline={
            1: [
                event("ReadyForReviewEvent", "READY_2", 5),
                event(
                    "HeadRefForcePushedEvent",
                    "F_2",
                    5,
                    beforeCommit={"oid": new},
                    afterCommit={"oid": old},
                ),
                event("ConvertToDraftEvent", "DRAFT_1", 4),
                event("ConvertToDraftEvent", "DRAFT_1", 4),
                event(
                    "HeadRefForcePushedEvent",
                    "F_1",
                    3,
                    beforeCommit={"oid": old},
                    afterCommit={"oid": new},
                ),
                event("ReadyForReviewEvent", "READY_1", 2),
            ],
            2: [event("MergedEvent", "M_2", 9)],
        },
        reviews={1: [review(1, "PENDING", submitted=None), review(2)]},
    )
    cache = lifecycle.PageCache()

    def scan(attempt, day, **options):
        start = datetime(2026, 11, day, tzinfo=timezone.utc)
        clock = iter(start + timedelta(seconds=tick) for tick in range(40))
        return collect(
            transport,
            attempt_id=UUID(int=attempt),
            clock=lambda: next(clock),
            page_size=2,
            **options,
        )

    def fault(request, body, _page):
        if request.operation == "timeline" and request.number == 1 and request.cursor:
            return lifecycle.Response(200, body | {"errors": [{"message": SECRET}]})
        return None

    transport.patch = fault
    partial = scan(3, 10)
    transport.patch = None
    first = scan(4, 11, cache=cache)
    stale = scan(7, 11, through=CUTOFF + timedelta(days=1))
    transport.reviews[1] = [review(2, "DISMISSED"), review(3, submitted=None)]

    def cached(request, _body, _page):
        if (
            request.operation == "timeline"
            and request.number == 2
            and transport.calls[-1][1]
        ):
            return lifecycle.Response(304)
        return None

    transport.patch = cached
    second = scan(
        6,
        12,
        checkpoint=first.proposal,
        through=CUTOFF + timedelta(days=1),
        cache=cache,
    )
    return {
        key: seal(val)
        for key, val in {
            "P": partial,
            "C1": first,
            "STALE": stale,
            "C2": second,
        }.items()
    }


def test_pinned_oracle(oracle):
    assert {key: importer.digest(value) for key, value in oracle.items()} == SEALS
    for name, payload in oracle.items():
        staged = writer._stage(importer.encode(payload), SEALS[name], SCOPE)
        assert len(staged.coverage) == 3
        assert len(staged.observations) == (5 if name == "P" else 14)
        assert SECRET not in json.dumps(payload)


@pytest.mark.parametrize(
    "fault",
    [
        "seal",
        "scope",
        "source",
        "fact-field",
        "failure-field",
        "interval",
        "coverage",
        "duplicate",
        "invalid-constant",
        "wrong-proposal",
        "coerced-number",
        "flow-field",
        "cache-donor",
    ],
)
def test_refuses_before_target(oracle, monkeypatch, fault):
    def forbidden(*_args, **_kwargs):
        pytest.fail("invalid saved input reached the target guard")

    monkeypatch.setattr(importer, "target", forbidden)
    payload = deepcopy(
        oracle[
            "C2" if fault in {"wrong-proposal", "flow-field", "cache-donor"} else "P"
        ]
    )
    report = payload["report"]
    fact = report["facts"][0]
    if fault == "seal":
        payload["scope"]["overlap_us"] -= 1
    elif fault == "scope":
        report["installation_id"] = str(uuid4())
    elif fault == "source":
        report["source_kind"] = "local-ci-v1"
    elif fault == "fact-field":
        fact["value"]["title"] = SECRET
    elif fault == "failure-field":
        report["failure"]["message"] = SECRET
    elif fault == "interval":
        fact["observed_from"] = "2026-11-20T00:00:00+00:00"
    elif fault == "coverage":
        report["coverage"]["pull_requests"]["2"]["reviews"] = "complete"
    elif fault == "wrong-proposal":
        payload["proposal"]["attempt_id"] = str(uuid4())
    elif fault == "coerced-number":
        fact["number"] = True
    elif fault == "flow-field":
        report["flow"][0]["ready_episodes"][0]["body"] = SECRET
        payload["proposal"]["evidence_sha256"] = importer.digest(report)
    elif fault == "cache-donor":
        cached = next(item for item in report["facts"] if item["http_status"] == 304)
        del cached["cached_from_request"]
        payload["proposal"]["evidence_sha256"] = importer.digest(report)
    raw = importer.encode(payload)
    expected = SEALS["P"] if fault == "seal" else importer.digest(payload)
    if fault == "seal":
        # Use an equally approved-looking scope so only the original seal refuses it.
        scope = replace(SCOPE, overlap=SCOPE.overlap - timedelta(microseconds=1))
    else:
        scope = SCOPE
    if fault == "duplicate":
        raw = b'{"format_version":1,' + raw[1:]
    if fault == "invalid-constant":
        raw = raw.replace(b'"format_version":1', b'"format_version":NaN')
    with pytest.raises(storage.Refusal) as exc:
        writer.publish(
            raw,
            expected,
            scope,
            guard_profile=Path("missing"),
            collector_profile=Path("missing"),
            database_name="unused",
            database_id=uuid4(),
        )
    assert SECRET not in str(exc.value)


@pytest.fixture(scope="module")
def disposable():
    if os.environ.get("SCHEMII_DEVOPS281_POSTGRES") != "1":
        pytest.skip(
            "opt in to a fresh private PostgreSQL cluster with SCHEMII_DEVOPS281_POSTGRES=1"
        )
    name, identity = "devops_analytics_issue281", uuid4()
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    with TemporaryDirectory(prefix="sc281-") as directory:
        root = Path(directory)
        data = root / "data"
        password = secrets.token_hex(24)
        pwfile, passfile = root / "init-password", root / "pgpass"
        for path, contents in ((pwfile, password), (passfile, f"*:*:*:*:{password}")):
            path.touch(mode=0o600)
            path.write_text(contents + "\n")
        env["PGPASSFILE"] = str(passfile)
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
            env=env,
            timeout=45,
            check=True,
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
                env=env,
                timeout=20,
                check=True,
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
                env=env,
                timeout=20,
                check=True,
            )
            profiles = {}
            config = dict(
                host=str(root), port=5432, dbname=name, passfile=str(passfile)
            )
            with importer.connect(config | {"user": "fixture_admin"}) as admin:
                assert admin.execute("SHOW listen_addresses").fetchone() == {
                    "listen_addresses": ""
                }
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
                    profiles[role] = root / f"{role}.json"
                    profiles[role].touch(mode=0o600)
                    profiles[role].write_text(
                        json.dumps(config | {"user": f"fixture_{role}"})
                    )
            yield (
                dict(
                    guard_profile=profiles["migrator"],
                    collector_profile=profiles["collector"],
                    database_name=name,
                    database_id=identity,
                ),
                profiles["reader"],
            )
        finally:
            if (data / "postmaster.pid").exists():
                subprocess.run(
                    [*control, "-w", "-t", "15", "-m", "fast", "stop"],
                    env=env,
                    timeout=20,
                    check=True,
                )


def expected_rows(payload):
    """Project exact saved oracle values independently of the writer's staging."""
    scope, before, report = (payload[key] for key in ("scope", "before", "report"))
    q = {key: UUID(scope[key]) for key in ("repository_id", "installation_id")}
    q["attempt_id"] = UUID(report["attempt_id"])
    coverage = report["coverage"]
    failure = report["failure"] or {}
    listing = report["listing_evidence"].get("listing", {})
    verification = report["listing_evidence"].get("verification", {})
    attempt = (
        q
        | {
            key: report[key]
            for key in ("source_kind", "requests", "status", "prior_evidence_sha256")
        }
        | {
            key: datetime.fromisoformat(report[key])
            for key in (
                "window_start",
                "prior_through",
                "scan_from",
                "through",
                "started_at",
                "finished_at",
            )
        }
        | {
            "format_version": payload["format_version"],
            "provider_repository_id": report["repository"]["provider_id"],
            "overlap_us": scope["overlap_us"],
            "prior_attempt_id": UUID(before["attempt_id"])
            if before["attempt_id"]
            else None,
            "prior_status": "complete",
            "listing_state": coverage["listing"],
            "verification_state": coverage["verification"],
            "listing_count": listing.get("count"),
            "listing_evidence_sha256": listing.get("evidence_sha256"),
            "verification_count": verification.get("count"),
            "verification_evidence_sha256": verification.get("evidence_sha256"),
            "report_sha256": importer.digest(report),
            "envelope_sha256": importer.digest(payload),
        }
    )
    for key in (
        "reason",
        "operation",
        "number",
        "request",
        "http_status",
        "cached_from_request",
        "partial_data",
        "clock_regressed",
    ):
        attempt[f"failure_{key}"] = failure.get(key)
    for key in ("observed_from", "observed_at", "timeline_updated_at"):
        attempt[f"failure_{key}"] = (
            datetime.fromisoformat(failure[key]) if failure.get(key) else None
        )
    attempt["failure_cached_from_attempt"] = (
        UUID(failure["cached_from_attempt"])
        if "cached_from_attempt" in failure
        else None
    )
    for key in ("remaining", "retry_after_seconds", "reset_epoch_seconds"):
        attempt[f"rate_{key}"] = report["last_rate_limit"].get(key)
    first = {
        str(fact["number"]): fact["pull_request_id"]
        for fact in report["facts"]
        if fact["provenance"] == "listing"
    }
    covered = [
        dict(
            q,
            number=int(number),
            pull_request_id=first[number],
            timeline_state=states["timeline"],
            review_state=states["reviews"],
        )
        for number, states in coverage["pull_requests"].items()
    ]
    observations = []
    for fact in report["facts"]:
        value = fact["value"]
        stream = fact["stream"]
        row = (
            q
            | {
                key: fact[key]
                for key in (
                    "evidence_ref",
                    "source_key",
                    "evidence_sha256",
                    "provenance",
                    "stream",
                    "number",
                    "pull_request_id",
                    "request",
                    "http_status",
                )
            }
            | dict(
                logical_id=UUID(fact["id"]),
                provider_id=value["provider_id"],
                cached_from_attempt=UUID(fact["cached_from_attempt"])
                if "cached_from_attempt" in fact
                else None,
                cached_from_request=fact.get("cached_from_request"),
                pr_state=value.get("state"),
                draft=value.get("draft"),
                head_sha=value.get("head_sha"),
                event_kind=value.get("kind") if stream == "timeline" else None,
                before_sha=value.get("before_sha"),
                after_sha=value.get("after_sha"),
                review_state=value.get("review_state"),
                reviewed_sha=value.get("reviewed_sha"),
            )
        )
        for key in ("observed_from", "observed_at", "timeline_updated_at"):
            row[key] = datetime.fromisoformat(fact[key]) if fact.get(key) else None
        for key in ("created_at", "updated_at", "closed_at", "merged_at"):
            row[key] = datetime.fromisoformat(value[key]) if value.get(key) else None
        stamp = value.get("occurred_at")
        row["occurred_at"] = (
            datetime.fromisoformat(stamp) if stamp and stream == "timeline" else None
        )
        row["submitted_at"] = (
            datetime.fromisoformat(stamp) if stamp and stream == "reviews" else None
        )
        observations.append(row)
    return attempt, covered, observations


def assert_rows(reader, collector, payloads, current=None):
    expected: dict[str, list[dict[str, Any]]] = {
        writer.ATTEMPTS: [],
        writer.COVERAGE: [],
        writer.OBSERVATIONS: [],
    }
    for payload in payloads:
        attempt, coverage, observations = expected_rows(payload)
        expected[writer.ATTEMPTS].append(attempt)
        expected[writer.COVERAGE].extend(coverage)
        expected[writer.OBSERVATIONS].extend(observations)
    actual = {}
    for table, order in (
        (writer.ATTEMPTS, "attempt_id"),
        (writer.COVERAGE, "attempt_id, pull_request_id"),
        (writer.OBSERVATIONS, "attempt_id, evidence_ref"),
    ):
        keys = order.split(", ")
        base = sorted(expected[table], key=lambda row: tuple(row[key] for key in keys))
        assert (
            collector.execute(
                f"SELECT * FROM devops.{table} ORDER BY {order}"
            ).fetchall()
            == base
        )
        projection = reader.execute(
            f"SELECT * FROM devops.v1_{table} ORDER BY {order}"
        ).fetchall()
        if table == writer.ATTEMPTS:
            base = [
                dict(row, is_current_checkpoint=row["attempt_id"] == current)
                for row in base
            ]
        assert projection == base
        actual[table] = projection
    assert SECRET not in importer.encode(actual).decode()
    return actual


def test_guarded_postgres_publication(disposable, oracle, monkeypatch):
    target, reader_profile = disposable
    name, identity = target["database_name"], target["database_id"]
    original = storage.migration_files()
    with psycopg.connect(
        **storage.connection_config(target["guard_profile"]), autocommit=True
    ) as migrator:
        with monkeypatch.context() as patch:
            patch.setattr(storage, "migration_files", lambda: original[:2])
            assert storage.operate(migrator, "migrate", name, identity)[
                "applied_versions"
            ] == [1, 2]
        assert storage.operate(migrator, "plan", name, identity)[
            "pending_versions"
        ] == [3]
        with pytest.raises(storage.Refusal, match="migration-pending"):
            writer.publish(importer.encode(oracle["P"]), SEALS["P"], SCOPE, **target)
        assert storage.operate(migrator, "migrate", name, identity)[
            "applied_versions"
        ] == [3]
        assert (
            storage.operate(migrator, "migrate", name, identity)["applied_versions"]
            == []
        )
        with migrator.transaction():
            migrator.execute("SET LOCAL ROLE devops_owner")
            stamps = migrator.execute(
                "SELECT version, checksum, schema_digest FROM devops_control.migrations ORDER BY version"
            ).fetchall()
            assert [(row[0], row[1]) for row in stamps] == [
                (v, checksum) for v, checksum, _ in original
            ]
            assert stamps[-1][2] == storage.catalog_digest(migrator)

    def invoke(key, operation=writer.publish, *, scope=SCOPE, **override):
        payload = oracle[key] if isinstance(key, str) else key
        return operation(
            importer.encode(payload),
            importer.digest(payload),
            scope,
            **(target | override),
        )

    with (
        importer.connect(storage.connection_config(reader_profile)) as reader,
        importer.connect(
            storage.connection_config(target["collector_profile"])
        ) as collector,
    ):
        with pytest.raises(storage.Refusal, match="wrong|identity"):
            invoke("P", database_id=uuid4())
        with pytest.raises(storage.Refusal, match="lifecycle-registry-mismatch"):
            invoke("P")
        collector.execute(
            "INSERT INTO devops.repositories VALUES (%s, 'github', %s, 'synthetic')",
            (SCOPE.repository_id, SCOPE.repository_key),
        )
        assert invoke("P", writer.readback).disposition == "absent"
        publication = invoke("P")
        assert (
            publication.disposition == "published" and publication.checkpoint == BEGIN
        )
        p = assert_rows(reader, collector, [oracle["P"]])
        receipt = p[writer.ATTEMPTS][0]
        assert (
            receipt["failure_reason"],
            receipt["failure_request"],
            receipt["failure_partial_data"],
        ) == ("graphql-errors", 4, True)
        assert all(row["request"] < 4 for row in p[writer.OBSERVATIONS])

        real_insert = importer.insert

        def abort(conn, table, row):
            result = real_insert(conn, table, row)
            if table == writer.OBSERVATIONS:
                assert assert_rows(reader, collector, [oracle["P"]]) == p
                raise RuntimeError(
                    "injected after attempt, coverage and first observation INSERT"
                )
            return result

        with monkeypatch.context() as patch:
            patch.setattr(importer, "insert", abort)
            with pytest.raises(RuntimeError, match="injected after attempt"):
                invoke("C1")
        assert assert_rows(reader, collector, [oracle["P"]]) == p
        absent = invoke("C1", writer.readback)
        assert absent.disposition == "absent" and not absent.checkpoint_conflict

        invoke("C1")  # Intentionally drop the first acknowledgement.
        first = invoke("C1", writer.readback)
        assert first.disposition == "replay" and first.attempt_id == UUID(int=4)
        assert first.checkpoint.through == CUTOFF
        assert (
            first.checkpoint.evidence_sha256
            == oracle["C1"]["proposal"]["evidence_sha256"]
        )
        assert invoke("C1") == first
        c1 = assert_rows(
            reader, collector, [oracle["P"], oracle["C1"]], first.attempt_id
        )
        assert [len(rows) for rows in c1.values()] == [2, 6, 19]
        stale = invoke("STALE", writer.readback)
        assert (
            stale.disposition == "absent"
            and stale.checkpoint_conflict
            and stale.checkpoint == first.checkpoint
        )
        with pytest.raises(storage.Refusal, match="lifecycle-checkpoint-conflict"):
            invoke("STALE")
        # The database independently rejects two complete successors of the initial tuple.
        competing = writer._stage(
            importer.encode(oracle["STALE"]), SEALS["STALE"], SCOPE
        )
        with pytest.raises(
            psycopg.errors.UniqueViolation, match="github_pr_lifecycle_successor"
        ):
            importer.insert(collector, writer.ATTEMPTS, competing.attempt)
        # The exact prior FK qualifies installation, attempt, cutoff and report digest.
        child = writer._stage(importer.encode(oracle["C2"]), SEALS["C2"], SCOPE)
        for wrong_prior in (
            {"prior_evidence_sha256": "0" * 64},
            {"installation_id": uuid4()},
        ):
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                importer.insert(collector, writer.ATTEMPTS, child.attempt | wrong_prior)
        assert (
            assert_rows(
                reader, collector, [oracle["P"], oracle["C1"]], first.attempt_id
            )
            == c1
        )

        second = invoke("C2")
        assert second.disposition == "published" and second.attempt_id == UUID(int=6)
        assert second.checkpoint.through == CUTOFF + timedelta(days=1)
        assert (
            second.checkpoint.evidence_sha256
            == oracle["C2"]["proposal"]["evidence_sha256"]
        )
        history = [oracle[key] for key in ("P", "C1", "C2")]
        c2 = assert_rows(reader, collector, history, second.attempt_id)
        assert [len(rows) for rows in c2.values()] == [3, 9, 33]
        assert_sql_semantics(reader, c2)
        assert invoke("P").disposition == "replay"
        historical = invoke("C1", writer.readback)
        assert (
            historical.disposition == "historically-committed"
            and historical.checkpoint_conflict
        )
        assert historical.checkpoint == second.checkpoint and invoke("C1") == historical
        changed = deepcopy(oracle["P"])
        changed["report"]["finished_at"] = "2026-11-10T00:01:00+00:00"
        with pytest.raises(storage.Refusal, match="lifecycle-attempt-conflict"):
            invoke(changed)
        new_scope = replace(SCOPE, overlap=SCOPE.overlap - timedelta(microseconds=1))
        changed["scope"]["overlap_us"] -= 1
        with pytest.raises(storage.Refusal, match="lifecycle-series-mismatch"):
            invoke(changed, scope=new_scope)
        assert assert_rows(reader, collector, history, second.attempt_id) == c2

        def edit(request, _body, page):
            if request.operation == "timeline" and request.cursor:
                page["nodes"] = [event("ReadyForReviewEvent", "X", 3)]

        transport = Fixture(
            [pr(1)],
            timeline={
                1: [event("ReadyForReviewEvent", token, 2) for token in ("X", "Y")]
            },
            patch=edit,
        )
        conflict = seal(collect(transport, page_size=1, attempt_id=UUID(int=8)))
        # A delayed partial referencing the true initial prior remains retainable.
        pending = invoke(conflict, writer.readback)
        assert pending.disposition == "absent" and not pending.checkpoint_conflict
        assert invoke(conflict).checkpoint == second.checkpoint
        final = assert_rows(reader, collector, [*history, conflict], second.attempt_id)
        versions = [
            row for row in final[writer.OBSERVATIONS] if row["provider_id"] == "X"
        ]
        assert len(versions) == 2
        assert len({row["logical_id"] for row in versions}) == 1
        assert len({row["evidence_ref"] for row in versions}) == 2
        assert len({row["evidence_sha256"] for row in versions}) == 2

        assert (
            reader.execute("SELECT * FROM devops.v1_collection_health").fetchall() == []
        )
        assert collector.execute("SELECT * FROM devops.import_batches").fetchall() == []
        for table in (writer.ATTEMPTS, writer.COVERAGE, writer.OBSERVATIONS):
            for statement in (
                f"SELECT * FROM devops.{table}",
                f"DELETE FROM devops.v1_{table}",
                "CREATE TABLE devops.forbidden(id integer)",
            ):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    reader.execute(statement)
            for statement in (
                f"UPDATE devops.{table} SET attempt_id = attempt_id",
                f"DELETE FROM devops.{table}",
            ):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    collector.execute(statement)
        evidence = os.environ.get("SCHEMII_DEVOPS281_EVIDENCE")
        if evidence:
            with open(
                evidence, "xb", opener=lambda path, flags: os.open(path, flags, 0o600)
            ) as output:
                output.write(
                    importer.encode(
                        {
                            "stamps": stamps,
                            "P": p,
                            "C1": c1,
                            "C2": c2,
                            "final": final,
                            "injected_failure": "after attempt, coverage and first observation INSERT",
                            "post_rollback_readback": p,
                            "stale_refusal": "lifecycle-checkpoint-conflict",
                        }
                    )
                    + b"\n"
                )


def assert_sql_semantics(reader, data):
    observations = data[writer.OBSERVATIONS]
    assert (
        len({row["logical_id"] for row in observations if row["stream"] == "timeline"})
        == 6
    )
    pushes = {
        (row["provider_id"], row["before_sha"], row["after_sha"])
        for row in observations
        if row["event_kind"] == "head-force-pushed"
    }
    assert pushes == {("F_1", HEAD, "b" * 40), ("F_2", "b" * 40, HEAD)}
    reviews = [row for row in observations if row["provider_id"] == "REVIEW_2"]
    assert len(reviews) == 2
    assert reviews[0]["logical_id"] == reviews[1]["logical_id"]
    assert reviews[0]["submitted_at"] == reviews[1]["submitted_at"]
    assert reviews[0]["evidence_sha256"] != reviews[1]["evidence_sha256"]
    for provider in ("REVIEW_1", "REVIEW_3"):
        review_rows = [row for row in observations if row["provider_id"] == provider]
        assert len(review_rows) == 1 and review_rows[0]["submitted_at"] is None
    cached = next(
        row
        for row in observations
        if row["provider_id"] == "M_2" and row["http_status"] == 304
    )
    assert (cached["cached_from_attempt"], cached["cached_from_request"]) == (
        UUID(int=4),
        7,
    )
    counts = reader.execute(
        """
        SELECT o.attempt_id, o.number, o.pr_state, o.draft,
            EXTRACT(EPOCH FROM o.merged_at - o.created_at) AS merge_seconds,
            CASE WHEN c.review_state = 'complete' AND NOT EXISTS (
                SELECT 1 FROM devops.v1_github_pr_lifecycle_observations AS r
                WHERE (r.repository_id, r.installation_id, r.attempt_id, r.pull_request_id) =
                      (o.repository_id, o.installation_id, o.attempt_id, o.pull_request_id)
                AND r.stream = 'reviews' AND r.review_state <> 'pending' AND r.submitted_at IS NULL
            ) THEN (
                SELECT count(DISTINCT r.logical_id) FROM devops.v1_github_pr_lifecycle_observations AS r
                WHERE (r.repository_id, r.installation_id, r.attempt_id, r.pull_request_id) =
                      (o.repository_id, o.installation_id, o.attempt_id, o.pull_request_id)
                AND r.stream = 'reviews' AND r.review_state <> 'pending'
            ) END AS formal_count
        FROM devops.v1_github_pr_lifecycle_observations AS o
        JOIN devops.v1_github_pr_lifecycle_coverage AS c
        USING (repository_id, installation_id, attempt_id, pull_request_id)
        WHERE o.repository_id = %s AND o.installation_id = %s
            AND o.attempt_id IN (%s, %s) AND o.provenance = 'listing'
        ORDER BY o.attempt_id, o.number
    """,
        (SCOPE.repository_id, SCOPE.installation_id, UUID(int=4), UUID(int=6)),
    ).fetchall()
    assert [tuple(row.values())[1:] for row in counts] == [
        (1, "open", True, None, 1),
        (2, "merged", False, 691200, 0),
        (3, "closed", False, None, 0),
        (1, "open", True, None, None),
        (2, "merged", False, 691200, 0),
        (3, "closed", False, None, 0),
    ]
    assert {
        (row["provider_id"], row["event_kind"])
        for row in observations
        if row["event_kind"] == "ready-for-review"
    } == {
        ("READY_1", "ready-for-review"),
        ("READY_2", "ready-for-review"),
    }
    assert not any(row["event_kind"] == "opened" for row in observations)
