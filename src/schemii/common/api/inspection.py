"""Bounded, source-derived metadata for the local API-map developer view."""

from __future__ import annotations

import ast
import hashlib
import inspect
from collections.abc import Iterable
from typing import Any, get_type_hints

from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import BaseModel

from schemii.common.source_inspection import (
    DEFAULT_DOCSTRING_LIMIT,
    DEFAULT_HIGHLIGHT_SEGMENT_LIMIT,
    DEFAULT_OBJECT_LIMIT,
    DEFAULT_SOURCE_LIMIT,
    DEFAULT_TOTAL_SOURCE_LIMIT,
    SourceInspectionLimits,
    SourceRegistry,
    attribute_parts,
    inspect_direct_calls,
    is_first_party,
    pydantic_model_tree,
    python_object_id,
    source_metadata,
)


DEVELOPER_ROUTES_PATH = "/_developer/routes"
_MAX_CALLS_PER_ROUTE = 24
_MAX_DEPENDENCIES_PER_ROUTE = 24
_MAX_MODELS_PER_ROLE = 32
# The rewrite deliberately registers typed planning handlers alongside active
# implementations so the API map can review the complete source-owned contract.
_MAX_OBJECTS = DEFAULT_OBJECT_LIMIT * 2
_MAX_ROUTES = 200
_MAX_DOCSTRING_CHARACTERS = DEFAULT_DOCSTRING_LIMIT
_MAX_HIGHLIGHT_SEGMENTS = DEFAULT_HIGHLIGHT_SEGMENT_LIMIT
_MAX_SOURCE_CHARACTERS = DEFAULT_SOURCE_LIMIT
_MAX_TOTAL_SOURCE_CHARACTERS = DEFAULT_TOTAL_SOURCE_LIMIT

# Compatibility aliases for existing internal callers and focused tests.
_first_party = is_first_party
_object_id = python_object_id
_source_metadata = source_metadata
_attribute_parts = attribute_parts


def _return_type(subject: object) -> object | None:
    try:
        return get_type_hints(subject).get("return")
    except (NameError, TypeError):
        return None


def _runtime_provider_value(
    provider: object,
    provided_type: object,
    services: object,
) -> object | None:
    provider_name = getattr(provider, "__name__", "").strip("_")
    if provider_name and hasattr(services, provider_name):
        return getattr(services, provider_name)
    for candidate in vars(services).values():
        if type(candidate) is provided_type:
            return candidate
        try:
            if inspect.isclass(provided_type) and isinstance(candidate, provided_type):
                return candidate
        except TypeError:
            # Non-runtime-checkable Protocols cannot be passed to isinstance().
            continue
    return None


def _resolve_callable_expression(
    node: ast.AST,
    *,
    endpoint_globals: dict[str, Any],
    services: object,
) -> tuple[object | None, str]:
    if isinstance(node, ast.Name):
        return endpoint_globals.get(node.id), "module"
    if not isinstance(node, ast.Attribute):
        return None, "unresolved"

    parts = _attribute_parts(node)
    if parts and "services" in parts:
        services_index = parts.index("services")
        remaining = parts[services_index + 1 :]
        if len(remaining) == 2 and hasattr(services, remaining[0]):
            implementation = type(getattr(services, remaining[0]))
            return getattr(implementation, remaining[1], None), "runtime-binding"

    if isinstance(node.value, ast.Name):
        owner = endpoint_globals.get(node.value.id)
        return getattr(owner, node.attr, None), "module"

    if isinstance(node.value, ast.Call):
        provider, _ = _resolve_callable_expression(
            node.value.func,
            endpoint_globals=endpoint_globals,
            services=services,
        )
        provided_type = _return_type(provider) if provider else None
        if inspect.isclass(provided_type):
            runtime_value = _runtime_provider_value(
                provider,
                provided_type,
                services,
            )
            if runtime_value is not None:
                implementation = getattr(type(runtime_value), node.attr, None)
                if implementation is not None:
                    return implementation, "runtime-provider"
            return getattr(provided_type, node.attr, None), "return-contract"

    return None, "unresolved"


def _direct_calls(
    endpoint: object,
    *,
    source_start_line: int | None,
    services: object,
    register: Any,
) -> tuple[list[dict[str, Any]], bool]:
    return inspect_direct_calls(
        endpoint,
        source_start_line=source_start_line,
        resolver=lambda node: _resolve_callable_expression(
            node,
            endpoint_globals=endpoint.__globals__,
            services=services,
        ),
        register=register,
        limit=_MAX_CALLS_PER_ROUTE,
    )


def _model_tree(roots: Iterable[object]) -> tuple[list[type[BaseModel]], bool]:
    """Compatibility wrapper around the shared model-closure inspection."""

    return pydantic_model_tree(roots, limit=_MAX_MODELS_PER_ROLE)


