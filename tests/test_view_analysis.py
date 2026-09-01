from __future__ import annotations

import pytest

from schemii.common.postgres.view_analysis import (
    ViewDefinitionError,
    analyze_view_definition,
)
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


def test_analysis_derives_inputs_transformations_and_output_lineage() -> None:
    analysis = analyze_view_definition(
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
    with pytest.raises(ViewDefinitionError) as invalid:
        analyze_view_definition(definition)
    assert invalid.value.code == code


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
