"""Stable service errors shared by migration planning and execution."""

from __future__ import annotations

from typing import Any

from .repository import MigrationConflictError, MigrationStorageUnavailableError


class MigrationServiceError(RuntimeError):
    """User-safe migration failure with an HTTP-compatible status and code."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        self.status = status
        self.code = code
        self.details = details or {}
        self.retryable = retryable
        super().__init__(message)


def migration_repository_conflict(
    error: MigrationConflictError,
) -> MigrationServiceError:
    return MigrationServiceError(409, error.code, str(error), details=error.details)


def migration_storage_error(
    error: MigrationStorageUnavailableError,
) -> MigrationServiceError:
    details = {
        key: value
        for key, value in {
            "sqlstate": error.sqlstate,
            "constraint": error.constraint,
            "errorType": error.error_type,
        }.items()
        if value is not None
    }
    return MigrationServiceError(
        503,
        "migration_metadata_unavailable",
        "Migration metadata is temporarily unavailable",
        details=details,
        retryable=True,
    )
