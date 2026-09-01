from __future__ import annotations

import hashlib

import pytest

from schemii.schemii.designs.export import export_design
from schemii.schemii.designs.models import (
    SchemiiDesignContent,
    SchemiiDesignExportRequest,
    SchemiiDesignLayoutContent,
    SchemiiDesignLayoutReplace,
    SchemiiDesignReplace,
)
from schemii.schemii.designs.store import (
    DesignConflictError,
    DesignLayoutConflictError,
    DesignValidationError,
    InMemoryDesignRepository,
    design_fingerprint,
)


OWNER = "owner"
WORKSPACE = "ws_" + "1" * 32
TABLE = "table_" + "a" * 32
ID_COLUMN = "column_" + "b" * 32
EMAIL_COLUMN = "column_" + "c" * 32
NAME_LENGTH_COLUMN = "column_" + "e" * 32
KEY = "key_" + "d" * 32
CHECK = "check_" + "f" * 32
INDEX = "index_" + "7" * 32
VIEW = "view_" + "8" * 32


def content() -> SchemiiDesignContent:
    return SchemiiDesignContent.model_validate(
        {
            "tables": [
                {
                    "id": TABLE,
                    "name": 'user "account"',
                    "columns": [
                        {
                            "id": ID_COLUMN,
                            "name": "id",
                            "dataType": "bigint",
                            "nullable": False,
                            "identity": "always",
                        },
                        {
                            "id": EMAIL_COLUMN,
                            "name": "email",
                            "dataType": "text",
                            "nullable": False,
                            "defaultExpression": "'unknown'::text",
                        },
                        {
                            "id": NAME_LENGTH_COLUMN,
                            "name": "email_length",
                            "dataType": "integer",
                            "nullable": False,
                            "generatedExpression": "char_length(email)",
                            "generatedSourceColumnIds": [EMAIL_COLUMN],
                        },
                    ],
                    "keys": [
                        {
                            "id": KEY,
                            "name": "user account_pkey",
                            "kind": "primary",
                            "columnIds": [ID_COLUMN],
                        }
                    ],
                    "checks": [
                        {
                            "id": CHECK,
                            "name": "user account_email_check",
                            "expression": "length(email) > 3",
                            "columnIds": [EMAIL_COLUMN],
                        }
                    ],
                    "indexes": [
                        {
                            "id": INDEX,
                            "name": "user account_email_idx",
                            "method": "btree",
                            "columnIds": [],
                            "expression": "lower(email)",
                            "expressionSourceColumnIds": [EMAIL_COLUMN],
                            "predicate": "email <> 'unknown'::text",
                            "predicateColumnIds": [EMAIL_COLUMN],
                            "unique": True,
                        }
                    ],
                }
            ]
        }
    )


def test_design_and_layout_revisions_are_independent_and_optimistic() -> None:
    repository = InMemoryDesignRepository()
    empty = repository.get(OWNER, WORKSPACE)

    assert empty.revision == 0
    assert empty.fingerprint == design_fingerprint(empty.content)

    saved = repository.replace(
        OWNER,
        WORKSPACE,
        SchemiiDesignReplace(expected_design_revision=0, content=content()),
    )
    assert saved.revision == 1
    with pytest.raises(DesignConflictError) as conflict:
        repository.replace(
            OWNER,
            WORKSPACE,
            SchemiiDesignReplace(expected_design_revision=0, content=content()),
        )
    assert conflict.value.current_revision == 1

    initial_layout = repository.get_layout(OWNER, WORKSPACE)
    assert (initial_layout.revision, initial_layout.design_revision) == (1, 1)
    positioned = repository.replace_layout(
        OWNER,
        WORKSPACE,
        SchemiiDesignLayoutReplace(
            expected_layout_revision=1,
            expected_design_revision=1,
            content=SchemiiDesignLayoutContent(
                objects=[
                    {"objectId": TABLE, "layer": "tables", "x": 12.5, "y": -4.0}
                ]
            ),
        ),
    )
    assert (positioned.revision, positioned.design_revision) == (2, 1)
    assert positioned.content.objects[0].object_id == TABLE
    assert repository.get(OWNER, WORKSPACE).revision == 1

    with pytest.raises(DesignLayoutConflictError) as layout_conflict:
        repository.replace_layout(
            OWNER,
            WORKSPACE,
            SchemiiDesignLayoutReplace(
                expected_layout_revision=1,
                expected_design_revision=1,
                content=SchemiiDesignLayoutContent(),
            ),
        )
    assert layout_conflict.value.current_layout_revision == 2


