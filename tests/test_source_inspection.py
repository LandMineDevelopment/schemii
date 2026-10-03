from __future__ import annotations

import ast
import hashlib
import weakref

import pytest

from schemii.common import source_inspection
from schemii.common.source_inspection import (
    SourceInspectionLimits,
    SourceRegistry,
    callable_signature,
    direct_call_sites,
    source_metadata,
)


def _source_subject():
    return None


_source_subject.__module__ = "schemii.test_inspection"


class _SourceClass:
    pass


_SourceClass.__module__ = "schemii.test_inspection"


def test_unresolvable_third_party_annotation_cannot_break_startup_inspection() -> None:
    def external(value: object) -> object:
        return value

    external.__annotations__ = {
        "value": "dependency.MissingType",
        "return": "dependency.MissingType",
    }
    external.__globals__["dependency"] = object()

    assert callable_signature(external) == {
        "parameters": [],
        "returnAnnotation": "Any",
        "available": False,
    }


def test_one_registry_derives_source_once_but_the_next_registry_reads_fresh_source(
    monkeypatch,
):
    original_lines = source_inspection.inspect.getsourcelines
    original_parse = ast.parse
    reads = []
    parses = []
    code = 'def _source_subject():\n    """Original static documentation."""\n    return first_call()\n'

    def source_lines(subject):
        if subject is _source_subject:
            reads.append(subject)
            return code.splitlines(keepends=True), 20
        return original_lines(subject)

    def parse(source, *args, **kwargs):
        parses.append(source)
        return original_parse(source, *args, **kwargs)

    monkeypatch.setattr(source_inspection.inspect, "getsourcelines", source_lines)
    monkeypatch.setattr(ast, "parse", parse)
    first = SourceRegistry()
    object_id = first.register(_source_subject, kind="handler")
    first_metadata = first.get(object_id)
    first_tree = first.source_tree(_source_subject)
    assert first.source_tree(_source_subject) is first_tree
    assert first.register(_source_subject) == object_id
    assert [
        ast.unparse(site.node.func)
        for site in direct_call_sites(_source_subject, registry=first)
    ] == ["first_call"]
    assert len(reads) == len(parses) == 1

    code = 'def _source_subject():\n    """Updated static documentation."""\n    return second_call()\n'
    second = SourceRegistry()
    second.register(_source_subject, kind="handler")
    assert [
        ast.unparse(site.node.func)
        for site in direct_call_sites(_source_subject, registry=second)
    ] == ["second_call"]
    assert len(reads) == len(parses) == 2
    assert first_metadata["source"]["text"] != second.get(object_id)["source"]["text"]
    assert first_metadata["docstring"] == "Original static documentation."
    assert second.get(object_id)["docstring"] == "Updated static documentation."
    assert (
        first_metadata["source"]["sha256"] != second.get(object_id)["source"]["sha256"]
    )
    assert second.source_tree(_source_subject) is not first_tree
    # Public one-off source reads must not inherit a previous registry's view.
    assert source_metadata(_source_subject)["source"] == second.get(object_id)["source"]
    assert len(reads) == len(parses) == 3


def test_source_analysis_keeps_complete_calls_and_digest_under_small_display_budgets(
    monkeypatch,
):
    code = 'def _source_subject():\n    """Documentation beyond display bounds."""\n    return complete_source_call()\n'
    monkeypatch.setattr(
        source_inspection.inspect,
        "getsourcelines",
        lambda subject: (code.splitlines(keepends=True), 40),
    )
    registry = SourceRegistry(
        SourceInspectionLimits(
            object_limit=1,
            source_limit=30,
            total_source_limit=25,
            docstring_limit=5,
            highlight_segment_limit=1,
        )
    )
    object_id = registry.register(_source_subject, kind="handler")
    metadata = registry.get(object_id)
    assert metadata["kind"] == "handler"
    assert metadata["docstring"] == "Docum"
    assert metadata["docstringTruncated"]
    assert metadata["source"]["sha256"] == hashlib.sha256(code.encode()).hexdigest()
    assert metadata["source"]["truncated"]
    assert metadata["source"]["tokens"] == []
    assert registry.source_characters == len(metadata["source"]["text"]) <= 25
    assert metadata["location"] == {
        "path": "schemii/test_inspection.py",
        "sourceStartLine": 40,
        "definitionLine": 40,
        "endLine": 42,
    }
    assert [
        ast.unparse(site.node.func)
        for site in direct_call_sites(_source_subject, registry=registry)
    ] == ["complete_source_call"]
    independent = SourceRegistry()
    independent.register(_source_subject, kind="function")
    assert independent.get(object_id)["kind"] == "function"
    assert independent.get(object_id)["source"]["text"] == code
    metadata["source"]["tokens"].append(["plain", "mutated"])
    assert (
        independent.get(object_id)["source"]["tokens"] != metadata["source"]["tokens"]
    )


