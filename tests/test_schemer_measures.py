import sqlite3
import pytest
from sqlglot import exp, parse_one
from schemii.common.api.errors import ApiProblem
from schemii.schemer.dashboard_models import DashboardTile
from schemii.schemer.measures import MeasureGraph
from schemii.schemer import tile_queries as queries
from test_schemer_tile_queries import context as context


def operand(column, aggregate="sum", scope="group"):
    return {
        "output": {"table": "people", "column": column, "aggregate": aggregate},
        "scope": scope,
    }


def calculation(identifier="ratio", left=None, right=None, operation="divide", **extra):
    return {
        "id": identifier,
        "label": identifier,
        "operation": operation,
        "left": left or operand("amount"),
        "right": right or operand("units"),
        **extra,
    }


def configured(context, **patch):
    dashboard, _, model = context
    catalog = queries.model_catalog(None, None, model)
    catalog["tables"][0]["columns"] += [
        {"name": "amount", "dataType": "numeric"},
        {"name": "units", "dataType": "numeric"},
    ]
    body = {
        "id": "ratio",
        "title": "Ratio",
        "kind": "aggregate",
        "dimensions": [{"table": "org", "column": "name"}],
        "hiddenMeasures": [
            {"table": "people", "column": name, "aggregate": "sum"}
            for name in ["amount", "units"]
        ],
        "calculations": [calculation()],
        "detailFields": [{"table": "people", "column": "id"}],
        **patch,
    }
    tile = DashboardTile.model_validate(body)
    dashboard.tiles = [tile]
    return dashboard, tile, model, catalog


def execute(sql):
    ast = parse_one(sql, read="postgres").transform(
        lambda node: (
            exp.Literal.string(node.this) if isinstance(node, exp.ByteString) else node
        )
    )
    with sqlite3.connect(":memory:") as connection:
        connection.executescript("""CREATE TABLE people(id TEXT,name TEXT,org_id TEXT,amount REAL,units REAL);
        INSERT INTO people VALUES ('1','same','a',100,2),('2','other','a',10,10),('3','same','b',90,3);
        CREATE TABLE org(id TEXT,name TEXT);INSERT INTO org VALUES('a','A'),('b','B');""")
        return connection.execute(
            ast.sql(dialect="sqlite").replace('"public".', "")
        ).fetchall()


def plan(context, **patch):
    dashboard, tile, model, catalog = configured(context, **patch)
    return tile, queries.tile_plan(None, None, dashboard, tile.id)[1]


def test_ratio_of_sums_is_weighted_and_hidden_base_inputs_stay_hidden(context):
    tile, result = plan(context)
    assert result["outputLabels"] == ["org.name", "ratio"]
    rows = execute(result["sql"])
    assert rows[0] == ("A", pytest.approx(110 / 12)) and rows[1] == ("B", 30)
    assert rows[0][1] != pytest.approx((100 / 2 + 10 / 10) / 2)
    assert "LIMIT" not in result["sql"]
    assert result["calculatedMeasures"][0]["index"] == 1


@pytest.mark.parametrize(
    "scope,expected", [("source_population", 55), ("qualifying_groups", 100)]
)
def test_population_reaggregates_before_top_n_with_explicit_filter_scope(
    context, scope, expected
):
    predicate = {
        "table": "people",
        "column": "units",
        "aggregate": "sum",
        "operator": "gt",
        "value": 5,
    }
    _, result = plan(
        context,
        calculations=[
            calculation(right=operand("amount", scope=scope), operation="percent_of")
        ],
        aggregateFilters=[predicate],
        topN=1,
    )
    assert execute(result["sql"]) == [("A", pytest.approx(expected))]
    assert (
        result["calculatedMeasures"][0]["unit"] == "percent"
        and result["calculatedMeasures"][0]["scale"] == 100
    )


