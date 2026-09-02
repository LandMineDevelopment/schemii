import pytest

from schemii.schemii.designs.models import (
    DesignHistoryBaseline,
    SchemiiDesignContent,
    SchemiiDesignLayoutContent,
    SchemiiDesignLayoutReplace,
    SchemiiDesignReplace,
)
from schemii.schemii.designs.history_retention import MAX_RETAINED_HISTORY_ACTIONS
from schemii.schemii.designs.store import (
    DesignHistoryBoundaryError,
    InMemoryDesignRepository,
    design_fingerprint,
)


WORKSPACE_ID = "ws_" + "1" * 32
GROUP_ID = "dgrp_" + "2" * 32


def content(*names: str) -> SchemiiDesignContent:
    return SchemiiDesignContent.model_validate(
        {
            "tables": [
                {
                    "id": f"table_{index:032x}",
                    "name": name,
                    "columns": [
                        {
                            "id": f"column_{index:032x}",
                            "name": "id",
                            "dataType": "bigint",
                            "nullable": False,
                        }
                    ],
                }
                for index, name in enumerate(names, 1)
            ]
        }
    )


def baseline(value: SchemiiDesignContent, revision: int = 0) -> DesignHistoryBaseline:
    return DesignHistoryBaseline(
        kind="workspace_start",
        id="workspace_start",
        revision=0,
        design_revision=revision,
        label="Workspace starting point",
        fingerprint=design_fingerprint(value),
    )


def test_grouped_history_is_atomic_and_a_new_branch_discards_redo() -> None:
    repository = InMemoryDesignRepository()
    empty = content()
    first = repository.replace(
        "owner",
        WORKSPACE_ID,
        SchemiiDesignReplace(
            expected_design_revision=0,
            content=content("accounts"),
            history_group_id=GROUP_ID,
        ),
    )
    second = repository.replace(
        "owner",
        WORKSPACE_ID,
        SchemiiDesignReplace(
            expected_design_revision=first.revision,
            content=content("accounts", "invoices"),
            history_group_id=GROUP_ID,
        ),
    )
    key = ("owner", WORKSPACE_ID)
    assert len(repository._history[key]) == 2

    undone = repository.undo("owner", WORKSPACE_ID, second.revision)
    assert undone.content == empty
    assert repository.history_state("owner", WORKSPACE_ID, baseline(empty)).can_redo

    redone = repository.redo("owner", WORKSPACE_ID, undone.revision)
    assert [table.name for table in redone.content.tables] == ["accounts", "invoices"]

    undone_again = repository.undo("owner", WORKSPACE_ID, redone.revision)
    branched = repository.replace(
        "owner",
        WORKSPACE_ID,
        SchemiiDesignReplace(
            expected_design_revision=undone_again.revision,
            content=content("products"),
        ),
    )
    state = repository.history_state("owner", WORKSPACE_ID, baseline(empty))
    assert branched.revision == 6
    assert state.can_undo is True
    assert state.can_redo is False
    assert len(repository._history[key]) == 2


def test_history_restores_last_canvas_position_and_keeps_revisions_monotonic() -> None:
    repository = InMemoryDesignRepository()
    saved = repository.replace(
        "owner",
        WORKSPACE_ID,
        SchemiiDesignReplace(expected_design_revision=0, content=content("orders")),
    )
    layout = repository.get_layout("owner", WORKSPACE_ID)
    positioned = repository.replace_layout(
        "owner",
        WORKSPACE_ID,
        SchemiiDesignLayoutReplace.model_validate(
            {
                "expectedLayoutRevision": layout.revision,
                "expectedDesignRevision": saved.revision,
                "content": {
                    "objects": [
                        {
                            "objectId": "table_" + "0" * 31 + "1",
                            "layer": "tables",
                            "x": 240.0,
                            "y": 180.0,
                        }
                    ]
                },
            }
        ),
    )
    removed = repository.replace(
        "owner",
        WORKSPACE_ID,
        SchemiiDesignReplace(expected_design_revision=saved.revision, content=content()),
    )
    restored = repository.undo("owner", WORKSPACE_ID, removed.revision)

    assert restored.revision == 3
    assert repository.get_layout("owner", WORKSPACE_ID).content.objects[0].model_dump() == {
        "object_id": "table_" + "0" * 31 + "1",
        "layer": "tables",
        "x": 240.0,
        "y": 180.0,
    }
    assert repository.get_layout("owner", WORKSPACE_ID).revision > positioned.revision


