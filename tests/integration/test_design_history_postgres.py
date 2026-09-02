"""Behavior parity for durable design-history retention."""

from schemii.schemii.designs.history_retention import (
    MAX_RETAINED_HISTORY_ACTIONS,
    MAX_RETAINED_HISTORY_TRANSITIONS,
)
from schemii.schemii.designs.models import SchemiiDesignContent, SchemiiDesignReplace
from schemii.schemii.designs.postgres_store import PostgresDesignRepository
from schemii.schemii.workspaces.models import SchemiiWorkspaceCreate
from schemii.schemii.workspaces.postgres_store import PostgresWorkspaceRepository
from tests.integration.postgres_fixture import PostgresMetadataHarness


def _content(name: str) -> SchemiiDesignContent:
    return SchemiiDesignContent.model_validate(
        {
            "tables": [
                {
                    "id": "table_" + "1" * 32,
                    "name": name,
                    "columns": [
                        {
                            "id": "column_" + "2" * 32,
                            "name": "id",
                            "dataType": "bigint",
                            "nullable": False,
                        }
                    ],
                }
            ]
        }
    )


def _stored_history_counts(
    harness: PostgresMetadataHarness,
    workspace_id: str,
) -> tuple[int, int, int]:
    with harness.connection_factory() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                WITH RECURSIVE active AS (
                    SELECT entry.id, entry.parent_id
                    FROM schemii.workspace_design_history_entries AS entry
                    JOIN schemii.workspace_design_history_state AS state
                      ON state.active_tip_id = entry.id
                    WHERE state.owner_id = %s AND state.workspace_id = %s
                    UNION ALL
                    SELECT parent.id, parent.parent_id
                    FROM schemii.workspace_design_history_entries AS parent
                    JOIN active ON active.parent_id = parent.id
                )
                SELECT
                    (
                        SELECT count(*)
                        FROM schemii.workspace_design_history_entries
                        WHERE owner_id = %s AND workspace_id = %s
                    ) AS stored_count,
                    (SELECT count(*) FROM active) AS active_count,
                    (
                        SELECT count(*)
                        FROM schemii.workspace_design_history_entries
                        WHERE owner_id = %s AND workspace_id = %s
                          AND parent_id IS NULL
                    ) AS root_count
                """,
                (
                    harness.owner_id,
                    workspace_id,
                    harness.owner_id,
                    workspace_id,
                    harness.owner_id,
                    workspace_id,
                ),
            )
            row = cursor.fetchone()
    return int(row["stored_count"]), int(row["active_count"]), int(row["root_count"])


def test_postgres_history_is_physically_bounded_and_discards_redo_branches(
    postgres_metadata: PostgresMetadataHarness,
) -> None:
    with postgres_metadata.connection_factory() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT delete_rule
                FROM information_schema.referential_constraints
                WHERE constraint_schema = 'schemii'
                  AND constraint_name =
                      'workspace_design_history_entries_parent_id_fkey'
                """
            )
            assert cursor.fetchone()["delete_rule"] == "RESTRICT"
    workspaces = PostgresWorkspaceRepository(postgres_metadata.connection_factory)
    designs = PostgresDesignRepository(postgres_metadata.connection_factory)
    workspace = workspaces.create(
        postgres_metadata.owner_id,
        SchemiiWorkspaceCreate(name="History retention integration"),
    )

    revision = 0
    group_id = "dgrp_" + "3" * 32
    for index in range(3):
        revision = designs.replace(
            postgres_metadata.owner_id,
            workspace.id,
            SchemiiDesignReplace(
                expected_design_revision=revision,
                content=_content(f"grouped_{index}"),
                history_group_id=group_id,
            ),
        ).revision
    assert _stored_history_counts(postgres_metadata, workspace.id) == (2, 2, 1)

    for index in range(MAX_RETAINED_HISTORY_ACTIONS + 25):
        revision = designs.replace(
            postgres_metadata.owner_id,
            workspace.id,
            SchemiiDesignReplace(
                expected_design_revision=revision,
                content=_content(f"edit_{index}"),
            ),
        ).revision

    expected_bound = MAX_RETAINED_HISTORY_ACTIONS + 2
    assert _stored_history_counts(postgres_metadata, workspace.id) == (
        expected_bound,
        expected_bound,
        1,
    )
    initial_revision, initial_content = designs.initial_content(
        postgres_metadata.owner_id,
        workspace.id,
    )
    assert initial_revision == 0
    assert initial_content == SchemiiDesignContent()

    for _ in range(3):
        revision = designs.undo(
            postgres_metadata.owner_id,
            workspace.id,
            revision,
        ).revision
    revision = designs.replace(
        postgres_metadata.owner_id,
        workspace.id,
        SchemiiDesignReplace(
            expected_design_revision=revision,
            content=_content("new_branch"),
        ),
    ).revision
    stored_count, active_count, root_count = _stored_history_counts(
        postgres_metadata,
        workspace.id,
    )
    assert stored_count == active_count
    assert stored_count < expected_bound
    assert root_count == 1

    reset = designs.replace(
        postgres_metadata.owner_id,
        workspace.id,
        SchemiiDesignReplace(
            expected_design_revision=revision,
            content=initial_content,
        ),
        operation_kind="baseline_reset",
    )
    assert reset.content == SchemiiDesignContent()
    assert designs.initial_content(postgres_metadata.owner_id, workspace.id) == (
        0,
        SchemiiDesignContent(),
    )

    revision = reset.revision
    for _ in range((MAX_RETAINED_HISTORY_TRANSITIONS // 2) + 5):
        revision = designs.undo(
            postgres_metadata.owner_id,
            workspace.id,
            revision,
        ).revision
        revision = designs.redo(
            postgres_metadata.owner_id,
            workspace.id,
            revision,
        ).revision
    with postgres_metadata.connection_factory() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(*) AS transition_count
                FROM schemii.workspace_design_history_transitions
                WHERE owner_id = %s AND workspace_id = %s
                """,
                (postgres_metadata.owner_id, workspace.id),
            )
            assert int(cursor.fetchone()["transition_count"]) == (
                MAX_RETAINED_HISTORY_TRANSITIONS
            )
