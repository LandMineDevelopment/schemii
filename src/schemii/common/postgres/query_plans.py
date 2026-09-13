"""Build bounded PostgreSQL plan requests for authorized product queries.

These explain the complete statement on a fresh read-only snapshot. A retained
console cursor can choose a different plan for first-page retrieval; consumers
must not label this result as the plan of an already running cursor.
"""

from pglast import parse_sql
from pglast.parser import ParseError
from pglast.stream import RawStream
from pglast.visitors import Visitor

from .console.execution import ConsoleStatementValidationError


class _ReadQuery(Visitor):
    def visit(self, ancestors, node):
        name = type(node).__name__
        if name in {"InsertStmt", "UpdateStmt", "DeleteStmt", "MergeStmt", "IntoClause", "LockingClause"}:
            raise ConsoleStatementValidationError(
                "explain_read_only_required",
                "Plan analysis supports read-only queries without writes or row locking.",
                0,
            )


def build_explain_sql(sql: str, analyze: bool = False) -> str:
    """Normalize exactly one read query and select server-owned EXPLAIN options.

    PostgreSQL's read-only transaction and the target role's grants remain the
    execution authority, including for functions called by a SELECT.
    """
    if not isinstance(sql, str) or not sql.strip() or len(sql.encode("utf-8")) > 256 * 1024:
        raise ConsoleStatementValidationError(
            "explain_sql_limit", "Select one nonempty query of at most 256 KiB to explain.", 0,
        )
    if type(analyze) is not bool:
        raise ConsoleStatementValidationError("explain_options_invalid", "Analyze must be true or false.", 0)
    try:
        statements = parse_sql(sql)
    except ParseError as error:
        raise ConsoleStatementValidationError(
            "console_statement_invalid", "PostgreSQL could not parse this statement.", 0,
        ) from error
    if len(statements) != 1 or type(statements[0].stmt).__name__ != "SelectStmt":
        raise ConsoleStatementValidationError(
            "explain_single_query_required", "Select exactly one SELECT, WITH, VALUES, or TABLE query to explain.", 0,
        )
    _ReadQuery()(statements)
    query = RawStream()(statements[0].stmt)
    options = "ANALYZE TRUE, BUFFERS TRUE, " if analyze else "ANALYZE FALSE, "
    return f"EXPLAIN ({options}VERBOSE TRUE, SETTINGS TRUE, FORMAT JSON) {query}"


def query_authorities(sql: str) -> tuple[str, ...]:
    """Classify SQL authority independently of the tool that supplied it.

    Invalid SQL retains ordinary read authority; the managed-read execution
    boundary rejects it before database execution. Ambiguous ANALYZE options
    conservatively require execution authority, never estimate-only authority.
    """
    try:
        statements = parse_sql(sql)
    except ParseError:
        return ("read",)
    authorities = []
    for statement in statements:
        node = statement.stmt
        authority = "read"
        if type(node).__name__ == "ExplainStmt":
            authority = "explain"
            for option in node.options or ():
                if option.defname != "analyze":
                    continue
                argument = option.arg
                value = getattr(argument, "sval", getattr(argument, "ival", None))
                if str(value).lower() not in {"false", "off", "no", "0", "f", "n", "of"}:
                    authority = "analyze"
        authorities.append(authority)
    return tuple(dict.fromkeys(authorities or ["read"]))
