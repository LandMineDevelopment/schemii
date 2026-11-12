"""Actual PostgreSQL row/value oracles over synthetic semantic compiler data."""

import pytest

from schemii.schemoo.prototype import compile_preview
from schemii.schemoo.service import domain_values_plan
from tests.integration.sql_oracles import execute as execute


CATALOG = {
    "tables": [
        {
            "name": name,
            "primaryKey": ["id"] if "id" in columns else [],
            "columns": [{"name": field} for field in columns],
        }
        for name, columns in [
            ("people", ["id", "name"]),
            ("assignment", ["person_id", "org_id", "start", "end"]),
            ("org", ["id", "name"]),
            ("cert", ["person_id", "name"]),
        ]
    ],
    "relationships": [
        {
            "id": "person_fk",
            "sourceTable": "assignment",
            "sourceColumn": "person_id",
            "targetTable": "people",
            "targetColumn": "id",
        },
        {
            "id": "org_fk",
            "sourceTable": "assignment",
            "sourceColumn": "org_id",
            "targetTable": "org",
            "targetColumn": "id",
        },
        {
            "id": "cert_fk",
            "sourceTable": "cert",
            "sourceColumn": "person_id",
            "targetTable": "people",
            "targetColumn": "id",
        },
    ],
}


@pytest.mark.parametrize(
    "operation,expected", [("sum", 500), ("count", 4), ("avg", 125)]
)
def test_optional_repetition_diagnostics_preserve_postgres_values(
    execute, operation, expected
):
    catalog = {
        "tables": [
            {
                "name": "orders",
                "primaryKey": ["id"],
                "columns": [{"name": "id"}, {"name": "amount"}],
            },
            {"name": "items", "columns": [{"name": "order_id"}, {"name": "category"}]},
        ],
        "relationships": [
            {
                "id": "items_fk",
                "sourceTable": "items",
                "sourceColumn": "order_id",
                "targetTable": "orders",
                "targetColumn": "id",
            }
        ],
    }
    plan = compile_preview(
        catalog,
        {
            "root": "orders",
            "relationships": ["items_fk"],
            "fields": [
                {"table": "orders", "column": "amount", "aggregate": operation},
                {"table": "items", "column": "category"},
            ],
        },
    )
    assert plan["repetitionDiagnostics"][0]["measure"]["aggregate"] == operation
    assert "DISTINCT" not in plan["sql"]
    sql = (
        plan["sql"]
        .replace('"public"."orders"', '"orders"')
        .replace('"public"."items"', '"items"')
    )
    rows = execute(
        """WITH orders(id, amount) AS (VALUES
        (1,100::numeric),(2,100),(3,200),(4,NULL)),
        items(order_id, category) AS (VALUES (1,'A'::text),(1,'A'),(2,'A'),(3,'A'),(4,'A'))
    """
        + sql
    )
    assert rows[0][0] == expected
    assert rows[0][1] == "A"


@pytest.mark.parametrize(
    "search,expected",
    [
        ("finance", "Finance North"),
        ("NORTH", "Finance North"),
        ("AAAA", "Finance North"),
        ("100%_O'", "100%_O'Reilly"),
        ("missing", None),
    ],
)
def test_domain_search_matches_label_or_uuid_case_insensitively(
    execute, search, expected
):
    plan = domain_values_plan(
        {**CATALOG, "fingerprint": "domain-fixture"}, "org", "id", "name", search
    )
    sql = plan["sql"].replace('"public"."org"', '"org"')
    rows = execute(
        """WITH org(id,name) AS (VALUES
      ('aaaaaaaa-0000-0000-0000-000000000001'::uuid, 'Finance North'::text),
      ('bbbbbbbb-0000-0000-0000-000000000002'::uuid, '100%_O''Reilly'::text))
    """
        + sql
    )
    assert [row[1] for row in rows] == ([] if expected is None else [expected])


