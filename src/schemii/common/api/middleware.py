"""Cross-cutting response safety for the shared API."""

import secrets

from fastapi import FastAPI, Request, Response


def install_api_middleware(application: FastAPI) -> None:
    @application.middleware("http")
    async def request_context(request: Request, call_next) -> Response:
        request_id = secrets.token_hex(16)
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Request-ID"] = request_id
        return response