def test_snapshot_returns_design_layout_and_history_from_one_revision() -> None:
    repository = InMemoryDesignRepository()
    initial = content()
    saved = repository.replace(
        "owner",
        WORKSPACE_ID,
        SchemiiDesignReplace(
            expected_design_revision=0,
            content=content("orders"),
        ),
    )

    snapshot = repository.snapshot(
        "owner",
        WORKSPACE_ID,
        baseline(initial),
    )

    assert snapshot.design == saved
    assert snapshot.layout.design_revision == saved.revision
    assert snapshot.history.design_revision == saved.revision
    assert snapshot.history.can_undo is True


def test_history_physically_retains_only_the_latest_one_hundred_actions() -> None:
    repository = InMemoryDesignRepository()
    revision = 0
    for index in range(MAX_RETAINED_HISTORY_ACTIONS + 25):
        saved = repository.replace(
            "owner",
            WORKSPACE_ID,
            SchemiiDesignReplace(
                expected_design_revision=revision,
                content=content(f"table_{index}"),
            ),
        )
        revision = saved.revision

    entries = repository._history[("owner", WORKSPACE_ID)]
    assert len(entries) == MAX_RETAINED_HISTORY_ACTIONS + 2
    assert sum(entry.parent_id is None for entry in entries.values()) == 1

    for _ in range(MAX_RETAINED_HISTORY_ACTIONS):
        revision = repository.undo("owner", WORKSPACE_ID, revision).revision

    with pytest.raises(DesignHistoryBoundaryError):
        repository.undo("owner", WORKSPACE_ID, revision)


def test_history_compacts_large_groups_without_changing_grouped_boundaries() -> None:
    repository = InMemoryDesignRepository()
    revision = 0
    for group_number in range(MAX_RETAINED_HISTORY_ACTIONS + 5):
        group_id = f"dgrp_{group_number + 1:032x}"
        for save_number in range(3):
            saved = repository.replace(
                "owner",
                WORKSPACE_ID,
                SchemiiDesignReplace(
                    expected_design_revision=revision,
                    content=content(f"table_{group_number}_{save_number}"),
                    history_group_id=group_id,
                ),
            )
            revision = saved.revision

    entries = repository._history[("owner", WORKSPACE_ID)]
    assert len(entries) == MAX_RETAINED_HISTORY_ACTIONS + 2

    first_undo = repository.undo("owner", WORKSPACE_ID, revision)
    assert first_undo.content == content(
        f"table_{MAX_RETAINED_HISTORY_ACTIONS + 3}_2"
    )
    revision = first_undo.revision
    for _ in range(MAX_RETAINED_HISTORY_ACTIONS - 1):
        revision = repository.undo("owner", WORKSPACE_ID, revision).revision
    with pytest.raises(DesignHistoryBoundaryError):
        repository.undo("owner", WORKSPACE_ID, revision)


def test_retention_preserves_workspace_start_for_detached_baseline_reset() -> None:
    repository = InMemoryDesignRepository()
    starting_content = content("seed_table")
    design, _ = repository.initialize(
        "owner",
        WORKSPACE_ID,
        starting_content,
        SchemiiDesignLayoutContent(),
    )
    revision = design.revision
    for index in range(MAX_RETAINED_HISTORY_ACTIONS + 10):
        revision = repository.replace(
            "owner",
            WORKSPACE_ID,
            SchemiiDesignReplace(
                expected_design_revision=revision,
                content=content(f"edited_{index}"),
            ),
        ).revision

    initial_revision, initial_content = repository.initial_content("owner", WORKSPACE_ID)
    assert initial_revision == 1
    assert initial_content == starting_content

    reset = repository.replace(
        "owner",
        WORKSPACE_ID,
        SchemiiDesignReplace(
            expected_design_revision=revision,
            content=initial_content,
        ),
        operation_kind="baseline_reset",
    )
    assert reset.content == starting_content
    assert len(repository._history[("owner", WORKSPACE_ID)]) == (
        MAX_RETAINED_HISTORY_ACTIONS + 2
    )


def test_identical_design_save_does_not_pollute_semantic_history() -> None:
    repository = InMemoryDesignRepository()
    value = content("orders")
    saved = repository.replace(
        "owner",
        WORKSPACE_ID,
        SchemiiDesignReplace(expected_design_revision=0, content=value),
    )
    unchanged = repository.replace(
        "owner",
        WORKSPACE_ID,
        SchemiiDesignReplace(expected_design_revision=saved.revision, content=value),
    )

    assert unchanged.revision == saved.revision
    undone = repository.undo("owner", WORKSPACE_ID, unchanged.revision)
    assert undone.content == content()
