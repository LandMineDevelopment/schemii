"""Model-driven report planning and bounded execution."""

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from schemii.common.metadata.models import Principal, get_current_principal
from schemii.schemoo.routes import api_errors
from .models import ReportQuery
from .service import query_report, report_plan

router = APIRouter(prefix="/api/v1/schemer", tags=["schemer"])


@router.post("/plan")
def plan_report(body: ReportQuery, request: Request,
                principal: Principal = Depends(get_current_principal)) -> dict:
    with api_errors():
        _, plan = report_plan(request.app.state.services, principal.user_id, body)
        return plan


@router.post("/query")
async def run_report(body: ReportQuery, request: Request,
                     principal: Principal = Depends(get_current_principal)) -> dict:
    with api_errors():
        return await query_report(request.app.state.services, principal.user_id, body, request)


# Dashboard queries accept saved tile IDs, not client-authored SQL or models.
import asyncio
from pydantic import Field
from schemii.schemoo.models import Contract
from schemii.common.api.errors import ApiProblem
from .dashboard_routes import dashboard_errors
from .tile_queries import tile_plan
from schemii.schemoo.service import execute_query


class TileRequest(Contract):
    expected_revision: int = Field(ge=1)
    selection: dict | None = None


def _dashboard(request, owner, dashboard_id, body):
    dashboard = request.app.state.services.dashboards.get(owner, dashboard_id)
    if dashboard.revision != body.expected_revision:
        raise ApiProblem(409, "dashboard_revision_conflict", "This dashboard changed. Reload it before running this tile.",
                         details={"currentRevision": dashboard.revision})
    return dashboard


@router.post("/dashboards/{dashboard_id}/tiles/{tile_id}/plan")
def plan_tile(dashboard_id: str, tile_id: str, body: TileRequest, request: Request,
              principal: Principal = Depends(get_current_principal)) -> dict:
    with dashboard_errors():
        dashboard = _dashboard(request, principal.user_id, dashboard_id, body)
        _, plan = tile_plan(request.app.state.services, principal.user_id, dashboard, tile_id,
                            selection=body.selection)
        return plan


@router.post("/dashboards/{dashboard_id}/tiles/{tile_id}/executions")
async def execute_tile(dashboard_id: str, tile_id: str, body: TileRequest, request: Request,
                       tasks: BackgroundTasks,
                       principal: Principal = Depends(get_current_principal)) -> dict:
    """Start one owned, retained read; consumers advance its shared result cursor."""
    with dashboard_errors():
        services = request.app.state.services
        dashboard = await asyncio.to_thread(_dashboard, request, principal.user_id, dashboard_id, body)
        model, plan = await asyncio.to_thread(tile_plan, services, principal.user_id, dashboard, tile_id,
                                              selection=body.selection, fresh=True)
        import secrets
        return await asyncio.to_thread(execute_query, services, principal.user_id, model,
                                       f"con_{secrets.token_hex(16)}", plan, tasks, row_page_size=plan["rowLimit"])


class DashboardExecutionRequest(Contract):
    expected_revision: int = Field(ge=1)


@router.post("/dashboards/{dashboard_id}/executions")
async def execute_dashboard(dashboard_id: str, body: DashboardExecutionRequest, request: Request,
                            tasks: BackgroundTasks,
                            principal: Principal = Depends(get_current_principal)) -> dict:
    """Keep a dashboard's named cursors in one retained database session.

    Authoring/parameter failures are reported per tile before admission. A
    database failure follows the shared transaction's execution error contract;
    this endpoint does not promise independent database transactions per tile.
    """
    from schemii.schemoo.service import document
    import secrets

    with dashboard_errors():
        services = request.app.state.services
        dashboard = await asyncio.to_thread(_dashboard, request, principal.user_id, dashboard_id, body)
        maximum = min(20, services.admin_config.console.maximum_statements_per_run)
        if len(dashboard.tiles) > maximum:
            raise ApiProblem(422, "dashboard_tile_execution_limit",
                             f"A dashboard can run at most {maximum} tiles in one retained execution.",
                             details={"maximumTiles": maximum})
        if services.console is None:
            raise ApiProblem(503, "console_unavailable", "Dashboard execution is unavailable.")
        tiles, errors, model = [], [], None
        for index, tile in enumerate(dashboard.tiles):
            try:
                model, plan = await asyncio.to_thread(tile_plan, services, principal.user_id,
                                                      dashboard, tile.id, fresh=index == 0)
            except ApiProblem as error:
                if error.status_code != 422:
                    raise
                errors.append({"tileId": tile.id, "message": error.message, "code": error.code})
                continue
            tiles.append({"tileId": tile.id, "statementIndex": len(tiles),
                          "plan": plan, "rowLimit": plan["rowLimit"]})
        if not tiles:
            return {"execution": None, "executionUrl": None, "tiles": [], "tileErrors": errors}
        reservation = asyncio.create_task(asyncio.to_thread(services.console.reserve_read_target,
            principal.user_id, connection_id=model.connection_id, database=model.database,
            namespace=model.namespace, console_id=f"con_{secrets.token_hex(16)}",
            statements=[tile["plan"]["sql"] for tile in tiles],
            row_page_size=max(tile["rowLimit"] for tile in tiles), protect_result=True))
        try:
            receipt = await asyncio.shield(reservation)
        except asyncio.CancelledError:
            # Admission runs in a thread and can finish after the HTTP task is
            # cancelled. Recover its receipt and release the protected lease.
            try:
                receipt = await asyncio.shield(reservation)
            except Exception:
                pass  # Admission failed before creating a retained execution.
            else:
                await asyncio.to_thread(services.console.cancel, principal.user_id, None, receipt.id)
            raise
        tasks.add_task(services.console.run, principal.user_id, receipt.id)
        return {"execution": document(receipt),
                "executionUrl": f"/api/v1/common/query-executions/{receipt.id}",
                "tiles": tiles, "tileErrors": errors}
