"""Consistent, non-disclosing API error handling."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHttpException

from schemii.common.connections.store import (
    ConnectionCredentialUnreadableError,
    ConnectionStorageUnavailableError,
)


class ApiProblem(RuntimeError):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def _response(
    request: Request,
    problem: ApiProblem,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    response_headers = {
        "Cache-Control": "no-store",
        "X-Request-ID": _request_id(request),
    }
    response_headers.update(headers or {})
    return JSONResponse(
        status_code=problem.status_code,
        headers=response_headers,
        content={
            "error": {
                "code": problem.code,
                "message": problem.message,
                "retryable": problem.retryable,
                "requestId": _request_id(request),
                "details": problem.details,
            }
        },
    )


def install_api_error_handlers(application: FastAPI) -> None:
    @application.exception_handler(ApiProblem)
    async def handle_api_problem(request: Request, error: ApiProblem) -> JSONResponse:
        return _response(request, error)

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        fields = [
            {
                "location": [str(item) for item in issue.get("loc", ())],
                "message": issue.get("msg", "Invalid value"),
                "type": issue.get("type", "validation_error"),
            }
            for issue in error.errors()
        ]
        return _response(
            request,
            ApiProblem(
                422,
                "validation_error",
                "The request did not match the API contract",
                details={"fields": fields},
            ),
        )

    @application.exception_handler(StarletteHttpException)
    async def handle_http_error(
        request: Request,
        error: StarletteHttpException,
    ) -> JSONResponse:
        code = {
            404: "not_found",
            405: "method_not_allowed",
        }.get(error.status_code, "http_error")
        message = {
            404: "The requested API resource was not found",
            405: "The HTTP method is not supported for this API resource",
        }.get(error.status_code, "The request could not be completed")
        safe_headers = {
            name: value
            for name, value in (error.headers or {}).items()
            if name.lower() in {"allow", "www-authenticate"}
        }
        return _response(
            request,
            ApiProblem(error.status_code, code, message),
            headers=safe_headers,
        )

    @application.exception_handler(ConnectionStorageUnavailableError)
    async def handle_connection_storage_error(
        request: Request,
        error: ConnectionStorageUnavailableError,
    ) -> JSONResponse:
        return _response(
            request,
            ApiProblem(
                503,
                "connection_storage_unavailable",
                str(error),
                retryable=True,
            ),
        )

    @application.exception_handler(ConnectionCredentialUnreadableError)
    async def handle_connection_credential_error(
        request: Request,
        error: ConnectionCredentialUnreadableError,
    ) -> JSONResponse:
        return _response(
            request,
            ApiProblem(
                500,
                "connection_credential_unreadable",
                str(error),
            ),
        )

    @application.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
        del error
        return _response(
            request,
            ApiProblem(
                500,
                "internal_error",
                "The server could not complete the request",
            ),
        )