def test_average_population_uses_actual_inputs_not_average_of_displayed_groups(context):
    hidden = [{"table": "people", "column": "amount", "aggregate": "avg"}]
    _, result = plan(
        context,
        hiddenMeasures=hidden,
        calculations=[
            calculation(
                left=operand("amount", "avg"),
                right=operand("amount", "avg", "source_population"),
            )
        ],
    )
    assert execute(result["sql"]) == [
        ("A", pytest.approx(55 / (200 / 3))),
        ("B", pytest.approx(90 / (200 / 3))),
    ]


def test_distinct_population_counts_values_once_across_groups(context):
    hidden = [{"table": "people", "column": "name", "aggregate": "count_distinct"}]
    _, result = plan(
        context,
        hiddenMeasures=hidden,
        calculations=[
            calculation(
                left=operand("name", "count_distinct"),
                right=operand("name", "count_distinct", "source_population"),
            )
        ],
    )
    assert execute(result["sql"]) == [("A", 1), ("B", pytest.approx(0.5))]


def test_calculations_validate_topologically_and_hidden_sort_reference_is_stable(
    context,
):
    ref = {
        "output": {"table": "@report", "column": "ratio", "kind": "calculated"},
        "scope": "group",
    }
    derived = calculation(
        "twice", left=ref, right={"constant": "2"}, operation="multiply"
    )
    _, result = plan(
        context,
        calculations=[derived, calculation(visible=False)],
        sort=[
            {
                "table": "@report",
                "column": "ratio",
                "kind": "calculated",
                "direction": "desc",
            }
        ],
    )
    assert execute(result["sql"]) == [("B", 60), ("A", pytest.approx(220 / 12))]
    assert result["outputLabels"] == ["org.name", "twice"]


def test_missing_cycles_types_and_circular_qualifying_scope_are_actionable(context):
    dashboard, tile, model, catalog = configured(context)
    bad = tile.model_copy(deep=True)
    bad.calculations[0].left.output.column = "gone"
    with pytest.raises(ApiProblem, match="removed"):
        MeasureGraph(bad, catalog, model.definition)
    bad = tile.model_copy(deep=True)
    bad.calculations[0].left.output.table = "@report"
    bad.calculations[0].left.output.column = "ratio"
    bad.calculations[0].left.output.aggregate = "none"
    bad.calculations[0].left.output.kind = "calculated"
    with pytest.raises(ApiProblem, match="cycle.*ratio"):
        MeasureGraph(bad, catalog, model.definition)
    catalog["tables"][0]["columns"][-2]["dataType"] = "text"
    with pytest.raises(ApiProblem, match="numeric"):
        MeasureGraph(tile, catalog, model.definition)
    catalog["tables"][0]["columns"][-2]["dataType"] = "numeric"
    bad = DashboardTile.model_validate(
        {
            **tile.model_dump(by_alias=True),
            "calculations": [
                calculation(right=operand("units", scope="qualifying_groups"))
            ],
            "aggregateFilters": [
                {
                    "table": "@report",
                    "column": "ratio",
                    "kind": "calculated",
                    "operator": "gt",
                    "value": 1,
                }
            ],
        }
    )
    with pytest.raises(ApiProblem, match="own qualifying-groups"):
        MeasureGraph(bad, catalog, model.definition)


def test_zero_divisor_and_null_operand_remain_null(context):
    _, result = plan(
        context,
        hiddenMeasures=[{"table": "people", "column": "amount", "aggregate": "sum"}],
        calculations=[calculation(right={"constant": "0"})],
    )
    assert execute(result["sql"]) == [("A", None), ("B", None)]


def test_kpi_target_preserves_exact_constant_and_emits_variances(context):
    _, result = plan(
        context,
        kind="kpi",
        dimensions=[],
        kpiComparison={
            "label": "Baseline",
            "baseline": {"constant": "10.00000000000000000001"},
            "favorable": "higher",
        },
    )
    assert "10.00000000000000000001" in result["sql"]
    row = execute(result["sql"])[0]
    assert row[0] == pytest.approx(200 / 15)
    assert (
        row[1] == 10
        and row[2] == pytest.approx(200 / 15 - 10)
        and row[3] == pytest.approx((200 / 15 - 10) / 10 * 100)
    )
    assert result["kpiComparison"]["favorable"] == "higher"


