"""Current identity contract shared by all product APIs."""

from typing import Literal

from fastapi import HTTPException, Request

from pydantic import BaseModel, ConfigDict


LOCAL_PROTOTYPE_USER_ID = "user_local_prototype"


class Principal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str
    authentication_source: Literal["local_prototype", "session"]


def get_current_principal(request: Request) -> Principal:
    """Return the single local owner until deployment-neutral auth is added.

    A future identity adapter can resolve an authenticated owner without
    changing product route or repository signatures.
    """
    if getattr(request.app.state, "auth", None) is not None and request.app.state.auth.enabled:
        principal = getattr(request.state, "principal", None)
        if principal is None:
            raise HTTPException(401, "Sign in required")
        return principal
    principal = Principal(
        user_id=LOCAL_PROTOTYPE_USER_ID,
        authentication_source="local_prototype",
    )
    request.state.principal = principal
    return principal
