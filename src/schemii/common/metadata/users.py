"""Shared owner registration inside metadata transactions."""

from __future__ import annotations

from typing import Any


def ensure_local_metadata_user(cursor: Any, owner_id: str) -> None:
    """Create the current local owner if needed and lock it for bounded writes."""

    cursor.execute(
        """
        INSERT INTO metadata.users (id, display_name)
        VALUES (%s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (owner_id, "Local user"),
    )
    cursor.execute(
        "SELECT id FROM metadata.users WHERE id = %s FOR UPDATE",
        (owner_id,),
    )
    if cursor.fetchone() is None:
        raise RuntimeError("metadata owner registration failed")
