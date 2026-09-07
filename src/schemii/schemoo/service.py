"""Saved semantic rules are authoritative; exploration supplies only query inputs."""

from schemii.common.api.errors import ApiProblem
from .catalog import source_issues
from .models import ModelDefinition, ExploreState
from .store import ModelConflictError
from .prototype import analyze_model, compile_preview
from .domain import domain_table


def document(value):
    return value.model_dump(mode="json", by_alias=True, exclude_none=True)


def load_model(services, owner, model_id, expected_revision=None):
    model = services.models.get(owner, model_id)
    if expected_revision is not None and model.revision != expected_revision:
        raise ModelConflictError(model.revision)
    return model


def model_catalog(services, owner, model, *, fresh=False):
    catalog = services.model_catalogs.get(services, owner, model.connection_id, model.namespace, fresh=fresh)
    if catalog["database"] != model.database:
        raise ApiProblem(409, "model_source_changed", "The model's connection now targets a different database.")
    return catalog


def plan_query(catalog, definition, explore, fingerprint=""):
    rules, query = document(definition), document(explore)
    if not query.get("root"):
        query["root"] = rules.get("root", "")
    exposed = rules.get("exposedFields")
    if exposed is not None:
        allowed = {(f["table"], f["column"]) for f in exposed}
        requested = query["fields"] + [c for group in query["reportFilters"] for c in group["conditions"]]
        hidden = list(dict.fromkeys((f["table"], f["column"]) for f in requested
                                    if (f["table"], f["column"]) not in allowed))
        if hidden:
            labels = ", ".join(f"{table}.{column}" for table, column in hidden)
            raise ApiProblem(422, "field_not_exposed",
                             f"These preview fields or report filters are not exposed by this model: {labels}. "
                             "Expose them in the model or remove them from the preview.",
                             details={"fields": [{"table": table, "column": column} for table, column in hidden]})
    issues = source_issues(catalog, rules)
    if issues:
        raise ApiProblem(422, "model_source_drift", "Source references need repair. Your saved model has not been replaced.", details={"issues": issues})
    diagnostics = {}
    try:
        combined = {**rules, **query}
        diagnostics = analyze_model(catalog, combined)
        result = compile_preview(catalog, combined)
    except ValueError as error:
        raise ApiProblem(422, "invalid_model_query", str(error),
                         details={**diagnostics, **getattr(error, "details", {})}) from error
    if fingerprint and fingerprint != catalog["fingerprint"]:
        result["warnings"].append("The source schema changed since this model was saved. Referenced objects were revalidated; the model was not replaced.")
    return {**diagnostics, **result, "catalogFingerprint": catalog["fingerprint"]}


def execute_query(services, owner, model, console_id, plan, tasks):
    if services.console is None:
        raise ApiProblem(503, "console_unavailable", "Read preview execution is unavailable.")
    receipt = services.console.reserve_read_target(
        owner, connection_id=model.connection_id, database=model.database,
        namespace=model.namespace, console_id=console_id, statements=[plan["sql"]])
    tasks.add_task(services.console.run, owner, receipt.id)
    return {"plan": plan, "execution": document(receipt),
            "executionUrl": f"/api/v1/common/query-executions/{receipt.id}"}


def domain_query(catalog, model, body):
    """Resolve lookup sources from saved author rules, never client-supplied tables."""
    definition = document(model.definition)
    scope = next((s for s in definition["scopes"] if s["id"] == body.scope_id), None)
    alternative = next((a for a in (scope or {}).get("alternatives", []) if a["id"] == body.alternative_id), None)
    parameter = next((p for p in (alternative or {}).get("inputs", []) if p["id"] == body.parameter_id), None)
    if parameter is None:
        raise ApiProblem(422, "unknown_model_parameter", "This parameter is not part of the saved model. Save your model changes first.")
    domain = parameter.get("domain")
    if domain:
        try:
            table = domain_table(domain, definition["nodes"])
        except ValueError as error:
            raise ApiProblem(422, "parameter_unbound", str(error)) from error
        column = domain["column"]
        label = domain.get("labelColumn") or column
    else:
        condition = next((c for c in alternative["conditions"] if c.get("parameterId") == body.parameter_id), None)
        node = next((n for n in definition["nodes"] if n["id"] == (condition or {}).get("table")), None)
        if not node or not condition.get("column"):
            raise ApiProblem(422, "parameter_unbound", "Bind this parameter to a source column before browsing values.")
        table, column = node["table"], condition["column"]
        fk = next((r for r in catalog["relationships"] if (r["sourceTable"], r["sourceColumn"]) == (table, column)), None)
        if parameter["type"] != "source" and fk:
            table, column = fk["targetTable"], fk["targetColumn"]
        columns = next((t["columns"] for t in catalog["tables"] if t["name"] == table), [])
        label = "name" if parameter["type"] != "source" and any(c["name"] == "name" for c in columns) else column
    return domain_values_plan(catalog, table, column, label, body.search)


def domain_values_plan(catalog, table, column, label, search=""):
    """Shared bounded lookup compiler for authoring and saved report inputs."""
    if not table or not column:
        raise ApiProblem(422, "parameter_unbound", "Choose a domain value column in the model parameter setup before browsing values.")
    fields = [{"table": table, "column": c} for c in dict.fromkeys([column, label])]
    fields.append({"table": table, "column": column, "aggregate": "count"})
    rules = ModelDefinition(root=table, nodes=[{"id": table, "table": table, "label": table}])
    explore = ExploreState(root=table, fields=fields, reportFilters=[{
        "id": "domain-search", "mode": "rows", "conditions": [
            {"table": table, "column": label, "operator": "contains", "value": search}]}] if search else [])
    return plan_query(catalog, rules, explore)
