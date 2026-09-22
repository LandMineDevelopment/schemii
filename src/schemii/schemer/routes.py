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
        return await query_report(owned_report_services(request, principal.user_id, body.model_id), principal.user_id, body, request)


# Dashboard queries accept saved tile IDs, not client-authored SQL or models.
import asyncio
from pydantic import Field
from schemii.schemoo.models import Contract, ScopeSelection
from schemii.common.api.errors import ApiProblem
from .dashboard_routes import dashboard_errors
from .tile_queries import tile_plan
from .access import prepare_dashboard, owned_report_services


class TileRequest(Contract):
    expected_revision: int = Field(ge=1)
    selection: dict | None = None
    selections: dict[str, ScopeSelection] | None = Field(default=None, max_length=20)


def _dashboard(request, owner, dashboard_id, body, *, export=False):
    dashboard, services, permissions = prepare_dashboard(request, owner, dashboard_id,
        export=export, drill=getattr(body, "selection", None) is not None)
    if dashboard.revision != body.expected_revision:
        raise ApiProblem(409, "dashboard_revision_conflict", "This dashboard changed. Reload it before running this tile.",
                         details={"currentRevision": dashboard.revision})
    if getattr(body, "selections", None) is not None:
        dashboard = dashboard.model_copy(update={"selections": body.selections})
    return dashboard, services


@router.post("/dashboards/{dashboard_id}/tiles/{tile_id}/plan")
def plan_tile(dashboard_id: str, tile_id: str, body: TileRequest, request: Request,
              principal: Principal = Depends(get_current_principal)) -> dict:
    with dashboard_errors():
        dashboard, services = _dashboard(request, principal.user_id, dashboard_id, body)
        _, plan = tile_plan(services, principal.user_id, dashboard, tile_id,
                            selection=body.selection)
        return plan


@router.post("/dashboards/{dashboard_id}/tiles/{tile_id}/executions")
async def execute_tile(dashboard_id: str, tile_id: str, body: TileRequest, request: Request,
                       tasks: BackgroundTasks,
                       principal: Principal = Depends(get_current_principal)):
    """Compatibility URL; report executions now own their entire stream."""
    return await stream_tile(dashboard_id, tile_id, body, request, principal)


class DashboardExecutionRequest(Contract):
    expected_revision: int = Field(ge=1)
    selections: dict[str, ScopeSelection] | None = Field(default=None, max_length=20)


@router.post("/dashboards/{dashboard_id}/executions")
async def execute_dashboard(dashboard_id: str, body: DashboardExecutionRequest, request: Request,
                            tasks: BackgroundTasks,
                            principal: Principal = Depends(get_current_principal)):
    """Compatibility URL for the bounded dashboard stream."""
    return await stream_dashboard(dashboard_id, body, request, principal)


@router.post("/dashboards/{dashboard_id}/tiles/{tile_id}/executions/stream")
async def stream_tile(dashboard_id: str, tile_id: str, body: TileRequest, request: Request,
                      principal: Principal = Depends(get_current_principal)):
    from .streaming import response
    with dashboard_errors():
        dashboard, services = await asyncio.to_thread(_dashboard, request, principal.user_id, dashboard_id, body)
        model, plan = await asyncio.to_thread(tile_plan, services, principal.user_id, dashboard,
                                              tile_id, selection=body.selection, fresh=True)
        return response(services, principal.user_id, model, [{"tileId": tile_id, "plan": plan}])


@router.post("/dashboards/{dashboard_id}/executions/stream")
async def stream_dashboard(dashboard_id: str, body: DashboardExecutionRequest, request: Request,
                           principal: Principal = Depends(get_current_principal)):
    from .streaming import response
    with dashboard_errors():
        dashboard, services = await asyncio.to_thread(_dashboard, request, principal.user_id, dashboard_id, body)
        maximum = min(20, services.admin_config.console.maximum_statements_per_run)
        if len(dashboard.tiles) > maximum:
            raise ApiProblem(422, "dashboard_tile_execution_limit",
                             f"A dashboard can run at most {maximum} tiles together.")
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
            tiles.append({"tileId": tile.id, "plan": plan})
        return response(services, principal.user_id, model, tiles, errors)


