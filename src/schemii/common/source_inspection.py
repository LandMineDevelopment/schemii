"""Shared, bounded Python source inspection for developer-facing maps."""

from __future__ import annotations

import ast
import builtins
import hashlib
import io
import inspect
import keyword
import textwrap
import token
import tokenize
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, fields as dataclass_fields, is_dataclass
from typing import Any, get_args, get_origin

from pydantic import BaseModel


FIRST_PARTY_MODULE = "schemii"
DEFAULT_DOCSTRING_LIMIT = 4_000
DEFAULT_HIGHLIGHT_SEGMENT_LIMIT = 12_000
DEFAULT_SOURCE_LIMIT = 32_000
DEFAULT_TOTAL_SOURCE_LIMIT = 512_000
DEFAULT_OBJECT_LIMIT = 160
_BUILTIN_NAMES = frozenset(dir(builtins))
_DATA_SHAPE_FIELD_LIMIT = 64
_CALL_EXPRESSION_LIMIT = 2_000


@dataclass(frozen=True)
class SourceInspectionLimits:
    """Hard document bounds shared by every source-inspection consumer."""

    object_limit: int = DEFAULT_OBJECT_LIMIT
    source_limit: int = DEFAULT_SOURCE_LIMIT
    total_source_limit: int = DEFAULT_TOTAL_SOURCE_LIMIT
    docstring_limit: int = DEFAULT_DOCSTRING_LIMIT
    highlight_segment_limit: int = DEFAULT_HIGHLIGHT_SEGMENT_LIMIT


def is_first_party(subject: object) -> bool:
    module = getattr(subject, "__module__", "") or ""
    if not isinstance(module, str):
        return False
    return module == FIRST_PARTY_MODULE or module.startswith(f"{FIRST_PARTY_MODULE}.")


def python_object_id(subject: object) -> str:
    unwrapped = inspect.unwrap(subject)
    return f"python:{unwrapped.__module__}:{unwrapped.__qualname__}"


def source_path(subject: object) -> str:
    module = getattr(subject, "__module__", FIRST_PARTY_MODULE)
    return f"{module.replace('.', '/')}.py"


def source_docstring(source: str | None, *, limit: int) -> tuple[str | None, bool]:
    if source is None:
        return None, False
    try:
        tree = ast.parse(textwrap.dedent(source))
    except (IndentationError, SyntaxError):
        return None, False
    definition = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ),
        None,
    )
    value = ast.get_docstring(definition, clean=True) if definition else None
    if not value:
        return None, False
    if len(value) <= limit:
        return value, False
    return value[:limit].rstrip(), True


def python_source_segments(source: str | None, *, limit: int) -> list[list[str]]:
    """Tokenize one displayed excerpt without evaluating its source."""

    if not source:
        return []
    lines = source.splitlines(keepends=True)
    line_offsets: list[int] = []
    offset = 0
    for line in lines:
        line_offsets.append(offset)
        offset += len(line)

    def source_offset(position: tuple[int, int]) -> int:
        row, column = position
        if row < 1 or row > len(line_offsets):
            return len(source)
        return min(line_offsets[row - 1] + column, len(source))

    segments: list[list[str]] = []

    def append(kind: str, value: str) -> None:
        if not value:
            return
        if segments and segments[-1][0] == kind:
            segments[-1][1] += value
        else:
            segments.append([kind, value])

    cursor = 0
    previous_significant = ""
    decorator = False
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for item in tokens:
            start = source_offset(item.start)
            end = source_offset(item.end)
            if start < cursor or end < start:
                continue
            append("plain", source[cursor:start])
            kind = "plain"
            if item.type == token.COMMENT:
                kind = "comment"
            elif item.type == token.STRING:
                kind = "string"
            elif item.type == token.NUMBER:
                kind = "number"
            elif item.type == token.OP:
                kind = "operator"
            elif item.type == token.NAME:
                if keyword.iskeyword(item.string):
                    kind = "keyword"
                elif previous_significant in {"def", "class"}:
                    kind = "definition"
                elif decorator:
                    kind = "decorator"
                elif item.string in _BUILTIN_NAMES:
                    kind = "builtin"
            append(kind, source[start:end])
            cursor = end
            if item.type not in {
                token.INDENT,
                token.DEDENT,
                token.NEWLINE,
                tokenize.NL,
                token.ENDMARKER,
            }:
                decorator = item.type == token.OP and item.string == "@"
                previous_significant = item.string
            if len(segments) > limit:
                return []
    except (IndentationError, tokenize.TokenError):
        pass
    append("plain", source[cursor:])
    return segments


