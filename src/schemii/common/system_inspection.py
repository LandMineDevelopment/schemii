"""Bounded source-derived call topology for the unified developer system map."""

from __future__ import annotations

import ast
import hashlib
import inspect
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, get_args, get_type_hints

from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import BaseModel

from schemii.common.postgres.gateway import PostgresGateway
from schemii.common.source_inspection import (
    SourceInspectionLimits,
    SourceRegistry,
    attribute_parts,
    call_argument_bindings,
    callable_signature,
    direct_call_sites,
    is_first_party,
    pydantic_model_tree,
    python_object_id,
)


DEVELOPER_SYSTEM_PATH = "/_developer/system"
_MAX_ROUTES = 200
_MAX_CALLABLES = 240
_MAX_CALL_DEPTH = 10
_MAX_CALLS_PER_CALLABLE = 64
_MAX_BINDINGS = 96
_MAX_OBJECTS = 400
_MAX_BINDING_DEPTH = 5
_MAX_MODELS_PER_ROUTE_ROLE = 32
_MAX_JOURNEY_NODES = 480
_JOURNEY_STAGES = ("api", "internals", "database", "response")


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


def _annotation_types(annotation: object) -> list[type[object]]:
    if inspect.isclass(annotation):
        return [annotation]
    discovered: list[type[object]] = []
    for argument in get_args(annotation):
        discovered.extend(_annotation_types(argument))
    return list(dict.fromkeys(discovered))


def _type_hints(subject: object) -> dict[str, object]:
    try:
        return get_type_hints(subject)
    except (NameError, TypeError):
        return {}


def _implementation_kind(subject_type: type[object]) -> str:
    name = subject_type.__name__
    if "Repository" in name:
        return "repository-implementation"
    if "Gateway" in name:
        return "gateway-implementation"
    if "Service" in name:
        return "service-implementation"
    return "runtime-component"


def _contract_kind(subject_type: type[object]) -> str:
    name = subject_type.__name__
    if "Repository" in name:
        return "repository-contract"
    if "Gateway" in name:
        return "gateway-contract"
    return "interface-contract"


def _contained_first_party_instances(value: object) -> list[object]:
    if is_first_party(type(value)):
        return [value]
    if isinstance(value, (tuple, list)):
        return [item for item in value if is_first_party(type(item))]
    return []


@dataclass(frozen=True)
class ResolvedCall:
    subject: object | None
    resolution: str