@router.post("/query/stream")
async def stream_report(body: ReportQuery, request: Request,
                        principal: Principal = Depends(get_current_principal)):
    from .streaming import response
    with api_errors():
        services = owned_report_services(request, principal.user_id, body.model_id)
        model, plan = await asyncio.to_thread(report_plan, services, principal.user_id, body,
                                              fresh=True, bounded=False)
        return response(services, principal.user_id, model, [{"tileId": "report", "plan": plan}])


@router.post("/dashboards/{dashboard_id}/tiles/{tile_id}/export")
async def export_tile(dashboard_id: str, tile_id: str, request: Request,
                      principal: Principal = Depends(get_current_principal)):
    """Native browser form downloads avoid constructing an unbounded Blob."""
    from urllib.parse import parse_qs
    from pydantic import ValidationError
    from .streaming import response
    with dashboard_errors():
        try:
            if request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
                payload = parse_qs((await request.body()).decode())["payload"][0]
                body = TileRequest.model_validate_json(payload)
            else:
                body = TileRequest.model_validate(await request.json())
        except (ValidationError, ValueError, KeyError, IndexError) as error:
            raise ApiProblem(422, "invalid_export_request", "Supply the dashboard revision and optional selection.") from error
        dashboard, services = await asyncio.to_thread(_dashboard, request, principal.user_id, dashboard_id, body, export=True)
        model, plan = await asyncio.to_thread(tile_plan, services, principal.user_id, dashboard,
                                              tile_id, selection=body.selection, fresh=True)
        return response(services, principal.user_id, model, [{"tileId": tile_id, "plan": plan}], export=True)


@router.post("/query/export")
async def export_report(body: ReportQuery, request: Request,
                        principal: Principal = Depends(get_current_principal)):
    from .streaming import response
    with api_errors():
        services = owned_report_services(request, principal.user_id, body.model_id)
        model, plan = await asyncio.to_thread(report_plan, services, principal.user_id, body,
                                              fresh=True, bounded=False)
        return response(services, principal.user_id, model,
                        [{"tileId": "report", "plan": plan}], export=True)


@router.get("/dashboards/{dashboard_id}/context")
def dashboard_context(dashboard_id: str, request: Request,
                      principal: Principal = Depends(get_current_principal)):
    from schemii.schemoo.service import load_model, model_catalog, document
    with dashboard_errors():
        dashboard, services, permissions = prepare_dashboard(request, principal.user_id, dashboard_id)
        model = load_model(services, principal.user_id, dashboard.model_id, dashboard.model_revision)
        catalog = model_catalog(services, principal.user_id, model, fresh=True)
        return {"model": document(model), "catalog": catalog, "permissions": permissions}


from schemii.schemoo.routes import ParameterValuesRequest


@router.post("/dashboards/{dashboard_id}/parameter-values")
async def dashboard_parameter_values(dashboard_id: str, body: ParameterValuesRequest, request: Request,
                                     principal: Principal = Depends(get_current_principal)):
    from schemii.schemoo.service import load_model, model_catalog, domain_query
    from .service import execute_prepared
    with dashboard_errors():
        dashboard, services, _ = await asyncio.to_thread(prepare_dashboard, request, principal.user_id, dashboard_id)
        model = await asyncio.to_thread(load_model, services, principal.user_id, dashboard.model_id, body.expected_revision)
        scope = next((item for item in model.definition.scopes if item.id == body.scope_id), None)
        if scope is None or (scope.requirement == "optional" and scope.id not in dashboard.optional_filters):
            raise ApiProblem(403, "report_parameter_forbidden", "This filter is not available on the dashboard.")
        catalog = await asyncio.to_thread(model_catalog, services, principal.user_id, model, fresh=True)
        plan = domain_query(catalog, model, body)
        return await execute_prepared(services, principal.user_id, model, plan, 100, request)
