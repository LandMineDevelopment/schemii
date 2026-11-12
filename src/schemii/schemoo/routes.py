"""Owner-scoped semantic modeling API, composed into the unified server."""

from contextlib import contextmanager
from typing import Annotated, Literal
from types import SimpleNamespace
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from pydantic import Field, StrictBool, ValidationError
from schemii.common.api.errors import ApiProblem
from schemii.common.api.postgres import postgres_api_problem
from schemii.common.connections.store import ConnectionNotFoundError
from schemii.common.metadata.models import Principal, get_current_principal
from schemii.common.metadata.limit_events import LimitEventNotice
from schemii.common.postgres.errors import PostgresGatewayError
from schemii.common.query_executions.errors import ConsoleServiceError
from .catalog import initial_positions, model_source_contract
from .models import (Contract, ModelCreate, ModelDuplicate, ModelUpdate, ModelPatch, LayoutUpdate, ExploreUpdate,
                     ModelDefinition, ExploreState, SchemooModel, ModelSummary, DomainValues,
                     PreviewCreate, PreviewUpdate, SavedPreview)
from .store import (ModelInUseError, ModelNotFoundError, ModelConflictError, ModelStorageUnavailableError, ModelLimitError, ModelDocumentLimitError, PreviewNameConflictError)
from .service import load_model, model_catalog, plan_query, execute_query, domain_query, domain_values_plan, document, patch_model_definition
from .domain import domain_source, definition_namespaces
from .prototype import analyze_model

router = APIRouter(prefix="/api/v1/schemoo", tags=["schemoo"])


def _services(request: Request):
    services = request.app.state.services
    scoped = SimpleNamespace(**vars(services))
    if hasattr(services.connections, "for_product"):
        scoped.connections = services.connections.for_product("schemoo")
    return scoped


class CreateRequest(ModelCreate):
    database: str = Field(default="", max_length=63)
    catalog_namespaces: list[Annotated[str, Field(min_length=1, max_length=63)]] | None = Field(
        default=None, min_length=1, max_length=16,
        description="The schema set returned with catalogFingerprint; creation review only, not saved model scope.")


class ModelList(Contract):
    models: list[ModelSummary]


class PreviewList(Contract):
    previews: list[SavedPreview]


class PlanRequest(Contract):
    expected_revision: int = Field(ge=1)
    explore: ExploreState


class ValidateRequest(PlanRequest):
    definition: ModelDefinition | None = None


class ExecutionRequest(PlanRequest):
    console_id: str = Field(pattern=r"^con_[0-9a-f]{32}$")


class ExplainRequest(ExecutionRequest):
    analyze: StrictBool = False


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
    except ModelInUseError as error:
        raise ApiProblem(409, "model_in_use", str(error), details={"dashboards": error.dashboards}) from error
    except ModelConflictError as error:
        raise ApiProblem(409, "model_revision_conflict", str(error), details={"currentRevision": error.current_revision}) from error
    except PreviewNameConflictError as error:
        raise ApiProblem(409, "preview_name_conflict", str(error)) from error
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


def _validate_connections(catalog, definition):
    try:
        analyze_model(catalog, document(definition))
    except ValueError as error:
        raise ApiProblem(422, "invalid_model_connection", str(error)) from error


def _scoped_definition(catalog, definition, previous=None, *, creating=False):
    rules = document(definition)
    # Assistants may intentionally create an empty draft and fill it through
    # later atomic edits. Its baseline is empty, never the complete catalog.
    if creating and (rules["nodes"] or rules.get("root")) and not any(
            node["id"] == rules.get("root") and not node.get("derivation") for node in rules["nodes"]):
        raise ApiProblem(422, "invalid_model_source", "Choose an accessible starting source for this model.")
    try:
        # Validate physical bindings and calculations, while keeping draft-save
        # semantics for incomplete filters and enabled cycles.
        analyze_model(catalog, rules)
        contract = model_source_contract(catalog, rules, document(previous) if previous is not None else None)
        return ModelDefinition.model_validate({**rules, "sourceContract": contract})
    except ValidationError as error:
        raise ApiProblem(422, "model_source_limit", "Selected sources and their required keys, joins, and filter dependencies exceed the model's source-contract limits. Choose a smaller subset.") from error
    except ValueError as error:
        raise ApiProblem(422, "invalid_model_source", str(error)) from error