class RuntimeBindingIndex:
    """Index installed component types without serializing their runtime values."""

    def __init__(self, services: object, registry: SourceRegistry) -> None:
        self.registry = registry
        self.services = services
        self.top_level = dict(vars(services))
        self.field_types: dict[tuple[type[object], str], tuple[type[object], ...]] = {}
        self.instances_by_type: dict[type[object], object] = {}
        self.database_types: set[type[object]] = set()
        self.bindings: list[dict[str, Any]] = []
        self.truncated = False
        self._seen: set[int] = set()
        self._binding_keys: set[tuple[str, str, tuple[str, ...]]] = set()
        self._visit(services, path="services", depth=0)

    def _annotations(self, owner_type: type[object]) -> dict[str, object]:
        annotations = _type_hints(owner_type)
        constructor_annotations = _type_hints(owner_type.__init__)
        for name, annotation in constructor_annotations.items():
            if name not in {"return", "self", "cls"}:
                annotations.setdefault(name, annotation)
                annotations.setdefault(f"_{name}", annotation)
        return annotations

    def _visit(self, instance: object, *, path: str, depth: int) -> None:
        if depth > _MAX_BINDING_DEPTH or id(instance) in self._seen:
            return
        instance_type = type(instance)
        if not is_first_party(instance_type):
            return
        self._seen.add(id(instance))
        self.instances_by_type.setdefault(instance_type, instance)
        try:
            if isinstance(instance, PostgresGateway):
                self.database_types.add(instance_type)
        except TypeError:
            pass
        owner_id = self.registry.register(
            instance_type,
            kind=_implementation_kind(instance_type),
        )
        if owner_id is None:
            self.truncated = True
            return
        try:
            attributes = vars(instance)
        except TypeError:
            return
        annotations = self._annotations(instance_type)
        for attribute, value in attributes.items():
            children = _contained_first_party_instances(value)
            if not children:
                continue
            child_types = tuple(dict.fromkeys(type(child) for child in children))
            self.field_types[(instance_type, attribute)] = child_types
            implementation_ids = tuple(
                item
                for item in (
                    self.registry.register(
                        child_type,
                        kind=_implementation_kind(child_type),
                    )
                    for child_type in child_types
                )
                if item is not None
            )
            contract_ids = tuple(
                item
                for item in (
                    self.registry.register(contract, kind=_contract_kind(contract))
                    for contract in _annotation_types(annotations.get(attribute))
                    if is_first_party(contract)
                    and getattr(contract, "_is_protocol", False)
                )
                if item is not None
            )
            key = (owner_id, attribute, implementation_ids)
            if key not in self._binding_keys:
                if len(self.bindings) >= _MAX_BINDINGS:
                    self.truncated = True
                else:
                    self._binding_keys.add(key)
                    self.bindings.append(
                        {
                            "ownerObjectId": owner_id,
                            "attribute": attribute,
                            "path": f"{path}.{attribute}",
                            "contractObjectIds": list(contract_ids),
                            "implementationObjectIds": list(implementation_ids),
                        }
                    )
            for child in children:
                self._visit(
                    child,
                    path=f"{path}.{attribute}",
                    depth=depth + 1,
                )

    def matching_types(self, annotation: object) -> list[type[object]]:
        matches: list[type[object]] = []
        for candidate_type, candidate in self.instances_by_type.items():
            for expected in _annotation_types(annotation):
                try:
                    if isinstance(candidate, expected):
                        matches.append(candidate_type)
                        break
                except TypeError:
                    if candidate_type is expected:
                        matches.append(candidate_type)
                        break
        return list(dict.fromkeys(matches))

    def method_candidates(self, name: str) -> list[object]:
        return list(
            dict.fromkeys(
                method
                for candidate_type in self.instances_by_type
                if (method := getattr(candidate_type, name, None)) is not None
                and is_first_party(method)
            )
        )

    def call_boundary(self, subject: object) -> dict[str, str] | None:
        """Classify installed callable ownership from runtime object bindings."""

        owner_type = self._owner_type(inspect.unwrap(subject))
        if owner_type is None or owner_type not in self.instances_by_type:
            return None
        if owner_type in self.database_types:
            return {
                "stage": "database",
                "role": "database-call",
                "evidence": "installed-postgres-gateway",
            }
        return {
            "stage": "internals",
            "role": "application-call",
            "evidence": "installed-runtime-component",
        }

    def is_material_unresolved_call(
        self,
        node: ast.AST,
        *,
        callable_subject: object,
    ) -> bool:
        """Identify unresolved expressions that cross a first-party boundary."""

        parts = attribute_parts(node)
        if parts and "services" in parts:
            return True
        owner_type = self._owner_type(inspect.unwrap(callable_subject))
        if (
            parts
            and len(parts) >= 3
            and parts[0] in {"self", "cls"}
            and owner_type is not None
            and (owner_type, parts[1]) in self.field_types
        ):
            return True
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Call):
            provider = self.resolve(
                node.value.func,
                callable_subject=callable_subject,
            ).subject
            return provider is not None and is_first_party(provider)
        return False

    def resolve(
        self,
        node: ast.AST,
        *,
        callable_subject: object,
    ) -> ResolvedCall:
        subject = inspect.unwrap(callable_subject)
        globals_by_name = getattr(subject, "__globals__", {})
        owner_type = self._owner_type(subject)
        if isinstance(node, ast.Name):
            return ResolvedCall(globals_by_name.get(node.id), "module")
        if not isinstance(node, ast.Attribute):
            return ResolvedCall(None, "unresolved")

        parts = attribute_parts(node)
        if parts and "services" in parts:
            service_index = parts.index("services")
            remaining = parts[service_index + 1 :]
            if len(remaining) == 2 and remaining[0] in self.top_level:
                implementation = type(self.top_level[remaining[0]])
                return ResolvedCall(
                    getattr(implementation, remaining[1], None),
                    "runtime-service",
                )

        if parts and owner_type is not None and parts[0] in {"self", "cls"}:
            if len(parts) == 2:
                return ResolvedCall(
                    getattr(owner_type, parts[1], None),
                    "runtime-owner",
                )
            if len(parts) == 3:
                candidates = [
                    getattr(candidate_type, parts[2], None)
                    for candidate_type in self.field_types.get(
                        (owner_type, parts[1]),
                        (),
                    )
                ]
                candidates = [item for item in candidates if item is not None]
                if len(candidates) == 1:
                    return ResolvedCall(candidates[0], "runtime-field")

        if isinstance(node.value, ast.Call):
            provider = self.resolve(
                node.value.func,
                callable_subject=callable_subject,
            ).subject
            if provider is not None:
                return_annotation = _type_hints(provider).get("return")
                candidate_types = self.matching_types(return_annotation)
                methods = [
                    getattr(candidate_type, node.attr, None)
                    for candidate_type in candidate_types
                ]
                methods = [item for item in methods if item is not None]
                if len(methods) == 1:
                    return ResolvedCall(methods[0], "runtime-provider")
                for contract in _annotation_types(return_annotation):
                    method = getattr(contract, node.attr, None)
                    if method is not None:
                        return ResolvedCall(method, "return-contract")

        if isinstance(node.value, ast.Name):
            global_owner = globals_by_name.get(node.value.id)
            if global_owner is not None:
                method = getattr(global_owner, node.attr, None)
                if method is not None:
                    return ResolvedCall(method, "module")
            candidates = self.method_candidates(node.attr)
            if len(candidates) == 1:
                return ResolvedCall(candidates[0], "unique-runtime-method")

        return ResolvedCall(None, "unresolved")

    @staticmethod
    def _owner_type(subject: object) -> type[object] | None:
        qualname = getattr(subject, "__qualname__", "")
        if "." not in qualname:
            return None
        owner_name = qualname.split(".", 1)[0]
        module = inspect.getmodule(subject)
        candidate = getattr(module, owner_name, None) if module is not None else None
        return candidate if inspect.isclass(candidate) else None


