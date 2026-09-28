"""Public route contexts shared by developer inspection views."""

from collections.abc import Iterable
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute


def public_route_contexts(application: FastAPI) -> Iterable[Any]:
    """Yield schema-visible FastAPI routes and mounted product route contexts."""
    for candidate in application.routes:
        if isinstance(candidate, APIRoute):
            if candidate.include_in_schema:
                yield candidate
            continue
        contexts = getattr(candidate, "effective_route_contexts", None)
        if not callable(contexts):
            continue
        for context in contexts():
            if context.include_in_schema:
                yield context