def test_returned_metadata_does_not_retain_the_document_source_cache():
    registry = SourceRegistry()
    object_id = registry.register(_source_subject)
    metadata = registry.objects
    registry_ref = weakref.ref(registry)
    del registry

    assert registry_ref() is None
    assert metadata[0]["id"] == object_id
    assert metadata[0]["source"]["available"]


def test_class_metadata_releases_its_syntax_tree_while_the_registry_is_live(
    monkeypatch,
):
    code = 'class _SourceClass:\n    """Static class documentation."""\n    value = 1\n'
    monkeypatch.setattr(
        source_inspection.inspect,
        "getsourcelines",
        lambda subject: (code.splitlines(keepends=True), 10),
    )
    original_parse = ast.parse
    trees = []

    def parse(*args, **kwargs):
        tree = original_parse(*args, **kwargs)
        trees.append(weakref.ref(tree))
        return tree

    monkeypatch.setattr(ast, "parse", parse)
    registry = SourceRegistry()
    object_id = registry.register(_SourceClass)
    assert registry.get(object_id)["docstring"] == "Static class documentation."
    assert registry.get(object_id)["source"]["text"] == code
    assert len(trees) == 1
    assert trees[0]() is None


@pytest.mark.parametrize("unavailable", [True, False])
def test_unavailable_or_invalid_source_does_not_escape_source_analysis(
    monkeypatch, unavailable
):
    def lines(subject):
        if unavailable:
            raise OSError("No source available")
        return ["def invalid(:\n"], 1

    monkeypatch.setattr(source_inspection.inspect, "getsourcelines", lines)
    registry = SourceRegistry()
    object_id = registry.register(_source_subject)
    assert registry.get(object_id)["source"]["available"] is not unavailable
    assert registry.get(object_id)["docstring"] is None
    assert registry.source_tree(_source_subject) is None
    assert direct_call_sites(_source_subject, registry=registry) == []


def test_direct_call_results_share_one_source_view_and_next_registry_reads_fresh(
    monkeypatch,
):
    from schemii.common.source_inspection import direct_call_nodes, inspect_direct_calls

    code = "def _source_subject():\n    return first_call()\n"
    counts = {"reads": 0, "parses": 0}
    original_parse = ast.parse

    def source_lines(subject):
        assert subject is _source_subject
        counts["reads"] += 1
        return code.splitlines(keepends=True), 10

    def parse(source, *args, **kwargs):
        counts["parses"] += 1
        return original_parse(source, *args, **kwargs)

    def calls(registry=None):
        return inspect_direct_calls(
            _source_subject,
            source_start_line=10,
            resolver=lambda node: (_source_subject, "module"),
            register=lambda subject: source_inspection.python_object_id(subject),
            limit=3,
            registry=registry,
        )

    monkeypatch.setattr(source_inspection.inspect, "getsourcelines", source_lines)
    monkeypatch.setattr(ast, "parse", parse)
    first = SourceRegistry()
    first.register(_source_subject)
    original_calls = calls(first)
    assert original_calls[0][0]["expression"] == "first_call"
    assert original_calls[0][0]["line"] == 11
    assert original_calls[1] is False
    assert counts == {"reads": 1, "parses": 1}

    code = "def _source_subject():\n    return second_call()\n"
    assert calls(first) == original_calls
    assert [
        ast.unparse(node.func)
        for node in direct_call_nodes(_source_subject, registry=first)
    ] == ["first_call"]
    assert counts == {"reads": 1, "parses": 1}
    second = SourceRegistry()
    second.register(_source_subject)
    fresh_calls = calls(second)
    assert fresh_calls[0][0]["expression"] == "second_call"
    assert counts == {"reads": 2, "parses": 2}
    assert calls() == fresh_calls
    assert counts == {"reads": 3, "parses": 3}