def _control_contexts(
    contexts: tuple[Any, ...],
    *,
    source_start_line: int | None,
) -> list[dict[str, Any]]:
    return [
        {
            "kind": context.kind,
            "label": context.label,
            "line": source_start_line + context.line - 1
            if source_start_line is not None
            else None,
        }
        for context in contexts
    ]


def _call_details(node: ast.Call, called: object) -> dict[str, Any]:
    if not inspect.isclass(called) or not issubclass(called, Exception):
        return {}
    details: dict[str, Any] = {"outcome": True}
    if called.__name__ == "ApiProblem":
        if node.args and isinstance(node.args[0], ast.Constant):
            status_code = node.args[0].value
            if type(status_code) is int:
                details["statusCode"] = status_code
        if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
            code = node.args[1].value
            if isinstance(code, str):
                details["code"] = code
    return details


def _public_methods(subject_type: type[object]) -> list[object]:
    return [
        method
        for name, method in vars(subject_type).items()
        if not name.startswith("_") and inspect.isfunction(method)
    ]


def _annotation_object_ids(
    annotation: object,
    registry: SourceRegistry,
) -> list[str]:
    object_ids: list[str] = []
    for subject_type in _annotation_types(annotation):
        if not is_first_party(subject_type):
            continue
        kind = "model" if issubclass(subject_type, BaseModel) else "class"
        object_id = registry.register(subject_type, kind=kind)
        if object_id is not None:
            object_ids.append(object_id)
    return list(dict.fromkeys(object_ids))


