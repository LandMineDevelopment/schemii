"""Current identity contract shared by all product APIs."""

from typing import Literal

from fastapi import Request

from pydantic import BaseModel, ConfigDict


LOCAL_PROTOTYPE_USER_ID = "user_local_prototype"


class Principal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str
    authentication_source: Literal["local_prototype"]


def get_current_principal(request: Request) -> Principal:
    """Return the single local owner until deployment-neutral auth is added.

    A future identity adapter can resolve an authenticated owner without
    changing product route or repository signatures.
    """
    # TODO(multiuser-auth): Replace the local principal with authenticated sessions
    # before supporting shared/public deployments. Keep connections, credentials,
    # and unmigrated designs scoped to the resolved owner; never share DB identities.
    # Deferred while Schemii, Schemoo, and Schemer use the single-user dev setup.
    principal = Principal(
        user_id=LOCAL_PROTOTYPE_USER_ID,
        authentication_source="local_prototype",
    )
    request.state.principal = principal
    return principal
