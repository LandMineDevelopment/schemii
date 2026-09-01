"""Assemble Schemii product routes in user-workflow order."""

from fastapi import APIRouter

from .ai.routes import router as ai_router
from .catalog.routes import router as catalog_router
from .console.routes import router as console_router
from .designs.routes import router as designs_router
from .migrations.routes import router as migrations_router
from .workspaces.planned_routes import router as planned_workspaces_router
from .workspaces.routes import (
    legacy_database_browser_router,
    legacy_design_router,
    router as workspaces_router,
)


router = APIRouter(prefix="/api/v1/schemii", tags=["schemii"])
router.include_router(workspaces_router)
router.include_router(planned_workspaces_router)
router.include_router(designs_router)
router.include_router(legacy_design_router)
router.include_router(catalog_router)
router.include_router(legacy_database_browser_router)
router.include_router(migrations_router)
router.include_router(console_router)
router.include_router(ai_router)