def test_design_validation_rejects_invalid_references_and_statement_boundaries() -> None:
    invalid = content().model_dump(mode="json", by_alias=True)
    invalid["tables"][0]["indexes"].append(
        {
            "id": "index_" + "e" * 32,
            "name": "unsafe",
            "method": "btree",
            "columnIds": [EMAIL_COLUMN],
            "predicate": "true; DROP TABLE users",
        }
    )

    with pytest.raises(DesignValidationError, match="statement boundaries"):
        InMemoryDesignRepository().replace(
            OWNER,
            WORKSPACE,
            SchemiiDesignReplace.model_validate(
                {"expectedDesignRevision": 0, "content": invalid}
            ),
        )


def test_design_validation_rejects_conflicting_generators_and_cross_table_dependencies() -> None:
    conflicting = content().model_dump(mode="json", by_alias=True)
    conflicting["tables"][0]["columns"][1]["identity"] = "always"
    with pytest.raises(DesignValidationError, match="only one"):
        InMemoryDesignRepository().replace(
            OWNER,
            WORKSPACE,
            SchemiiDesignReplace.model_validate(
                {"expectedDesignRevision": 0, "content": conflicting}
            ),
        )

    invalid_reference = content().model_dump(mode="json", by_alias=True)
    invalid_reference["tables"][0]["checks"][0]["columnIds"] = [
        "column_" + "9" * 32
    ]
    with pytest.raises(DesignValidationError, match="own table"):
        InMemoryDesignRepository().replace(
            OWNER,
            WORKSPACE,
            SchemiiDesignReplace.model_validate(
                {"expectedDesignRevision": 0, "content": invalid_reference}
            ),
        )

    invalid_index_reference = content().model_dump(mode="json", by_alias=True)
    invalid_index_reference["tables"][0]["indexes"][0][
        "expressionSourceColumnIds"
    ] = ["column_" + "8" * 32]
    with pytest.raises(DesignValidationError, match="own table"):
        InMemoryDesignRepository().replace(
            OWNER,
            WORKSPACE,
            SchemiiDesignReplace.model_validate(
                {"expectedDesignRevision": 0, "content": invalid_index_reference}
            ),
        )

    generated_dependency = content().model_dump(mode="json", by_alias=True)
    generated_dependency["tables"][0]["columns"].append(
        {
            "id": "column_" + "6" * 32,
            "name": "double_email_length",
            "dataType": "integer",
            "nullable": False,
            "generatedExpression": "email_length * 2",
            "generatedSourceColumnIds": [NAME_LENGTH_COLUMN],
        }
    )
    with pytest.raises(DesignValidationError, match="other generated columns"):
        InMemoryDesignRepository().replace(
            OWNER,
            WORKSPACE,
            SchemiiDesignReplace.model_validate(
                {"expectedDesignRevision": 0, "content": generated_dependency}
            ),
        )