def _signature_contract(
    subject: object,
    registry: SourceRegistry,
) -> dict[str, Any]:
    signature = callable_signature(subject)
    hint_subject = subject.__init__ if inspect.isclass(subject) else subject
    hints = _type_hints(hint_subject)
    parameters = [
        {
            **parameter,
            "objectIds": _annotation_object_ids(
                hints.get(parameter["name"]),
                registry,
            ),
        }
        for parameter in signature["parameters"]
    ]
    return_ids = (
        [object_id]
        if inspect.isclass(subject)
        and (object_id := registry.register(subject)) is not None
        else _annotation_object_ids(hints.get("return"), registry)
    )
    return {
        **signature,
        "parameters": parameters,
        "returnObjectIds": return_ids,
    }


def _route_model_ids(
    roots: Iterable[object],
    registry: SourceRegistry,
) -> tuple[list[str], bool]:
    models, truncated = pydantic_model_tree(
        roots,
        limit=_MAX_MODELS_PER_ROUTE_ROLE,
    )
    return (
        [
            object_id
            for model in models
            if (object_id := registry.register(model, kind="model")) is not None
        ],
        truncated,
    )


def _request_parameters(route: Any) -> list[dict[str, Any]]:
    parameters: list[dict[str, Any]] = []
    for location, attribute in (
        ("path", "path_params"),
        ("query", "query_params"),
        ("header", "header_params"),
        ("cookie", "cookie_params"),
    ):
        for parameter in getattr(route.dependant, attribute, ()):  # pragma: no branch
            parameters.append(
                {
                    "name": parameter.name,
                    "location": location,
                    "required": bool(getattr(parameter, "required", False)),
                }
            )
    return parameters


def _grouped_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mirror the visual graph's source-call collapsing without UI inference."""

    grouped: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    for call in calls:
        context_key = tuple(
            (context["kind"], context["label"])
            for context in call.get("contexts", [])
        )
        key = (call["objectId"], context_key)
        if key in seen:
            continue
        seen.add(key)
        grouped.append(call)
    return grouped


def _journey_call_classification(
    call: dict[str, Any],
    *,
    parent_stage: str,
    target: dict[str, Any],
) -> tuple[str, str, str]:
    contexts = {context["kind"] for context in call.get("contexts", [])}
    if call.get("outcome") or target["kind"] == "outcome" or "raise" in contexts:
        return "response", "error-outcome", "raised-source-outcome"
    boundary = call.get("boundary")
    if isinstance(boundary, dict) and boundary.get("stage") in _JOURNEY_STAGES:
        return (
            boundary["stage"],
            boundary.get("role", "source-call"),
            boundary.get("evidence", "installed-runtime-binding"),
        )
    return parent_stage, "source-call", "source-call-inherits-caller-boundary"