def source_kind(subject: object, fallback: str | None = None) -> str:
    if fallback:
        return fallback
    if inspect.isclass(subject):
        if issubclass(subject, BaseModel):
            return "model"
        if issubclass(subject, Exception):
            return "outcome"
        return "class"
    qualname = getattr(subject, "__qualname__", "")
    owner = qualname.rsplit(".", 1)[0] if "." in qualname else ""
    if "Repository" in owner:
        return "repository"
    if "Gateway" in owner:
        return "gateway"
    if "Service" in owner:
        return "service"
    if getattr(subject, "__name__", "").startswith("_"):
        return "helper"
    return "function"


def annotation_label(annotation: object) -> str:
    """Render one inspected type annotation without evaluating runtime values."""

    if annotation is inspect.Signature.empty:
        return "Any"
    if annotation is None or annotation is type(None):
        return "None"
    origin = get_origin(annotation)
    if origin is not None:
        arguments = get_args(annotation)
        origin_name = getattr(origin, "__name__", str(origin).replace("typing.", ""))
        if origin_name == "Annotated":
            return annotation_label(arguments[0]) if arguments else "Any"
        if origin_name in {"Union", "UnionType"}:
            return " | ".join(annotation_label(argument) for argument in arguments)
        if origin_name == "Literal":
            return "Literal[{}]".format(
                ", ".join(repr(argument) for argument in arguments)
            )
        return f"{origin_name}[{', '.join(annotation_label(argument) for argument in arguments)}]"
    if inspect.isclass(annotation):
        return annotation.__name__
    if callable(annotation) and isinstance(getattr(annotation, "__name__", None), str):
        return annotation.__name__
    return str(annotation).replace("typing.", "")


def callable_signature(subject: object) -> dict[str, Any]:
    """Derive a callable's public parameter and return contract from its signature."""

    shape = source_data_shape(subject)
    if shape is not None:
        return {
            "parameters": [
                {
                    "name": field["name"],
                    "attribute": field["attribute"],
                    "kind": "keyword_only",
                    "annotation": field["annotation"],
                    "required": field["required"],
                }
                for field in shape["fields"]
            ],
            "returnAnnotation": shape["name"],
            "available": True,
        }
    try:
        signature = inspect.signature(subject, eval_str=True)
    except (NameError, TypeError, ValueError):
        return {"parameters": [], "returnAnnotation": "Any", "available": False}
    parameters = [
        {
            "name": parameter.name,
            "kind": parameter.kind.name.lower(),
            "annotation": annotation_label(parameter.annotation),
            "required": parameter.default is inspect.Signature.empty,
        }
        for parameter in signature.parameters.values()
        if parameter.name not in {"self", "cls"}
    ]
    return {
        "parameters": parameters,
        "returnAnnotation": (
            subject.__name__
            if inspect.isclass(subject)
            else annotation_label(signature.return_annotation)
        ),
        "available": True,
    }


def call_argument_bindings(node: ast.Call, called: object) -> list[dict[str, str]]:
    """Map source call expressions onto the called signature's parameters."""

    signature = callable_signature(called)
    parameters = signature["parameters"]
    positional = [
        parameter
        for parameter in parameters
        if parameter["kind"]
        in {"positional_only", "positional_or_keyword", "var_positional"}
    ]
    var_positional = next(
        (parameter for parameter in positional if parameter["kind"] == "var_positional"),
        None,
    )
    var_keyword = next(
        (parameter for parameter in parameters if parameter["kind"] == "var_keyword"),
        None,
    )

    def expression(value: ast.AST) -> str:
        rendered = ast.unparse(value)
        return rendered[:_CALL_EXPRESSION_LIMIT]

    bindings: list[dict[str, str]] = []
    position = 0
    for argument in node.args:
        if isinstance(argument, ast.Starred):
            parameter = var_positional
            rendered = f"*{expression(argument.value)}"
        else:
            parameter = positional[position] if position < len(positional) else var_positional
            rendered = expression(argument)
            if parameter is not var_positional:
                position += 1
        bindings.append(
            {
                "parameter": parameter["name"] if parameter else f"arg{position + 1}",
                "annotation": parameter["annotation"] if parameter else "Any",
                "expression": rendered,
                "kind": "positional",
            }
        )
    by_name = {
        name: parameter
        for parameter in parameters
        for name in {parameter["name"], parameter.get("attribute", parameter["name"])}
    }
    for keyword_argument in node.keywords:
        parameter = by_name.get(keyword_argument.arg) if keyword_argument.arg else var_keyword
        bindings.append(
            {
                "parameter": (
                    keyword_argument.arg
                    or (parameter["name"] if parameter else "kwargs")
                ),
                "annotation": parameter["annotation"] if parameter else "Any",
                "expression": (
                    expression(keyword_argument.value)
                    if keyword_argument.arg
                    else f"**{expression(keyword_argument.value)}"
                ),
                "kind": "keyword" if keyword_argument.arg else "keyword-unpack",
            }
        )
    return bindings


