"""Conservative comparison of PostgreSQL CHECK expressions."""

from __future__ import annotations

from typing import Mapping

from sqlglot import exp, parse_one
from sqlglot.errors import ParseError


def _unparen(node: exp.Expression) -> exp.Expression:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _literal_for_column(
    node: exp.Expression, column: exp.Expression, column_types: Mapping[str, str]
) -> exp.Expression:
    """Remove only casts PostgreSQL adds to bare literals for this column type."""
    node = _unparen(node)
    column = _unparen(column)
    if not isinstance(column, exp.Column) or column.table or not isinstance(node, exp.Cast):
        return node
    literal = _unparen(node.this)
    if not isinstance(literal, exp.Literal):
        return node
    data_type = column_types.get(column.name)
    cast_type = node.args.get("to")
    if (
        not data_type
        or not isinstance(cast_type, exp.DataType)
        or cast_type.expressions
    ):
        return node
    declared = data_type.lower().split("(", 1)[0].strip()
    if (
        (declared in {"numeric", "decimal"} and not literal.is_string
         and cast_type.this == exp.DataType.Type.DECIMAL)
        or (declared == "text" and literal.is_string
            and cast_type.this == exp.DataType.Type.TEXT)
    ):
        return literal
    return node


def _normalize(node: exp.Expression, column_types: Mapping[str, str]) -> exp.Expression:
    node = _unparen(node).copy()
    for key, value in list(node.args.items()):
        if isinstance(value, exp.Expression):
            node.set(key, _normalize(value, column_types))
        elif isinstance(value, list):
            node.set(key, [
                _normalize(item, column_types) if isinstance(item, exp.Expression) else item
                for item in value
            ])

    if isinstance(node, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
        left, right = node.this, node.expression
        node.set("this", _literal_for_column(left, right, column_types))
        node.set("expression", _literal_for_column(right, left, column_types))

    if isinstance(node, exp.In) and node.args.get("expressions"):
        node.set("expressions", [
            _literal_for_column(item, node.this, column_types)
            for item in node.expressions
        ])

    if (
        isinstance(node, exp.EQ)
        and isinstance(node.this, exp.Column)
        and column_types.get(node.this.name, "").lower().split("(", 1)[0].strip()
        in {"text", "numeric", "decimal"}
    ):
        right = node.expression
        if isinstance(right, exp.Any) and isinstance(right.this, exp.Array):
            values = [
                _literal_for_column(item, node.this, column_types)
                for item in right.this.expressions
            ]
            if values and all(isinstance(item, exp.Literal) for item in values):
                return exp.In(this=node.this, expressions=values)
    return node


def equivalent_check_expressions(
    left: str, right: str, column_types: Mapping[str, str]
) -> bool:
    if left == right:
        return True
    try:
        return _normalize(parse_one(left, read="postgres"), column_types) == _normalize(
            parse_one(right, read="postgres"), column_types
        )
    except (ParseError, ValueError):
        return False
