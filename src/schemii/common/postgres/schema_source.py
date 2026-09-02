"""Reusable PostgreSQL source normalization for desired-schema consumers."""

from __future__ import annotations

from dataclasses import dataclass

from pglast import ast, parse_sql
from pglast.enums import ConstrType, SortByDir, SortByNulls
from pglast.parser import ParseError
from pglast.stream import RawStream
from sqlglot import exp, parse_one
from sqlglot.errors import SqlglotError


class SchemaSourceError(ValueError):
    """A catalog definition cannot be represented by the shared design model."""


@dataclass(frozen=True, slots=True)
class IndexSource:
    """A target-independent index contract derived from pg_get_indexdef output."""

    name: str
    table: str
    method: str
    unique: bool
    columns: tuple[str, ...]
    expression: str | None
    predicate: str | None


def expression_column_names(expression: str, available: set[str]) -> tuple[str, ...]:
    """Return referenced local columns in first-use order from a SQL fragment."""

    try:
        statement = parse_one(f"SELECT {expression}", dialect="postgres")
    except SqlglotError as error:
        raise SchemaSourceError("The PostgreSQL expression could not be parsed") from error
    names: list[str] = []
    for column in statement.find_all(exp.Column):
        if column.name in available and column.name not in names:
            names.append(column.name)
    return tuple(names)


def check_expression(definition: str) -> str:
    """Extract only the condition from pg_get_constraintdef CHECK output."""

    try:
        statement = parse_sql(
            f"ALTER TABLE schemii_import ADD CONSTRAINT schemii_import {definition}"
        )[0].stmt
    except (ParseError, IndexError) as error:
        raise SchemaSourceError("The PostgreSQL check constraint could not be parsed") from error
    if not isinstance(statement, ast.AlterTableStmt) or len(statement.cmds or ()) != 1:
        raise SchemaSourceError("The PostgreSQL check constraint is not supported")
    constraint = statement.cmds[0].def_
    if (
        not isinstance(constraint, ast.Constraint)
        or constraint.contype != ConstrType.CONSTR_CHECK
        or constraint.raw_expr is None
    ):
        raise SchemaSourceError("The PostgreSQL definition is not a CHECK constraint")
    return RawStream()(constraint.raw_expr).strip()


def index_source(definition: str) -> IndexSource:
    """Derive the subset of CREATE INDEX represented losslessly by DesignIndex."""

    try:
        statements = parse_sql(definition)
    except ParseError as error:
        raise SchemaSourceError("The PostgreSQL index definition could not be parsed") from error
    if len(statements) != 1 or not isinstance(statements[0].stmt, ast.IndexStmt):
        raise SchemaSourceError("The PostgreSQL definition is not one CREATE INDEX statement")
    statement = statements[0].stmt
    if not statement.idxname or not statement.relation.relname:
        raise SchemaSourceError("The PostgreSQL index identity could not be derived")
    if statement.indexIncludingParams:
        raise SchemaSourceError("INCLUDE columns are not represented by the design index model")
    if statement.options:
        raise SchemaSourceError("Index storage parameters are not represented by the design index model")
    if statement.tableSpace:
        raise SchemaSourceError("Index tablespaces are not represented by the design index model")
    if statement.nulls_not_distinct:
        raise SchemaSourceError("NULLS NOT DISTINCT is not represented by the design index model")

    parameters = tuple(statement.indexParams or ())
    simple = all(
        parameter.name
        and parameter.expr is None
        and not parameter.collation
        and not parameter.opclass
        and not parameter.opclassopts
        and parameter.ordering == SortByDir.SORTBY_DEFAULT
        and parameter.nulls_ordering == SortByNulls.SORTBY_NULLS_DEFAULT
        for parameter in parameters
    )
    columns = tuple(parameter.name for parameter in parameters) if simple else ()
    expression = None if simple else ", ".join(
        RawStream()(parameter).strip() for parameter in parameters
    )
    predicate = (
        RawStream()(statement.whereClause).strip()
        if statement.whereClause is not None
        else None
    )
    return IndexSource(
        name=statement.idxname,
        table=statement.relation.relname,
        method=statement.accessMethod or "btree",
        unique=statement.unique,
        columns=columns,
        expression=expression,
        predicate=predicate,
    )


def target_independent_routine(definition: str, namespace: str) -> str:
    """Remove only the imported workspace namespace from a routine identity."""

    try:
        statements = parse_sql(definition)
    except ParseError as error:
        raise SchemaSourceError("The PostgreSQL routine definition could not be parsed") from error
    if len(statements) != 1 or not isinstance(statements[0].stmt, ast.CreateFunctionStmt):
        raise SchemaSourceError("The PostgreSQL routine definition is not supported")
    statement = statements[0].stmt
    names = tuple(statement.funcname or ())
    if len(names) == 2 and names[0].sval == namespace:
        statement.funcname = (names[1],)
    elif len(names) != 1:
        raise SchemaSourceError("The routine belongs to a different namespace")
    return RawStream()(statement).strip()


def target_independent_trigger(definition: str, namespace: str) -> str:
    """Remove the imported namespace from a trigger target and local function."""

    try:
        statements = parse_sql(definition)
    except ParseError as error:
        raise SchemaSourceError("The PostgreSQL trigger definition could not be parsed") from error
    if len(statements) != 1 or not isinstance(statements[0].stmt, ast.CreateTrigStmt):
        raise SchemaSourceError("The PostgreSQL trigger definition is not supported")
    statement = statements[0].stmt
    if statement.relation.schemaname not in {None, namespace}:
        raise SchemaSourceError("The trigger belongs to a different namespace")
    statement.relation.schemaname = None
    names = tuple(statement.funcname or ())
    if len(names) == 2 and names[0].sval == namespace:
        statement.funcname = (names[1],)
    return RawStream()(statement).strip()
