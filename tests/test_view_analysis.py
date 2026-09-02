from __future__ import annotations

import pytest

from schemii.common.postgres.query_analysis import (
    QueryDefinitionError,
    analyze_query_definition,
)
from schemii.common.postgres.query_models import QueryAnalysis
from schemii.schemii.designs.models import (
    DesignViewAnalysisRequest,
    SchemiiDesignContent,
)
from schemii.schemii.designs.view_analysis import analyze_design_view


TABLE_ID = "table_" + "a" * 32
COLUMN_ID = "column_" + "b" * 32
VIEW_ID = "view_" + "c" * 32
CONSUMER_ID = "view_" + "d" * 32


def relations() -> list[dict[str, object]]:
    return [
        {
            "namespace": "desired",
            "name": "orders",
            "kind": "table",
            "columns": [
                {"name": "customer_id", "data_type": "uuid"},
                {"name": "total", "data_type": "numeric"},
                {"name": "state", "data_type": "text"},
            ],
        },
        {
            "namespace": "desired",
            "name": "customers",
            "kind": "table",
            "columns": [
                {"name": "id", "data_type": "uuid"},
                {"name": "name", "data_type": "text"},
            ],
        },
    ]


def commerce_relations() -> list[dict[str, object]]:
    return [
        {
            "namespace": "desired",
            "name": "orders",
            "kind": "table",
            "columns": [
                {"name": "id", "data_type": "uuid"},
                {"name": "customer_id", "data_type": "uuid"},
                {"name": "ordered_at", "data_type": "timestamp with time zone"},
                {"name": "status", "data_type": "text"},
                {"name": "total", "data_type": "numeric(12,2)"},
            ],
        },
        {
            "namespace": "desired",
            "name": "customers",
            "kind": "table",
            "columns": [
                {"name": "id", "data_type": "uuid"},
                {"name": "name", "data_type": "text"},
                {"name": "region", "data_type": "text"},
                {"name": "status", "data_type": "text"},
            ],
        },
        {
            "namespace": "desired",
            "name": "order_items",
            "kind": "table",
            "columns": [
                {"name": "order_id", "data_type": "uuid"},
                {"name": "product_id", "data_type": "uuid"},
                {"name": "quantity", "data_type": "integer"},
                {"name": "unit_price", "data_type": "numeric(12,2)"},
                {"name": "discount_amount", "data_type": "numeric(12,2)"},
            ],
        },
        {
            "namespace": "desired",
            "name": "products",
            "kind": "table",
            "columns": [
                {"name": "id", "data_type": "uuid"},
                {"name": "category", "data_type": "text"},
                {"name": "active", "data_type": "boolean"},
            ],
        },
        {
            "namespace": "desired",
            "name": "payments",
            "kind": "table",
            "columns": [
                {"name": "order_id", "data_type": "uuid"},
                {"name": "status", "data_type": "text"},
                {"name": "amount", "data_type": "numeric(12,2)"},
                {"name": "paid_at", "data_type": "timestamp with time zone"},
            ],
        },
    ]


