"""Small, deterministic JSON deltas for optimistic client projections."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


JsonDeltaOperation = dict[str, Any]


def json_delta(before: Any, after: Any) -> list[JsonDeltaOperation]:
    """Return operations that transform one JSON-compatible value into another.

    Lists of objects with stable ``id`` values are reconciled by identity. Other
    lists are compared positionally when their length is unchanged and replaced
    as one value when their shape changes. The result is intentionally smaller
    than a second complete snapshot while remaining deterministic.
    """

    operations: list[JsonDeltaOperation] = []
    _append_delta(before, after, [], operations)
    return operations


def _append_delta(
    before: Any,
    after: Any,
    path: list[str | int],
    operations: list[JsonDeltaOperation],
) -> None:
    if before == after:
        return
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(before.keys() - after.keys()):
            operations.append({"operation": "remove", "path": [*path, key]})
        for key in sorted(after.keys() - before.keys()):
            operations.append(
                {
                    "operation": "add",
                    "path": [*path, key],
                    "value": deepcopy(after[key]),
                }
            )
        for key in sorted(before.keys() & after.keys()):
            _append_delta(before[key], after[key], [*path, key], operations)
        return
    if isinstance(before, list) and isinstance(after, list):
        if _has_stable_object_ids(before) and _has_stable_object_ids(after):
            _append_identified_list_delta(before, after, path, operations)
            return
        if len(before) == len(after):
            for index, (previous, current) in enumerate(zip(before, after, strict=True)):
                _append_delta(previous, current, [*path, index], operations)
            return
    operations.append(
        {
            "operation": "replace",
            "path": list(path),
            "value": deepcopy(after),
        }
    )


def _has_stable_object_ids(values: list[Any]) -> bool:
    ids = [value.get("id") for value in values if isinstance(value, dict)]
    return len(ids) == len(values) and all(isinstance(value, str) for value in ids) and len(set(ids)) == len(ids)


def _append_identified_list_delta(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    path: list[str | int],
    operations: list[JsonDeltaOperation],
) -> None:
    """Reconcile a stable-ID list with operations valid in emitted order."""

    desired_ids = {value["id"] for value in after}
    working = deepcopy(before)
    for index in range(len(working) - 1, -1, -1):
        if working[index]["id"] in desired_ids:
            continue
        operations.append({"operation": "remove", "path": [*path, index]})
        working.pop(index)

    for target_index, target in enumerate(after):
        current_index = next(
            (index for index, value in enumerate(working) if value["id"] == target["id"]),
            None,
        )
        if current_index is None:
            operations.append(
                {
                    "operation": "add",
                    "path": [*path, target_index],
                    "value": deepcopy(target),
                }
            )
            working.insert(target_index, deepcopy(target))
            continue
        if current_index != target_index:
            operations.append({"operation": "remove", "path": [*path, current_index]})
            working.pop(current_index)
            operations.append(
                {
                    "operation": "add",
                    "path": [*path, target_index],
                    "value": deepcopy(target),
                }
            )
            working.insert(target_index, deepcopy(target))
            continue
        _append_delta(working[target_index], target, [*path, target_index], operations)
        working[target_index] = deepcopy(target)
