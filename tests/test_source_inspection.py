from __future__ import annotations

from schemii.common.source_inspection import callable_signature


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
