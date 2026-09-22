from types import SimpleNamespace as NS

import pytest
from sqlglot import parse_one

from schemii.common.api.errors import ApiProblem
from schemii.schemoo.models import ModelDefinition, SelectedField
from schemii.schemer import tile_queries as queries


def field(table, column, aggregate="none"):
    return SelectedField(table=table, column=column, aggregate=aggregate)


@pytest.fixture
def context(monkeypatch):
    catalog = {"fingerprint": "", "namespace": "public", "tables": [
        {"name": name, "columns": [{"name": column, "dataType": "text"} for column in columns]}
        for name, columns in [("people", ["id", "name", "org_id"]), ("org", ["id", "name"]),
                              ("cert", ["id", "person_id"])]],
        "relationships": [{"id": "org_fk", "sourceTable": "people", "sourceColumn": "org_id", "targetTable": "org", "targetColumn": "id"},
                          {"id": "cert_fk", "sourceTable": "cert", "sourceColumn": "person_id", "targetTable": "people", "targetColumn": "id"}]}
    definition = ModelDefinition.model_validate({"root": "people", "nodes": [{"id": table["name"], "table": table["name"], "label": table["name"]} for table in catalog["tables"]],
            "edges": [{"id": "org_fk", "relationshipId": "org_fk", "source": "people", "target": "org", "enabled": True},
                      {"id": "cert_fk", "relationshipId": "cert_fk", "source": "cert", "target": "people", "enabled": True}]})
    model = NS(definition=definition, catalog_fingerprint="")
    tile = NS(id="tile", kind="bar", dimensions=[field("org", "name")], measures=[field("people", "id", "count_distinct")],
              detail_fields=[field("people", "name")], report_filters=[], limit=100)
    dashboard = NS(model_id="model_" + "a" * 32, model_revision=1, selections={}, tiles=[tile])
    monkeypatch.setattr(queries, "load_model", lambda *args: model)
    monkeypatch.setattr(queries, "model_catalog", lambda *args, **kwargs: catalog)
    return dashboard, tile, model


def selection(value="Engineering", measure=0):
    return {"dimensions": [{"table": "org", "column": "name", "value": value}], "measureIndex": measure}


def test_chart_and_drill_keep_join_and_group(context):
    dashboard, tile, _ = context
    _, chart = queries.tile_plan(None, None, dashboard, tile.id)
    assert 'COUNT(DISTINCT' in chart["sql"]
    assert 'GROUP BY' in chart["sql"]
    assert 'LIMIT' not in chart["sql"]
    _, drill = queries.tile_plan(None, None, dashboard, tile.id, selection=selection())
    assert 'LEFT JOIN "public"."org"' in drill["sql"]
    assert '"contributors"."org.name" = ' in drill["sql"]
    assert 'NOT "contributors"."people.id" IS NULL' in drill["sql"]
    assert 'GROUP BY' not in drill["sql"]
    assert 'LIMIT' not in drill["sql"] and 'OFFSET' not in drill["sql"]
    assert any("count once" in warning for warning in drill["warnings"])


def test_repetition_diagnostics_survive_tile_ordering_without_blocking_drill(context):
    dashboard, tile, _ = context
    tile.dimensions = [field("cert", "id")]
    tile.measures = [field("people", "id", "count")]
    _, plan = queries.tile_plan(None, None, dashboard, tile.id)
    diagnostic, = plan["repetitionDiagnostics"]
    assert diagnostic["outputIndex"] == 1
    assert diagnostic["relationships"][0]["id"] == "cert_fk"
    assert "COUNT(DISTINCT" not in plan["sql"]
    _, drill = queries.tile_plan(None, None, dashboard, tile.id, selection={
        "dimensions": [{"table": "cert", "column": "id", "value": "1"}], "measureIndex": 0})
    assert drill["drill"]
    assert '"cert"' in drill["sql"]


def test_null_and_quoted_dimension_values_are_safe(context):
    dashboard, tile, _ = context
    _, null = queries.tile_plan(None, None, dashboard, tile.id, selection=selection(None))
    assert '"contributors"."org.name" IS NULL' in null["sql"]
    _, quoted = queries.tile_plan(None, None, dashboard, tile.id, selection=selection("O'Reilly; DROP TABLE people"))
    assert parse_one(quoted["sql"], read="postgres").key == "select"


@pytest.mark.parametrize("value", [selection(measure=9), {"dimensions": [], "measureIndex": 0},
    {"dimensions": [{"table": "people", "column": "name", "value": "x"}], "measureIndex": 0}])
def test_cannot_forge_drill_fields_or_measures(context, value):
    dashboard, tile, _ = context
    with pytest.raises(ApiProblem):
        queries.tile_plan(None, None, dashboard, tile.id, selection=value)


