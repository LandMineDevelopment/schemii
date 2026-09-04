"""Bound durable design history without changing undo/redo semantics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence


MAX_RETAINED_HISTORY_ACTIONS = 100
MAX_RETAINED_HISTORY_TRANSITIONS = MAX_RETAINED_HISTORY_ACTIONS * 2


class HistoryEntry(Protocol):
    id: int
    operation_group_id: str | None


@dataclass(frozen=True, slots=True)
class HistoryEntryRef:
    id: int
    parent_id: int | None
    operation_group_id: str | None


def history_group(entry: HistoryEntry) -> str:
    """Return the logical action identity for one immutable snapshot."""

    return entry.operation_group_id or f"entry:{entry.id}"


def history_boundary_index(
    entries: Sequence[HistoryEntry],
    retained_limit: int = MAX_RETAINED_HISTORY_ACTIONS,
) -> int:
    """Locate the oldest state needed to undo the retained logical actions."""

    index = len(entries) - 1
    actions = 0
    while index > 0 and actions < retained_limit:
        group = history_group(entries[index])
        while index > 0 and history_group(entries[index]) == group:
            index -= 1
        actions += 1
    return index


def history_target_index(
    entries: Sequence[HistoryEntry],
    cursor_index: int,
    action: str,
) -> int | None:
    """Resolve a grouped undo/redo destination within the retained boundary."""

    if action == "undo":
        if cursor_index == 0:
            return None
        group = history_group(entries[cursor_index])
        target = cursor_index - 1
        while target > 0 and history_group(entries[target]) == group:
            target -= 1
        return target if target >= history_boundary_index(entries) else None
    if cursor_index >= len(entries) - 1:
        return None
    group = history_group(entries[cursor_index + 1])
    target = cursor_index + 1
    while target + 1 < len(entries) and history_group(entries[target + 1]) == group:
        target += 1
    return target


def retained_history_entries(
    entries: Sequence[HistoryEntry],
    retained_limit: int = MAX_RETAINED_HISTORY_ACTIONS,
) -> list[HistoryEntry]:
    """Keep the immutable root, one snapshot per action, and the undo boundary.

    A grouped action can produce several saves while a gesture is in progress. Only
    its final state is addressable by undo or redo, so intermediate snapshots are
    redundant once the next save commits.
    """

    if not entries:
        return []
    compacted: list[HistoryEntry] = [entries[0]]
    for entry in entries[1:]:
        if len(compacted) > 1 and history_group(compacted[-1]) == history_group(entry):
            compacted[-1] = entry
        else:
            compacted.append(entry)
    boundary = history_boundary_index(compacted, retained_limit)
    if boundary == 0:
        return compacted
    return [compacted[0], *compacted[boundary:]]


def prune_postgres_history(
    cursor: Any,
    owner_id: str,
    workspace_id: str,
    retained_limit: int = MAX_RETAINED_HISTORY_ACTIONS,
) -> None:
    """Physically retain only the active, bounded history chain.

    The state row is locked by each caller before this runs. Kept entries are first
    linked into a complete chain. Obsolete entries are then detached from each
    other before deletion so the deployed restrictive parent key remains safe.
    """

    cursor.execute(
        """
        WITH RECURSIVE chain AS (
            SELECT entry.id, entry.parent_id, entry.operation_group_id
            FROM schemii.workspace_design_history_entries AS entry
            JOIN schemii.workspace_design_history_state AS state
              ON state.active_tip_id = entry.id
            WHERE state.owner_id = %s AND state.workspace_id = %s
            UNION ALL
            SELECT parent.id, parent.parent_id, parent.operation_group_id
            FROM schemii.workspace_design_history_entries AS parent
            JOIN chain ON chain.parent_id = parent.id
        )
        SELECT chain.*
        FROM chain
        """,
        (owner_id, workspace_id),
    )
    rows = cursor.fetchall()
    if not rows:
        return
    by_id = {
        int(row["id"]): HistoryEntryRef(
            id=int(row["id"]),
            parent_id=int(row["parent_id"]) if row["parent_id"] is not None else None,
            operation_group_id=row["operation_group_id"],
        )
        for row in rows
    }
    parent_ids = {entry.parent_id for entry in by_id.values() if entry.parent_id is not None}
    tip = next(entry for entry in by_id.values() if entry.id not in parent_ids)
    chain: list[HistoryEntryRef] = []
    current: HistoryEntryRef | None = tip
    while current is not None:
        chain.append(current)
        current = by_id.get(current.parent_id) if current.parent_id is not None else None
    chain.reverse()
    retained = retained_history_entries(chain, retained_limit)
    previous_id: int | None = None
    for entry in retained:
        if entry.parent_id != previous_id:
            cursor.execute(
                """
                UPDATE schemii.workspace_design_history_entries
                SET parent_id = %s
                WHERE owner_id = %s AND workspace_id = %s AND id = %s
                """,
                (previous_id, owner_id, workspace_id, entry.id),
            )
        previous_id = entry.id
    retained_ids = [entry.id for entry in retained]
    cursor.execute(
        """
        UPDATE schemii.workspace_design_history_entries
        SET parent_id = NULL
        WHERE owner_id = %s AND workspace_id = %s
          AND NOT (id = ANY(%s))
        """,
        (owner_id, workspace_id, retained_ids),
    )
    cursor.execute(
        """
        DELETE FROM schemii.workspace_design_history_entries
        WHERE owner_id = %s AND workspace_id = %s
          AND NOT (id = ANY(%s))
        """,
        (owner_id, workspace_id, retained_ids),
    )
    cursor.execute(
        """
        DELETE FROM schemii.workspace_design_history_transitions
        WHERE id IN (
            SELECT id
            FROM schemii.workspace_design_history_transitions
            WHERE owner_id = %s AND workspace_id = %s
            ORDER BY id DESC
            OFFSET %s
        )
        """,
        (owner_id, workspace_id, retained_limit * 2),
    )
