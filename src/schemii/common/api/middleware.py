"""Cross-cutting response safety for the shared API."""

import secrets

from fastapi import FastAPI, Request, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware


LOCAL_PROTOTYPE_HOSTS = ("127.0.0.1", "localhost")
FRONTEND_DOCUMENT_PATHS = frozenset(("/", "/api-map", "/db-map", "/system-map"))


FRONTEND_CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "base-uri 'self'",
        "connect-src 'self'",
        "font-src 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "img-src 'self' data:",
        "object-src 'none'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
    )
)


def install_api_middleware(application: FastAPI) -> None:
    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=LOCAL_PROTOTYPE_HOSTS,
        www_redirect=False,
    )

    @application.middleware("http")
    async def request_context(request: Request, call_next) -> Response:
        request_id = secrets.token_hex(16)
        request.state.request_id = request_id
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=0, must-revalidate"
        elif path in FRONTEND_DOCUMENT_PATHS:
            response.headers["Cache-Control"] = "no-cache"
        else:
            response.headers["Cache-Control"] = "no-store"
        response.headers["Permissions-Policy"] = (
            "camera=(), geolocation=(), microphone=(), payment=(), usb=()"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if path in FRONTEND_DOCUMENT_PATHS or path.startswith("/assets/"):
            response.headers["Content-Security-Policy"] = FRONTEND_CONTENT_SECURITY_POLICY
        response.headers["X-Request-ID"] = request_id
        return response
