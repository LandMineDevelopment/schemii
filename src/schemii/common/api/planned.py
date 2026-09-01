"""Explicit failure boundary for reviewable but unfinished API contracts."""

from typing import NoReturn

from .errors import ApiProblem
from .models import ApiErrorResponse


PLANNED_OPENAPI = {"x-schemii-status": "planned"}
PLANNED_RESPONSES = {
    501: {
        "model": ApiErrorResponse,
        "description": "The reviewed contract is registered but not implemented yet",
    }
}


def planned_capability(capability: str) -> NoReturn:
    """Reject an unfinished route without implying that any work occurred."""

    raise ApiProblem(
        501,
        "planned_capability",
        "This API contract is available for review but is not implemented yet",
        details={"capability": capability, "status": "planned"},
    )
