"""Owner-scoped semantic modeling API, composed into the unified server."""

from contextlib import contextmanager
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from pydantic import Field
from schemii.common.api.errors import ApiProblem
from schemii.common.api.postgres import postgres_api_problem
from schemii.common.connections.store import ConnectionNotFoundError
from schemii.common.metadata.models import Principal, get_current_principal
from schemii.common.metadata.limit_events import LimitEventNotice
from schemii.common.postgres.errors import PostgresGatewayError
from schemii.common.query_executions.errors import ConsoleServiceError
from .catalog import initial_positions
from .models import (Contract, ModelCreate, ModelUpdate, LayoutUpdate, ExploreUpdate,
                     ModelDefinition, ExploreState, SchemooModel, ModelSummary, DomainValues)
from .store import (ModelNotFoundError, ModelConflictError, ModelStorageUnavailableError, ModelLimitError, ModelDocumentLimitError)
from .service import load_model, model_catalog, plan_query, execute_query, domain_query, domain_values_plan, document
from .domain import domain_table

router = APIRouter(prefix="/api/v1/schemoo", tags=["schemoo"])


class CreateRequest(ModelCreate):
    database: str = Field(default="", max_length=63)


class ModelList(Contract):
    models: list[ModelSummary]


class PlanRequest(Contract):
    expected_revision: int = Field(ge=1)
    explore: ExploreState


class ValidateRequest(PlanRequest):
    definition: ModelDefinition | None = None


class ExecutionRequest(PlanRequest):
    console_id: str = Field(pattern=r"^con_[0-9a-f]{32}$")


class ParameterValuesRequest(Contract):
    expected_revision: int = Field(ge=1)
    scope_id: str = Field(min_length=1, max_length=200)
    alternative_id: str = Field(min_length=1, max_length=200)
    parameter_id: str = Field(min_length=1, max_length=200)
    search: str = Field(default="", max_length=500)
    console_id: str = Field(pattern=r"^con_[0-9a-f]{32}$")


class AuthorDomainRequest(Contract):
    expected_revision: int = Field(ge=1)
    definition: ModelDefinition
    domain: DomainValues
    search: str = Field(default="", max_length=500)
    console_id: str = Field(pattern=r"^con_[0-9a-f]{32}$")


@contextmanager
def api_errors():
    try:
        yield
    except (ModelNotFoundError, ConnectionNotFoundError) as error:
        raise ApiProblem(404, "model_source_not_found", str(error)) from error
    except ModelConflictError as error:
        raise ApiProblem(409, "model_revision_conflict", str(error), details={"currentRevision": error.current_revision}) from error
    except ModelLimitError as error:
        raise ApiProblem(409, "model_limit_reached", str(error), limit_event=LimitEventNotice(
            resource="semantic_models", limit_name="resources.maximum_models_per_user", configured_limit=error.limit)) from error
    except ModelDocumentLimitError as error:
        raise ApiProblem(413, "model_document_limit_reached", str(error), limit_event=LimitEventNotice(
            resource="semantic_model_document", limit_name="resources.maximum_model_document_bytes", configured_limit=error.limit)) from error
    except ModelStorageUnavailableError as error:
        raise ApiProblem(503, "model_storage_unavailable", str(error), retryable=True) from error
    except PostgresGatewayError as error:
        raise postgres_api_problem(error) from error
    except ConsoleServiceError as error:
        raise ApiProblem(error.status, error.code, str(error), details=error.details,
                         retryable=error.retryable, limit_event=error.limit_event) from error


@router.get("/catalog")
def get_catalog(request: Request, connection_id: str = Query(pattern=r"^pg_[0-9a-f]{32}$"),
                namespace: str = Query(min_length=1, max_length=63),
                principal: Principal = Depends(get_current_principal)) -> dict:
    with api_errors():
        services = request.app.state.services
        return initial_positions(services, principal.user_id, services.model_catalogs.get(
            services, principal.user_id, connection_id, namespace, fresh=True))


@router.get("/models", response_model=ModelList)
def list_models(request: Request, principal: Principal = Depends(get_current_principal)):
    with api_errors():
        return ModelList(models=request.app.state.services.models.list(principal.user_id))


@router.post("/models", response_model=SchemooModel, status_code=201)
def create_model(body: CreateRequest, request: Request, principal: Principal = Depends(get_current_principal)):
    with api_errors():
        services = request.app.state.services
        catalog = services.model_catalogs.get(services, principal.user_id, body.connection_id, body.namespace, fresh=True)
        record = ModelCreate.model_validate({**body.model_dump(), "database": catalog["database"],
                                             "catalog_fingerprint": catalog["fingerprint"]})
        return services.models.create(principal.user_id, record)


