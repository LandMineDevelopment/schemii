"""Source-derived PostgreSQL gateway metadata for the developer DB map."""

from __future__ import annotations

import ast
import hashlib
import importlib
import inspect
import re
from types import ModuleType
from typing import Any

from fastapi import FastAPI

from schemii.common.source_inspection import (
    SourceInspectionLimits,
    SourceRegistry,
    attribute_parts,
    callable_signature,
    inspect_direct_calls,
    is_first_party,
    python_object_id,
)

from .gateway import PostgresGateway


DEVELOPER_DATABASE_PATH = "/_developer/database"
_MAX_CALLABLES = 96
_MAX_CALL_DEPTH = 8
_MAX_CALLS_PER_CALLABLE = 64
_MAX_QUERIES = 64
_MAX_QUERY_CHARACTERS = 64_000
_MAX_TOTAL_QUERY_CHARACTERS = 512_000
_MAX_INLINE_STATEMENTS = 32
_MAX_INLINE_EXPRESSION_CHARACTERS = 2_000
_SQL_MARKER = re.compile(r"/\*\s*([a-z][a-z0-9_]*)\s*\*/", re.IGNORECASE)
_SQL_CATALOG_SOURCE = re.compile(
    r"\b(?:FROM|JOIN)\s+(pg_catalog\.[a-z_][a-z0-9_]*)",
    re.IGNORECASE,
)
_SQL_ALIAS = re.compile(
    r'\bAS\s+(?:"([^"]+)"|([a-z_][a-z0-9_$]*))\s*$',
    re.IGNORECASE | re.DOTALL,
)
_SQL_TRAILING_IDENTIFIER = re.compile(
    r'(?:"([^"]+)"|([a-z_][a-z0-9_$]*))\s*$',
    re.IGNORECASE,
)
_SQL_LEADING_COMMENTS = re.compile(
    r"\A\s*(?:(?:/\*.*?\*/)|(?:--[^\n]*(?:\n|\Z)))\s*",
    re.DOTALL,
)
_SQL_STATEMENTS = frozenset(
    {
        "ALTER",
        "BEGIN",
        "CALL",
        "CREATE",
        "DELETE",
        "DROP",
        "INSERT",
        "MERGE",
        "SELECT",
        "SET",
        "SHOW",
        "TRUNCATE",
        "UPDATE",
        "WITH",
    }
)
_READ_ONLY_STATEMENTS = frozenset({"BEGIN", "SELECT", "SET", "SHOW", "WITH"})


def _contract_parameters(subject: object) -> list[dict[str, Any]]:
    return callable_signature(subject)["parameters"]


def _return_annotation(subject: object) -> str:
    return callable_signature(subject)["returnAnnotation"]


def _gateway_service(services: object) -> tuple[str, PostgresGateway]:
    matches: list[tuple[str, PostgresGateway]] = []
    for name, candidate in vars(services).items():
        try:
            if isinstance(candidate, PostgresGateway):
                matches.append((name, candidate))
        except TypeError:
            continue
    if len(matches) != 1:
        raise RuntimeError("Developer database inspection requires one PostgreSQL gateway")
    return matches[0]


def _contract_methods() -> list[tuple[str, object]]:
    return [
        (name, subject)
        for name, subject in vars(PostgresGateway).items()
        if not name.startswith("_") and inspect.isfunction(subject)
    ]


def _resolve_callable(
    node: ast.AST,
    *,
    callable_subject: object,
    gateway_type: type[object],
) -> tuple[object | None, str]:
    subject = inspect.unwrap(callable_subject)
    globals_by_name = getattr(subject, "__globals__", {})
    if isinstance(node, ast.Name):
        return globals_by_name.get(node.id), "module"
    if not isinstance(node, ast.Attribute):
        return None, "unresolved"
    parts = attribute_parts(node)
    if parts and len(parts) == 2 and parts[0] in {"self", "cls"}:
        return getattr(gateway_type, parts[1], None), "runtime-binding"
    if isinstance(node.value, ast.Name):
        owner = globals_by_name.get(node.value.id)
        return getattr(owner, node.attr, None), "module"
    return None, "unresolved"