def source_data_shape(subject: object) -> dict[str, Any] | None:
    """Derive a bounded field-level value shape for typed source classes."""

    if not inspect.isclass(subject):
        return None
    discovered: list[dict[str, Any]] = []
    truncated = False
    if issubclass(subject, BaseModel):
        model_fields = list(subject.model_fields.items())
        for name, field in model_fields[:_DATA_SHAPE_FIELD_LIMIT]:
            alias = field.alias if isinstance(field.alias, str) else name
            discovered.append(
                {
                    "name": alias,
                    "attribute": name,
                    "annotation": annotation_label(field.annotation),
                    "required": field.is_required(),
                }
            )
        truncated = len(model_fields) > _DATA_SHAPE_FIELD_LIMIT
    elif is_dataclass(subject):
        fields = list(dataclass_fields(subject))
        for field in fields[:_DATA_SHAPE_FIELD_LIMIT]:
            discovered.append(
                {
                    "name": field.name,
                    "attribute": field.name,
                    "annotation": annotation_label(field.type),
                    "required": True,
                }
            )
        truncated = len(fields) > _DATA_SHAPE_FIELD_LIMIT
    else:
        return None
    return {
        "kind": "object",
        "name": subject.__name__,
        "fields": discovered,
        "truncated": truncated,
    }


def pydantic_model_tree(
    roots: Iterable[object],
    *,
    limit: int = 32,
) -> tuple[list[type[BaseModel]], bool]:
    """Derive the bounded Pydantic model closure of inspected annotations."""

    def model_types(annotation: object) -> Iterable[type[BaseModel]]:
        if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
            yield annotation
            return
        for argument in get_args(annotation):
            yield from model_types(argument)

    discovered: list[type[BaseModel]] = []
    queued = list(roots)
    seen: set[type[BaseModel]] = set()
    while queued and len(discovered) < limit:
        candidate = queued.pop(0)
        for model in model_types(candidate):
            if model in seen or not is_first_party(model):
                continue
            if len(discovered) >= limit:
                return discovered, True
            seen.add(model)
            discovered.append(model)
            queued.extend(field.annotation for field in model.model_fields.values())
    return discovered, bool(queued)


def source_metadata(
    subject: object,
    *,
    kind: str | None = None,
    source_budget: int = DEFAULT_SOURCE_LIMIT,
    limits: SourceInspectionLimits | None = None,
) -> dict[str, Any]:
    active_limits = limits or SourceInspectionLimits()
    unwrapped = inspect.unwrap(subject)
    full_source: str | None = None
    source_excerpt: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    definition_line: int | None = None
    truncated = False
    try:
        lines, start_line = inspect.getsourcelines(unwrapped)
        full_source = "".join(lines)
        end_line = start_line + len(lines) - 1
        for offset, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped.startswith(("def ", "async def ", "class ")):
                definition_line = start_line + offset
                break
        excerpt_limit = min(active_limits.source_limit, max(0, source_budget))
        source_excerpt = full_source[:excerpt_limit]
        if len(full_source) > excerpt_limit:
            newline = source_excerpt.rfind("\n")
            if newline >= 0:
                source_excerpt = source_excerpt[: newline + 1]
            truncated = True
    except (OSError, TypeError):
        pass
    docstring, docstring_truncated = source_docstring(
        full_source,
        limit=active_limits.docstring_limit,
    )
    digest = hashlib.sha256((full_source or "").encode("utf-8")).hexdigest()
    return {
        "id": python_object_id(unwrapped),
        "name": getattr(unwrapped, "__name__", unwrapped.__class__.__name__),
        "qualname": getattr(unwrapped, "__qualname__", ""),
        "module": getattr(unwrapped, "__module__", ""),
        "kind": source_kind(unwrapped, kind),
        "dataShape": source_data_shape(unwrapped),
        "docstring": docstring,
        "docstringTruncated": docstring_truncated,
        "location": {
            "path": source_path(unwrapped),
            "sourceStartLine": start_line,
            "definitionLine": definition_line,
            "endLine": end_line,
        },
        "source": {
            "available": full_source is not None,
            "sha256": digest,
            "text": source_excerpt,
            "tokens": python_source_segments(
                source_excerpt,
                limit=active_limits.highlight_segment_limit,
            ),
            "truncated": truncated,
        },
    }