def test_new_detail_branch_does_not_activate_conditional_scope(context):
    dashboard, tile, model = context
    from schemii.schemoo.models import ModelScope
    model.definition.scopes = [ModelScope.model_validate({"id": "cert_filter", "label": "Certificate", "kind": "conditional", "alternatives": [
        {"id": "choice", "label": "Choice", "conditions": [{"table": "cert", "column": "id", "operator": "eq", "value": "special"}]}]})]
    tile.detail_fields.append(field("cert", "id"))
    _, drill = queries.tile_plan(None, None, dashboard, tile.id, selection=selection())
    assert 'LEFT JOIN "public"."cert"' in drill["sql"]
    assert "special" not in drill["sql"]
    assert "cert_filter" not in drill["activeScopes"]
    assert any("repeat a contributing row" in message for message in drill["warnings"])


def test_dashboard_exposed_optional_scope_only_filters_when_viewer_activates_it(context):
    dashboard, tile, model = context
    from schemii.schemoo.models import ModelScope, ScopeSelection
    model.definition.scopes = [ModelScope.model_validate({"id": "organization", "label": "Organization",
        "kind": "required", "requirement": "optional", "alternatives": [{"id": "choice", "inputs": [
            {"id": "org", "label": "Organization", "type": "integer"}], "conditions": [
            {"table": "people", "column": "org_id", "operator": "eq", "parameterId": "org"}]}]})]
    dashboard.optional_filters = ["organization"]
    dashboard.selections = {"organization": ScopeSelection(active=False, values={"org": 1})}
    _, inactive = queries.tile_plan(None, None, dashboard, tile.id)
    assert '"org_id" = 1' not in inactive["sql"]

    dashboard.selections["organization"].active = True
    _, active = queries.tile_plan(None, None, dashboard, tile.id)
    assert '"org_id" = 1' in active["sql"]


def test_new_detail_branch_preserves_required_exists(context):
    dashboard, tile, model = context
    from schemii.schemoo.models import ModelScope
    model.definition.scopes = [ModelScope.model_validate({"id": "cert_filter", "label": "Certificate", "kind": "required", "alternatives": [
        {"id": "choice", "label": "Choice", "conditions": [{"table": "cert", "column": "id", "operator": "eq", "value": "special"}]}]})]
    tile.detail_fields.append(field("cert", "id"))
    _, drill = queries.tile_plan(None, None, dashboard, tile.id, selection=selection())
    assert 'EXISTS(SELECT 1 FROM "public"."cert"' in drill["sql"]
    assert 'e0_1."id"' in drill["sql"]
    assert 'LEFT JOIN "public"."cert"' in drill["sql"]


def execute(sql):
    import sqlite3
    from sqlglot import exp
    with sqlite3.connect(":memory:") as connection:
        connection.executescript("""
            ATTACH DATABASE ':memory:' AS public;
            CREATE TABLE public.people(id INTEGER, name TEXT, org_id INTEGER);
            CREATE TABLE public.org(id INTEGER, name TEXT);
            CREATE TABLE public.cert(id TEXT, person_id INTEGER);
            INSERT INTO public.org VALUES (1, 'Engineering'), (2, 'Sales');
            INSERT INTO public.people VALUES (1, 'Alice', 1), (2, 'Bob', 1), (3, 'Carol', 2), (NULL, 'Unknown', 1);
            INSERT INTO public.cert VALUES ('special', 1), ('other', 1), ('other', 2);
        """)
        sql = parse_one(sql, read="postgres").transform(
            lambda node: exp.Literal.string(node.this) if isinstance(node, exp.ByteString) else node).sql(dialect="sqlite")
        return connection.execute(sql).fetchall()


def test_drill_retains_all_nonnull_contributors_without_sql_paging(context):
    dashboard, tile, _ = context
    tile.measures = [field("people", "id", "count")]
    _, chart = queries.tile_plan(None, None, dashboard, tile.id)
    assert execute(chart["sql"]) == [("Engineering", 2), ("Sales", 1)]
    tile.limit = 1
    _, drill = queries.tile_plan(None, None, dashboard, tile.id, selection=selection())
    assert execute(drill["sql"]) == [("Alice",), ("Bob",)]
    assert drill["rowLimit"] == 1
    assert "LIMIT" not in drill["sql"]


def test_required_scope_keeps_original_population_with_related_details(context):
    dashboard, tile, model = context
    from schemii.schemoo.models import ModelScope
    model.definition.scopes = [ModelScope.model_validate({"id": "cert_filter", "label": "Certificate", "kind": "required", "alternatives": [
        {"id": "choice", "label": "Choice", "conditions": [{"table": "cert", "column": "id", "operator": "eq", "value": "special"}]}]})]
    _, chart = queries.tile_plan(None, None, dashboard, tile.id)
    assert execute(chart["sql"]) == [("Engineering", 1)]
    tile.detail_fields.append(field("cert", "id"))
    _, drill = queries.tile_plan(None, None, dashboard, tile.id, selection=selection())
    assert execute(drill["sql"]) == [("Alice", "other"), ("Alice", "special")]


