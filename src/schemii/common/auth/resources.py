"""Administrator inventory for explicit role-to-report source bindings."""
from fastapi import APIRouter, Depends, Request
from schemii.common.connections.models import SCHEMII_CONNECTION_OWNER_ID
from .routes import admin

router = APIRouter(prefix="/api/v1/admin")


@router.get("/resources")
def resources(request: Request, actor=Depends(admin)):
    services, auth = request.app.state.services, request.app.state.auth
    with auth.store.transaction() as state:
        owners = list(state["users"])
    connections, dashboards = [], []
    for item in services.connections.list_schemii_owned():
        connections.append({"id": item.id, "owner_id": SCHEMII_CONNECTION_OWNER_ID,
                            "ownership": "schemii", "name": item.name,
                            "database": item.database, "username": item.username})
    for owner in owners:
        dashboards.extend({"id": item.id, "owner_id": owner, "name": item.name}
                          for item in services.dashboards.list(owner))
    return {"connections": connections, "dashboards": dashboards}
