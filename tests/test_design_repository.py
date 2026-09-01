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
KEY = "key_" + "d" * 32


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
