"""Single-row libpq execution for SELECT and command/RETURNING results.

Cursor.stream() cannot supply command-only or empty-result receipts. This small
driver boundary retains their final PGresult metadata without buffering rows.
"""

from collections.abc import Callable
from typing import Any

from psycopg import generators, pq
from psycopg.adapt import Transformer
from psycopg.errors import error_from_result

from schemii.common.query_executions.cancellation import check_query_authority


def execute_incremental(
    connection: Any,
    statement: str,
    consume: Callable[[tuple[Any, ...]], None],
) -> tuple[tuple[tuple[str, int], ...], str]:
    """Dispatch exactly once, adapt one row, and restore protocol on all exits.

    This is the same single-row mechanism as Psycopg Cursor.stream, including its
    transaction scheduler. Keep its Psycopg 3.x dependency at this tested boundary.
    Extended query protocol deliberately accepts exactly one validated statement.
    """
    with connection.lock:
        check_query_authority()
        # Match Cursor.execute/stream implicit BEGIN and configured isolation.
        connection.wait(connection._start_query())
        pg = connection.pgconn
        encoding = connection.info.encoding
        transformer = Transformer(connection)
        columns: tuple[tuple[str, int], ...] = ()
        command = "OK"
        sent = False
        consumer_failed = False
        try:
            pg.send_query_params(statement.encode(encoding), None)
            sent = True
            pg.set_single_row_mode()
            connection.wait(generators.send(pg))
            first = True
            while (response := connection.wait(generators.fetch(pg))) is not None:
                status = response.status
                if status in (
                    pq.ExecStatus.SINGLE_TUPLE,
                    pq.ExecStatus.TUPLES_OK,
                    pq.ExecStatus.COMMAND_OK,
                ):
                    if first:
                        columns = tuple(
                            (
                                (response.fname(index) or b"").decode(encoding),
                                response.ftype(index),
                            )
                            for index in range(response.nfields)
                        )
                    if status == pq.ExecStatus.SINGLE_TUPLE:
                        try:
                            check_query_authority()
                            transformer.set_pgresult(response, set_loaders=first)
                            consume(transformer.load_row(0, tuple))
                        except BaseException:
                            consumer_failed = True
                            raise
                        first = False
                    else:
                        command = (response.command_status or b"OK").decode(encoding)
                elif status == pq.ExecStatus.FATAL_ERROR:
                    raise error_from_result(response, encoding)
                else:
                    # COPY/pipeline modes are excluded by managed validation.
                    # An unsupported protocol cannot be safely reused.
                    connection.close()
                    raise RuntimeError("Unsupported managed Console result protocol")
            return columns, command
        finally:
            if (
                sent
                and not getattr(connection, "closed", False)
                and pg.transaction_status == pq.TransactionStatus.ACTIVE
            ):
                # A cap or authority failure may interrupt a still-running
                # RETURNING. Cancel only this owned query; never replay it.
                try:
                    if consumer_failed:
                        connection.cancel_safe(timeout=1)
                    while connection.wait(generators.fetch(pg)) is not None:
                        pass
                except Exception:
                    # A broken transport must not retain an unusable permit.
                    connection.close()
