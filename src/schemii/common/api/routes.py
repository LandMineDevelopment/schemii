"""Runtime routes shared by all product APIs."""

from fastapi import APIRouter, Depends, Request, Response, status

from schemii.common.errors import MetadataStorageUnavailableError

from schemii.common.metadata.models import Principal, get_current_principal

from .models import ApiModel


router = APIRouter(prefix="/api/v1", tags=["runtime"])


class SessionResponse(ApiModel):
    """Identity and persistence characteristics of the active local session."""

    user_id: str
    authentication_source: str
    ephemeral: bool


class ReadinessResponse(ApiModel):
    """Readiness and storage mode reported by this Schemii process."""

    ready: bool
    metadata: str
    persistence: str


@router.get("/session", response_model=SessionResponse)
def session(
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> SessionResponse:
    """Describe the owner identity used to scope requests in this process."""

    return SessionResponse(
        user_id=principal.user_id,
        authentication_source=principal.authentication_source,
        ephemeral=not request.app.state.services.metadata.durable,
    )


@router.get(
    "/readiness",
    response_model=ReadinessResponse,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ReadinessResponse,
            "description": "Metadata dependency is unavailable",
        }
    },
)
def readiness(request: Request, response: Response) -> ReadinessResponse:
    """Report whether this process can currently serve API requests."""

    ready = True
    try:
        request.app.state.services.metadata.check_readiness()
    except MetadataStorageUnavailableError:
        ready = False
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        ready=ready,
        metadata=request.app.state.services.metadata.storage,
        persistence=(
            "durable"
            if request.app.state.services.metadata.durable
            else "memory"
        ),
    )