def _build_route_journey(
    route: dict[str, Any],
    *,
    callables_by_id: dict[str, dict[str, Any]],
    registry: SourceRegistry,
) -> dict[str, Any]:
    dependency_ids = {
        dependency["objectId"] for dependency in route["dependencies"]
    }
    queued: deque[dict[str, Any]] = deque(
        {
            "objectId": object_id,
            "depth": 0,
            "parentKey": None,
            "parentStage": None,
            "stage": "api",
            "role": "request-dependency"
            if object_id in dependency_ids
            else "route-handler",
            "evidence": "fastapi-dependency"
            if object_id in dependency_ids
            else "registered-fastapi-handler",
            "call": None,
            "order": str(index),
        }
        for index, object_id in enumerate(route["rootObjectIds"])
    )
    nodes: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    expanded: set[str] = set()
    node_limit_reached = False
    while queued:
        item = queued.popleft()
        if len(nodes) >= _MAX_JOURNEY_NODES:
            node_limit_reached = True
            break
        object_id = item["objectId"]
        target = registry.get(object_id)
        if target is None:
            issues.append(
                {
                    "kind": "missing-source-object",
                    "objectId": object_id,
                    "parentKey": item["parentKey"],
                }
            )
            continue
        key = (
            f"{item['parentKey']}>{item['order']}:{object_id}"
            if item["parentKey"]
            else f"root:{item['order']}:{object_id}"
        )
        call = item["call"]
        node = {
            "key": key,
            "objectId": object_id,
            "parentKey": item["parentKey"],
            "depth": item["depth"],
            "stage": item["stage"],
            "role": item["role"],
            "provenance": "derived",
            "evidence": {
                "kind": item["evidence"],
                "resolution": call.get("resolution") if call else "fastapi-registry",
                "line": call.get("line") if call else target["location"]["definitionLine"],
            },
        }
        nodes.append(node)
        if item["parentStage"] and item["parentStage"] != item["stage"]:
            transitions.append(
                {
                    "fromKey": item["parentKey"],
                    "toKey": key,
                    "fromStage": item["parentStage"],
                    "toStage": item["stage"],
                    "provenance": "derived",
                    "evidence": node["evidence"],
                }
            )
        if object_id in expanded:
            continue
        expanded.add(object_id)
        callable_record = callables_by_id.get(object_id)
        if callable_record is None:
            if item["role"] in {"request-dependency", "route-handler"} or (
                call is not None and call.get("targetCallable") is True
            ):
                issues.append(
                    {
                        "kind": "call-graph-unavailable",
                        "objectId": object_id,
                        "nodeKey": key,
                    }
                )
            continue
        if callable_record["truncated"]["calls"]:
            issues.append(
                {
                    "kind": "truncated-source-calls",
                    "objectId": object_id,
                    "nodeKey": key,
                }
            )
        for unresolved in callable_record.get("unresolvedCalls", []):
            issues.append(
                {
                    "kind": "unresolved-first-party-call",
                    "objectId": object_id,
                    "nodeKey": key,
                    **unresolved,
                }
            )
        for child_call in _grouped_calls(callable_record["calls"]):
            child = registry.get(child_call["objectId"])
            if child is None:
                continue
            stage, role, evidence = _journey_call_classification(
                child_call,
                parent_stage=item["stage"],
                target=child,
            )
            queued.append(
                {
                    "objectId": child_call["objectId"],
                    "depth": item["depth"] + 1,
                    "parentKey": key,
                    "parentStage": item["stage"],
                    "stage": stage,
                    "role": role,
                    "evidence": evidence,
                    "call": child_call,
                    "order": f"{item['order']}.{child_call['sequence']:03d}",
                }
            )
    if node_limit_reached:
        issues.append(
            {
                "kind": "journey-node-limit",
                "limit": _MAX_JOURNEY_NODES,
            }
        )
    return {
        "status": "complete" if not issues else "unresolved",
        "nodes": nodes,
        "transitions": transitions,
        "issues": issues,
    }


