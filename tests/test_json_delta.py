from schemii.common.json_delta import json_delta


def apply_delta(value, operations):
    """Minimal independent interpreter used to prove emitted operation order."""

    import copy

    result = copy.deepcopy(value)
    for operation in operations:
        parent = result
        for segment in operation["path"][:-1]:
            parent = parent[segment]
        key = operation["path"][-1]
        if isinstance(parent, list):
            if operation["operation"] == "add":
                parent.insert(key, copy.deepcopy(operation["value"]))
            elif operation["operation"] == "remove":
                parent.pop(key)
            else:
                parent[key] = copy.deepcopy(operation["value"])
        elif operation["operation"] == "remove":
            del parent[key]
        else:
            parent[key] = copy.deepcopy(operation["value"])
    return result


def test_json_delta_keeps_stable_object_edits_compact() -> None:
    before = {
        "tables": [
            {
                "id": "table_1",
                "name": "projects",
                "columns": [
                    {"id": "column_1", "name": "name", "dataType": "text"},
                ],
            }
        ]
    }
    after = {
        "tables": [
            {
                "id": "table_1",
                "name": "projects",
                "columns": [
                    {
                        "id": "column_1",
                        "name": "name",
                        "dataType": "character varying(200)",
                    },
                ],
            }
        ]
    }

    operations = json_delta(before, after)

    assert operations == [
        {
            "operation": "replace",
            "path": ["tables", 0, "columns", 0, "dataType"],
            "value": "character varying(200)",
        }
    ]
    assert apply_delta(before, operations) == after


def test_json_delta_reconciles_stable_id_add_remove_and_reorder() -> None:
    before = {
        "tables": [
            {"id": "a", "name": "accounts"},
            {"id": "b", "name": "billing"},
            {"id": "c", "name": "customers"},
        ]
    }
    after = {
        "tables": [
            {"id": "c", "name": "clients"},
            {"id": "a", "name": "accounts"},
            {"id": "d", "name": "deliveries"},
        ]
    }

    operations = json_delta(before, after)

    assert apply_delta(before, operations) == after
    assert len(operations) == 4
