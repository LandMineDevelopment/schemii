"""Cross-cutting response safety for the shared API."""

import logging
import base64
import hashlib
import secrets
import time

from fastapi import FastAPI, Request, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware
from schemii.common.frontend import COMMON_IMPORT_MAP
from schemii.schemer.frontend import SCHEMER_IMPORT_MAP

from .observability import (
    REQUEST_ID_PATTERN,
    emit_event,
    log_safe_exception,
    request_log_fields,
)


LOCAL_PROTOTYPE_HOSTS = ("127.0.0.1", "localhost")
FRONTEND_DOCUMENT_PATHS = frozenset(("/", "/api-map", "/db-map", "/system-map", "/ai-prototype", "/schemoo", "/schemer", "/login", "/account", "/admin"))
IMPORT_MAP_DIGEST = base64.b64encode(hashlib.sha256(COMMON_IMPORT_MAP.encode()).digest()).decode()
SCHEMER_IMPORT_MAP_DIGEST = base64.b64encode(hashlib.sha256(SCHEMER_IMPORT_MAP.encode()).digest()).decode()


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
        f"script-src 'self' 'sha256-{IMPORT_MAP_DIGEST}' 'sha256-{SCHEMER_IMPORT_MAP_DIGEST}'",
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
        if path.startswith(("/assets/", "/schemoo-assets/")):
            response.headers["Cache-Control"] = "public, max-age=0, must-revalidate"
        elif path in FRONTEND_DOCUMENT_PATHS:
            response.headers["Cache-Control"] = "no-store" if getattr(getattr(request.app.state, "auth", None), "enabled", False) else "no-cache"
        else:
            response.headers["Cache-Control"] = "no-store"
        response.headers["Permissions-Policy"] = (
            "camera=(), geolocation=(), microphone=(), payment=(), usb=()"
        )
        # Keep cross-origin referrers private while preserving a verifiable Origin
        # on same-origin POST forms (including streamed CSV downloads).
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if path in FRONTEND_DOCUMENT_PATHS or path.startswith(("/assets/", "/schemoo-assets/")):
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