FIXTURES = """WITH
people(id, name) AS (VALUES (1, 'Ada'::text), (2, 'Bea'), (3, 'Cy')),
assignment(person_id, org_id, start, "end") AS (
    VALUES (1, 10, DATE '2020-01-01', NULL::date),
           (1, 20, DATE '2020-01-01', NULL::date),
           (2, 20, DATE '2010-01-01', DATE '2015-01-01')
),
org(id, name) AS (VALUES (10, 'A'::text), (20, 'B')),
cert(person_id, name) AS (VALUES (1, 'A'::text), (1, 'B'), (2, 'B'))
"""


def compiled(**changes):
    request = {
        "root": "people",
        "relationships": ["person_fk", "org_fk", "cert_fk"],
        "fields": [{"table": "people", "column": "name"}],
        **changes,
    }
    sql = compile_preview(CATALOG, request)["sql"]
    for table in ("people", "assignment", "org", "cert"):
        sql = sql.replace(f'"public"."{table}"', f'"{table}"')
    return FIXTURES + sql


def required_org():
    return {
        "id": "scope",
        "kind": "required",
        "alternatives": [
            {
                "id": "a",
                "conditions": [
                    {"table": "org", "column": "id", "operator": "eq", "value": 10}
                ],
            }
        ],
    }


def test_required_scope_person_rows_do_not_fan_out(execute):
    assert execute(compiled(scopes=[required_org()])) == [["Ada"]]


def test_required_scope_limits_returned_assignment_not_just_person(execute):
    fields = [
        {"table": "people", "column": "name"},
        {"table": "assignment", "column": "org_id"},
    ]
    assert execute(compiled(fields=fields, scopes=[required_org()])) == [["Ada", 10]]


def test_conditional_period_preserves_people_without_current_assignment(execute):
    temporal = {
        "id": "time",
        "kind": "conditional",
        "alternatives": [
            {
                "id": "current",
                "inputs": [
                    {"id": "asof", "type": "date", "defaultValue": "2026-09-05"}
                ],
                "conditions": [
                    {
                        "table": "assignment",
                        "column": "start",
                        "operator": "lte",
                        "parameterId": "asof",
                    },
                    {
                        "table": "assignment",
                        "column": "end",
                        "operator": "gte",
                        "parameterId": "asof",
                        "allowNull": True,
                    },
                ],
            }
        ],
    }
    fields = [
        {"table": "people", "column": "name"},
        {"table": "assignment", "column": "org_id"},
    ]
    rows = execute(compiled(fields=fields, scopes=[temporal]))
    assert sorted(rows, key=lambda row: (row[0], row[1] or 0)) == [
        ["Ada", 10],
        ["Ada", 20],
        ["Bea", None],
        ["Cy", None],
    ]


def test_existence_filter_retains_all_returned_certifications(execute):
    fields = [
        {"table": "people", "column": "name"},
        {"table": "cert", "column": "name"},
    ]
    condition = {"table": "cert", "column": "name", "operator": "eq", "value": "A"}
    rows = execute(
        compiled(
            fields=fields, reportFilters=[{"mode": "exists", "conditions": [condition]}]
        )
    )
    assert sorted(rows) == [["Ada", "A"], ["Ada", "B"]]
    rows = execute(
        compiled(
            fields=fields, reportFilters=[{"mode": "rows", "conditions": [condition]}]
        )
    )
    assert rows == [["Ada", "A"]]


def test_not_exists_returns_people_without_matching_certification(execute):
    condition = {"table": "cert", "column": "name", "operator": "eq", "value": "A"}
    assert sorted(
        execute(
            compiled(reportFilters=[{"mode": "not_exists", "conditions": [condition]}])
        )
    ) == [["Bea"], ["Cy"]]


