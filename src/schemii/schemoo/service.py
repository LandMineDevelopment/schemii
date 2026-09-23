"""Saved semantic rules are authoritative; exploration supplies only query inputs."""

from schemii.common.api.errors import ApiProblem
from pydantic import ValidationError
from sqlglot import exp, parse_one
from .catalog import source_issues
from .models import ModelDefinition, ModelUpdate, ExploreState
from .store import ModelConflictError
from .prototype import analyze_model, compile_preview, validate_definition
from .domain import domain_table


def document(value):
    return value.model_dump(mode="json", by_alias=True, exclude_none=True)


def load_model(services, owner, model_id, expected_revision=None):
    model = services.models.get(owner, model_id)
    profile = services.connections.get(owner, model.connection_id)
    if (getattr(profile, "owner_id", None) or owner) != (model.connection_owner_id or owner):
        raise ApiProblem(409, "model_source_changed", "The model's database identity changed. Reload its source.")
    if expected_revision is not None and model.revision != expected_revision:
        raise ModelConflictError(model.revision)
    return model


def model_catalog(services, owner, model, *, fresh=False):
    catalog = services.model_catalogs.get(services, owner, model.connection_id, model.namespace, fresh=fresh)
    if catalog["database"] != model.database:
        raise ApiProblem(409, "model_source_changed", "The model's connection now targets a different database.")
    return catalog


def validate_model_definition(catalog, definition):
    try:
        return validate_definition(catalog, document(definition))
    except ValueError as error:
        raise ApiProblem(422, "invalid_model_definition", str(error), details=getattr(error, "details", {})) from error


def patch_model_definition(services, owner, model_id, body):
    """Merge typed edits once, validate them, then save with the original CAS revision.

    The repository performs the final revision check atomically. Layout, explore,
    schema baseline and untouched authored records are never sent back by callers.
    """
    current = load_model(services, owner, model_id, body.expected_revision)
    definition = document(current.definition)
    for label in ("nodes", "edges", "scopes"):
        changes = getattr(body, label)
        items = {item["id"]: item for item in definition[label]}
        unknown = set(changes.remove) - items.keys()
        if unknown:
            raise ApiProblem(422, "unknown_model_patch_id", f"Cannot remove unknown {label}: {', '.join(sorted(unknown))}.")
        for key in changes.remove:
            del items[key]
        items.update((item.id, document(item)) for item in changes.upsert)
        definition[label] = list(items.values())
    if body.root is not None:
        definition["root"] = body.root
    catalog = model_catalog(services, owner, current)
    exposed = definition.get("exposedFields")
    if exposed is None and body.hide:
        # Unrestricted exposure becomes an explicit list only when something is
        # hidden. Merely exposing an already available field stays unrestricted.
        tables = {table["name"]: table["columns"] for table in catalog["tables"]}
        exposed = [{"table": node["id"], "column": column}
                   for node in definition["nodes"]
                   for column in ([output["id"] for output in node["derivation"]["outputs"]]
                                  if node.get("derivation") else [c["name"] for c in tables.get(node["table"], [])])]
    if exposed is not None:
        hidden = {(field.table, field.column) for field in body.hide}
        selected = {(field["table"], field["column"]): field for field in exposed}
        selected.update(((field.table, field.column), {"table": field.table, "column": field.column}) for field in body.expose)
        definition["exposedFields"] = [field for key, field in selected.items() if key not in hidden]
    try:
        definition = ModelDefinition.model_validate(definition)
    except ValidationError as error:
        raise ApiProblem(422, "invalid_model_definition", "The combined model exceeds its bounds or contains invalid records.") from error
    # Even unrestricted models must reject invented fields in explicit expose
    # requests rather than acknowledging a no-op as a successful edit.
    validation = definition.model_copy(update={"exposedFields": [*(definition.exposedFields or []), *body.expose]})
    validate_model_definition(catalog, validation)
    return services.models.update(owner, model_id, ModelUpdate(
        expected_revision=body.expected_revision, name=body.name if body.name is not None else current.name,
        definition=definition, catalog_fingerprint=current.catalog_fingerprint))