def build_developer_system_document(application: FastAPI) -> dict[str, Any]:
    """Describe route-rooted and internal calls without executing application work."""

    registry = SourceRegistry(
        SourceInspectionLimits(
            object_limit=_MAX_OBJECTS,
            total_source_limit=1_024_000,
        )
    )
    runtime = RuntimeBindingIndex(application.state.services, registry)
    services: list[dict[str, Any]] = []
    queued: deque[tuple[object, int]] = deque()

    service_annotations = _type_hints(type(application.state.services))
    for name, instance in runtime.top_level.items():
        instance_type = type(instance)
        if not is_first_party(instance_type):
            continue
        implementation_id = registry.register(
            instance_type,
            kind=_implementation_kind(instance_type),
        )
        if implementation_id is None:
            continue
        contract_ids = [
            item
            for item in (
                registry.register(contract, kind=_contract_kind(contract))
                for contract in _annotation_types(service_annotations.get(name))
                if is_first_party(contract) and getattr(contract, "_is_protocol", False)
            )
            if item is not None
        ]
        method_ids: list[str] = []
        for method in _public_methods(instance_type):
            method_id = registry.register(method)
            if method_id is None:
                continue
            method_ids.append(method_id)
            queued.append((method, 0))
        services.append(
            {
                "name": name,
                "implementationObjectId": implementation_id,
                "contractObjectIds": contract_ids,
                "methodObjectIds": method_ids,
            }
        )

    routes: list[dict[str, Any]] = []
    routes_truncated = False
    for route in _public_route_contexts(application):
        if len(routes) >= _MAX_ROUTES:
            routes_truncated = True
            break
        endpoint = inspect.unwrap(route.endpoint)
        if not is_first_party(endpoint):
            continue
        endpoint_id = registry.register(endpoint, kind="handler")
        if endpoint_id is None:
            continue
        dependencies: list[dict[str, Any]] = []
        dependency_queue = list(route.dependant.dependencies)
        seen_dependencies: set[int] = set()
        while dependency_queue:
            dependency = dependency_queue.pop(0)
            if id(dependency) in seen_dependencies:
                continue
            seen_dependencies.add(id(dependency))
            dependency_queue.extend(dependency.dependencies)
            if dependency.call is None or not is_first_party(dependency.call):
                continue
            dependency_id = registry.register(dependency.call, kind="dependency")
            if dependency_id is None:
                continue
            dependencies.append(
                {
                    "parameterName": dependency.name,
                    "objectId": dependency_id,
                    "useCache": dependency.use_cache,
                    "resultObjectIds": _annotation_object_ids(
                        _type_hints(dependency.call).get("return"),
                        registry,
                    ),
                }
            )
            queued.append((dependency.call, 0))
        queued.append((endpoint, 0))
        request_object_ids, request_models_truncated = _route_model_ids(
            (
                parameter.field_info.annotation
                for parameter in route.dependant.body_params
            ),
            registry,
        )
        response_object_ids, response_models_truncated = _route_model_ids(
            (route.response_model,),
            registry,
        )
        methods = sorted(method.lower() for method in route.methods)
        for method in methods:
            routes.append(
                {
                    "id": f"{method}:{route.path}",
                    "method": method,
                    "path": route.path,
                    "operationId": route.unique_id,
                    "endpointObjectId": endpoint_id,
                    "dependencies": dependencies,
                    "rootObjectIds": [
                        *(item["objectId"] for item in dependencies),
                        endpoint_id,
                    ],
                    "request": {
                        "bodyObjectIds": request_object_ids,
                        "parameters": _request_parameters(route),
                    },
                    "response": {
                        "statusCode": route.status_code or 200,
                        "objectIds": response_object_ids,
                    },
                    "truncated": {
                        "requestObjects": request_models_truncated,
                        "responseObjects": response_models_truncated,
                    },
                }
            )

    callables: list[dict[str, Any]] = []
    visited: set[str] = set()
    callables_truncated = False
    while queued:
        subject, depth = queued.popleft()
        if depth > _MAX_CALL_DEPTH or not callable(subject):
            continue
        subject_id = registry.register(subject)
        if subject_id is None or subject_id in visited:
            continue
        if len(callables) >= _MAX_CALLABLES:
            callables_truncated = True
            break
        visited.add(subject_id)
        metadata = registry.get(subject_id)
        source_start_line = (
            metadata["location"]["sourceStartLine"] if metadata is not None else None
        )
        calls: list[dict[str, Any]] = []
        unresolved_calls: list[dict[str, Any]] = []
        calls_truncated = False
        for site in direct_call_sites(subject):
            resolved = runtime.resolve(site.node.func, callable_subject=subject)
            called = resolved.subject
            if called is None:
                if runtime.is_material_unresolved_call(
                    site.node.func,
                    callable_subject=subject,
                ):
                    unresolved_calls.append(
                        {
                            "expression": ast.unparse(site.node.func),
                            "line": source_start_line + site.node.lineno - 1
                            if source_start_line is not None
                            else None,
                            "contexts": _control_contexts(
                                site.contexts,
                                source_start_line=source_start_line,
                            ),
                        }
                    )
                continue
            if not is_first_party(called):
                continue
            if len(calls) >= _MAX_CALLS_PER_CALLABLE:
                calls_truncated = True
                continue
            called_id = registry.register(called)
            if called_id is None:
                continue
            record = {
                "sequence": len(calls) + 1,
                "expression": ast.unparse(site.node.func),
                "objectId": called_id,
                "resolution": resolved.resolution,
                "line": source_start_line + site.node.lineno - 1
                if source_start_line is not None
                else None,
                "endLine": source_start_line + site.node.end_lineno - 1
                if source_start_line is not None
                and site.node.end_lineno is not None
                else None,
                "contexts": _control_contexts(
                    site.contexts,
                    source_start_line=source_start_line,
                ),
                "targetCallable": callable(called) and not inspect.isclass(called),
                "arguments": call_argument_bindings(site.node, called),
                "targetSignature": _signature_contract(called, registry),
                **_call_details(site.node, called),
            }
            boundary = runtime.call_boundary(called)
            if boundary is not None:
                record["boundary"] = boundary
            calls.append(record)
            if callable(called) and not inspect.isclass(called):
                queued.append((called, depth + 1))
        callables.append(
            {
                "objectId": subject_id,
                "signature": _signature_contract(subject, registry),
                "calls": calls,
                "unresolvedCalls": unresolved_calls,
                "truncated": {"calls": calls_truncated},
            }
        )

    callables_by_id = {item["objectId"]: item for item in callables}

    def route_digest(route: dict[str, Any]) -> str:
        materials: list[str] = []
        pending = list(route["rootObjectIds"])
        digested: set[str] = set()
        while pending:
            object_id = pending.pop(0)
            if object_id in digested:
                continue
            digested.add(object_id)
            metadata = registry.get(object_id)
            if metadata is not None:
                materials.append(metadata["source"]["sha256"])
            pending.extend(
                call["objectId"]
                for call in callables_by_id.get(object_id, {}).get("calls", [])
            )
        return hashlib.sha256("|".join(materials).encode("utf-8")).hexdigest()

    for route in routes:
        route["implementationDigest"] = route_digest(route)
        route["journey"] = _build_route_journey(
            route,
            callables_by_id=callables_by_id,
            registry=registry,
        )

    services.sort(key=lambda item: item["name"])
    return {
        "schemaVersion": 1,
        "analysis": {
            "kind": "bounded-python-source",
            "generation": "application-startup",
            "callGraph": "recursive-route-and-runtime-calls",
            "controlFlow": "static-source-regions",
            "runtimeValues": "types-only",
            "routeLimit": _MAX_ROUTES,
            "callableLimit": _MAX_CALLABLES,
            "callDepthLimit": _MAX_CALL_DEPTH,
            "callLimitPerCallable": _MAX_CALLS_PER_CALLABLE,
            "bindingLimit": _MAX_BINDINGS,
            "journeyNodeLimit": _MAX_JOURNEY_NODES,
            "journeyClassification": "runtime-bindings-and-source-control-flow",
            **registry.analysis_limits(),
            "truncated": {
                "routes": routes_truncated,
                "callables": callables_truncated,
                "bindings": runtime.truncated,
                "objects": registry.objects_truncated,
                "source": any(
                    item["source"]["truncated"] for item in registry.objects
                ),
            },
        },
        "services": services,
        "bindings": runtime.bindings,
        "routes": routes,
        "callables": callables,
        "objects": registry.objects,
    }


def install_developer_system_inspection(application: FastAPI) -> None:
    """Derive and install one hidden topology document for this app run."""

    document = build_developer_system_document(application)

    @application.get(DEVELOPER_SYSTEM_PATH, include_in_schema=False)
    def developer_system() -> dict[str, Any]:
        return document