def certification_summary():
    return [
        *[
            {"id": t["name"], "table": t["name"], "label": t["name"]}
            for t in CATALOG["tables"]
        ],
        {
            "id": "summary",
            "table": "people",
            "label": "Certifications",
            "derivation": {
                "kind": "aggregate",
                "source": "people",
                "groupBy": ["id"],
                "outputs": [
                    {
                        "id": "names",
                        "label": "Names",
                        "operation": "list",
                        "nodeId": "cert",
                        "column": "name",
                        "distinct": True,
                    },
                    {
                        "id": "number",
                        "label": "Number",
                        "operation": "count",
                        "nodeId": "cert",
                        "column": "name",
                    },
                ],
            },
        },
    ]


def test_aggregate_summary_preserves_lists_and_counts_across_outer_fanout(execute):
    fields = [
        {"table": "people", "column": "name"},
        {"table": "summary", "column": "names"},
        {"table": "summary", "column": "number"},
        {"table": "assignment", "column": "org_id"},
    ]
    rows = execute(compiled(nodes=certification_summary(), fields=fields))
    assert sorted(rows, key=lambda row: (row[0], row[3] or 0)) == [
        ["Ada", "A, B", 2, 10],
        ["Ada", "A, B", 2, 20],
        ["Bea", "B", 1, 20],
        ["Cy", None, 0, None],
    ]


def test_summary_applies_conditional_source_parameter_in_postgres(execute):
    rule = {
        "id": "name",
        "kind": "conditional",
        "alternatives": [
            {
                "id": "a",
                "inputs": [{"id": "value", "type": "text", "defaultValue": "B"}],
                "conditions": [
                    {
                        "table": "cert",
                        "column": "name",
                        "operator": "eq",
                        "parameterId": "value",
                    }
                ],
            }
        ],
    }
    fields = [
        {"table": "people", "column": "name"},
        {"table": "summary", "column": "names"},
        {"table": "summary", "column": "number"},
    ]
    rows = execute(
        compiled(nodes=certification_summary(), fields=fields, scopes=[rule])
    )
    assert sorted(rows) == [["Ada", "B", 1], ["Bea", "B", 1], ["Cy", None, 0]]


def test_per_output_temporal_filter_preserves_total_and_owner_rows(execute):
    from copy import deepcopy

    catalog = deepcopy(CATALOG)
    catalog["tables"][-1]["columns"].extend(
        [
            {"name": "effective", "dataType": "date"},
            {"name": "expires", "dataType": "date"},
        ]
    )
    nodes = certification_summary()
    outputs = nodes[-1]["derivation"]["outputs"]
    conditions = [
        {
            "table": "cert",
            "column": "effective",
            "operator": "lte",
            "valueSource": "today",
        },
        {
            "table": "cert",
            "column": "expires",
            "operator": "gte",
            "valueSource": "today",
            "allowNull": True,
        },
    ]
    outputs[0]["conditions"] = conditions
    outputs.append(
        {
            "id": "valid",
            "label": "Valid",
            "operation": "count",
            "nodeId": "cert",
            "column": "name",
            "conditions": conditions,
        }
    )
    request = {
        "root": "people",
        "nodes": nodes,
        "relationships": ["person_fk", "org_fk", "cert_fk"],
        "fields": [
            {"table": "people", "column": "name"},
            {"table": "summary", "column": "number"},
            {"table": "summary", "column": "valid"},
            {"table": "summary", "column": "names"},
        ],
    }
    sql = compile_preview(catalog, request, _today="2026-09-07")["sql"]
    for table in ("people", "cert"):
        sql = sql.replace(f'"public"."{table}"', f'"{table}"')
    fixtures = """WITH
people(id, name) AS (VALUES (1, 'Ada'::text), (2, 'Bea'), (3, 'Cy')),
cert(person_id, name, effective, expires) AS (VALUES
    (1, 'Current'::text, DATE '2020-01-01', DATE '2030-01-01'),
    (1, 'Expired', DATE '2020-01-01', DATE '2021-01-01'),
    (1, 'Future', DATE '2030-01-01', DATE '2040-01-01'),
    (2, 'No expiry', DATE '2020-01-01', NULL::date))
"""
    assert sorted(execute(fixtures + sql)) == [
        ["Ada", 3, 1, "Current"],
        ["Bea", 1, 1, "No expiry"],
        ["Cy", 0, 0, None],
    ]