class SourceRegistry:
    """Deduplicate inspected objects while enforcing one shared source budget."""

    def __init__(self, limits: SourceInspectionLimits | None = None) -> None:
        self.limits = limits or SourceInspectionLimits()
        self._objects: dict[str, dict[str, Any]] = {}
        self.source_characters = 0
        self.objects_truncated = False

    @property
    def objects(self) -> list[dict[str, Any]]:
        return list(self._objects.values())

    def get(self, object_id: str) -> dict[str, Any] | None:
        return self._objects.get(object_id)

    def register(self, subject: object, *, kind: str | None = None) -> str | None:
        if not is_first_party(subject):
            return None
        subject_id = python_object_id(subject)
        if subject_id not in self._objects:
            if len(self._objects) >= self.limits.object_limit:
                self.objects_truncated = True
                return None
            metadata = source_metadata(
                subject,
                kind=kind,
                source_budget=self.limits.total_source_limit - self.source_characters,
                limits=self.limits,
            )
            self._objects[subject_id] = metadata
            self.source_characters += len(metadata["source"]["text"] or "")
        return subject_id

    def analysis_limits(self) -> dict[str, int]:
        return {
            "objectLimit": self.limits.object_limit,
            "docstringLimit": self.limits.docstring_limit,
            "highlightSegmentLimit": self.limits.highlight_segment_limit,
            "sourceLimit": self.limits.source_limit,
            "totalSourceLimit": self.limits.total_source_limit,
        }


def attribute_parts(node: ast.AST) -> list[str] | None:
    if isinstance(node, ast.Name):
        return [node.id]
    if not isinstance(node, ast.Attribute):
        return None
    parent = attribute_parts(node.value)
    return [*parent, node.attr] if parent else None