def test_pivot_totals_reaggregate_base_inputs_before_computing_weighted_ratio(context):
    _, result = plan(
        context,
        kind="pivot",
        dimensions=[],
        rowDimensions=[{"table": "org", "column": "name"}],
        columnDimensions=[{"table": "people", "column": "name"}],
    )
    rows = execute(result["sql"])
    total = next(row for row in rows if row[-7:-5] == (0, 0))
    assert total[2] == pytest.approx(200 / 15)
    assert (
        result["pivot"]["measureCount"] == 1 and result["pivot"]["metadataOffset"] == 3
    )


@pytest.mark.parametrize(
    "scope,ids",
    [
        ("group", ["1", "2"]),
        ("source_population", ["1", "2", "3"]),
        ("qualifying_groups", ["1", "2"]),
    ],
)
def test_composite_drill_requires_explicit_constituent_and_honors_its_population(
    context, scope, ids
):
    predicate = {
        "table": "people",
        "column": "units",
        "aggregate": "sum",
        "operator": "gt",
        "value": 5,
    }
    dashboard, tile, _, _ = configured(
        context,
        calculations=[calculation(right=operand("amount", scope=scope))],
        aggregateFilters=[predicate],
    )
    selection = {
        "dimensions": [{"table": "org", "column": "name", "value": "A"}],
        "measureIndex": 0,
    }
    with pytest.raises(ApiProblem, match="constituent"):
        queries.tile_plan(None, None, dashboard, tile.id, selection=selection)
    selection["constituent"] = operand("amount", scope=scope)
    _, result = queries.tile_plan(None, None, dashboard, tile.id, selection=selection)
    assert [row[0] for row in execute(result["sql"])] == ids
    assert result["calculationScope"]["constituent"]["scope"] == scope
    assert result["calculationScope"]["selectedDimensions"] == selection["dimensions"]
    _, export = queries.tile_plan(
        None, None, dashboard, tile.id, selection=selection, exporting=True
    )
    rows = execute(export["sql"])
    assert [row[0] for row in rows] == ids and export["outputLabels"][
        -1
    ] == "Calculation selection"
    selection["constituent"] = operand("units", scope="source_population")
    with pytest.raises(ApiProblem, match="does not belong"):
        queries.tile_plan(None, None, dashboard, tile.id, selection=selection)


def test_kpi_zero_baseline_has_null_percentage_and_explicit_favorable_status(context):
    _, result = plan(
        context,
        kind="kpi",
        dimensions=[],
        kpiComparison={"baseline": {"constant": "0"}, "favorable": "lower"},
    )
    row = execute(result["sql"])[0]
    assert row[3] is None and row[4] == "unfavorable"


def test_unused_hidden_input_is_rejected_without_activating_its_branch(context):
    dashboard, tile, model, catalog = configured(context)
    tile.hidden_measures.append(
        tile.hidden_measures[0].model_copy(
            update={"table": "cert", "column": "id", "aggregate": "count"}
        )
    )
    with pytest.raises(ApiProblem, match="unused.*unrelated branches"):
        MeasureGraph(tile, catalog, model.definition)


def test_calculated_discriminator_does_not_alias_a_legal_model_node_and_field(context):
    from schemii.schemoo.models import ModelNode

    dashboard, tile, model, catalog = configured(
        context,
        dimensions=[{"table": "people", "column": "id"}],
        hiddenMeasures=[{"table": "@report", "column": "ratio", "aggregate": "none"}],
        calculations=[
            calculation(
                left={
                    "output": {
                        "table": "@report",
                        "column": "ratio",
                        "aggregate": "none",
                        "kind": "value",
                    },
                    "scope": "group",
                },
                right={"constant": "1"},
                operation="add",
            )
        ],
    )
    catalog["tables"][0]["primaryKey"] = ["id"]
    model.definition.nodes.append(
        ModelNode.model_validate(
            {
                "id": "@report",
                "table": "people",
                "label": "Actual source",
                "derivation": {
                    "kind": "aggregate",
                    "source": "people",
                    "groupBy": ["id"],
                    "outputs": [
                        {
                            "id": "ratio",
                            "label": "Actual ratio",
                            "operation": "sum",
                            "column": "amount",
                        }
                    ],
                },
            }
        )
    )
    graph = MeasureGraph(tile, catalog, model.definition)
    assert (
        graph.positions[("@report", "ratio", "none", "value")]
        != graph.positions[("@report", "ratio", "none", "calculated")]
    )
    _, result = queries.tile_plan(None, None, dashboard, tile.id)
    assert execute(result["sql"]) == [("1", 101), ("2", 11), ("3", 91)]