def _dependencies(dependant: Any, register: Any) -> tuple[list[dict[str, Any]], bool]:
    dependencies: list[dict[str, Any]] = []
    queued = list(dependant.dependencies)
    seen_nodes: set[int] = set()
    seen_objects: set[str] = set()
    truncated = False
    while queued:
        dependency = queued.pop(0)
        if id(dependency) in seen_nodes:
            continue
        seen_nodes.add(id(dependency))
        queued.extend(dependency.dependencies)
        if dependency.call is None:
            continue
        dependency_id = register(dependency.call, kind="dependency")
        if dependency_id is None or dependency_id in seen_objects:
            continue
        if len(dependencies) >= _MAX_DEPENDENCIES_PER_ROUTE:
            truncated = True
            continue
        seen_objects.add(dependency_id)
        dependencies.append(
            {
                "parameterName": dependency.name,
                "objectId": dependency_id,
                "useCache": dependency.use_cache,
            }
        )
    return dependencies, truncated


def _public_route_contexts(application: FastAPI) -> Iterable[Any]:
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


def build_developer_route_document(application: FastAPI) -> dict[str, Any]:
    """Describe registered first-party routes without inspecting runtime object state."""

    registry = SourceRegistry(
        SourceInspectionLimits(
            object_limit=_MAX_OBJECTS,
            source_limit=_MAX_SOURCE_CHARACTERS,
            total_source_limit=_MAX_TOTAL_SOURCE_CHARACTERS,
            docstring_limit=_MAX_DOCSTRING_CHARACTERS,
            highlight_segment_limit=_MAX_HIGHLIGHT_SEGMENTS,
        )
    )
    register = registry.register

    services = application.state.services
    routes: list[dict[str, Any]] = []
    routes_truncated = False
    for route in _public_route_contexts(application):
        if len(routes) >= _MAX_ROUTES:
            routes_truncated = True
            break
        endpoint = inspect.unwrap(route.endpoint)
        if not _first_party(endpoint):
            continue
        endpoint_id = register(endpoint, kind="handler")
        if endpoint_id is None:
            continue
        methods = sorted(method.lower() for method in route.methods)
        dependencies, dependencies_truncated = _dependencies(
            route.dependant,
            register,
        )

        request_roots = [
            parameter.field_info.annotation
            for parameter in route.dependant.body_params
        ]
        request_models, request_models_truncated = pydantic_model_tree(
            request_roots,
            limit=_MAX_MODELS_PER_ROLE,
        )
        response_models, response_models_truncated = pydantic_model_tree(
            [route.response_model],
            limit=_MAX_MODELS_PER_ROLE,
        )
        request_object_ids = [register(model, kind="model") for model in request_models]
        response_object_ids = [register(model, kind="model") for model in response_models]
        calls, calls_truncated = _direct_calls(
            endpoint,
            source_start_line=registry.get(endpoint_id)["location"]["sourceStartLine"],
            services=services,
            register=register,
        )
        related_ids = [
            endpoint_id,
            *(dependency["objectId"] for dependency in dependencies),
            *(call["objectId"] for call in calls),
            *(item for item in request_object_ids if item),
            *(item for item in response_object_ids if item),
        ]
        digest_material = "|".join(
            registry.get(item)["source"]["sha256"]
            for item in dict.fromkeys(related_ids)
            if registry.get(item) is not None
        )
        implementation_digest = hashlib.sha256(
            digest_material.encode("utf-8")
        ).hexdigest()
        for method in methods:
            if len(routes) >= _MAX_ROUTES:
                routes_truncated = True
                break
            routes.append(
                {
                    "id": f"{method}:{route.path}",
                    "method": method,
                    "path": route.path,
                    "operationId": route.unique_id,
                    "endpointId": endpoint_id,
                    "dependencies": dependencies,
                    "calls": calls,
                    "requestObjectIds": [item for item in request_object_ids if item],
                    "responseObjectIds": [item for item in response_object_ids if item],
                    "implementationDigest": implementation_digest,
                    "truncated": {
                        "dependencies": dependencies_truncated,
                        "calls": calls_truncated,
                        "requestObjects": request_models_truncated,
                        "responseObjects": response_models_truncated,
                    },
                }
            )

    routes.sort(key=lambda item: (item["path"], item["method"]))
    return {
        "schemaVersion": 1,
        "analysis": {
            "kind": "bounded-python-source",
            "generation": "application-startup",
            "callGraph": "direct-first-party-calls",
            "routeLimit": _MAX_ROUTES,
            "dependencyLimitPerRoute": _MAX_DEPENDENCIES_PER_ROUTE,
            "callLimitPerRoute": _MAX_CALLS_PER_ROUTE,
            "modelLimitPerRole": _MAX_MODELS_PER_ROLE,
            "objectLimit": _MAX_OBJECTS,
            "docstringLimit": _MAX_DOCSTRING_CHARACTERS,
            "highlightSegmentLimit": _MAX_HIGHLIGHT_SEGMENTS,
            "sourceLimit": _MAX_SOURCE_CHARACTERS,
            "totalSourceLimit": _MAX_TOTAL_SOURCE_CHARACTERS,
            "truncated": {
                "routes": routes_truncated,
                "objects": registry.objects_truncated,
                "source": any(
                    item["source"]["truncated"] for item in registry.objects
                ),
            },
            "syntaxHighlighting": "python-tokenize",
        },
        "routes": routes,
        "objects": registry.objects,
    }


def install_developer_route_inspection(application: FastAPI) -> None:
    """Derive and install one hidden route document for this app run."""

    document = build_developer_route_document(application)

    @application.get(DEVELOPER_ROUTES_PATH, include_in_schema=False)
    def developer_routes() -> dict[str, Any]:
        return document