def test_new_dependency_fields_are_backward_compatible_with_saved_design_json() -> None:
    legacy = content().model_dump(mode="json", by_alias=True)
    legacy_column = legacy["tables"][0]["columns"][1]
    legacy_column.pop("generatedExpression")
    legacy_column.pop("generatedSourceColumnIds")
    legacy_check = legacy["tables"][0]["checks"][0]
    legacy_check.pop("columnIds")
    legacy_index = legacy["tables"][0]["indexes"][0]
    legacy_index.pop("expressionSourceColumnIds")
    legacy_index.pop("predicateColumnIds")

    parsed = SchemiiDesignContent.model_validate(legacy)

    assert parsed.tables[0].columns[1].generated_expression is None
    assert parsed.tables[0].columns[1].generated_source_column_ids == []
    assert parsed.tables[0].checks[0].column_ids == []
    assert parsed.tables[0].indexes[0].expression_source_column_ids == []
    assert parsed.tables[0].indexes[0].predicate_column_ids == []


def test_legacy_materialized_population_intent_migrates_and_exports_explicitly() -> None:
    legacy = {
        "views": [
            {
                "id": VIEW,
                "name": "empty_rollup",
                "kind": "materialized_view",
                "definition": "SELECT 1 AS total",
                "populated": False,
            }
        ]
    }
    parsed = SchemiiDesignContent.model_validate(legacy)
    assert parsed.views[0].populate_on_create is False
    document = parsed.model_dump(mode="json", by_alias=True)
    assert "populated" not in document["views"][0]
    assert document["views"][0]["populateOnCreate"] is False

    repository = InMemoryDesignRepository()
    design = repository.replace(
        OWNER,
        WORKSPACE,
        SchemiiDesignReplace(expected_design_revision=0, content=parsed),
    )
    exported = export_design(
        design,
        SchemiiDesignExportRequest(
            expected_design_revision=1,
            format="postgresql_sql",
        ),
    )
    assert 'CREATE MATERIALIZED VIEW "empty_rollup" AS\nSELECT 1 AS total\nWITH NO DATA;' in exported.content


def test_design_validation_rejects_non_query_view_definitions() -> None:
    invalid = content().model_dump(mode="json", by_alias=True)
    invalid["views"] = [
        {
            "id": VIEW,
            "name": "unsafe",
            "kind": "view",
            "definition": "DELETE FROM accounts",
        }
    ]
    with pytest.raises(DesignValidationError, match="SELECT query"):
        InMemoryDesignRepository().replace(
            OWNER,
            WORKSPACE,
            SchemiiDesignReplace.model_validate(
                {"expectedDesignRevision": 0, "content": invalid}
            ),
        )


def test_exports_are_deterministic_quoted_and_bound_to_the_saved_revision() -> None:
    repository = InMemoryDesignRepository()
    design = repository.replace(
        OWNER,
        WORKSPACE,
        SchemiiDesignReplace(expected_design_revision=0, content=content()),
    )
    request = SchemiiDesignExportRequest(
        expected_design_revision=1,
        format="postgresql_sql",
        include_drop_statements=True,
    )

    first = export_design(design, request)
    second = export_design(design, request)

    assert first == second
    assert 'DROP TABLE IF EXISTS "user ""account""" CASCADE;' in first.content
    assert 'CREATE TABLE "user ""account"""' in first.content
    assert "GENERATED ALWAYS AS IDENTITY" in first.content
    assert "DEFAULT 'unknown'::text" in first.content
    assert "GENERATED ALWAYS AS (char_length(email)) STORED" in first.content
    assert 'CONSTRAINT "user account_email_check" CHECK (length(email) > 3)' in first.content
    assert 'CREATE UNIQUE INDEX "user account_email_idx"' in first.content
    assert "USING \"btree\" (lower(email)) WHERE email <> 'unknown'::text;" in first.content
    assert first.sha256 == hashlib.sha256(first.content.encode()).hexdigest()

    json_export = export_design(
        design,
        SchemiiDesignExportRequest(
            expected_design_revision=1,
            format="schemii_json",
        ),
    )
    assert '"formatVersion": 1' in json_export.content
    assert '"dataType": "bigint"' in json_export.content
