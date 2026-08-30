"""Assemble Schemii's physical PostgreSQL API routes."""

from fastapi import APIRouter

from .workspaces.routes import router as workspaces_router


router = APIRouter(prefix="/api/v1/schemii", tags=["schemii"])
router.include_router(workspaces_router)