def test_source_row_filters_apply_to_the_explicit_report_population(context):
    _, result = plan(
        context,
        hiddenMeasures=[{"table": "people", "column": "amount", "aggregate": "sum"}],
        calculations=[
            calculation(
                right=operand("amount", scope="source_population"),
                operation="percent_of",
            )
        ],
        reportFilters=[
            {
                "mode": "rows",
                "conditions": [
                    {
                        "table": "people",
                        "column": "amount",
                        "operator": "gt",
                        "value": 20,
                    }
                ],
            }
        ],
    )
    assert execute(result["sql"]) == [
        ("A", pytest.approx(100 / 190 * 100)),
        ("B", pytest.approx(90 / 190 * 100)),
    ]


def test_null_aggregate_operand_is_not_coalesced_to_zero(context):
    _, result = plan(
        context,
        kind="kpi",
        dimensions=[],
        hiddenMeasures=[{"table": "people", "column": "amount", "aggregate": "sum"}],
        calculations=[calculation(right={"constant": "1"})],
        reportFilters=[
            {
                "mode": "rows",
                "conditions": [
                    {
                        "table": "people",
                        "column": "amount",
                        "operator": "gt",
                        "value": 999,
                    }
                ],
            }
        ],
    )
    assert execute(result["sql"]) == [(None,)]


@pytest.mark.parametrize(
    "direction,status",
    [("higher", "unfavorable"), ("lower", "favorable"), ("equal", "unfavorable")],
)
def test_kpi_compatible_measure_baseline_uses_declared_direction(
    context, direction, status
):
    _, result = plan(
        context,
        kind="kpi",
        dimensions=[],
        kpiComparison={
            "label": "Measure baseline",
            "baseline": operand("amount"),
            "favorable": direction,
        },
    )
    row = execute(result["sql"])[0]
    assert row[1] == 200 and row[4] == status
    assert result["kpiComparison"]["percentUnit"] == "percent"


def test_hidden_base_output_used_only_by_sort_and_filter_is_preserved(context):
    _, result = plan(
        context,
        measures=[{"table": "people", "column": "amount", "aggregate": "sum"}],
        hiddenMeasures=[{"table": "people", "column": "units", "aggregate": "sum"}],
        calculations=[],
        sort=[
            {
                "table": "people",
                "column": "units",
                "aggregate": "sum",
                "direction": "asc",
            }
        ],
    )
    assert execute(result["sql"]) == [("B", 90), ("A", 110)]
    assert result["outputLabels"] == ["org.name", "sum(people.amount)"]


@pytest.mark.parametrize("value", ["NaN", "Infinity", "1e99999", "1; SELECT 1", True])
def test_calculation_constants_are_bounded_numeric_data_without_sql(value):
    from pydantic import ValidationError
    from schemii.schemer.dashboard_models import MeasureOperand

    with pytest.raises(ValidationError):
        MeasureOperand.model_validate({"constant": value})