def calculated_fixture():
    catalog = {"tables":[
        {"name":"people","primaryKey":["id"],"columns":[{"name":name,"dataType":kind} for name,kind in [
            ("id","integer"),("salary","integer"),("allowance","integer"),("big","bigint"),("at","date")]]},
        {"name":"cert","columns":[{"name":"person_id","dataType":"integer"},{"name":"name","dataType":"text"},{"name":"active","dataType":"boolean"}]}],
        "relationships":[{"id":"cert_fk","sourceTable":"cert","targetTable":"people","sourceColumn":"person_id","targetColumn":"id"}]}
    nodes = [{"id":"people","table":"people","label":"People"},{"id":"cert","table":"cert","label":"Certifications"},
        {"id":"summary","table":"cert","label":"Summary","derivation":{"kind":"aggregate","source":"cert","groupBy":["person_id"],
            "connection":{"target":"people","columns":[{"source":"person_id","target":"id"}]},
            "outputs":[{"id":"n","label":"Count","operation":"count","column":"name"},
                {"id":"active","label":"Active","operation":"count","column":"name","conditions":[{"table":"cert","column":"active","operator":"eq","value":True}]}]}}]
    query = {"root":"people","nodes":nodes,"relationships":["cert_fk"],"fields":[{"table":"people","column":"id"},{"table":"summary","column":"n"}]}
    return catalog, query


def calculated_sql(plan):
    sql = plan["sql"].replace('"public"."people"','"people"').replace('"public"."cert"','"cert"').removesuffix(";")
    return """WITH people(id,salary,allowance,big,at) AS (VALUES
        (1,100,20,100::bigint,DATE '2024-01-01'),(2,80,10,200::bigint,DATE '2024-02-01'),(3,NULL,0,NULL::bigint,NULL::date)),
        cert(person_id,name,active) AS (VALUES (1,'CPR'::text,TRUE),(1,'First aid',FALSE),(2,'CPR',TRUE))
    """, sql


@pytest.mark.parametrize("mode",["rows","exists","not_exists"])
def test_calculated_filter_only_certification_count_and_zero_have_postgres_values(execute,mode):
    catalog, query = calculated_fixture()
    query["fields"] = [{"table":"people","column":"id"}]
    query["reportFilters"] = [{"mode":mode,"conditions":[{"table":"summary","column":"n","operator":"eq","value":0}]}]
    plan = compile_preview(catalog,query)
    prefix, sql = calculated_sql(plan)
    assert sorted(execute(prefix+sql)) == ([[1],[2]] if mode=="not_exists" else [[3]])
    assert '"active"' not in sql and "FILTER" not in sql


def test_calculated_row_dependencies_keep_case_null_division_and_authored_order(execute):
    catalog, query = calculated_fixture()
    query["nodes"] = [
        {"id":"downstream","table":"people","label":"Downstream","derivation":{"kind":"row","source":"people","outputs":[
            {"id":"value","label":"Value","operation":"divide","nodeId":"base","column":"total","operand":"allowance"}]}},
        *query["nodes"],
        {"id":"base","table":"people","label":"Base","derivation":{"kind":"row","source":"people","outputs":[
            {"id":"total","label":"Total","operation":"add","column":"salary","operand":"allowance","conditions":[{"table":"people","column":"salary","operator":"gt","value":80}]}]}}]
    query["fields"] = [{"table":"people","column":"id"},{"table":"downstream","column":"value"}]
    prefix, sql = calculated_sql(compile_preview(catalog,query))
    assert sorted(execute(prefix+sql)) == [[1,6],[2,None],[3,None]]
    query["reportFilters"] = [{"mode":"rows","conditions":[{"table":"downstream","column":"value","operator":"not_null"}]}]
    query["nodes"].reverse()
    prefix, sql = calculated_sql(compile_preview(catalog,query))
    assert execute(prefix+sql) == [[1,6]]
    assert "JOIN" not in sql and "NULLIF" in sql and "CASE WHEN" in sql