@router.get("/catalog")
def get_catalog(request: Request, connection_id: str = Query(pattern=r"^pg_[0-9a-f]{32}$"),
                namespace: str = Query(min_length=1, max_length=63),
                namespaces: list[Annotated[str, Field(min_length=1, max_length=63)]] | None = Query(default=None, max_length=16),
                principal: Principal = Depends(get_current_principal)) -> dict:
    with api_errors():
        services = _services(request)
        if len(set([namespace, *(namespaces or [])])) > 16:
            raise ApiProblem(422, "model_source_limit", "Choose at most 16 source schemas for one model.")
        return initial_positions(services, principal.user_id, services.model_catalogs.get(
            services, principal.user_id, connection_id, namespace, fresh=True, **({"namespaces": namespaces} if namespaces else {})))


@router.get("/models", response_model=ModelList)
def list_models(request: Request, principal: Principal = Depends(get_current_principal)):
    with api_errors():
        return ModelList(models=request.app.state.services.models.list(principal.user_id))


@router.post("/models", response_model=SchemooModel, status_code=201)
def create_model(body: CreateRequest, request: Request, principal: Principal = Depends(get_current_principal)):
    with api_errors():
        services = _services(request)
        try:
            namespaces = definition_namespaces(body.namespace, document(body.definition))
            cohort = sorted(set([body.namespace, *(body.catalog_namespaces or namespaces)]))
            if len(cohort) > 16:
                raise ValueError("Choose at most 16 source schemas for one model.")
        except ValueError as error:
            raise ApiProblem(422, "model_source_limit", str(error)) from error
        if not set(namespaces).issubset(cohort):
            raise ApiProblem(409, "model_source_changed", "The model selects a schema outside the reviewed source catalog. Reload sources and review your selection before creating the model.")
        catalog = services.model_catalogs.get(services, principal.user_id, body.connection_id, body.namespace, fresh=True,
                                             **({"namespaces": cohort} if cohort != [body.namespace] else {}))
        profile = services.connections.get(principal.user_id, body.connection_id)
        if body.catalog_fingerprint and body.catalog_fingerprint != catalog["fingerprint"]:
            raise ApiProblem(409, "model_source_changed", "The source schema changed while you were choosing sources. Reload sources and review your selection before creating the model.")
        if any(edge.kind == "logical" for edge in body.definition.edges):
            _validate_connections(catalog, body.definition)
        definition = _scoped_definition(catalog, body.definition, creating=True)
        record = ModelCreate.model_validate({**body.model_dump(exclude={"catalog_namespaces"}), "definition": definition,
                                             "database": catalog["database"],
                                             "catalog_fingerprint": catalog["fingerprint"]})
        return services.models.create(principal.user_id, record,
                                      connection_owner_id=getattr(profile, "owner_id", None) or principal.user_id)


@router.get("/models/{model_id}", response_model=SchemooModel)
def get_model(model_id: str, request: Request, principal: Principal = Depends(get_current_principal)):
    with api_errors():
        return load_model(_services(request), principal.user_id, model_id)


@router.post("/models/{model_id}/duplicate", response_model=SchemooModel, status_code=201)
def duplicate_model(model_id: str, body: ModelDuplicate, request: Request,
                    principal: Principal = Depends(get_current_principal)):
    with api_errors():
        services = _services(request)
        source = load_model(services, principal.user_id, model_id)
        connection = services.connections.get(principal.user_id, source.connection_id)
        if connection.database != source.database:
            raise ApiProblem(409, "model_source_changed", "The model's connection now targets a different database.")
        return services.models.duplicate(principal.user_id, model_id, body)


