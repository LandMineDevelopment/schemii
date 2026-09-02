"""Source-derived PostgreSQL trigger contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pglast import ast, parse_sql
from pglast.enums import (
    TRIGGER_TYPE_BEFORE,
    TRIGGER_TYPE_DELETE,
    TRIGGER_TYPE_INSERT,
    TRIGGER_TYPE_INSTEAD,
    TRIGGER_TYPE_TRUNCATE,
    TRIGGER_TYPE_UPDATE,
)
from pglast.parser import ParseError
from pglast.stream import RawStream


class TriggerDefinitionError(ValueError):
    """A trigger definition cannot be represented safely by Schemii."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class TriggerDefinition:
    """Metadata derived entirely from one CREATE TRIGGER statement."""

    name: str
    relation_name: str
    timing: str
    events: tuple[str, ...]
    orientation: str
    function_name: str
    function_arguments: tuple[str, ...]
    update_columns: tuple[str, ...]
    referenced_columns: tuple[str, ...]
    when_expression: str | None
    transition_relations: tuple[str, ...]
    constraint: bool
    deferrable: bool
    initially_deferred: bool


def _render(node: object) -> str:
    return RawStream()(node).strip()


def _qualified_name(parts: tuple[ast.String, ...]) -> str:
    return ".".join(part.sval for part in parts)


def _when_columns(node: ast.Node | None) -> tuple[str, ...]:
    """Find OLD/NEW column references without interpreting trigger expressions."""

    if node is None:
        return ()
    columns: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("@") == "ColumnRef":
                fields = value.get("fields") or ()
                if len(fields) >= 2:
                    owner = fields[0]
                    column = fields[1]
                    if (
                        isinstance(owner, dict)
                        and owner.get("@") == "String"
                        and str(owner.get("sval", "")).lower() in {"old", "new"}
                        and isinstance(column, dict)
                        and column.get("@") == "String"
                    ):
                        name = column.get("sval")
                        if isinstance(name, str) and name not in columns:
                            columns.append(name)
            for child in value.values():
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)

    visit(node())
    return tuple(columns)


def analyze_trigger_definition(definition: str) -> TriggerDefinition:
    """Parse one target-independent trigger statement with PostgreSQL's grammar."""

    source = definition.strip()
    if not source:
        raise TriggerDefinitionError(
            "Enter one CREATE TRIGGER statement",
            code="definition_required",
        )
    try:
        statements = parse_sql(source)
    except ParseError as error:
        raise TriggerDefinitionError(
            "The trigger definition is not valid PostgreSQL syntax",
            code="invalid_syntax",
        ) from error
    if len(statements) != 1:
        raise TriggerDefinitionError(
            "A trigger definition must contain exactly one statement",
            code="multiple_statements",
        )
    statement = statements[0].stmt
    if not isinstance(statement, ast.CreateTrigStmt):
        raise TriggerDefinitionError(
            "The definition must be a CREATE TRIGGER statement",
            code="create_trigger_required",
        )
    if statement.relation.catalogname or statement.relation.schemaname:
        raise TriggerDefinitionError(
            "Omit the schema from the trigger target; the workspace supplies it",
            code="target_independent_relation_required",
        )
    if not statement.trigname or not statement.relation.relname:
        raise TriggerDefinitionError(
            "The trigger name and target relation could not be derived",
            code="identity_required",
        )

    if statement.timing == TRIGGER_TYPE_BEFORE:
        timing = "before"
    elif statement.timing == TRIGGER_TYPE_INSTEAD:
        timing = "instead_of"
    else:
        timing = "after"
    event_flags = (
        (TRIGGER_TYPE_INSERT, "insert"),
        (TRIGGER_TYPE_UPDATE, "update"),
        (TRIGGER_TYPE_DELETE, "delete"),
        (TRIGGER_TYPE_TRUNCATE, "truncate"),
    )
    events = tuple(label for flag, label in event_flags if statement.events & flag)
    update_columns = tuple(column.sval for column in statement.columns or ())
    referenced_columns = tuple(
        dict.fromkeys((*update_columns, *_when_columns(statement.whenClause)))
    )
    return TriggerDefinition(
        name=statement.trigname,
        relation_name=statement.relation.relname,
        timing=timing,
        events=events,
        orientation="row" if statement.row else "statement",
        function_name=_qualified_name(tuple(statement.funcname)),
        function_arguments=tuple(argument.sval for argument in statement.args or ()),
        update_columns=update_columns,
        referenced_columns=referenced_columns,
        when_expression=(
            _render(statement.whenClause) if statement.whenClause is not None else None
        ),
        transition_relations=tuple(
            _render(transition) for transition in statement.transitionRels or ()
        ),
        constraint=statement.isconstraint,
        deferrable=statement.deferrable,
        initially_deferred=statement.initdeferred,
    )