def test_calculated_row_on_summary_and_reaggregation_preserve_materialized_grain(execute):
    catalog, query = calculated_fixture()
    query["nodes"].insert(0,{"id":"row","table":"people","label":"Row","derivation":{"kind":"row","source":"people","outputs":[
        {"id":"value","label":"Value","operation":"add","nodeId":"summary","column":"n","operand":"allowance"}]}})
    query["fields"] = [{"table":"people","column":"id"},{"table":"row","column":"value"}]
    prefix, sql = calculated_sql(compile_preview(catalog,query))
    assert sorted(execute(prefix+sql)) == [[1,22],[2,11],[3,0]]
    query["fields"] = [{"table":"row","column":"value","aggregate":"sum"}]
    prefix, sql = calculated_sql(compile_preview(catalog,query))
    assert execute(prefix+sql) == [[33]]
    query["fields"].append({"table":"cert","column":"name"})
    with pytest.raises(ValueError,match="materialized grain"):
        compile_preview(catalog,query)
    query["nodes"][0] = {"id":"nested","table":"cert","label":"Nested","derivation":{"kind":"aggregate","source":"summary","groupBy":["person_id"],
        "outputs":[{"id":"n","label":"Nested count","operation":"sum","column":"n"}]}}
    query["fields"] = [{"table":"people","column":"id"},{"table":"nested","column":"n"}]
    prefix, sql = calculated_sql(compile_preview(catalog,query))
    assert sorted(execute(prefix+sql)) == [[1,2],[2,1],[3,None]]
    assert "SUM(COUNT" not in sql


@pytest.mark.parametrize("behavior,expected",[("keep_unmatched",[[1,2],[2,0],[3,0]]),("require_matching",[[1,2]])])
def test_calculated_summary_model_input_is_applied_after_grouping(execute,behavior,expected):
    catalog, query = calculated_fixture()
    query["scopes"] = [{"id":"count","kind":"required","rowBehavior":behavior,"alternatives":[{"id":"enough","inputs":[{"id":"minimum","type":"integer","defaultValue":1}],
        "conditions":[{"table":"summary","column":"n","operator":"gt","parameterId":"minimum"}]}]}]
    plan = compile_preview(catalog,query)
    prefix, sql = calculated_sql(plan)
    assert sorted(execute(prefix+sql)) == expected
    assert plan["parameterValues"] == {"count":{"minimum":1}}
    # The result oracle above distinguishes post-group qualification (count 2)
    # from filtering raw inputs. Both a wrapper and an outer WHERE are valid.
    from sqlglot import exp, parse_one
    statement = parse_one(sql, read="postgres")
    assert any(select.args.get("group") for select in statement.find_all(exp.Select))
    count_filter = next(column for column in statement.find_all(exp.Column) if column.name == "n" and isinstance(column.parent, exp.Coalesce) and column.find_ancestor(exp.Where))
    assert count_filter.find_ancestor(exp.Where)
    assert not count_filter.find_ancestor(exp.Select).args.get("group")


def test_calculated_postgres_type_promotions_match_validated_field_metadata(execute):
    from schemii.schemoo.prototype import model_field_details
    catalog, query = calculated_fixture()
    query["nodes"][-1] = {"id":"types","table":"people","label":"Types","derivation":{"kind":"aggregate","source":"people","groupBy":["id"],"outputs":[
        {"id":"n","label":"N","operation":"count","column":"id"},{"id":"sum","label":"Sum","operation":"sum","column":"salary"},
        {"id":"big","label":"Big","operation":"sum","column":"big"},{"id":"avg","label":"Average","operation":"avg","column":"salary"},
        {"id":"date","label":"Date","operation":"min","column":"at"},{"id":"list","label":"List","operation":"list","column":"id"}]}}
    ids = ["n","sum","big","avg","date","list"]
    query["fields"] = [{"table":"types","column":name} for name in ids]
    prefix, sql = calculated_sql(compile_preview(catalog,query))
    labels = ["N","Sum","Big","Average","Date","List"]
    actual = execute(prefix+"SELECT "+", ".join(f'pg_typeof(q."Types.{label}")::text' for label in labels)+f" FROM ({sql}) AS q LIMIT 1")[0]
    assert actual == [model_field_details(catalog,query,"types",name)["dataType"] for name in ids] == ["bigint","bigint","numeric","numeric","date","text"]


