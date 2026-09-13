"""Bounded application-neutral read-only SQL execution values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import json
from typing import Any, Literal
from uuid import UUID

from pglast import parse_sql
from pglast.enums import TransactionStmtKind
from pglast.parser import ParseError
from pglast.stream import RawStream

from .models import ConsoleResultColumn


MAX_CONSOLE_STATEMENTS = 20
MAX_CONSOLE_RESULT_BYTES = 4 * 1024 * 1024
MAX_CONSOLE_CELL_BYTES = 256 * 1024


class ConsoleStatementValidationError(ValueError):
    """One editor submission is not a supported read-only statement."""

    def __init__(
        self,
        code: str,
        message: str,
        statement_index: int,
        *,
        limit: int | None = None,
        observed: int | None = None,
    ) -> None:
        self.code = code
        self.statement_index = statement_index
        self.limit = limit
        self.observed = observed
        super().__init__(message)


class ConsoleValueLimitError(ValueError):
    """One result cell cannot be represented inside the retained result bound."""

    def __init__(self, limit: int, observed: int) -> None:
        self.limit = limit
        self.observed = observed
        super().__init__(
            f"One PostgreSQL result value is {observed} bytes, above the configured Console cell limit of {limit} bytes. Narrow or cast the value, or ask the administrator to raise console.results.maximum_cell_bytes."
        )


@dataclass(frozen=True, slots=True)
class ConsoleQueryResult:
    statement_index: int
    command: str
    columns: tuple[ConsoleResultColumn, ...]
    rows: tuple[tuple[Any, ...], ...]
    truncated: bool
    row_count: int | None = None
    replayable: bool = False


@dataclass(frozen=True, slots=True)
class ConsoleTransactionScript:
    """Parsed write statements plus an optional final transaction action."""

    statements: tuple[str, ...]
    terminal_action: Literal["commit", "rollback"] | None = None


def validate_read_only_statements(
    statements: list[str], *, statement_limit: int = MAX_CONSOLE_STATEMENTS
) -> tuple[str, ...]:
    """Parse scripts and return individually validated read-only statements."""

    if not statements or len(statements) > statement_limit:
        raise ConsoleStatementValidationError(
            "console_statement_limit",
            f"Run between 1 and {statement_limit} statements at a time. Split a larger script into smaller runs or ask the administrator to raise console.maximum_statements_per_run.",
            0,
            limit=statement_limit if len(statements) > statement_limit else None,
            observed=len(statements) if len(statements) > statement_limit else None,
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
            is_explain = type(node).__name__ == "ExplainStmt"
            # EXPLAIN without execution can plan writes in a read-only session.
            # Treat unrecognized ANALYZE values conservatively; PostgreSQL will
            # still validate its options and enforce the read-only transaction.
            analyze = is_explain and any(
                option.defname == "analyze"
                and str(getattr(option.arg, "sval", getattr(option.arg, "ival", None))).lower()
                not in {"false", "off", "no", "0", "f", "n", "of"}
                for option in node.options or ()
            )
            select = node.query if is_explain else node
            planning_only = is_explain and not analyze
            if not planning_only and type(select).__name__ not in {"SelectStmt", "VariableShowStmt"}:
                raise ConsoleStatementValidationError(
                    "console_statement_not_read_only",
                    "EXPLAIN ANALYZE in read-only mode is limited to read-only queries",
                    statement_index,
                )
            if not planning_only and type(select).__name__ == "SelectStmt" and getattr(select, "intoClause", None):
                raise ConsoleStatementValidationError(
                    "console_select_into_blocked",
                    "Read-only Console does not allow SELECT INTO",
                    statement_index,
                )
            # pglast exposes character offsets (including Unicode). Keep the
            # submitted spelling, literals, and comments rather than rewriting
            # executable SQL through the AST pretty-printer.
            end = raw.stmt_location + raw.stmt_len if raw.stmt_len else len(text)
            validated.append(text[raw.stmt_location:end].strip())
            if len(validated) > statement_limit:
                raise ConsoleStatementValidationError(
                    "console_statement_limit",
                    f"Run no more than {statement_limit} statements at a time. Split a larger script into smaller runs or ask the administrator to raise console.maximum_statements_per_run.",
                    statement_index,
                    limit=statement_limit,
                    observed=len(validated),
                )
    return tuple(validated)


def validate_transaction_statements(
    statements: list[str], *, statement_limit: int = MAX_CONSOLE_STATEMENTS
) -> ConsoleTransactionScript:
    """Parse one explicit-transaction submission without weakening its boundary.

    PostgreSQL permissions remain authoritative for data and schema changes. The
    application only rejects commands that would escape, replace, or require an
    interactive protocol outside the server-owned transaction. A final COMMIT or
    ROLLBACK is represented as a lifecycle action so typed SQL and UI buttons use
    the same commit authority.
    """

    if not statements or len(statements) > statement_limit:
        raise ConsoleStatementValidationError(
            "console_statement_limit",
            f"Run between 1 and {statement_limit} statements at a time. Split a larger script into smaller runs or ask the administrator to raise console.maximum_statements_per_run.",
            0,
            limit=statement_limit if len(statements) > statement_limit else None,
            observed=len(statements) if len(statements) > statement_limit else None,
        )
    parsed_statements: list[tuple[str, Literal["commit", "rollback"] | None]] = []
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
            statement_index = len(parsed_statements)
            node = raw.stmt
            node_name = type(node).__name__
            terminal_action: Literal["commit", "rollback"] | None = None
            if node_name == "CopyStmt":
                raise ConsoleStatementValidationError(
                    "console_copy_not_supported",
                    "The SQL Console does not support PostgreSQL COPY streams",
                    statement_index,
                )
            if node_name == "TransactionStmt":
                kind = node.kind
                if kind is TransactionStmtKind.TRANS_STMT_COMMIT:
                    terminal_action = "commit"
                elif kind is TransactionStmtKind.TRANS_STMT_ROLLBACK:
                    terminal_action = "rollback"
                elif kind in {
                    TransactionStmtKind.TRANS_STMT_SAVEPOINT,
                    TransactionStmtKind.TRANS_STMT_RELEASE,
                    TransactionStmtKind.TRANS_STMT_ROLLBACK_TO,
                }:
                    pass
                else:
                    raise ConsoleStatementValidationError(
                        "console_transaction_control_blocked",
                        "Write mode already owns the transaction; BEGIN and prepared transaction commands are not allowed",
                        statement_index,
                    )
                if terminal_action is not None and bool(getattr(node, "chain", False)):
                    raise ConsoleStatementValidationError(
                        "console_transaction_chain_blocked",
                        "COMMIT AND CHAIN and ROLLBACK AND CHAIN cannot replace the server-owned transaction",
                        statement_index,
                    )
            parsed_statements.append((RawStream()(node), terminal_action))
            if len(parsed_statements) > statement_limit:
                raise ConsoleStatementValidationError(
                    "console_statement_limit",
                    f"Run no more than {statement_limit} statements at a time. Split a larger script into smaller runs or ask the administrator to raise console.maximum_statements_per_run.",
                    statement_index,
                    limit=statement_limit,
                    observed=len(parsed_statements),
                )

    terminal_indexes = [
        index for index, (_statement, action) in enumerate(parsed_statements) if action
    ]
    if terminal_indexes and terminal_indexes != [len(parsed_statements) - 1]:
        raise ConsoleStatementValidationError(
            "console_transaction_action_not_final",
            "COMMIT or ROLLBACK must be the final statement in the submitted script",
            terminal_indexes[0],
        )
    terminal_action = parsed_statements[-1][1] if parsed_statements else None
    executable = tuple(
        statement for statement, action in parsed_statements if action is None
    )
    return ConsoleTransactionScript(executable, terminal_action)


def json_console_value(value: Any, *, maximum_bytes: int = MAX_CONSOLE_CELL_BYTES) -> Any:
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
        converted = {
            str(key): json_console_value(item, maximum_bytes=maximum_bytes)
            for key, item in value.items()
        }
    elif isinstance(value, (list, tuple)):
        converted = [json_console_value(item, maximum_bytes=maximum_bytes) for item in value]
    else:
        converted = str(value)
    observed = len(json.dumps(converted, ensure_ascii=False).encode("utf-8"))
    if observed > maximum_bytes:
        raise ConsoleValueLimitError(maximum_bytes, observed)
    return converted
