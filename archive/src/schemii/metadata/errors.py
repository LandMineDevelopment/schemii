from __future__ import annotations

import copy
from typing import Any


class MetadataStoreError(Exception):
    """A structured metadata failure that never exposes connection details."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 500,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        self.details = copy.deepcopy(details or {})

    def to_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details:
            error["details"] = copy.deepcopy(self.details)
        return {"error": error}