def plan_query(catalog, definition, explore, fingerprint="", *, _bounded=True,
               _participation_fields=None, _scope_outer_nodes=None):
    rules, query = document(definition), document(explore)
    if not query.get("root"):
        query["root"] = rules.get("root", "")
    exposed = rules.get("exposedFields")
    if exposed is not None:
        allowed = {(f["table"], f["column"]) for f in exposed}
        requested = query["fields"] + [c for group in query["reportFilters"] for c in group["conditions"]]
        requested += [{"table": condition["table"], "column": condition["compareColumn"]}
                      for group in query["reportFilters"] for condition in group["conditions"]
                      if condition.get("compareColumn") is not None]
        hidden = list(dict.fromkeys((f["table"], f["column"]) for f in requested
                                    if (f["table"], f["column"]) not in allowed))
        if hidden:
            labels = ", ".join(f"{table}.{column}" for table, column in hidden)
            raise ApiProblem(422, "field_not_exposed",
                             f"These preview fields or report filters are not exposed by this model: {labels}. "
                             "Expose them in the model or remove them from the preview.",
                             details={"fields": [{"table": table, "column": column} for table, column in hidden]})
    issues = source_issues(catalog, rules)
    blocking = [issue for issue in issues if issue.get("severity", "breaking") == "breaking"]
    if blocking:
        raise ApiProblem(422, "model_source_drift", "Source references need repair. Your saved model has not been replaced.", details={"issues": issues})
    diagnostics = {}
    try:
        combined = {**rules, **query}
        diagnostics = analyze_model(catalog, combined)
        result = compile_preview(catalog, combined, _bounded=_bounded,
                                 _participation_fields=_participation_fields,
                                 _scope_outer_nodes=_scope_outer_nodes)
    except ValueError as error:
        raise ApiProblem(422, "invalid_model_query", str(error),
                         details={**diagnostics, **getattr(error, "details", {})}) from error
    if fingerprint and fingerprint != catalog["fingerprint"]:
        result["warnings"].append("The source schema changed since this model was saved. Referenced objects were revalidated; the model was not replaced.")
    result["warnings"].extend(issue["message"] for issue in issues)
    return {**diagnostics, **result, "sourceIssues": issues, "catalogFingerprint": catalog["fingerprint"]}


def execute_query(services, owner, model, console_id, plan, tasks, *, row_page_size=None):
    if services.console is None:
        raise ApiProblem(503, "console_unavailable", "Read preview execution is unavailable.")
    options = {} if row_page_size is None else {"row_page_size": row_page_size}
    receipt = services.console.reserve_read_target(
        owner, connection_id=model.connection_id, database=model.database,
        namespace=model.namespace, console_id=console_id, statements=[plan["sql"]],
        connection_access=services.connections, **options)
    tasks.add_task(services.console.run, owner, receipt.id,
                   connection_access=services.connections)
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
    plan = plan_query(catalog, rules, ExploreState(root=table, fields=fields))
    if search:
        # Lookup text matches either the displayed label or the stored value.
        # STRPOS treats %, _ and quotes literally; SQL AST literals handle escaping.
        query = parse_one(plan["sql"], read="postgres")
        alias = query.args["from_"].this.alias_or_name
        predicates = [exp.GT(
            this=exp.func("STRPOS",
                exp.Lower(this=exp.Cast(this=exp.column(name, table=alias, quoted=True), to=exp.DataType.build("TEXT"))),
                exp.Lower(this=exp.Literal.string(search))),
            expression=exp.Literal.number(0)) for name in dict.fromkeys([label, column])]
        query = query.where(exp.or_(*predicates))
        plan["sql"] = query.sql(dialect="postgres", pretty=True) + ";"
    return plan
