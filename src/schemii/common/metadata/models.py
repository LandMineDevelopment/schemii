"""Current identity contract shared by all product APIs."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


LOCAL_PROTOTYPE_USER_ID = "user_local_prototype"


class Principal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str
    authentication_source: Literal["local_prototype"]


def get_current_principal() -> Principal:
    """Return the valid single-user identity used during API prototyping.

    TODO(metadata-postgres): resolve an authenticated, owner-scoped principal
    from a server-side session. The future implementation must support users,
    authentication identities, Tailscale identity mapping, session expiry, and
    password reset/recovery without changing product route signatures.
    """
    return Principal(
        user_id=LOCAL_PROTOTYPE_USER_ID,
        authentication_source="local_prototype",
    )
