"""Reviewed, single-column PostgreSQL conversions and preservation checks.

Only built-in scalar expressions are accepted. Rendering the parsed tree keeps
the same expression in preview and ALTER; renames are resolved by stable ID.
No row values leave PostgreSQL.
"""

from __future__ import annotations

from dataclasses import dataclass

from pglast import ast, parse_sql
from pglast.stream import RawStream

from .models import ColumnTypeConversion, ColumnTypeConversionChoice
from .type_changes import classify_type_change


_TYPES = frozenset({
    "int2", "int4", "int8", "smallint", "integer", "bigint", "numeric",
    "decimal", "float4", "float8", "real", "text", "varchar", "bpchar",
    "bool", "boolean", "date", "timestamp", "timestamptz", "time", "timetz",
    "interval", "uuid", "json", "jsonb", "bytea",
})
_FUNCTIONS = frozenset({
    "lower", "upper", "btrim", "ltrim", "rtrim", "replace", "substring",
    "substr", "left", "right", "length", "char_length", "round", "trunc",
    "abs", "ceil", "ceiling", "floor",
})
_NODES = frozenset({
    "ColumnRef", "A_Const", "Integer", "Float", "String", "Boolean",
    "TypeCast", "TypeName", "A_Expr", "BoolExpr", "NullTest", "BooleanTest",
    "CaseExpr", "CaseWhen", "CoalesceExpr", "MinMaxExpr", "FuncCall",
})
_OPERATORS = frozenset({"+", "-", "*", "/", "%", "^", "||", "=", "<>", "<", ">", "<=", ">="})


def quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _expression(source: str) -> ast.Node:
    try:
        statements = parse_sql(f"SELECT ({source})")
        statement = statements[0].stmt
        if len(statements) != 1 or not isinstance(statement, ast.SelectStmt):
            raise ValueError()
        if len(statement.targetList or ()) != 1 or any(
            getattr(statement, key) for key in (
                "fromClause", "whereClause", "withClause", "intoClause", "sortClause",
                "groupClause", "havingClause", "limitCount", "limitOffset", "larg", "rarg",
            )
        ):
            raise ValueError()
        return statement.targetList[0].val
    except Exception:
        raise ValueError("Enter one scalar USING expression, without statements or subqueries.") from None


def _walk(node: object):
    if isinstance(node, ast.Node):
        yield node
        for key in node:
            yield from _walk(getattr(node, key))
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _walk(value)


def _builtin_type(node: ast.TypeName) -> None:
    names = [part.sval for part in node.names or ()]
    if (len(names) == 2 and names[0] != "pg_catalog") or len(names) not in (1, 2) or names[-1] not in _TYPES or node.arrayBounds or node.setof:
        raise ValueError("Reviewed conversions currently support PostgreSQL built-in scalar types only.")
    node.names = (ast.String(sval="pg_catalog"), ast.String(sval=names[-1]))


def type_sql(value: str) -> str:
    tree = _expression(f"NULL::{value}")
    if not isinstance(tree, ast.TypeCast) or not isinstance(tree.arg, ast.A_Const) or not tree.arg.isnull:
        raise ValueError("The conversion type must be a PostgreSQL scalar type.")
    _builtin_type(tree.typeName)
    # Type modifiers must be numeric literals, never executable expressions.
    for modifier in tree.typeName.typmods or ():
        if not isinstance(modifier, ast.A_Const) or not isinstance(modifier.val, ast.Integer):
            raise ValueError("Type modifiers must be integer constants.")
    return RawStream()(tree.typeName)