def test_calculated_precision_zoned_timestamp_uses_the_requested_calendar_day(execute):
    from datetime import date
    from sqlglot import parse_one
    from schemii.schemer.dashboard_models import TimeAnalysis
    from schemii.schemer.time_analysis import bucket_expression, source_type
    from schemii.schemoo.models import ModelDefinition
    catalog = {"tables":[{"name":"events","primaryKey":["id"],"columns":[
        {"name":"id","dataType":"integer"},{"name":"at","dataType":"timestamp(6) with time zone"}]}],"relationships":[]}
    query = {"root":"events","nodes":[{"id":"events","table":"events","label":"Events"},
        {"id":"summary","table":"events","label":"Summary","derivation":{"kind":"aggregate","source":"events","groupBy":["id"],"outputs":[
            {"id":"earliest","label":"Earliest","operation":"min","column":"at"}]}}],
        "fields":[{"table":"events","column":"id"},{"table":"summary","column":"earliest"}]}
    definition = ModelDefinition.model_validate({key:value for key,value in query.items() if key!="fields"})
    kind = source_type(catalog,definition,"summary","earliest")
    analysis = TimeAnalysis(table="summary",column="earliest",granularity="day",timezone="America/New_York")
    statement = parse_one(compile_preview(catalog,query)["sql"].replace('"public"."events"','"events"'),read="postgres")
    output = statement.expressions[1]
    output.set("this",bucket_expression(output.this,analysis,kind))
    sql = """WITH events(id,at) AS (VALUES
        (1,CAST('2024-01-01 02:30:00+00' AS timestamp(6) with time zone)),
        (2,CAST('2024-01-01 06:30:00+00' AS timestamp(6) with time zone)))
    """ + statement.sql(dialect="postgres")
    assert sorted(execute(sql)) == [[1,date(2023,12,31)],[2,date(2024,1,1)]]


@pytest.mark.parametrize("explicit,nullable",[(False,False),(True,False),(False,True)])
def test_calculated_hidden_composite_association_keys_project_upstream_outputs(execute,explicit,nullable):
    catalog, query = calculated_fixture()
    groups = ["person_id","n"]
    if nullable:
        groups.append("maybe")
        query["nodes"][-1]["derivation"]["outputs"].append({"id":"maybe","label":"Maybe","operation":"min","column":"name",
            "conditions":[{"table":"cert","column":"active","operator":"eq","value":False}]})
    derivation = {"kind":"aggregate","source":"summary","groupBy":groups,"outputs":[{"id":"again","label":"Again","operation":"sum","column":"n"}]}
    if explicit:
        derivation["connection"] = {"target":"summary","columns":[{"source":column,"target":column} for column in groups]}
    query["nodes"].insert(0,{"id":"nested","table":"cert","label":"Nested","derivation":derivation})
    query["fields"] = [{"table":"nested","column":"again"}]
    prefix, sql = calculated_sql(compile_preview(catalog,query))
    assert sorted(execute(prefix+sql),key=repr) == [[1],[2],[None]]
    query["fields"].append({"table":"summary","column":"n"})
    prefix, visible_sql = calculated_sql(compile_preview(catalog,query))
    assert sorted(execute(prefix+visible_sql),key=repr) == [[1,1],[2,2],[None,0]]
    from sqlglot import exp, parse_one
    assert parse_one(sql,read="postgres").find(exp.Distinct) is None
    assert "SUM(COUNT" not in sql
