"""API routes for reports, dashboards, filters, and visualizations."""

from fastapi import APIRouter


router = APIRouter(prefix="/api/v1/schemer", tags=["schemer"])