def expression_sql(source: str, column: str, *, renamed: str | None = None) -> str:
    tree = _expression(source)
    for node in _walk(tree):
        if type(node).__name__ not in _NODES:
            raise ValueError("Use a scalar expression of this column; queries, aggregates and window functions are not supported.")
        if isinstance(node, ast.ColumnRef):
            fields = node.fields or ()
            if len(fields) != 1 or not isinstance(fields[0], ast.String) or fields[0].sval != column:
                raise ValueError(f"The expression may reference only {quote(column)}. Use its desired column name.")
            node.fields = (ast.String(sval=renamed or column),)
        elif isinstance(node, ast.TypeName):
            _builtin_type(node)
        elif isinstance(node, ast.FuncCall):
            names = [part.sval for part in node.funcname]
            if len(names) not in (1, 2) or (len(names) == 2 and names[0] != "pg_catalog") or names[-1] not in _FUNCTIONS or node.over or node.agg_star or node.agg_distinct or node.agg_order or node.agg_filter:
                raise ValueError("Use built-in scalar functions such as lower, trim, round or trunc; custom routines and aggregates are not supported.")
            node.funcname = (ast.String(sval="pg_catalog"), ast.String(sval=names[-1]))
        elif isinstance(node, ast.A_Expr):
            names = [part.sval for part in node.name or ()]
            if len(names) not in (1, 2) or (len(names) == 2 and names[0] != "pg_catalog") or names[-1] not in _OPERATORS:
                raise ValueError("This conversion operator is not supported.")
            node.name = (ast.String(sval="pg_catalog"), ast.String(sval=names[-1]))
    return RawStream()(tree)


@dataclass(frozen=True)
class CompiledConversion:
    review: ColumnTypeConversion
    expression: str
    target_type: str
    validation_sql: str
    guard_sql: str | None


def compile_conversion(namespace, before_table, after_table, old, new, choice: ColumnTypeConversionChoice) -> CompiledConversion:
    source_type = type_sql(old.data_type)
    target_type = type_sql(new.data_type)
    default = f"{quote(new.name)}::{target_type}"
    expression = expression_sql(choice.expression if choice.strategy == "custom" else default, new.name)
    live_expression = expression_sql(choice.expression if choice.strategy == "custom" else default, new.name, renamed=old.name)
    live_table = f"{quote(namespace)}.{quote(before_table.name)}"
    target_table = f"{quote(namespace)}.{quote(after_table.name)}"

    def loss_query(table: str, column: str, expr: str) -> str:
        return (
            f"SELECT EXISTS (SELECT 1 FROM {table} WHERE "
            f"({quote(column)}::pg_catalog.text COLLATE \"C\") IS DISTINCT FROM "
            f"((({expr})::{target_type})::{source_type})::pg_catalog.text COLLATE \"C\")"
        )

    guard = None
    if choice.strategy == "strict":
        validation = loss_query(live_table, old.name, live_expression) + " AS invalid"
        body = (
            "BEGIN IF ("
            + loss_query(target_table, new.name, expression)
            + ") THEN RAISE EXCEPTION USING ERRCODE = 'SC001', "
            "MESSAGE = 'Strict conversion would change existing values'; END IF; END"
        )
        guard = "DO " + quote_literal(body) + ";"
    else:
        validation = f"SELECT count(({live_expression})::{target_type}) FROM {live_table}"
    return CompiledConversion(
        review=ColumnTypeConversion(
            column_id=new.id, table_id=after_table.id, table_name=after_table.name,
            column_name=new.name, source_type=old.data_type, target_type=new.data_type,
            reason=classify_type_change(old.data_type, new.data_type).reason,
            default_expression=default, strategy=choice.strategy, expression=expression,
        ),
        expression=f"({expression})::{target_type}", target_type=target_type,
        validation_sql=validation, guard_sql=guard,
    )


def conversion_candidates(live, desired) -> list[tuple]:
    tables = {table.id: table for table in live.tables}
    candidates = []
    for after in desired.tables:
        before = tables.get(after.id)
        if before is None:
            continue
        columns = {column.id: column for column in before.columns}
        for new in after.columns:
            old = columns.get(new.id)
            if old and classify_type_change(old.data_type, new.data_type).disposition == "blocked":
                candidates.append((before, after, old, new))
    return candidates