@pytest.mark.parametrize("definition", ["def", "async def"])
def test_direct_calls_reuse_unwrapped_full_source_with_identical_scope_and_bounds(
    monkeypatch, definition
):
    from functools import wraps

    from schemii.common.source_inspection import direct_call_nodes, inspect_direct_calls

    code = (
        "@decorate(factory())\n"
        f"{definition} _source_subject():\n"
        '    """Static documentation."""\n'
        "    def nested():\n"
        "        hidden_function()\n"
        "    class Nested:\n"
        "        hidden_class()\n"
        "    deferred = lambda: hidden_lambda()\n"
        "    return outer(inner(), key=keyword_call())\n"
    )
    reads = []
    parses = []
    original_parse = ast.parse

    def lines(subject):
        assert subject is _source_subject
        reads.append(subject)
        return code.splitlines(keepends=True), 100

    def parse(source, *args, **kwargs):
        parses.append(source)
        return original_parse(source, *args, **kwargs)

    @wraps(_source_subject)
    def wrapped():
        raise AssertionError("Source inspection must not execute decorated callables")

    monkeypatch.setattr(source_inspection.inspect, "getsourcelines", lines)
    monkeypatch.setattr(ast, "parse", parse)
    registry = SourceRegistry(
        SourceInspectionLimits(source_limit=20, total_source_limit=20)
    )
    object_id = registry.register(wrapped)
    metadata = registry.get(object_id)
    assert metadata["source"]["truncated"] is True
    assert metadata["source"]["sha256"] == hashlib.sha256(code.encode()).hexdigest()
    assert metadata["location"]["definitionLine"] == 101
    assert registry.source_characters <= 20
    nodes = direct_call_nodes(wrapped, registry=registry)
    assert [ast.unparse(node.func) for node in nodes] == [
        "inner",
        "keyword_call",
        "outer",
    ]

    def calls(*, registry=None, limit=3, register=lambda subject: object_id):
        return inspect_direct_calls(
            wrapped,
            source_start_line=100,
            resolver=lambda node: (_source_subject, "module"),
            register=register,
            limit=limit,
            decorate=lambda node: {"detail": ast.unparse(node)},
            registry=registry,
        )

    cached, truncated = calls(registry=registry)
    assert cached == [
        {
            "sequence": sequence,
            "expression": name,
            "objectId": object_id,
            "resolution": "module",
            "line": 108,
            "detail": detail,
        }
        for sequence, (name, detail) in enumerate(
            [
                ("inner", "inner()"),
                ("keyword_call", "keyword_call()"),
                ("outer", "outer(inner(), key=keyword_call())"),
            ],
            start=1,
        )
    ]
    assert truncated is False
    assert calls(registry=registry, limit=2) == (cached[:2], True)
    assert calls(registry=registry, register=lambda subject: None) == ([], False)
    assert len(reads) == len(parses) == 1
    assert calls() == (cached, False)
    assert len(reads) == len(parses) == 2


def _source_reuse_target() -> str:
    raise AssertionError("Inspection must not execute the endpoint or its calls")


_source_reuse_target.__module__ = "schemii.test_inspection"


