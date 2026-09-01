"""Current identity contract shared by all product APIs."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


LOCAL_PROTOTYPE_USER_ID = "user_local_prototype"


class Principal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str
    authentication_source: Literal["local_prototype"]


def get_current_principal() -> Principal:
    """Return the single local owner until deployment-neutral auth is added.

    A future identity adapter can resolve an authenticated owner without
    changing product route or repository signatures.
    """
    return Principal(
        user_id=LOCAL_PROTOTYPE_USER_ID,
        authentication_source="local_prototype",
    )
