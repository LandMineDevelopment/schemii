"""Opt-in isolated PostgreSQL RSS comparison; never part of normal PR checks.

Credentials arrive only through the explicit SCHEMII_TEST_METADATA environment.
Each attempt owns a fresh connection and, for RETURNING, a connection-local table.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import resource
import statistics
import subprocess
import sys
import time


def rss_bytes(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (FileNotFoundError, ProcessLookupError):
        pass
    return 0


def child(args: argparse.Namespace) -> None:
    import psycopg
    from psycopg import pq
    from psycopg.rows import dict_row

    from schemii.common.postgres.console.execution import (
        ConsoleValueLimitError,
        json_console_value,
    )
    from schemii.common.postgres.console.gateway import (
        PsycopgConsoleReadSession,
        execute_console_statements,
    )
    from schemii.common.postgres.errors import PostgresConsoleLimitError

    timeline: list[dict[str, object]] = []
    started = time.monotonic()

    def sample(phase: str) -> None:
        timeline.append(
            {
                "phase": phase,
                "seconds": time.monotonic() - started,
                "rss_bytes": rss_bytes(os.getpid()),
            }
        )

    sample("before_connect")
    count = args.rows[0]
    width = len(str(count)) if args.fixed_scalar else args.width
    with psycopg.connect(
        os.environ["SCHEMII_TEST_METADATA_DSN"],
        password=os.environ["SCHEMII_TEST_METADATA_PASSWORD"],
        row_factory=dict_row,
    ) as connection:
        server_version = connection.info.server_version
        if args.path == "returning":
            connection.execute(
                "CREATE TEMP TABLE memory_marker (id integer, visits integer)"
            )
            connection.execute(
                "INSERT INTO memory_marker SELECT n, 0 FROM generate_series(1, %s) n",
                (count,),
            )
            connection.commit()
            statement = (
                "UPDATE memory_marker SET visits = visits + 1 RETURNING id::bigint"
                if args.fixed_scalar
                else f"UPDATE memory_marker SET visits = visits + 1 RETURNING repeat('x', {width})"
            )
        else:
            statement = (
                f"SELECT n::bigint FROM generate_series(1, {count}) AS n"
                if args.fixed_scalar
                else f"SELECT repeat('x', {width}) FROM generate_series(1, {count})"
            )
        sample("before_query")
        outcome = "complete"
        observed: int | None = None
        try:
            if args.mode[0] == "buffered-reference":
                # Reproduce the old execute-before-limit ownership boundary,
                # including its native PGresult retained during bounded fetches.
                used = 0
                with connection.cursor(row_factory=psycopg.rows.tuple_row) as cursor:
                    cursor.execute(statement)
                    sample("after_buffered_execute")
                    while batch := cursor.fetchmany(100):
                        for row in batch:
                            converted = tuple(
                                json_console_value(value, maximum_bytes=args.cell_bytes)
                                for value in row
                            )
                            used += len(json.dumps(converted).encode())
                            if used > args.page_bytes:
                                observed = used
                                raise PostgresConsoleLimitError(
                                    "preview memory cap",
                                    statement_index=0,
                                    limit=args.page_bytes,
                                    observed=used,
                                )
            elif args.mode[0] == "named":
                if args.path != "select":
                    raise ValueError("named cursors cannot own DML RETURNING")
                session = PsycopgConsoleReadSession(
                    connection,
                    connection.info.backend_pid,
                    [statement],
                    page_memory_bytes=args.page_bytes,
                    maximum_cell_bytes=args.cell_bytes,
                )
                try:
                    page = session.page(0, 0, 1000)
                    outcome = "page"
                    observed = len(page)
                    sample("after_named_page")
                except PostgresConsoleLimitError as error:
                    outcome = "limit"
                    observed = error.observed
                finally:
                    session.close()
                with psycopg.connect(
                    os.environ["SCHEMII_TEST_METADATA_DSN"],
                    password=os.environ["SCHEMII_TEST_METADATA_PASSWORD"],
                ) as fresh:
                    assert fresh.execute("SELECT 1").fetchone() == (1,)
                print(
                    json.dumps(
                        {
                            "mode": "named",
                            "path": args.path,
                            "rows": count,
                            "width": width,
                            "outcome": outcome,
                            "observed": observed,
                            "server_version": server_version,
                            "psycopg": psycopg.__version__,
                            "libpq": pq.version(),
                            "peak_rss_bytes": resource.getrusage(
                                resource.RUSAGE_SELF
                            ).ru_maxrss
                            * 1024,
                            "timeline": timeline,
                            "largest_fixture_cell_bytes": width,
                            "connection_closed": connection.closed,
                        }
                    )
                )
                return
            else:
                execute_console_statements(
                    connection,
                    [statement],
                    maximum_result_bytes=args.page_bytes,
                    maximum_cell_bytes=args.cell_bytes,
                )
        except (PostgresConsoleLimitError, ConsoleValueLimitError) as error:
            outcome = "limit"
            observed = getattr(error, "observed", observed)
        sample("after_query")
        connection.rollback()
        if args.path == "returning":
            assert (
                connection.execute(
                    "SELECT sum(visits) AS visits FROM memory_marker"
                ).fetchone()["visits"]
                == 0
            )
        assert connection.execute("SELECT 1 AS healthy").fetchone()["healthy"] == 1
        sample("after_rollback_and_fresh_read")
    sample("after_close")
    print(
        json.dumps(
            {
                "mode": args.mode[0],
                "path": args.path,
                "rows": count,
                "width": width,
                "outcome": outcome,
                "observed": observed,
                "server_version": server_version,
                "psycopg": psycopg.__version__,
                "libpq": pq.version(),
                "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                * 1024,
                "timeline": timeline,
                "largest_fixture_cell_bytes": width,
                "connection_closed": connection.closed,
            }
        )
    )


def named_latency(args: argparse.Namespace) -> None:
    """Alternate identical real 1,000-row bigint pages with different fetch boundaries."""
    import psycopg
    from psycopg import pq
    from schemii.common.postgres.console.gateway import PsycopgConsoleReadSession
    from schemii.common.postgres.console.execution import json_console_value

    measurements = []
    modes = ("one-row-reference", "bounded-fixed", "old-batch-reference")
    server_version = None
    for trial in range(7):
        for mode in modes if trial % 2 == 0 else reversed(modes):
            with psycopg.connect(
                os.environ["SCHEMII_TEST_METADATA_DSN"],
                password=os.environ["SCHEMII_TEST_METADATA_PASSWORD"],
            ) as connection:
                server_version = connection.info.server_version
                session = PsycopgConsoleReadSession(
                    connection,
                    connection.info.backend_pid,
                    ["SELECT n::bigint FROM generate_series(1, 1000) AS n"],
                    page_memory_bytes=args.page_bytes,
                    maximum_cell_bytes=args.cell_bytes,
                )
                reader = session._readers[0]
                if mode == "one-row-reference":
                    reader.fixed_row_memory_bytes = None
                fetch_sizes = []

                class CountedCursor:
                    def fetchmany(self, size):
                        fetch_sizes.append(size)
                        return cursor.fetchmany(size)

                    def close(self):
                        cursor.close()

                cursor = reader.cursor
                reader.cursor = CountedCursor()
                try:
                    started = time.perf_counter()
                    if mode == "old-batch-reference":
                        # Faithful 77af33c boundary: one FETCH then a straight
                        # conversion/byte-check loop, without new carry costs.
                        raw_rows = reader.cursor.fetchmany(1000)
                        rows = []
                        used_bytes = 0
                        for raw_row in raw_rows:
                            converted = tuple(
                                json_console_value(value, maximum_bytes=args.cell_bytes)
                                for value in raw_row
                            )
                            row_bytes = len(
                                json.dumps(converted, ensure_ascii=False).encode(
                                    "utf-8"
                                )
                            )
                            assert used_bytes + row_bytes <= args.page_bytes
                            rows.append(converted)
                            used_bytes += row_bytes
                        page = tuple(rows)
                    else:
                        page = session.page(0, 0, 1000)
                    elapsed = time.perf_counter() - started
                    assert len(page) == 1000 and sum(row[0] for row in page) == 500500
                    measurements.append(
                        {
                            "trial": trial,
                            "mode": mode,
                            "seconds": elapsed,
                            "source_rows": 1000,
                            "source_columns": 1,
                            "column_oid": 20,
                            "largest_cell_bytes": 4,
                            "serialized_page_bytes": sum(
                                len(json.dumps(row).encode()) for row in page
                            ),
                            "fetch_calls": len(fetch_sizes),
                            "largest_fetch_rows": max(fetch_sizes),
                        }
                    )
                finally:
                    session.close()
    print(
        json.dumps(
            {
                "topology": "Shared-host disposable PostgreSQL loopback; alternating fresh snapshots",
                "page_bytes": args.page_bytes,
                "server_version": server_version,
                "psycopg": psycopg.__version__,
                "libpq": pq.version(),
                "measurements": measurements,
                "median_seconds": {
                    mode: statistics.median(
                        item["seconds"] for item in measurements if item["mode"] == mode
                    )
                    for mode in modes
                },
            },
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, nargs="+", default=[1000, 10000, 100000])
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument(
        "--fixed-scalar",
        action="store_true",
        help="Measure bigint instead of variable text",
    )
    parser.add_argument("--page-bytes", type=int, default=1024 * 1024)
    parser.add_argument("--cell-bytes", type=int, default=256 * 1024)
    parser.add_argument("--timeout-seconds", type=float, default=120)
    parser.add_argument("--path", choices=["select", "returning"], default="select")
    parser.add_argument(
        "--mode",
        nargs="+",
        choices=["buffered-reference", "incremental", "named"],
        default=["buffered-reference", "incremental"],
    )
    parser.add_argument(
        "--named-latency",
        action="store_true",
        help="Compare narrow named-page FETCH boundaries",
    )
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if (
        min(args.rows) < 1
        or args.width < 1
        or args.page_bytes < 1
        or args.cell_bytes < 1
        or args.timeout_seconds <= 0
    ):
        parser.error("rows, width, byte caps and timeout must be positive")
    if args.path == "returning" and "named" in args.mode:
        parser.error("named cursors support SELECT only")
    if args.named_latency:
        named_latency(args)
        return
    if args.child:
        child(args)
        return
    measurements = []
    for mode in args.mode:
        for rows in args.rows:
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--child",
                "--rows",
                str(rows),
                "--width",
                str(args.width),
                "--page-bytes",
                str(args.page_bytes),
                "--path",
                args.path,
                "--cell-bytes",
                str(args.cell_bytes),
                "--mode",
                mode,
            ]
            if args.fixed_scalar:
                command.append("--fixed-scalar")
            samples = []
            attempt_started = time.monotonic()
            with subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            ) as process:
                try:
                    while process.poll() is None:
                        elapsed = time.monotonic() - attempt_started
                        if elapsed > args.timeout_seconds:
                            raise TimeoutError(
                                "Owned console memory attempt exceeded its probe deadline"
                            )
                        samples.append(
                            {"seconds": elapsed, "rss_bytes": rss_bytes(process.pid)}
                        )
                        time.sleep(0.02)
                    output, error = process.communicate()
                    if process.returncode:
                        raise RuntimeError(
                            f"Owned probe failed (exit {process.returncode}): {error}"
                        )
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
            result = json.loads(output)
            result["rss_samples"] = samples
            result["observed_peak_rss_bytes"] = max(
                result["peak_rss_bytes"],
                *(item["rss_bytes"] for item in result["timeline"]),
                *(item["rss_bytes"] for item in samples),
            )
            result["source_columns"] = 1
            result["largest_fixture_row_payload_bytes"] = result[
                "largest_fixture_cell_bytes"
            ]
            result["column_oid"] = 20 if args.fixed_scalar else 25
            result["fresh_read_passed"] = True
            result["rollback_checked"] = mode != "named"
            if args.path == "returning":
                result["returning_rows_visible_after_rollback"] = 0
            measurements.append(result)
    print(
        json.dumps(
            {
                "platform": platform.platform(),
                "cpu_count": os.cpu_count(),
                "page_bytes": args.page_bytes,
                "cell_bytes": args.cell_bytes,
                "measurements": measurements,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