def _imported_symbol_module(subject: object, symbol: str) -> ModuleType | None:
    module = inspect.getmodule(inspect.unwrap(subject))
    if module is None:
        return None
    try:
        source = inspect.getsource(module)
        tree = ast.parse(source)
    except (OSError, SyntaxError, TypeError):
        return module if symbol in vars(module) else None
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == symbol for target in targets):
                return module
        if not isinstance(node, ast.ImportFrom):
            continue
        if not any((alias.asname or alias.name) == symbol for alias in node.names):
            continue
        relative = f"{'.' * node.level}{node.module or ''}"
        try:
            module_name = importlib.util.resolve_name(relative, module.__package__ or "")
            return importlib.import_module(module_name)
        except (ImportError, ValueError):
            return None
    return None


def _assignment_location(module: ModuleType | None, name: str) -> dict[str, Any]:
    if module is None:
        return {"path": "", "definitionLine": None, "endLine": None}
    try:
        source = inspect.getsource(module)
        tree = ast.parse(source)
    except (OSError, SyntaxError, TypeError):
        return {
            "path": f"{module.__name__.replace('.', '/')}.py",
            "definitionLine": None,
            "endLine": None,
        }
    for node in tree.body:
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            return {
                "path": f"{module.__name__.replace('.', '/')}.py",
                "definitionLine": node.lineno,
                "endLine": getattr(node, "end_lineno", node.lineno),
            }
    return {
        "path": f"{module.__name__.replace('.', '/')}.py",
        "definitionLine": None,
        "endLine": None,
    }


def _sql_statement(sql: str) -> str:
    candidate = sql
    while True:
        stripped = _SQL_LEADING_COMMENTS.sub("", candidate, count=1)
        if stripped == candidate:
            break
        candidate = stripped
    match = re.match(r"\s*([A-Za-z]+)", candidate)
    return match.group(1).upper() if match else "SQL"


def _sql_top_level_words(sql: str) -> list[tuple[str, int, int]]:
    """Return unquoted words outside comments, strings, and parentheses."""

    words: list[tuple[str, int, int]] = []
    depth = 0
    index = 0
    while index < len(sql):
        character = sql[index]
        following = sql[index + 1] if index + 1 < len(sql) else ""
        if character == "'":
            index += 1
            while index < len(sql):
                if sql[index] == "'":
                    if index + 1 < len(sql) and sql[index + 1] == "'":
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            continue
        if character == '"':
            index += 1
            while index < len(sql):
                if sql[index] == '"':
                    if index + 1 < len(sql) and sql[index + 1] == '"':
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            continue
        if character == "-" and following == "-":
            newline = sql.find("\n", index + 2)
            index = len(sql) if newline < 0 else newline + 1
            continue
        if character == "/" and following == "*":
            end = sql.find("*/", index + 2)
            index = len(sql) if end < 0 else end + 2
            continue
        if character == "(":
            depth += 1
            index += 1
            continue
        if character == ")":
            depth = max(0, depth - 1)
            index += 1
            continue
        if depth == 0 and (character.isalpha() or character == "_"):
            start = index
            index += 1
            while index < len(sql) and (
                sql[index].isalnum() or sql[index] in {"_", "$"}
            ):
                index += 1
            words.append((sql[start:index].upper(), start, index))
            continue
        index += 1
    return words


def _split_sql_projection(projection: str) -> list[str]:
    fields: list[str] = []
    depth = 0
    start = 0
    index = 0
    while index < len(projection):
        character = projection[index]
        following = projection[index + 1] if index + 1 < len(projection) else ""
        if character in {"'", '"'}:
            quote = character
            index += 1
            while index < len(projection):
                if projection[index] == quote:
                    if index + 1 < len(projection) and projection[index + 1] == quote:
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            continue
        if character == "-" and following == "-":
            newline = projection.find("\n", index + 2)
            index = len(projection) if newline < 0 else newline + 1
            continue
        if character == "/" and following == "*":
            end = projection.find("*/", index + 2)
            index = len(projection) if end < 0 else end + 2
            continue
        if character == "(":
            depth += 1
        elif character == ")":
            depth = max(0, depth - 1)
        elif character == "," and depth == 0:
            fields.append(projection[start:index].strip())
            start = index + 1
        index += 1
    fields.append(projection[start:].strip())
    return [field for field in fields if field]


