"""Source-derived PostgreSQL enum and domain contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pglast import ast, parse_sql
from pglast.enums import ConstrType
from pglast.parser import ParseError
from pglast.stream import RawStream


class TypeDefinitionError(ValueError):
    """A user-defined type cannot be represented safely by Schemii."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class DomainCheckDefinition:
    """One optional named CHECK clause derived from a domain statement."""

    name: str | None
    expression: str


@dataclass(frozen=True, slots=True)
class TypeDefinition:
    """Metadata derived entirely from one CREATE TYPE or CREATE DOMAIN statement."""

    name: str
    kind: Literal["enum", "domain"]
    enum_values: tuple[str, ...]
    base_type: str | None
    base_type_name: str | None
    default_expression: str | None
    not_null: bool
    checks: tuple[DomainCheckDefinition, ...]
    collation: str | None


def _render(node: object) -> str:
    return RawStream()(node).strip()


def _name(parts: tuple[ast.String, ...], *, category: str) -> str:
    if len(parts) != 1 or not parts[0].sval:
        raise TypeDefinitionError(
            f"Omit the schema from the {category}; the workspace target supplies it",
            code="target_independent_name_required",
        )
    return parts[0].sval


def _collation(statement: ast.CreateDomainStmt) -> str | None:
    clause = statement.collClause
    if clause is None:
        return None
    parts = tuple(clause.collname or ())
    if len(parts) > 1 and parts[0].sval != "pg_catalog":
        raise TypeDefinitionError(
            "Omit the schema from a domain collation unless it is pg_catalog",
            code="target_independent_collation_required",
        )
    rendered = _render(clause)
    return rendered.removeprefix("COLLATE ").strip()


def _domain_definition(statement: ast.CreateDomainStmt) -> TypeDefinition:
    name = _name(tuple(statement.domainname), category="domain name")
    type_parts = tuple(statement.typeName.names or ())
    if len(type_parts) > 1 and type_parts[0].sval != "pg_catalog":
        raise TypeDefinitionError(
            "Omit the schema from a domain base type unless it is pg_catalog",
            code="target_independent_base_type_required",
        )
    if not type_parts:
        raise TypeDefinitionError(
            "The domain base type could not be derived",
            code="base_type_required",
        )

    defaults: list[str] = []
    nullability: list[ConstrType] = []
    checks: list[DomainCheckDefinition] = []
    for constraint in statement.constraints or ():
        if constraint.contype == ConstrType.CONSTR_DEFAULT:
            if constraint.raw_expr is None:
                raise TypeDefinitionError(
                    "The domain default expression could not be derived",
                    code="default_expression_required",
                )
            defaults.append(_render(constraint.raw_expr))
        elif constraint.contype in {
            ConstrType.CONSTR_NULL,
            ConstrType.CONSTR_NOTNULL,
        }:
            nullability.append(constraint.contype)
        elif constraint.contype == ConstrType.CONSTR_CHECK:
            if constraint.raw_expr is None:
                raise TypeDefinitionError(
                    "The domain check expression could not be derived",
                    code="check_expression_required",
                )
            checks.append(
                DomainCheckDefinition(
                    name=constraint.conname,
                    expression=_render(constraint.raw_expr),
                )
            )
        else:
            raise TypeDefinitionError(
                "The domain contains an unsupported constraint",
                code="unsupported_domain_constraint",
            )
    if len(defaults) > 1:
        raise TypeDefinitionError(
            "A domain may declare only one default expression",
            code="duplicate_default",
        )
    if len(nullability) > 1:
        raise TypeDefinitionError(
            "A domain may declare NULL or NOT NULL only once",
            code="duplicate_nullability",
        )
    named_checks = [check.name for check in checks if check.name is not None]
    if len(named_checks) != len(set(named_checks)):
        raise TypeDefinitionError(
            "Named domain constraints must be unique",
            code="duplicate_constraint_name",
        )

    return TypeDefinition(
        name=name,
        kind="domain",
        enum_values=(),
        base_type=_render(statement.typeName),
        base_type_name=(type_parts[-1].sval if len(type_parts) == 1 else None),
        default_expression=defaults[0] if defaults else None,
        not_null=(nullability == [ConstrType.CONSTR_NOTNULL]),
        checks=tuple(checks),
        collation=_collation(statement),
    )


def analyze_type_definition(definition: str) -> TypeDefinition:
    """Parse one target-independent enum or domain with PostgreSQL's grammar."""

    source = definition.strip()
    if not source:
        raise TypeDefinitionError(
            "Enter one CREATE TYPE AS ENUM or CREATE DOMAIN statement",
            code="definition_required",
        )
    try:
        statements = parse_sql(source)
    except ParseError as error:
        raise TypeDefinitionError(
            "The type definition is not valid PostgreSQL syntax",
            code="invalid_syntax",
        ) from error
    if len(statements) != 1:
        raise TypeDefinitionError(
            "A type definition must contain exactly one statement",
            code="multiple_statements",
        )
    statement = statements[0].stmt
    if isinstance(statement, ast.CreateEnumStmt):
        name = _name(tuple(statement.typeName), category="type name")
        values = tuple(value.sval for value in statement.vals or ())
        if not values:
            raise TypeDefinitionError(
                "An enum must contain at least one value",
                code="enum_value_required",
            )
        if len(values) != len(set(values)):
            raise TypeDefinitionError(
                "Enum values must be unique",
                code="duplicate_enum_value",
            )
        if any(len(value.encode("utf-8")) > 63 for value in values):
            raise TypeDefinitionError(
                "Enum values must fit PostgreSQL's 63-byte label limit",
                code="enum_value_too_long",
            )
        return TypeDefinition(
            name=name,
            kind="enum",
            enum_values=values,
            base_type=None,
            base_type_name=None,
            default_expression=None,
            not_null=False,
            checks=(),
            collation=None,
        )
    if isinstance(statement, ast.CreateDomainStmt):
        return _domain_definition(statement)
    raise TypeDefinitionError(
        "The definition must be CREATE TYPE AS ENUM or CREATE DOMAIN",
        code="create_type_required",
    )
