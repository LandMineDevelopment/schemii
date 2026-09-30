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