def test_route_document_reuses_registered_endpoint_source(monkeypatch) -> None:
    import textwrap
    from types import SimpleNamespace

    from fastapi import FastAPI

    from schemii.common.api.inspection import build_developer_route_document

    application = FastAPI()
    application.state.services = SimpleNamespace()

    def endpoint() -> str:
        return _source_reuse_target()

    endpoint.__module__ = "schemii.test_inspection"
    application.get("/source-reuse", response_model=None)(endpoint)
    original_lines = source_inspection.inspect.getsourcelines
    original_parse = ast.parse
    counts = {"reads": 0, "parses": 0}
    lines, start_line = original_lines(endpoint)
    expected_source = textwrap.dedent("".join(lines))

    def source_lines(subject):
        if subject is endpoint:
            counts["reads"] += 1
        return original_lines(subject)

    def parse(source, *args, **kwargs):
        if isinstance(source, str) and source.strip() == expected_source.strip():
            counts["parses"] += 1
        return original_parse(source, *args, **kwargs)

    monkeypatch.setattr(source_inspection.inspect, "getsourcelines", source_lines)
    monkeypatch.setattr(ast, "parse", parse)
    document = build_developer_route_document(application)
    route = document["routes"][0]
    assert route["calls"] == [
        {
            "sequence": 1,
            "expression": "_source_reuse_target",
            "objectId": source_inspection.python_object_id(_source_reuse_target),
            "resolution": "module",
            "line": start_line + 1,
        }
    ]
    assert counts == {"reads": 1, "parses": 1}


def test_database_document_reuses_registered_callable_source(monkeypatch) -> None:
    import inspect
    import textwrap
    from types import SimpleNamespace

    from fastapi import FastAPI

    from schemii.common.postgres import inspection as database_inspection
    from schemii.common.postgres.gateway import PostgresGateway

    class _SourceReuseGateway(PostgresGateway):
        def test_connection(self, connection):
            return self._source_reuse_helper()

        def _source_reuse_helper(self):
            raise AssertionError(
                "Inspection must not call the gateway or open a connection"
            )

    for subject in (
        _SourceReuseGateway,
        _SourceReuseGateway.test_connection,
        _SourceReuseGateway._source_reuse_helper,
    ):
        subject.__module__ = "schemii.test_inspection"

    application = FastAPI()
    application.state.services = SimpleNamespace(postgres=_SourceReuseGateway())
    implementation = _SourceReuseGateway.test_connection
    original_lines = source_inspection.inspect.getsourcelines
    original_parse = ast.parse
    original_calls = database_inspection.inspect_direct_calls
    lines, start_line = original_lines(implementation)
    source = "".join(lines)
    expected_sources = {textwrap.dedent(source), inspect.cleandoc(source)}
    counts = {"reads": 0, "parses": 0}
    analyzed = []

    def source_lines(subject):
        if subject is implementation:
            counts["reads"] += 1
        return original_lines(subject)

    def parse(source, *args, **kwargs):
        if isinstance(source, str) and source in expected_sources:
            counts["parses"] += 1
        return original_parse(source, *args, **kwargs)

    def direct_calls(subject, **kwargs):
        registry = kwargs.get("registry")
        assert isinstance(registry, source_inspection.SourceRegistry)
        assert registry.get(source_inspection.python_object_id(subject)) is not None
        before = counts.copy()
        result = original_calls(subject, **kwargs)
        assert counts == before
        analyzed.append(subject)
        return result

    monkeypatch.setattr(source_inspection.inspect, "getsourcelines", source_lines)
    monkeypatch.setattr(ast, "parse", parse)
    monkeypatch.setattr(database_inspection, "inspect_direct_calls", direct_calls)
    document = database_inspection.build_developer_database_document(application)
    operation = next(
        item for item in document["operations"] if item["name"] == "test_connection"
    )
    callable_record = next(
        item
        for item in document["callables"]
        if item["objectId"] == operation["implementationObjectId"]
    )
    assert callable_record["calls"] == [
        {
            "sequence": 1,
            "expression": "self._source_reuse_helper",
            "objectId": source_inspection.python_object_id(
                _SourceReuseGateway._source_reuse_helper
            ),
            "resolution": "runtime-binding",
            "line": start_line + 1,
            "queryIds": [],
        }
    ]
    assert implementation in analyzed
    assert _SourceReuseGateway._source_reuse_helper in analyzed
    assert len(operation["implementationDigest"]) == 64
    # The separate inline-SQL pass intentionally keeps its existing normalization.
    assert counts == {"reads": 2, "parses": 2}