def test_tiles_cannot_override_dashboard_required_slicers(context):
    dashboard, tile, model = context
    from schemii.schemoo.models import ModelScope, ScopeSelection
    model.definition.scopes = [ModelScope.model_validate({"id": "required", "kind": "required", "label": "Required", "alternatives": [
        {"id": "choice", "label": "Choice", "conditions": []}]})]
    tile.selections = {"required": ScopeSelection(alternativeId="choice")}
    with pytest.raises(ApiProblem, match="cannot be overridden"):
        queries.tile_plan(None, None, dashboard, tile.id)


def test_calculated_dimension_is_filtered_by_result_expression(context):
    dashboard, tile, model = context
    from schemii.schemoo.models import ModelNode
    catalog = queries.model_catalog(None, None, model)
    for column in catalog["tables"][0]["columns"]:
        if column["name"] in {"id", "org_id"}:
            column["dataType"] = "integer"
    model.definition.nodes.append(ModelNode.model_validate({"id": "calculation", "table": "people", "label": "Calculated", "derivation": {
        "kind": "row", "source": "people", "groupBy": [], "outputs": [
            {"id": "combined", "label": "Combined", "operation": "add", "column": "id", "operand": "org_id"}]}}))
    tile.dimensions = [field("calculation", "combined")]
    value = {"dimensions": [{"table": "calculation", "column": "combined", "value": 2}], "measureIndex": 0}
    _, drill = queries.tile_plan(None, None, dashboard, tile.id, selection=value)
    assert '"contributors"."Calculated.Combined" = 2' in drill["sql"]
    assert execute(drill["sql"]) == [("Alice",)]


def test_numeric_chart_dimensions_keep_native_order():
    import sqlite3
    plan = queries._ordered({}, parse_one("SELECT value FROM numbers"), 100)
    with sqlite3.connect(":memory:") as connection:
        connection.executescript("CREATE TABLE numbers(value INTEGER); INSERT INTO numbers VALUES (10), (2), (1);")
        assert connection.execute(parse_one(plan["sql"], read="postgres").sql(dialect="sqlite")).fetchall() == [(1,), (2,), (10,)]


def test_json_detail_uses_text_only_for_unorderable_output(context):
    dashboard, tile, model = context
    tile.kind = "detail"
    tile.detail_fields = [field("people", "id"), field("people", "name")]
    catalog = queries.model_catalog(None, None, model)
    catalog["tables"][0]["columns"][1]["dataType"] = "json"
    _, plan = queries.tile_plan(None, None, dashboard, tile.id)
    assert 'ORDER BY "page_rows"."output_1", CAST("page_rows"."output_2" AS TEXT)' in plan["sql"]
    assert plan["outputLabels"] == ["people.id", "people.name"]


def test_long_output_labels_use_distinct_safe_sql_aliases(context):
    dashboard, tile, model = context
    model.definition.nodes[0].label = "Current personnel slate status and slot"
    tile.kind = "detail"
    tile.detail_fields = [field("people", "personnel_pay_band_level"),
                          field("people", "personnel_pay_band_level_matches_slot")]
    catalog = queries.model_catalog(None, None, model)
    catalog["tables"][0]["columns"].extend([
        {"name": "personnel_pay_band_level", "dataType": "text"},
        {"name": "personnel_pay_band_level_matches_slot", "dataType": "boolean"},
    ])
    _, plan = queries.tile_plan(None, None, dashboard, tile.id)
    assert 'AS "output_1"' in plan["sql"] and 'AS "output_2"' in plan["sql"]
    assert plan["outputLabels"] == [
        "Current personnel slate status and slot.personnel_pay_band_level",
        "Current personnel slate status and slot.personnel_pay_band_level_matches_slot",
    ]

def test_dashboard_can_save_and_plan_count_with_join_multiplication(context, monkeypatch):
    from schemii.schemer import dashboard_routes
    from schemii.schemer.dashboard_models import DashboardCreate
    dashboard, tile, model = context
    tile.dimensions = [field("cert", "id")]
    tile.measures = [field("people", "id", "count")]
    _, plan = queries.tile_plan(None, None, dashboard, tile.id)
    assert any("repeated rows from joins" in warning for warning in plan["warnings"])
    assert "COUNT(" in plan["sql"] and "COUNT(DISTINCT" not in plan["sql"]
    monkeypatch.setattr(dashboard_routes, "load_model", queries.load_model)
    monkeypatch.setattr(dashboard_routes, "model_catalog", queries.model_catalog)
    request = DashboardCreate.model_validate({
        "name": "Counts", "modelId": dashboard.model_id, "modelRevision": 1,
        "tiles": [{"id": "tile", "title": "Counts", "kind": "bar",
                   "dimensions": [{"table": "cert", "column": "id"}],
                   "measures": [{"table": "people", "column": "id", "aggregate": "count"}]}],
    })
    services = NS(admin_config=NS(console=NS(maximum_statements_per_run=20)))
    dashboard_routes.validate_dashboard(services, "alice", request)
