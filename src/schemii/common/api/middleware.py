"""Cross-cutting response safety for the shared API."""

import logging
import secrets
import time

from fastapi import FastAPI, Request, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .observability import (
    REQUEST_ID_PATTERN,
    emit_event,
    log_safe_exception,
    request_log_fields,
)


LOCAL_PROTOTYPE_HOSTS = ("127.0.0.1", "localhost")
FRONTEND_DOCUMENT_PATHS = frozenset(("/", "/api-map", "/db-map", "/system-map", "/ai-prototype"))


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
LOGGER = logging.getLogger("schemii.http")


def install_api_middleware(application: FastAPI) -> None:
    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=LOCAL_PROTOTYPE_HOSTS,
        www_redirect=False,
    )

    @application.middleware("http")
    async def request_context(request: Request, call_next) -> Response:
        incoming_request_id = request.headers.get("X-Request-ID", "")
        request_id = (
            incoming_request_id
            if REQUEST_ID_PATTERN.fullmatch(incoming_request_id)
            else secrets.token_hex(16)
        )
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as error:
            log_safe_exception(
                LOGGER,
                request,
                error,
                status_code=500,
                error_code="unhandled_request_error",
            )
            emit_event(
                LOGGER,
                logging.INFO,
                "request_complete",
                **request_log_fields(request),
                status_code=500,
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
            )
            raise
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
        emit_event(
            LOGGER,
            logging.DEBUG if path == "/api/v1/readiness" else logging.INFO,
            "request_complete",
            **request_log_fields(request),
            status_code=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
        )
        return response