COMPLEX_VIEW_SQL = """
WITH order_profitability AS (
    SELECT
        o.id AS order_id,
        o.customer_id,
        c.region,
        c.name AS customer_name,
        CONCAT(c.region, ' / ', item_rollup.category_mix) AS customer_market,
        item_rollup.gross_item_value,
        item_rollup.discount_total,
        item_rollup.product_count,
        payment_rollup.captured_total,
        payment_rollup.last_paid_at,
        item_rollup.gross_item_value
            - item_rollup.discount_total
            - GREATEST(o.total - payment_rollup.captured_total, 0)
            AS recognized_revenue,
        CASE
            WHEN payment_rollup.captured_total >= o.total THEN 'settled'
            ELSE 'partial'
        END AS payment_state
    FROM orders AS o
    INNER JOIN customers AS c
        ON c.id = o.customer_id
    INNER JOIN (
        SELECT
            oi.order_id,
            SUM(oi.quantity * oi.unit_price) AS gross_item_value,
            SUM(oi.discount_amount) AS discount_total,
            COUNT(DISTINCT pr.id) AS product_count,
            STRING_AGG(DISTINCT pr.category, ', ' ORDER BY pr.category)
                AS category_mix
        FROM order_items AS oi
        INNER JOIN products AS pr
            ON pr.id = oi.product_id
        WHERE pr.active = TRUE
        GROUP BY oi.order_id
        HAVING SUM(oi.quantity * oi.unit_price - oi.discount_amount) > 0
    ) AS item_rollup
        ON item_rollup.order_id = o.id
    LEFT JOIN (
        SELECT
            p.order_id,
            COALESCE(
                SUM(p.amount) FILTER (WHERE p.status = 'captured'),
                0
            ) AS captured_total,
            MAX(p.paid_at) FILTER (WHERE p.status = 'captured') AS last_paid_at,
            COUNT(*) FILTER (WHERE p.status = 'captured') AS capture_events
        FROM payments AS p
        GROUP BY p.order_id
    ) AS payment_rollup
        ON payment_rollup.order_id = o.id
    WHERE c.status = 'active'
      AND o.status IN ('paid', 'shipped', 'completed')
      AND o.ordered_at >= CURRENT_DATE - INTERVAL '365 days'
      AND payment_rollup.capture_events > 0
)
SELECT
    op.region,
    op.customer_market,
    COUNT(DISTINCT op.customer_id) AS customer_count,
    COUNT(op.order_id) AS order_count,
    SUM(op.gross_item_value - op.discount_total) AS merchandise_net,
    SUM(op.recognized_revenue) AS recognized_revenue,
    SUM(op.captured_total) AS captured_revenue,
    SUM(op.recognized_revenue)
        / NULLIF(SUM(op.gross_item_value - op.discount_total), 0)
        AS recognition_rate,
    AVG(op.product_count) AS average_products_per_order,
    MAX(op.last_paid_at) AS last_payment_at
FROM order_profitability AS op
WHERE op.payment_state = 'settled'
GROUP BY op.region, op.customer_market
HAVING SUM(op.recognized_revenue) > 100
ORDER BY recognized_revenue DESC, customer_count DESC
LIMIT 50
"""


def test_analysis_derives_inputs_transformations_and_output_lineage() -> None:
    analysis = analyze_query_definition(
        """
        WITH paid AS (
          SELECT customer_id, total FROM orders WHERE state = 'paid'
        )
        SELECT c.id AS customer_id, c.name, SUM(p.total) AS lifetime_value
        FROM customers c JOIN paid p ON p.customer_id = c.id
        GROUP BY c.id, c.name
        ORDER BY lifetime_value DESC
        """,
        relations(),
    )

    assert QueryAnalysis.model_validate(analysis).query_steps
    assert analysis["status"] == "available"
    assert {source["name"] for source in analysis["sources"]} == {"orders", "customers"}
    assert [item["kind"] for item in analysis["transformations"]] == [
        "stages",
        "joins",
        "filters",
        "groups",
        "aggregates",
        "sorts",
    ]
    assert analysis["outputs"][0] == {
        "ordinal": 1,
        "name": "customer_id",
        "data_type": "uuid",
        "derivation": "direct",
        "expression": "c.id",
        "inputs": [{"source": "customers", "column": "id", "resolved": True}],
    }
    assert analysis["outputs"][2]["derivation"] == "aggregate"
    assert analysis["outputs"][2]["inputs"] == [
        {"source": "paid", "column": "total", "resolved": True}
    ]
    assert analysis["stages"] == ["paid"]
    assert analysis["joins"] == [
        {
            "join_type": "INNER",
            "target": "paid",
            "alias": "p",
            "expression": "p.customer_id = c.id",
            "inputs": [
                {"source": "paid", "column": "customer_id", "resolved": True},
                {"source": "customers", "column": "id", "resolved": True},
            ],
            "scope": None,
        }
    ]
    assert analysis["row_filters"] == [
        {
            "expression": "state = 'paid'",
            "inputs": [
                {"source": "orders", "column": "state", "resolved": True}
            ],
            "scope": "paid",
        }
    ]
    assert [item["expression"] for item in analysis["grouping"]] == [
        "c.id",
        "c.name",
    ]
    assert analysis["ordering"] == [
        {"expression": "lifetime_value DESC", "inputs": [], "scope": None}
    ]
    assert "SUM(p.total) AS lifetime_value" in analysis["formatted_sql"]
    customers = next(source for source in analysis["sources"] if source["name"] == "customers")
    assert next(column for column in customers["columns"] if column["name"] == "id")["uses"] == [
        "read",
        "output",
        "join",
        "group",
    ]