def _sql_result_columns(sql: str) -> list[str]:
    """Derive the outer SELECT row keys from the static SQL projection."""

    words = _sql_top_level_words(sql)
    selected = next(
        (
            (end, position)
            for position, (word, _start, end) in enumerate(words)
            if word == "SELECT"
        ),
        None,
    )
    if selected is None:
        return []
    select_end, select_position = selected
    from_start = next(
        (start for word, start, _end in words[select_position + 1 :] if word == "FROM"),
        len(sql),
    )
    columns: list[str] = []
    for expression in _split_sql_projection(sql[select_end:from_start]):
        alias = _SQL_ALIAS.search(expression)
        terminal = alias or _SQL_TRAILING_IDENTIFIER.search(expression)
        if terminal is None:
            continue
        name = terminal.group(1) or terminal.group(2)
        if name and name not in columns:
            columns.append(name)
    return columns


def _query_record(name: str, sql: str, *, subject: object) -> dict[str, Any]:
    module = _imported_symbol_module(subject, name)
    marker_match = _SQL_MARKER.search(sql)
    marker = marker_match.group(1) if marker_match else ""
    return {
        "id": f"sql:{module.__name__ if module else 'unknown'}:{name}",
        "name": name,
        "marker": marker,
        "statement": _sql_statement(sql),
        "placeholderCount": len(re.findall(r"(?<!%)%s", sql)),
        "resultColumns": _sql_result_columns(sql),
        "catalogObjects": list(dict.fromkeys(_SQL_CATALOG_SOURCE.findall(sql))),
        "location": _assignment_location(module, name),
        "sha256": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
        "sql": sql[:_MAX_QUERY_CHARACTERS],
        "truncated": len(sql) > _MAX_QUERY_CHARACTERS,
    }


class _QueryRegistry:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.characters = 0
        self.truncated = False

    def register(self, name: str, sql: str, *, subject: object) -> str | None:
        candidate = _query_record(name, sql, subject=subject)
        query_id = candidate["id"]
        if query_id in self.records:
            return query_id
        remaining = _MAX_TOTAL_QUERY_CHARACTERS - self.characters
        if len(self.records) >= _MAX_QUERIES or remaining <= 0:
            self.truncated = True
            return None
        if len(candidate["sql"]) > remaining:
            candidate["sql"] = candidate["sql"][:remaining]
            candidate["truncated"] = True
            self.truncated = True
        self.records[query_id] = candidate
        self.characters += len(candidate["sql"])
        return query_id


def _query_references(
    node: ast.Call,
    *,
    subject: object,
    registry: _QueryRegistry,
) -> list[str]:
    globals_by_name = getattr(inspect.unwrap(subject), "__globals__", {})
    references: list[str] = []
    argument_nodes = [*node.args, *(keyword.value for keyword in node.keywords)]

    class QueryNameCollector(ast.NodeVisitor):
        def __init__(self) -> None:
            self.names: list[ast.Name] = []

        def visit_Name(self, candidate: ast.Name) -> None:  # noqa: N802
            self.names.append(candidate)

        def visit_Call(self, candidate: ast.Call) -> None:  # noqa: N802
            # Nested calls are emitted separately by the shared direct-call collector.
            return

    collector = QueryNameCollector()
    for argument in argument_nodes:
        collector.visit(argument)
    names = sorted(
        collector.names,
        key=lambda candidate: (candidate.lineno, candidate.col_offset),
    )
    for candidate in names:
        value = globals_by_name.get(candidate.id)
        if not candidate.id.endswith("_QUERY") or not isinstance(value, str):
            continue
        query_id = registry.register(candidate.id, value, subject=subject)
        if query_id and query_id not in references:
            references.append(query_id)
    return references


