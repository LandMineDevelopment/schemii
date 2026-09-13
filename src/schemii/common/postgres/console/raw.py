"""Unmodified PostgreSQL protocol execution for owner-operated SQL sessions.

Unlike managed AI execution, this boundary does not classify or rewrite SQL.
libpq single-row mode bounds application memory even for arbitrary result sets.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import Any
import time

from psycopg import generators, pq, sql
from pglast import parse_sql


class RawSession:
    def __init__(self, connection: Any, namespace: str, *, autocommit: bool,
                 row_limit: int = 1000, byte_limit: int = 4 * 1024 * 1024):
        self.connection = connection
        self.row_limit = row_limit
        self.byte_limit = byte_limit
        connection.set_autocommit(True)
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(namespace)))
            # No application statement timeout: role/database/session settings apply.
            if not autocommit:
                cursor.execute("BEGIN")

    @property
    def backend_pid(self) -> int:
        return self.connection.info.backend_pid

    @property
    def transaction_status(self) -> str:
        return self.connection.info.transaction_status.name.lower()

    def cancel(self) -> None:
        self.connection.cancel_safe(timeout=2)

    def close(self) -> None:
        self.connection.close()  # PostgreSQL rolls back any uncommitted transaction.

    def execute(self, statement: str, publish: Callable[[dict], None]) -> dict:
        """Send one untouched script; PostgreSQL owns its transaction semantics."""
        try:
            parsed = parse_sql(statement)
        except Exception:
            parsed = ()
        if any(type(node.stmt).__name__ == "CopyStmt" and node.stmt.filename is None for node in parsed):
            return {"results": [], "errorMessage": "COPY STDIN/STDOUT requires Upload or Download COPY data; no commands in this run were executed.", "sqlstate": "0A000", "transactionStatus": self.transaction_status, "elapsedMs": 0}
        pg = self.connection.pgconn
        encoding = self.connection.info.encoding
        decode = lambda value: value.decode(encoding, errors="replace") if value is not None else None
        result: dict = {"results": [], "errorMessage": None, "sqlstate": None}
        current: dict | None = None
        used = 0
        started = time.monotonic()
        pg.send_query(statement.encode(encoding))
        pg.set_single_row_mode()
        self.connection.wait(generators.send(pg))
        while (response := self.connection.wait(generators.fetch(pg))) is not None:
            status = response.status
            if status in (pq.ExecStatus.SINGLE_TUPLE, pq.ExecStatus.TUPLES_OK, pq.ExecStatus.COMMAND_OK):
                if current is None:
                    current = {"columns": [{"name": decode(response.fname(i)), "dataType": f"oid:{response.ftype(i)}"}
                                           for i in range(response.nfields)], "rows": [], "rowCount": 0,
                               "command": "", "truncated": False}
                for index in range(response.ntuples):
                    current["rowCount"] += 1
                    values = [response.get_value(index, column) for column in range(response.nfields)]
                    size = 64 + sum(32 + len(value) if value is not None else 8 for value in values)
                    if len(current["rows"]) < self.row_limit and used + size <= self.byte_limit:
                        current["rows"].append([decode(value) for value in values])
                        used += size
                    else:
                        current["truncated"] = True
                if status != pq.ExecStatus.SINGLE_TUPLE:
                    current["command"] = decode(response.command_status) or "OK"
                    if not current["columns"]:
                        current["rowCount"] = response.command_tuples or 0
                    if len(result["results"]) < 1000:
                        result["results"].append(current)
                    else:
                        result["resultsTruncated"] = True
                    current = None
                    publish({"completedStatements": len(result["results"]),
                             "elapsedMs": round((time.monotonic() - started) * 1000)})
            elif status == pq.ExecStatus.COPY_IN:
                self.connection.wait(generators.copy_end(pg, b"Use the console COPY upload endpoint for COPY FROM STDIN"))
                result.update(errorMessage="COPY FROM STDIN needs an upload. Use Upload COPY data.", sqlstate="57014")
            elif status == pq.ExecStatus.COPY_OUT:
                # Release COPY mode without retaining output. Download endpoint streams it.
                while not isinstance(self.connection.wait(generators.copy_from(pg)), pq.PGresult):
                    pass
                result.update(errorMessage="COPY TO STDOUT needs a download. Use Download COPY data.", sqlstate="0A000")
            elif status == pq.ExecStatus.FATAL_ERROR:
                result.update(errorMessage=decode(response.error_field(pq.DiagnosticField.MESSAGE_PRIMARY)) or "PostgreSQL rejected the command",
                              sqlstate=decode(response.error_field(pq.DiagnosticField.SQLSTATE)))
            elif status == pq.ExecStatus.BAD_RESPONSE:
                result.update(errorMessage=decode(response.error_message), sqlstate=None)
        result.update(transactionStatus=self.transaction_status,
                      elapsedMs=round((time.monotonic() - started) * 1000))
        return result

    def copy_upload(self, statement: str, chunks: Iterable[bytes]) -> dict:
        with self.connection.cursor() as cursor:
            with cursor.copy(statement) as copy:
                for chunk in chunks:
                    copy.write(chunk)
            return {"command": cursor.statusmessage, "transactionStatus": self.transaction_status}

    def copy_download(self, statement: str) -> Iterator[bytes]:
        with self.connection.cursor() as cursor:
            with cursor.copy(statement) as copy:
                for chunk in copy:
                    yield bytes(chunk)