@pytest.mark.parametrize(
    ("definition", "code"),
    [
        ("CREATE VIEW example AS SELECT 1", "unsupported_statement"),
        ("SELECT 1; SELECT 2", "multiple_statements"),
        ("SELECT 1 INTO new_table", "select_into"),
        (" ", "empty_definition"),
    ],
)
def test_analysis_accepts_only_one_select_query_body(definition: str, code: str) -> None:
    with pytest.raises(QueryDefinitionError) as invalid:
        analyze_query_definition(definition)
    assert invalid.value.code == code


def test_analysis_reports_each_set_operation_once_and_preserves_all() -> None:
    analysis = analyze_query_definition(
        "SELECT 1 AS value UNION ALL SELECT 2 INTERSECT SELECT 3"
    )

    assert analysis["set_operations"] == ["INTERSECT", "UNION ALL"]
    sets = next(
        item for item in analysis["transformations"] if item["kind"] == "sets"
    )
    assert sets == {
        "kind": "sets",
        "count": 2,
        "items": ["INTERSECT", "UNION ALL"],
        "sql": None,
    }


def test_analysis_keeps_cte_grain_and_aggregate_filters_in_their_query_scope() -> None:
    analysis = analyze_query_definition(
        """
        WITH paid_orders AS (
          SELECT o.customer_id,
                 SUM(o.total) FILTER (WHERE o.state = 'paid') AS paid_total
          FROM orders o
          WHERE o.total > 0
          GROUP BY o.customer_id
        )
        SELECT p.customer_id, SUM(p.paid_total) AS lifetime_value
        FROM paid_orders p
        GROUP BY p.customer_id
        HAVING SUM(p.paid_total) > 100
        """,
        relations(),
    )

    assert analysis["row_filters"] == [
        {
            "expression": "o.total > 0",
            "inputs": [{"source": "orders", "column": "total", "resolved": True}],
            "scope": "paid_orders",
        }
    ]
    assert analysis["aggregate_filters"] == [
        {
            "expression": "o.state = 'paid'",
            "inputs": [{"source": "orders", "column": "state", "resolved": True}],
            "scope": "paid_orders",
        }
    ]
    assert [(item["expression"], item["scope"]) for item in analysis["grouping"]] == [
        ("p.customer_id", None),
        ("o.customer_id", "paid_orders"),
    ]
    assert analysis["filter_count"] == 1


def test_analysis_builds_dependency_ordered_query_steps_and_column_roles() -> None:
    analysis = analyze_query_definition(
        """
        WITH order_totals AS (
          SELECT o.customer_id, SUM(o.total) AS revenue
          FROM orders o
          INNER JOIN customers buyer ON buyer.id = o.customer_id
          WHERE o.state = 'paid' AND buyer.name IS NOT NULL
          GROUP BY o.customer_id
          ORDER BY revenue DESC
        )
        SELECT c.name, totals.revenue
        FROM order_totals totals
        LEFT JOIN customers c ON c.id = totals.customer_id
        WHERE c.name <> 'Anonymous'
        ORDER BY totals.revenue DESC
        """,
        relations(),
    )

    steps = analysis["query_steps"]
    assert [(step["kind"], step["result_name"]) for step in steps] == [
        ("cte", "order_totals"),
        ("final", "query result"),
    ]
    cte = steps[0]
    assert [(item["name"], item["reference"]) for item in cte["participants"]] == [
        ("orders", "o"),
        ("customers", "buyer"),
    ]
    buyer_name = next(
        column
        for participant in cte["participants"]
        if participant["reference"] == "buyer"
        for column in participant["columns"]
        if column["name"] == "name"
    )
    assert buyer_name == {
        "name": "name",
        "data_type": "text",
        "roles": ["filter"],
        "filter_only": True,
    }
    assert cte["joins"][0]["join_type"] == "INNER"
    assert [output["name"] for output in cte["outputs"]] == [
        "customer_id",
        "revenue",
    ]
    final = steps[1]
    assert [(item["name"], item["kind"]) for item in final["participants"]] == [
        ("order_totals", "intermediate"),
        ("customers", "table"),
    ]
    assert final["joins"][0]["join_type"] == "LEFT"
    assert [output["name"] for output in final["outputs"]] == ["name", "revenue"]