def _literal_sql(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            value.value
            for value in node.values
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
        )
    return ""


def _sql_literal_expressions(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.IfExp):
        return [
            *_sql_literal_expressions(node.body),
            *_sql_literal_expressions(node.orelse),
        ]
    return [node] if _literal_sql(node).strip() else []


def _inline_statements(subject: object, *, source_start_line: int | None) -> list[dict[str, Any]]:
    try:
        source = inspect.getsource(inspect.unwrap(subject))
        tree = ast.parse(inspect.cleandoc(source))
    except (IndentationError, OSError, SyntaxError, TypeError):
        return []
    statements: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for argument in [*node.args, *(keyword.value for keyword in node.keywords)]:
            for variant, expression_node in enumerate(_sql_literal_expressions(argument)):
                literal = _literal_sql(expression_node).strip()
                statement = _sql_statement(literal) if literal else ""
                if statement not in _SQL_STATEMENTS:
                    continue
                expression = ast.unparse(expression_node)
                statements.append(
                    {
                        "id": f"inline:{python_object_id(subject)}:{node.lineno}:{node.col_offset}:{variant}",
                        "statement": statement,
                        "expression": expression[:_MAX_INLINE_EXPRESSION_CHARACTERS],
                        "readOnly": statement in _READ_ONLY_STATEMENTS,
                        "line": source_start_line + node.lineno - 1
                        if source_start_line is not None
                        else None,
                        "truncated": len(expression) > _MAX_INLINE_EXPRESSION_CHARACTERS,
                    }
                )
                if len(statements) >= _MAX_INLINE_STATEMENTS:
                    return statements
    statements.sort(key=lambda item: item["line"] or 0)
    return statements


def _called_kind(subject: object) -> str | None:
    name = getattr(subject, "__name__", "")
    if name.startswith("_") and not inspect.isclass(subject):
        return "helper"
    return None