def test_group_calculation_can_reference_complete_calendar_outputs(context):
    dashboard, tile, model, catalog = configured(
        context,
        dimensions=[{"table": "people", "column": "created_at"}],
        hiddenMeasures=[{"table": "people", "column": "amount", "aggregate": "sum"}],
        timeAnalysis={
            "table": "people",
            "column": "created_at",
            "granularity": "month",
            "comparison": "previous_period",
        },
        calculations=[
            calculation(
                right={
                    "output": {
                        "table": "people",
                        "column": "amount",
                        "aggregate": "sum",
                        "kind": "comparison",
                    },
                    "scope": "group",
                }
            )
        ],
    )
    catalog["tables"][0]["columns"].append({"name": "created_at", "dataType": "date"})
    _, result = queries.tile_plan(None, None, dashboard, tile.id)
    assert result["outputLabels"] == ["people.created_at", "ratio"]
    assert "LEFT JOIN buckets AS previous" in result["sql"]
    assert result["calculatedMeasures"][0]["index"] == 1
    assert result["computedOutputs"] == []
    assert result["reportOutputIdentities"][-1]["kind"] == "calculated"
    from sqlglot.optimizer.qualify import qualify

    qualify(
        parse_one(result["sql"], read="postgres"),
        schema={"public": {"people": {"created_at": "DATE", "amount": "NUMERIC"}}},
        validate_qualify_columns=True,
    )
    tile.calculations[0].right.scope = "source_population"
    with pytest.raises(
        ApiProblem, match="population scopes require|population denominators"
    ):
        queries.tile_plan(None, None, dashboard, tile.id)


def test_multiplying_calculation_inputs_are_rejected_on_save_and_execution(
    context, monkeypatch
):
    from types import SimpleNamespace
    from schemii.schemer import dashboard_routes
    from schemii.schemer.dashboard_models import DashboardCreate

    dashboard, tile, model, catalog = configured(
        context,
        hiddenMeasures=[
            {"table": "people", "column": "amount", "aggregate": "sum"},
            {"table": "cert", "column": "id", "aggregate": "count"},
        ],
        calculations=[
            calculation(
                right={
                    "output": {"table": "cert", "column": "id", "aggregate": "count"},
                    "scope": "group",
                }
            )
        ],
    )
    with pytest.raises(ApiProblem, match="multiplying relationship grain"):
        queries.tile_plan(None, None, dashboard, tile.id)
    monkeypatch.setattr(dashboard_routes, "load_model", lambda *args: model)
    monkeypatch.setattr(dashboard_routes, "model_catalog", lambda *args: catalog)
    body = DashboardCreate(
        name="Unsafe", model_id=dashboard.model_id, model_revision=1, tiles=[tile]
    )
    services = SimpleNamespace(
        admin_config=SimpleNamespace(
            console=SimpleNamespace(maximum_statements_per_run=20)
        )
    )
    with pytest.raises(ApiProblem, match="multiplying relationship grain"):
        dashboard_routes.validate_dashboard(services, "author", body)


def test_qualifying_population_is_complete_before_top_n_when_other_groups_qualify(
    context,
):
    _, result = plan(
        context,
        hiddenMeasures=[{"table": "people", "column": "amount", "aggregate": "sum"}],
        calculations=[
            calculation(
                right=operand("amount", scope="qualifying_groups"),
                operation="percent_of",
            )
        ],
        topN=1,
    )
    assert execute(result["sql"]) == [("A", pytest.approx(55))]


def test_calendar_source_population_preserves_date_eligibility(context):
    dashboard, tile, model, catalog = configured(
        context,
        dimensions=[{"table": "people", "column": "created_at"}],
        hiddenMeasures=[{"table": "people", "column": "amount", "aggregate": "sum"}],
        timeAnalysis={
            "table": "people",
            "column": "created_at",
            "granularity": "month",
        },
        calculations=[
            calculation(
                right=operand("amount", scope="source_population"),
                operation="percent_of",
            )
        ],
    )
    catalog["tables"][0]["columns"].append({"name": "created_at", "dataType": "date"})
    _, result = queries.tile_plan(None, None, dashboard, tile.id)
    statement = parse_one(result["sql"], read="postgres")
    population = next(
        cte
        for cte in statement.find_all(exp.CTE)
        if cte.alias == "population_source_population"
    )
    assert population.this.args["where"].find(exp.Is).expression == exp.Null()
    assert population.this.args["where"].find(exp.Not) is not None
