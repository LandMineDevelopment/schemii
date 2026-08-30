"""Runtime routes shared by all product APIs."""

from fastapi import APIRouter, Depends

from schemii.common.metadata.models import Principal, get_current_principal

from .models import ApiModel


router = APIRouter(prefix="/api/v1", tags=["runtime"])


class SessionResponse(ApiModel):
    user_id: str
    authentication_source: str
    ephemeral: bool


class ReadinessResponse(ApiModel):
    ready: bool
    metadata: str
    persistence: str


@router.get("/session", response_model=SessionResponse)
def session(
    principal: Principal = Depends(get_current_principal),
) -> SessionResponse:
    return SessionResponse(
        user_id=principal.user_id,
        authentication_source=principal.authentication_source,
        ephemeral=True,
    )


@router.get("/readiness", response_model=ReadinessResponse)
def readiness() -> ReadinessResponse:
    return ReadinessResponse(
        ready=True,
        metadata="local_prototype",
        persistence="memory",
    )