@router.put("/models/{model_id}", response_model=SchemooModel)
def update_model(model_id: str, body: ModelUpdate, request: Request, principal: Principal = Depends(get_current_principal)):
    with api_errors():
        services = _services(request)
        current = load_model(services, principal.user_id, model_id, body.expected_revision)
        catalog = model_catalog(services, principal.user_id, current, fresh=True, definition=body.definition)
        if any(edge.kind == "logical" for edge in body.definition.edges):
            _validate_connections(catalog, body.definition)
        body = body.model_copy(update={"definition": _scoped_definition(catalog, body.definition, current.definition),
                                       "catalog_fingerprint": catalog["fingerprint"]})
        return services.models.update(principal.user_id, model_id, body)


@router.put("/models/{model_id}/layout", response_model=SchemooModel)
def update_layout(model_id: str, body: LayoutUpdate, request: Request, principal: Principal = Depends(get_current_principal)):
    with api_errors():
        return request.app.state.services.models.update_layout(principal.user_id, model_id, body)


@router.patch("/models/{model_id}", response_model=SchemooModel)
def patch_model(model_id: str, body: ModelPatch, request: Request, principal: Principal = Depends(get_current_principal)):
    with api_errors():
        return patch_model_definition(_services(request), principal.user_id, model_id, body)


@router.put("/models/{model_id}/explore", response_model=SchemooModel)
def update_explore(model_id: str, body: ExploreUpdate, request: Request, principal: Principal = Depends(get_current_principal)):
    with api_errors():
        return request.app.state.services.models.update_explore(principal.user_id, model_id, body)


@router.get("/models/{model_id}/dependencies")
def model_dependencies(model_id: str, request: Request,
                       principal: Principal = Depends(get_current_principal)):
    with api_errors():
        return {"dashboards": request.app.state.services.models.dashboard_dependencies(principal.user_id, model_id)}


@router.delete("/models/{model_id}", status_code=204)
def delete_model(model_id: str, request: Request, expected_revision: int = Query(ge=1),
                 expected_layout_revision: int | None = Query(default=None, ge=1),
                 expected_explore_revision: int | None = Query(default=None, ge=1),
                 require_no_previews: Literal["true", "false"] = Query(default="false"),
                 principal: Principal = Depends(get_current_principal)):
    with api_errors():
        request.app.state.services.models.delete(principal.user_id, model_id, expected_revision,
            expected_layout_revision=expected_layout_revision, expected_explore_revision=expected_explore_revision,
            require_no_previews=require_no_previews == "true")
        return Response(status_code=204)


@router.get("/models/{model_id}/previews", response_model=PreviewList)
def list_previews(model_id: str, request: Request, principal: Principal = Depends(get_current_principal)):
    with api_errors():
        return PreviewList(previews=request.app.state.services.models.list_previews(principal.user_id, model_id))


@router.post("/models/{model_id}/previews", response_model=SavedPreview, status_code=201)
def create_preview(model_id: str, body: PreviewCreate, request: Request, principal: Principal = Depends(get_current_principal)):
    with api_errors():
        return request.app.state.services.models.create_preview(principal.user_id, model_id, body)


@router.put("/models/{model_id}/previews/{preview_id}", response_model=SavedPreview)
def update_preview(model_id: str, preview_id: str, body: PreviewUpdate, request: Request,
                   principal: Principal = Depends(get_current_principal)):
    with api_errors():
        return request.app.state.services.models.update_preview(principal.user_id, model_id, preview_id, body)


@router.delete("/models/{model_id}/previews/{preview_id}", status_code=204)
def delete_preview(model_id: str, preview_id: str, request: Request, expected_revision: int = Query(ge=1),
                   principal: Principal = Depends(get_current_principal)):
    with api_errors():
        request.app.state.services.models.delete_preview(principal.user_id, model_id, preview_id, expected_revision)
        return Response(status_code=204)


