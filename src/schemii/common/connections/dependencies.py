"""Product-neutral connection dependency snapshots."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class ConnectionDependentResource:
    """One owner-scoped product resource retaining a saved connection."""

    provider: str
    kind: str
    resource_id: str
    revision: int
    name: str
    target: str | None = None
    deletion_blocked: bool = False
    blocking_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ConnectionDeletionImpact:
    """Revision-bound resources that must be removed before a connection."""

    connection_id: str
    connection_revision: int
    dependencies: tuple[ConnectionDependentResource, ...]
    fingerprint: str

    @classmethod
    def build(
        cls,
        connection_id: str,
        connection_revision: int,
        dependencies: tuple[ConnectionDependentResource, ...],
    ) -> "ConnectionDeletionImpact":
        ordered = tuple(
            sorted(
                dependencies,
                key=lambda item: (
                    item.provider,
                    item.kind,
                    item.resource_id,
                ),
            )
        )
        document = {
            "connectionId": connection_id,
            "connectionRevision": connection_revision,
            "dependencies": [asdict(item) for item in ordered],
        }
        canonical = json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return cls(
            connection_id=connection_id,
            connection_revision=connection_revision,
            dependencies=ordered,
            fingerprint=hashlib.sha256(canonical).hexdigest(),
        )
