from __future__ import annotations

import copy
from typing import Any


class AiDisclosureError(Exception):
    """A bounded AI disclosure failure safe for an HTTP response."""

    def __init__(self, status: int, code: str, message: str, **details: Any):
        super().__init__(message)
        self.status = status
        self.code = code
        error = {"code": code, "message": message}
        if details:
            error["details"] = copy.deepcopy(details)
        self.payload = {"error": error}

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.payload)