def test_analysis_places_derived_subquery_before_its_outer_query() -> None:
    analysis = analyze_query_definition(
        """
        SELECT summary.customer_id, c.name
        FROM (
          SELECT customer_id FROM orders WHERE state = 'paid'
        ) summary
        JOIN customers c ON c.id = summary.customer_id
        """,
        relations(),
    )

    assert [(step["kind"], step["result_name"]) for step in analysis["query_steps"]] == [
        ("derived_table", "summary"),
        ("final", "query result"),
    ]


def test_analysis_propagates_complex_subquery_types_and_cross_source_lineage() -> None:
    analysis = analyze_query_definition(COMPLEX_VIEW_SQL, commerce_relations())

    assert analysis["status"] == "available"
    assert analysis["warnings"] == []
    assert [(step["kind"], step["result_name"]) for step in analysis["query_steps"]] == [
        ("derived_table", "item_rollup"),
        ("derived_table", "payment_rollup"),
        ("cte", "order_profitability"),
        ("final", "query result"),
    ]

    item_rollup, payment_rollup, profitability, final = analysis["query_steps"]
    assert {output["name"]: output["data_type"] for output in item_rollup["outputs"]} == {
        "order_id": "uuid",
        "gross_item_value": "DECIMAL(12, 2)",
        "discount_total": "DECIMAL(12, 2)",
        "product_count": "BIGINT",
        "category_mix": "VARCHAR",
    }
    assert payment_rollup["aggregate_filters"] == [
        {
            "expression": "p.status = 'captured'",
            "inputs": [
                {"source": "p", "column": "status", "resolved": True},
            ],
            "scope": "payment_rollup",
        }
    ]
    assert {output["name"]: output["data_type"] for output in payment_rollup["outputs"]} == {
        "order_id": "uuid",
        "captured_total": "DECIMAL(12, 2)",
        "last_paid_at": "TIMESTAMPTZ",
        "capture_events": "BIGINT",
    }

    assert len(profitability["joins"]) == 3
    customer_market = next(
        output for output in profitability["outputs"] if output["name"] == "customer_market"
    )
    assert customer_market["data_type"] == "VARCHAR"
    assert {item["source"] for item in customer_market["inputs"]} == {
        "c",
        "item_rollup",
    }
    recognized_revenue = next(
        output
        for output in profitability["outputs"]
        if output["name"] == "recognized_revenue"
    )
    assert recognized_revenue["data_type"] == "DECIMAL(12, 2)"
    assert {item["source"] for item in recognized_revenue["inputs"]} == {
        "item_rollup",
        "o",
        "payment_rollup",
    }

    final_types = {output["name"]: output["data_type"] for output in final["outputs"]}
    assert final_types["customer_count"] == "BIGINT"
    assert final_types["recognition_rate"] == "DECIMAL"
    assert final_types["last_payment_at"] == "TIMESTAMPTZ"
    assert [item["expression"] for item in final["ordering"]] == [
        "recognized_revenue DESC",
        "customer_count DESC",
    ]


def test_design_analysis_derives_downstream_consumers_without_persisting_lineage() -> None:
    content = SchemiiDesignContent.model_validate(
        {
            "tables": [
                {
                    "id": TABLE_ID,
                    "name": "orders",
                    "columns": [
                        {
                            "id": COLUMN_ID,
                            "name": "total",
                            "dataType": "numeric",
                            "nullable": False,
                        }
                    ],
                }
            ],
            "views": [
                {
                    "id": VIEW_ID,
                    "name": "order_totals",
                    "kind": "view",
                    "definition": "SELECT total FROM orders",
                },
                {
                    "id": CONSUMER_ID,
                    "name": "positive_order_totals",
                    "kind": "materialized_view",
                    "definition": "SELECT total FROM order_totals WHERE total > 0",
                    "populateOnCreate": False,
                },
            ],
        }
    )
    analysis = analyze_design_view(
        content,
        DesignViewAnalysisRequest(
            view_id=VIEW_ID,
            name="order_totals",
            definition="SELECT total FROM orders",
        ),
    )

    assert analysis.outputs[0].data_type == "numeric"
    assert [consumer.name for consumer in analysis.consumers] == [
        "positive_order_totals"
    ]
    assert content.views[0].model_dump().keys() == {
        "id",
        "name",
        "kind",
        "definition",
        "populate_on_create",
    }
