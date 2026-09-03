"""Bounded application-neutral read-only SQL execution values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import json
from typing import Any
from uuid import UUID

from pglast import parse_sql
from pglast.parser import ParseError
from pglast.stream import RawStream

from .models import ConsoleResultColumn


MAX_CONSOLE_STATEMENTS = 20
MAX_CONSOLE_ROWS = 5_000
MAX_CONSOLE_RESULT_BYTES = 4 * 1024 * 1024
MAX_CONSOLE_CELL_BYTES = 256 * 1024


class ConsoleStatementValidationError(ValueError):
    """One editor submission is not a supported read-only statement."""

    def __init__(self, code: str, message: str, statement_index: int) -> None:
        self.code = code
        self.statement_index = statement_index
        super().__init__(message)


class ConsoleValueLimitError(ValueError):
    """One result cell cannot be represented inside the retained result bound."""


@dataclass(frozen=True, slots=True)
class ConsoleQueryResult:
    statement_index: int
    command: str
    columns: tuple[ConsoleResultColumn, ...]
    rows: tuple[tuple[Any, ...], ...]
    truncated: bool


def validate_read_only_statements(statements: list[str]) -> tuple[str, ...]:
    """Parse scripts and return individually validated read-only statements."""

    if not statements or len(statements) > MAX_CONSOLE_STATEMENTS:
        raise ConsoleStatementValidationError(
            "console_statement_limit",
            f"Run between 1 and {MAX_CONSOLE_STATEMENTS} statements at a time",
            0,
        )
    validated: list[str] = []
    allowed = {"SelectStmt", "ExplainStmt", "VariableShowStmt"}
    for submitted_index, statement in enumerate(statements):
        text = statement.strip()
        if not text:
            raise ConsoleStatementValidationError(
                "console_statement_empty",
                "SQL statements cannot be empty",
                submitted_index,
            )
        try:
            parsed = parse_sql(text)
        except ParseError as error:
            raise ConsoleStatementValidationError(
                "console_statement_invalid",
                "PostgreSQL could not parse this statement",
                submitted_index,
            ) from error
        if not parsed:
            raise ConsoleStatementValidationError(
                "console_statement_empty",
                "SQL statements cannot contain only comments",
                submitted_index,
            )
        for raw in parsed:
            statement_index = len(validated)
            node = raw.stmt
            if type(node).__name__ not in allowed:
                raise ConsoleStatementValidationError(
                    "console_statement_not_read_only",
                    "Read-only Console accepts SELECT, WITH, VALUES, EXPLAIN, and SHOW",
                    statement_index,
                )
            select = node.query if type(node).__name__ == "ExplainStmt" else node
            if type(select).__name__ not in {"SelectStmt", "VariableShowStmt"}:
                raise ConsoleStatementValidationError(
                    "console_statement_not_read_only",
                    "EXPLAIN is limited to read-only queries",
                    statement_index,
                )
            if type(select).__name__ == "SelectStmt" and getattr(select, "intoClause", None):
                raise ConsoleStatementValidationError(
                    "console_select_into_blocked",
                    "Read-only Console does not allow SELECT INTO",
                    statement_index,
                )
            validated.append(RawStream()(node))
            if len(validated) > MAX_CONSOLE_STATEMENTS:
                raise ConsoleStatementValidationError(
                    "console_statement_limit",
                    f"Run no more than {MAX_CONSOLE_STATEMENTS} statements at a time",
                    statement_index,
                )
    return tuple(validated)


def json_console_value(value: Any) -> Any:
    """Convert common psycopg values to stable JSON without losing precision."""

    if value is None or type(value) in {bool, int, float, str}:
        converted = value
    elif isinstance(value, Decimal):
        converted = str(value)
    elif isinstance(value, (datetime, date, time)):
        converted = value.isoformat()
    elif isinstance(value, timedelta):
        converted = str(value)
    elif isinstance(value, UUID):
        converted = str(value)
    elif isinstance(value, bytes):
        converted = "\\x" + value.hex()
    elif isinstance(value, dict):
        converted = {str(key): json_console_value(item) for key, item in value.items()}
    elif isinstance(value, (list, tuple)):
        converted = [json_console_value(item) for item in value]
    else:
        converted = str(value)
    if len(json.dumps(converted, ensure_ascii=False).encode("utf-8")) > MAX_CONSOLE_CELL_BYTES:
        raise ConsoleValueLimitError(
            "A PostgreSQL result value exceeds the Console cell limit"
        )
    return converted
