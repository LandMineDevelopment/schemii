"""Small secret-safe structured logging helpers for the HTTP boundary."""

from __future__ import annotations

import json
import logging
import re
import traceback
from typing import Any

from fastapi import Request


REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def emit_event(
    logger: logging.Logger,
    level: int,
    event: str,
    **fields: str | int | float | bool | None | list[str],
) -> None:
    """Emit one predictable JSON object without request bodies or headers."""

    logger.log(
        level,
        json.dumps(
            {"event": event, **fields},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ),
    )


def request_log_fields(request: Request) -> dict[str, Any]:
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    return {
        "request_id": getattr(request.state, "request_id", "unknown"),
        "method": request.method,
        "path": request.url.path,
        "route": route_path if isinstance(route_path, str) else None,
    }


def log_safe_exception(
    logger: logging.Logger,
    request: Request,
    error: BaseException,
    *,
    status_code: int,
    error_code: str,
) -> None:
    """Log exception type and frame locations without its possibly secret message."""

    if getattr(request.state, "safe_exception_logged", False):
        return
    request.state.safe_exception_logged = True
    frames = traceback.extract_tb(error.__traceback__)[-20:]
    emit_event(
        logger,
        logging.ERROR,
        "request_error",
        **request_log_fields(request),
        status_code=status_code,
        error_code=error_code,
        exception_type=type(error).__name__,
        stack=[f"{frame.filename}:{frame.lineno}:{frame.name}" for frame in frames],
    )