@router.post("/models/{model_id}/validate")
def validate_model(model_id: str, body: ValidateRequest, request: Request, principal: Principal = Depends(get_current_principal)) -> dict:
    with api_errors():
        services = _services(request)
        model = load_model(services, principal.user_id, model_id, body.expected_revision)
        definition = body.definition or model.definition
        return plan_query(model_catalog(services, principal.user_id, model, fresh=True, definition=definition), definition,
                          body.explore, model.catalog_fingerprint)


@router.post("/models/{model_id}/plan")
def plan_model(model_id: str, body: PlanRequest, request: Request, principal: Principal = Depends(get_current_principal)) -> dict:
    with api_errors():
        services = _services(request)
        model = load_model(services, principal.user_id, model_id, body.expected_revision)
        return plan_query(model_catalog(services, principal.user_id, model), model.definition, body.explore, model.catalog_fingerprint)


@router.post("/models/{model_id}/executions", status_code=201)
def execute_model(model_id: str, body: ExecutionRequest, request: Request, background_tasks: BackgroundTasks,
                  principal: Principal = Depends(get_current_principal)) -> dict:
    with api_errors():
        services = _services(request)
        model = load_model(services, principal.user_id, model_id, body.expected_revision)
        plan = plan_query(model_catalog(services, principal.user_id, model, fresh=True), model.definition, body.explore, model.catalog_fingerprint)
        return execute_query(services, principal.user_id, model, body.console_id, plan, background_tasks)


@router.post("/models/{model_id}/explain", status_code=201)
def explain_model(model_id: str, body: ExplainRequest, request: Request, background_tasks: BackgroundTasks,
                  principal: Principal = Depends(get_current_principal)) -> dict:
    """Explain an owned saved model on a fresh read-only snapshot."""
    from schemii.common.postgres.query_plans import build_explain_sql
    with api_errors():
        services = _services(request)
        model = load_model(services, principal.user_id, model_id, body.expected_revision)
        plan = plan_query(model_catalog(services, principal.user_id, model, fresh=True), model.definition, body.explore, model.catalog_fingerprint)
        response = execute_query(services, principal.user_id, model, body.console_id,
                                 {**plan, "sql": build_explain_sql(plan["sql"], body.analyze)}, background_tasks)
        response["plan"] = plan
        return response


@router.post("/models/{model_id}/parameter-values", status_code=201)
def parameter_values(model_id: str, body: ParameterValuesRequest, request: Request, background_tasks: BackgroundTasks,
                     principal: Principal = Depends(get_current_principal)) -> dict:
    with api_errors():
        services = _services(request)
        model = load_model(services, principal.user_id, model_id, body.expected_revision)
        catalog = model_catalog(services, principal.user_id, model, fresh=True)
        return execute_query(services, principal.user_id, model, body.console_id, domain_query(catalog, model, body), background_tasks)


@router.post("/models/{model_id}/domain-values", status_code=201)
def author_domain_values(model_id: str, body: AuthorDomainRequest, request: Request, background_tasks: BackgroundTasks,
                         principal: Principal = Depends(get_current_principal)) -> dict:
    """Browse draft bindings using the owned model's live source, without saving the draft."""
    with api_errors():
        services = _services(request)
        model = load_model(services, principal.user_id, model_id, body.expected_revision)
        catalog = model_catalog(services, principal.user_id, model, fresh=True, definition=body.definition)
        domain = document(body.domain)
        try:
            source = domain_source(domain, document(body.definition)["nodes"])
        except ValueError as error:
            raise ApiProblem(422, "parameter_unbound", str(error)) from error
        plan = domain_values_plan(catalog, source.get("table", ""), domain["column"], domain.get("labelColumn") or domain["column"], body.search, namespace=source.get("namespace"))
        return execute_query(services, principal.user_id, model, body.console_id, plan, background_tasks)