def build_developer_database_document(application: FastAPI) -> dict[str, Any]:
    """Describe the installed PostgreSQL contract and implementation from source."""

    service_name, gateway = _gateway_service(application.state.services)
    gateway_type = type(gateway)
    registry = SourceRegistry(SourceInspectionLimits())
    query_registry = _QueryRegistry()
    contract_id = registry.register(PostgresGateway, kind="gateway-contract")
    implementation_id = registry.register(gateway_type, kind="gateway-implementation")
    operations: list[dict[str, Any]] = []
    queued: list[tuple[object, int]] = []

    for name, contract_method in _contract_methods():
        implementation = getattr(gateway_type, name, None)
        if implementation is None or not is_first_party(implementation):
            continue
        contract_method_id = registry.register(contract_method, kind="gateway-contract")
        implementation_method_id = registry.register(
            implementation,
            kind="gateway-operation",
        )
        if contract_method_id is None or implementation_method_id is None:
            continue
        queued.append((implementation, 0))
        operations.append(
            {
                "id": name,
                "name": name,
                "contractObjectId": contract_method_id,
                "implementationObjectId": implementation_method_id,
                "parameters": _contract_parameters(contract_method),
                "returnAnnotation": _return_annotation(contract_method),
            }
        )

    callables: list[dict[str, Any]] = []
    callable_by_id: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    callable_graph_truncated = False
    while queued:
        callable_subject, depth = queued.pop(0)
        callable_id = python_object_id(callable_subject)
        if callable_id in seen:
            continue
        if len(callables) >= _MAX_CALLABLES or depth > _MAX_CALL_DEPTH:
            callable_graph_truncated = True
            continue
        seen.add(callable_id)
        registered_id = registry.register(
            callable_subject,
            kind=_called_kind(callable_subject),
        )
        if registered_id is None:
            callable_graph_truncated = True
            continue
        metadata = registry.get(registered_id)
        resolved: dict[str, object] = {}

        def resolve(node: ast.AST) -> tuple[object | None, str]:
            called, resolution = _resolve_callable(
                node,
                callable_subject=callable_subject,
                gateway_type=gateway_type,
            )
            if called is not None and is_first_party(called):
                resolved[python_object_id(called)] = called
            return called, resolution

        calls, calls_truncated = inspect_direct_calls(
            callable_subject,
            source_start_line=metadata["location"]["sourceStartLine"],
            resolver=resolve,
            register=lambda subject: registry.register(
                subject,
                kind=_called_kind(subject),
            ),
            limit=_MAX_CALLS_PER_CALLABLE,
            decorate=lambda node: {
                "queryIds": _query_references(
                    node,
                    subject=callable_subject,
                    registry=query_registry,
                )
            },
        )
        for call in calls:
            called = resolved.get(call["objectId"])
            if called is None:
                continue
            if inspect.isfunction(called) or inspect.ismethod(called):
                queued.append((called, depth + 1))
        record = {
            "objectId": registered_id,
            "depth": depth,
            "calls": calls,
            "queryIds": list(
                dict.fromkeys(
                    query_id
                    for call in calls
                    for query_id in call.get("queryIds", [])
                )
            ),
            "inlineStatements": _inline_statements(
                callable_subject,
                source_start_line=metadata["location"]["sourceStartLine"],
            ),
            "truncated": {"calls": calls_truncated},
        }
        callables.append(record)
        callable_by_id[registered_id] = record

    query_by_id = query_registry.records

    def implementation_digest(root_id: str) -> str:
        queued_ids = [root_id]
        visited_ids: set[str] = set()
        materials: list[str] = []
        while queued_ids:
            object_id = queued_ids.pop(0)
            if object_id in visited_ids:
                continue
            visited_ids.add(object_id)
            metadata = registry.get(object_id)
            if metadata is not None:
                materials.append(metadata["source"]["sha256"])
            callable_record = callable_by_id.get(object_id)
            if callable_record is None:
                continue
            for query_id in callable_record["queryIds"]:
                query = query_by_id.get(query_id)
                if query is not None:
                    materials.append(query["sha256"])
            queued_ids.extend(call["objectId"] for call in callable_record["calls"])
        return hashlib.sha256("|".join(materials).encode("utf-8")).hexdigest()

    for operation in operations:
        operation["implementationDigest"] = implementation_digest(
            operation["implementationObjectId"]
        )

    all_inline_statements = [
        statement
        for callable_record in callables
        for statement in callable_record["inlineStatements"]
    ]
    query_records = list(query_registry.records.values())
    return {
        "schemaVersion": 1,
        "analysis": {
            "kind": "bounded-python-source",
            "generation": "application-startup",
            "callGraph": "recursive-first-party-calls",
            "queryDiscovery": "referenced-static-query-constants",
            "serviceBinding": "runtime-protocol-match",
            "callableLimit": _MAX_CALLABLES,
            "callDepthLimit": _MAX_CALL_DEPTH,
            "callLimitPerCallable": _MAX_CALLS_PER_CALLABLE,
            "queryLimit": _MAX_QUERIES,
            "querySourceLimit": _MAX_QUERY_CHARACTERS,
            "totalQuerySourceLimit": _MAX_TOTAL_QUERY_CHARACTERS,
            **registry.analysis_limits(),
            "truncated": {
                "callables": callable_graph_truncated,
                "objects": registry.objects_truncated,
                "queries": query_registry.truncated,
                "source": any(
                    item["source"]["truncated"] for item in registry.objects
                ),
            },
            "syntaxHighlighting": "python-tokenize",
        },
        "gateway": {
            "serviceName": service_name,
            "contractObjectId": contract_id,
            "implementationObjectId": implementation_id,
        },
        "operations": operations,
        "callables": callables,
        "queries": query_records,
        "inlineStatements": all_inline_statements,
        "objects": registry.objects,
    }


def install_developer_database_inspection(application: FastAPI) -> None:
    """Derive and install one hidden database document for this app run."""

    document = build_developer_database_document(application)

    @application.get(DEVELOPER_DATABASE_PATH, include_in_schema=False)
    def developer_database() -> dict[str, Any]:
        return document
