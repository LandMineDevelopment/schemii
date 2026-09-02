"""Source-derived PostgreSQL function and procedure contracts."""

from __future__ import annotations

from dataclasses import dataclass

from pglast import ast, parse_sql
from pglast.enums import FunctionParameterMode
from pglast.parser import ParseError
from pglast.stream import RawStream


class RoutineDefinitionError(ValueError):
    """A routine definition cannot be represented safely by Schemii."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RoutineDefinition:
    """Metadata derived entirely from one CREATE FUNCTION/PROCEDURE statement."""

    name: str
    kind: str
    arguments: str
    identity_arguments: str
    return_type: str | None
    language: str


def _render(node: object) -> str:
    return RawStream()(node).strip()


def _render_parameter(parameter: ast.FunctionParameter) -> str:
    rendered = _render(parameter)
    if parameter.defexpr is not None:
        return rendered.replace(" = ", " DEFAULT ", 1)
    return rendered


def _input_parameter(parameter: ast.FunctionParameter) -> bool:
    return parameter.mode not in {
        FunctionParameterMode.FUNC_PARAM_OUT,
        FunctionParameterMode.FUNC_PARAM_TABLE,
    }


def _return_type(
    statement: ast.CreateFunctionStmt,
    parameters: tuple[ast.FunctionParameter, ...],
) -> str | None:
    if statement.is_procedure:
        return None
    table_columns = [
        parameter
        for parameter in parameters
        if parameter.mode == FunctionParameterMode.FUNC_PARAM_TABLE
    ]
    if table_columns:
        return "TABLE (" + ", ".join(_render_parameter(parameter) for parameter in table_columns) + ")"
    return _render(statement.returnType) if statement.returnType is not None else None


def analyze_routine_definition(definition: str) -> RoutineDefinition:
    """Parse one target-independent statement with PostgreSQL's own grammar."""

    source = definition.strip()
    if not source:
        raise RoutineDefinitionError(
            "Enter one CREATE FUNCTION or CREATE PROCEDURE statement",
            code="definition_required",
        )
    try:
        statements = parse_sql(source)
    except ParseError as error:
        raise RoutineDefinitionError(
            "The routine definition is not valid PostgreSQL syntax",
            code="invalid_syntax",
        ) from error
    if len(statements) != 1:
        raise RoutineDefinitionError(
            "A routine definition must contain exactly one statement",
            code="multiple_statements",
        )
    statement = statements[0].stmt
    if not isinstance(statement, ast.CreateFunctionStmt):
        raise RoutineDefinitionError(
            "The definition must be a CREATE FUNCTION or CREATE PROCEDURE statement",
            code="create_routine_required",
        )
    if len(statement.funcname) != 1:
        raise RoutineDefinitionError(
            "Omit the schema from the routine name; the workspace target supplies it",
            code="target_independent_name_required",
        )
    name = statement.funcname[0].sval
    if not name:
        raise RoutineDefinitionError(
            "The routine name could not be derived",
            code="name_required",
        )

    language_options = [
        option
        for option in statement.options or ()
        if option.defname == "language"
    ]
    if (
        len(language_options) != 1
        or not isinstance(language_options[0].arg, ast.String)
        or not language_options[0].arg.sval
    ):
        raise RoutineDefinitionError(
            "The routine must declare exactly one LANGUAGE",
            code="language_required",
        )
    kind = "procedure" if statement.is_procedure else "function"
    parameters = tuple(statement.parameters or ())
    return_type = _return_type(statement, parameters)
    if kind == "function" and return_type is None:
        raise RoutineDefinitionError(
            "A function must declare exactly one RETURNS contract",
            code="return_type_required",
        )
    input_parameters = [parameter for parameter in parameters if _input_parameter(parameter)]
    rendered_arguments = ", ".join(_render_parameter(parameter) for parameter in input_parameters)
    identity_arguments = ", ".join(
        (
            "VARIADIC "
            if parameter.mode == FunctionParameterMode.FUNC_PARAM_VARIADIC
            else ""
        )
        + _render(parameter.argType)
        for parameter in input_parameters
    )
    return RoutineDefinition(
        name=name,
        kind=kind,
        arguments=rendered_arguments,
        identity_arguments=identity_arguments,
        return_type=return_type,
        language=language_options[0].arg.sval.lower(),
    )
