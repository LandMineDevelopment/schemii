"""Shared FastAPI contracts and runtime routes."""

from .errors import ApiProblem, install_api_error_handlers
from .middleware import install_api_middleware
from .routes import router

__all__ = [
    "ApiProblem",
    "install_api_error_handlers",
    "install_api_middleware",
    "router",
]