class DirectCallCollector(ast.NodeVisitor):
    """Collect direct calls in Python evaluation order, excluding nested scopes."""

    def __init__(self) -> None:
        self.calls: list[ast.Call] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 - ast visitor API
        self.visit(node.func)
        for argument in node.args:
            self.visit(argument)
        for keyword_argument in node.keywords:
            self.visit(keyword_argument.value)
        self.calls.append(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        return


@dataclass(frozen=True)
class SourceControlContext:
    """One source-derived control region containing a call site."""

    kind: str
    label: str
    line: int


@dataclass(frozen=True)
class SourceCallSite:
    """A direct call paired with its enclosing source control regions."""

    node: ast.Call
    contexts: tuple[SourceControlContext, ...]


def _expression_label(node: ast.AST | None, fallback: str) -> str:
    if node is None:
        return fallback
    try:
        value = ast.unparse(node).strip()
    except (TypeError, ValueError):
        return fallback
    if not value:
        return fallback
    return value if len(value) <= 180 else f"{value[:177].rstrip()}…"


class SourceCallSiteCollector(DirectCallCollector):
    """Collect calls in evaluation order without discarding branch context."""

    def __init__(self) -> None:
        super().__init__()
        self.sites: list[SourceCallSite] = []
        self._contexts: list[SourceControlContext] = []

    def _visit_region(
        self,
        statements: Sequence[ast.stmt],
        *,
        kind: str,
        label: str,
        line: int,
    ) -> None:
        self._contexts.append(SourceControlContext(kind, label, line))
        try:
            for statement in statements:
                self.visit(statement)
        finally:
            self._contexts.pop()

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 - ast visitor API
        self.visit(node.func)
        for argument in node.args:
            self.visit(argument)
        for keyword_argument in node.keywords:
            self.visit(keyword_argument.value)
        self.calls.append(node)
        self.sites.append(SourceCallSite(node, tuple(self._contexts)))

    def visit_If(self, node: ast.If) -> None:  # noqa: N802 - ast visitor API
        self.visit(node.test)
        label = _expression_label(node.test, "condition")
        self._visit_region(node.body, kind="if", label=label, line=node.lineno)
        if node.orelse:
            self._visit_region(node.orelse, kind="else", label=label, line=node.lineno)

    def visit_Try(self, node: ast.Try) -> None:  # noqa: N802 - ast visitor API
        self._visit_region(node.body, kind="try", label="try", line=node.lineno)
        for handler in node.handlers:
            label = _expression_label(handler.type, "exception")
            self._visit_region(
                handler.body,
                kind="except",
                label=label,
                line=handler.lineno,
            )
        if node.orelse:
            self._visit_region(
                node.orelse,
                kind="try-else",
                label="success",
                line=node.lineno,
            )
        if node.finalbody:
            self._visit_region(
                node.finalbody,
                kind="finally",
                label="always",
                line=node.lineno,
            )

    def visit_TryStar(self, node: ast.TryStar) -> None:  # noqa: N802 - ast visitor API
        self.visit_Try(node)

    def visit_With(self, node: ast.With) -> None:  # noqa: N802 - ast visitor API
        label = ", ".join(
            _expression_label(item.context_expr, "context") for item in node.items
        )
        self._contexts.append(SourceControlContext("with", label, node.lineno))
        try:
            for item in node.items:
                self.visit(item.context_expr)
            for statement in node.body:
                self.visit(statement)
        finally:
            self._contexts.pop()

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:  # noqa: N802
        self.visit_With(node)

    def visit_For(self, node: ast.For) -> None:  # noqa: N802 - ast visitor API
        self.visit(node.iter)
        label = _expression_label(node.target, "item")
        self._visit_region(node.body, kind="for", label=label, line=node.lineno)
        if node.orelse:
            self._visit_region(
                node.orelse,
                kind="loop-else",
                label=label,
                line=node.lineno,
            )

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:  # noqa: N802
        self.visit_For(node)

    def visit_While(self, node: ast.While) -> None:  # noqa: N802 - ast visitor API
        self.visit(node.test)
        label = _expression_label(node.test, "condition")
        self._visit_region(node.body, kind="while", label=label, line=node.lineno)
        if node.orelse:
            self._visit_region(
                node.orelse,
                kind="loop-else",
                label=label,
                line=node.lineno,
            )

    def visit_Raise(self, node: ast.Raise) -> None:  # noqa: N802 - ast visitor API
        if node.exc is None:
            return
        self._contexts.append(SourceControlContext("raise", "raised outcome", node.lineno))
        try:
            self.visit(node.exc)
        finally:
            self._contexts.pop()

    def visit_Return(self, node: ast.Return) -> None:  # noqa: N802 - ast visitor API
        if node.value is None:
            return
        self._contexts.append(SourceControlContext("return", "returned result", node.lineno))
        try:
            self.visit(node.value)
        finally:
            self._contexts.pop()


def direct_call_sites(subject: object) -> list[SourceCallSite]:
    """Return direct call sites and bounded control labels for one callable."""

    try:
        source = inspect.getsource(inspect.unwrap(subject))
        tree = ast.parse(textwrap.dedent(source))
    except (IndentationError, OSError, SyntaxError, TypeError):
        return []
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ),
        None,
    )
    if function is None:
        return []
    collector = SourceCallSiteCollector()
    for statement in function.body:
        collector.visit(statement)
    return collector.sites


def direct_call_nodes(subject: object) -> list[ast.Call]:
    try:
        source = inspect.getsource(inspect.unwrap(subject))
        tree = ast.parse(textwrap.dedent(source))
    except (IndentationError, OSError, SyntaxError, TypeError):
        return []
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ),
        None,
    )
    if function is None:
        return []
    collector = DirectCallCollector()
    for statement in function.body:
        collector.visit(statement)
    return collector.calls


CallResolver = Callable[[ast.AST], tuple[object | None, str]]
CallDecorator = Callable[[ast.Call], Mapping[str, Any]]


def inspect_direct_calls(
    subject: object,
    *,
    source_start_line: int | None,
    resolver: CallResolver,
    register: Callable[..., str | None],
    limit: int,
    decorate: CallDecorator | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Resolve one callable's direct first-party calls with stable bounds."""

    calls: list[dict[str, Any]] = []
    truncated = False
    for node in direct_call_nodes(subject):
        called, resolution = resolver(node.func)
        if called is None or not is_first_party(called):
            continue
        if len(calls) >= limit:
            truncated = True
            continue
        subject_id = register(called)
        if not subject_id:
            continue
        record: dict[str, Any] = {
            "sequence": len(calls) + 1,
            "expression": ast.unparse(node.func),
            "objectId": subject_id,
            "resolution": resolution,
            "line": source_start_line + node.lineno - 1
            if source_start_line is not None
            else None,
        }
        if decorate is not None:
            record.update(decorate(node))
        calls.append(record)
    return calls, truncated
