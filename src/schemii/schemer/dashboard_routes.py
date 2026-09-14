"""Save private dashboard definitions independently of current slicer values."""
from contextlib import contextmanager
from fastapi import APIRouter, Depends, Query, Request, Response
from schemii.common.api.errors import ApiProblem
from schemii.common.metadata.models import Principal, get_current_principal
from schemii.schemoo.models import ExploreState
from schemii.schemoo.derived import type_family
from schemii.schemoo.routes import api_errors
from schemii.schemoo.service import load_model, model_catalog, plan_query
from .dashboard_models import Dashboard, DashboardCreate, DashboardUpdate
from .dashboard_store import (DashboardNotFoundError, DashboardConflictError,
                              DashboardLimitError, DashboardStorageUnavailableError)

router = APIRouter(prefix="/api/v1/schemer/dashboards", tags=["schemer"])


@contextmanager
def dashboard_errors():
    with api_errors():
        try:
            yield
        except DashboardNotFoundError as error:
            raise ApiProblem(404, "dashboard_not_found", str(error)) from error
        except DashboardConflictError as error:
            raise ApiProblem(409, "dashboard_revision_conflict", str(error),
                             details={"currentRevision": error.current_revision}) from error
        except DashboardLimitError as error:
            raise ApiProblem(413, "dashboard_limit_reached", str(error)) from error
        except DashboardStorageUnavailableError as error:
            raise ApiProblem(503, "dashboard_storage_unavailable", str(error), retryable=True) from error
        except ValueError as error:
            raise ApiProblem(422, "invalid_dashboard", str(error)) from error


def validate_dashboard(services, owner, body):
    maximum = min(20, services.admin_config.console.maximum_statements_per_run)
    if len(body.tiles) > maximum:
        raise ApiProblem(422, "dashboard_tile_execution_limit",
                         f"A dashboard can contain at most {maximum} tiles in one retained execution.",
                         details={"maximumTiles": maximum})
    model = load_model(services, owner, body.model_id, body.model_revision)
    scopes = {scope.id: scope for scope in model.definition.scopes}
    invalid_optional = [scope_id for scope_id in body.optional_filters
                        if scope_id not in scopes or scopes[scope_id].requirement != "optional"]
    if invalid_optional:
        raise ValueError("Optional dashboard filters must be model scopes marked optional in Schemoo")
    invalid_current_selections = [scope_id for scope_id in body.selections
                                  if scope_id in scopes and not (
                                      scopes[scope_id].kind == "required" and scopes[scope_id].requirement == "required"
                                      or scope_id in body.optional_filters)]
    if invalid_current_selections:
        raise ValueError("Dashboard selections must belong to always-evaluated scopes or exposed optional filters")
    if any(selection.active and scope_id not in body.optional_filters
           for scope_id, selection in body.selections.items()):
        raise ValueError("Only exposed optional dashboard filters may be activated")
    if not body.tiles:
        return
    catalog = model_catalog(services, owner, model)
    # Saving is independent of runtime slicer values. The real query keeps every
    # model scope; this validation copy only checks authored fields and filters.
    definition = model.definition.model_copy(update={"scopes": []})
    nodes = {node.id: node for node in model.definition.nodes}
    tables = {table["name"]: table for table in catalog["tables"]}

    def numeric(node_id, column):
        node = nodes.get(node_id)
        if not node:
            return False
        if node.derivation:
            output = next((item for item in node.derivation.outputs if item.id == column), None)
            if output:
                if output.operation in {"count", "count_distinct", "add", "subtract", "multiply", "divide", "sum", "avg"}:
                    return True
                if output.operation == "list":
                    return False
                return numeric(output.nodeId or node.derivation.source, output.column)
            return numeric(node.derivation.source, column)
        details = next((c for c in tables[node.table]["columns"] if c["name"] == column), {})
        return type_family(details) == "numeric"

    for tile in body.tiles:
        if any(key not in scopes or scopes[key].kind == "required" or scopes[key].requirement == "optional"
               for key in tile.selections):
            raise ValueError("Tile selections may only override required source-conditional model filters")
        for fields in (tile.dimensions + tile.measures, tile.detail_fields):
            if fields:
                plan_query(catalog, definition, ExploreState(root=definition.root, fields=fields,
                    reportFilters=tile.report_filters, limit=tile.limit), model.catalog_fingerprint)
        for field in tile.measures:
            if tile.kind in {"bar", "line", "donut"} and field.aggregate not in {"count", "count_distinct"} and not numeric(field.table, field.column):
                raise ValueError("Charts require numeric measures")
            if field.aggregate in {"sum", "avg"} and not numeric(field.table, field.column):
                raise ValueError("SUM and AVG measures require numeric fields")
            if field.aggregate == "none":
                node = next((n for n in definition.nodes if n.id == field.table), None)
                if not node or not node.derivation or node.derivation.kind != "aggregate" or not any(
                    output.id == field.column for output in node.derivation.outputs):
                    raise ValueError("Raw measures require an aggregation; only model aggregate outputs may use none")


@router.get("")
def list_dashboards(request: Request, principal: Principal = Depends(get_current_principal)):
    with dashboard_errors():
        return {"dashboards": [d.model_dump(mode="json", by_alias=True) for d in
                               request.app.state.services.dashboards.list(principal.user_id)]}


@router.post("", response_model=Dashboard, status_code=201)
def create_dashboard(body: DashboardCreate, request: Request,
                     principal: Principal = Depends(get_current_principal)):
    with dashboard_errors():
        validate_dashboard(request.app.state.services, principal.user_id, body)
        return request.app.state.services.dashboards.create(principal.user_id, body)


@router.get("/{dashboard_id}", response_model=Dashboard)
def get_dashboard(dashboard_id: str, request: Request,
                  principal: Principal = Depends(get_current_principal)):
    with dashboard_errors():
        return request.app.state.services.dashboards.get(principal.user_id, dashboard_id)


@router.put("/{dashboard_id}", response_model=Dashboard)
def update_dashboard(dashboard_id: str, body: DashboardUpdate, request: Request,
                     principal: Principal = Depends(get_current_principal)):
    with dashboard_errors():
        services = request.app.state.services
        services.dashboards.get(principal.user_id, dashboard_id)
        validate_dashboard(services, principal.user_id, body)
        return services.dashboards.update(principal.user_id, dashboard_id, body)


@router.delete("/{dashboard_id}", status_code=204)
def delete_dashboard(dashboard_id: str, request: Request,
                     expected_revision: int = Query(ge=1, alias="expectedRevision"),
                     principal: Principal = Depends(get_current_principal)):
    with dashboard_errors():
        request.app.state.services.dashboards.delete(principal.user_id, dashboard_id, expected_revision)
        return Response(status_code=204)