@router.get("/models/{model_id}", response_model=SchemooModel)
def get_model(model_id: str, request: Request, principal: Principal = Depends(get_current_principal)):
    with api_errors():
        return load_model(request.app.state.services, principal.user_id, model_id)


@router.put("/models/{model_id}", response_model=SchemooModel)
def update_model(model_id: str, body: ModelUpdate, request: Request, principal: Principal = Depends(get_current_principal)):
    with api_errors():
        return request.app.state.services.models.update(principal.user_id, model_id, body)


@router.put("/models/{model_id}/layout", response_model=SchemooModel)
def update_layout(model_id: str, body: LayoutUpdate, request: Request, principal: Principal = Depends(get_current_principal)):
    with api_errors():
        return request.app.state.services.models.update_layout(principal.user_id, model_id, body)


@router.put("/models/{model_id}/explore", response_model=SchemooModel)
def update_explore(model_id: str, body: ExploreUpdate, request: Request, principal: Principal = Depends(get_current_principal)):
    with api_errors():
        return request.app.state.services.models.update_explore(principal.user_id, model_id, body)


@router.delete("/models/{model_id}", status_code=204)
def delete_model(model_id: str, request: Request, expected_revision: int = Query(ge=1),
                 principal: Principal = Depends(get_current_principal)):
    with api_errors():
        request.app.state.services.models.delete(principal.user_id, model_id, expected_revision)
        return Response(status_code=204)


@router.post("/models/{model_id}/validate")
def validate_model(model_id: str, body: ValidateRequest, request: Request, principal: Principal = Depends(get_current_principal)) -> dict:
    with api_errors():
        services = request.app.state.services
        model = load_model(services, principal.user_id, model_id, body.expected_revision)
        return plan_query(model_catalog(services, principal.user_id, model), body.definition or model.definition,
                          body.explore, model.catalog_fingerprint)


@router.post("/models/{model_id}/plan")
def plan_model(model_id: str, body: PlanRequest, request: Request, principal: Principal = Depends(get_current_principal)) -> dict:
    with api_errors():
        services = request.app.state.services
        model = load_model(services, principal.user_id, model_id, body.expected_revision)
        return plan_query(model_catalog(services, principal.user_id, model), model.definition, body.explore, model.catalog_fingerprint)


@router.post("/models/{model_id}/executions", status_code=201)
def execute_model(model_id: str, body: ExecutionRequest, request: Request, background_tasks: BackgroundTasks,
                  principal: Principal = Depends(get_current_principal)) -> dict:
    with api_errors():
        services = request.app.state.services
        model = load_model(services, principal.user_id, model_id, body.expected_revision)
        plan = plan_query(model_catalog(services, principal.user_id, model, fresh=True), model.definition, body.explore, model.catalog_fingerprint)
        return execute_query(services, principal.user_id, model, body.console_id, plan, background_tasks)


@router.post("/models/{model_id}/parameter-values", status_code=201)
def parameter_values(model_id: str, body: ParameterValuesRequest, request: Request, background_tasks: BackgroundTasks,
                     principal: Principal = Depends(get_current_principal)) -> dict:
    with api_errors():
        services = request.app.state.services
        model = load_model(services, principal.user_id, model_id, body.expected_revision)
        catalog = model_catalog(services, principal.user_id, model, fresh=True)
        return execute_query(services, principal.user_id, model, body.console_id, domain_query(catalog, model, body), background_tasks)


@router.post("/models/{model_id}/domain-values", status_code=201)
def author_domain_values(model_id: str, body: AuthorDomainRequest, request: Request, background_tasks: BackgroundTasks,
                         principal: Principal = Depends(get_current_principal)) -> dict:
    """Browse draft bindings using the owned model's live source, without saving the draft."""
    with api_errors():
        services = request.app.state.services
        model = load_model(services, principal.user_id, model_id, body.expected_revision)
        catalog = model_catalog(services, principal.user_id, model, fresh=True)
        domain = document(body.domain)
        try:
            table = domain_table(domain, document(body.definition)["nodes"])
        except ValueError as error:
            raise ApiProblem(422, "parameter_unbound", str(error)) from error
        plan = domain_values_plan(catalog, table, domain["column"], domain.get("labelColumn") or domain["column"], body.search)
        return execute_query(services, principal.user_id, model, body.console_id, plan, background_tasks)
