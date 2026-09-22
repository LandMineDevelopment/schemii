"""Administrator inventory for explicit role-to-report source bindings."""
from fastapi import APIRouter, Depends, Request
from .routes import admin

router = APIRouter(prefix="/api/v1/admin")


@router.get("/resources")
def resources(request: Request, actor=Depends(admin)):
    services, auth = request.app.state.services, request.app.state.auth
    with auth.store.transaction() as state:
        owners = list(state["users"])
    connections, dashboards = [], []
    for owner in owners:
        connections.extend({"id": item.id, "owner_id": owner, "name": item.name,
                            "database": item.database, "username": item.username}
                           for item in services.connections.list(owner))
        dashboards.extend({"id": item.id, "owner_id": owner, "name": item.name}
                          for item in services.dashboards.list(owner))
    return {"connections": connections, "dashboards": dashboards}
