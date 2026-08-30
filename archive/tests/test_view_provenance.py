import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.view_provenance import derive_column_provenance, derive_join_provenance, derive_sql_stages


class ViewProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.sources = [{
            "profileId": "local", "database": "demo", "namespace": "sales",
            "relation": "orders", "kind": "table",
            "columns": [
                {"name": "id", "type": "bigint", "ordinal": 1},
                {"name": "subtotal", "type": "numeric", "ordinal": 2},
                {"name": "tax", "type": "numeric", "ordinal": 3},
                {"name": "created_at", "type": "timestamp", "ordinal": 4},
            ],
        }]

    def derive(self, sql, names):
        return derive_column_provenance(
            sql, [{"name": name, "ordinal": index + 1} for index, name in enumerate(names)], self.sources,
            current_namespace="reports", relation_fingerprint="a" * 64,
        )

    def test_classifies_direct_expression_aggregate_constant_and_window_outputs(self):
        result = self.derive("""
            SELECT o.id AS order_id,
                   o.subtotal + o.tax AS gross_total,
                   count(o.id) OVER () AS order_count,
                   'USD' AS currency,
                   row_number() OVER (ORDER BY o.created_at) AS sequence
            FROM sales.orders o
        """, ["order_id", "gross_total", "order_count", "currency", "sequence"])

        self.assertEqual(result["status"], "available")
        self.assertEqual([item["derivation"] for item in result["outputs"]], [
            "direct", "expression", "window", "constant", "window",
        ])
        gross = result["outputs"][1]
        self.assertEqual(gross["expression"]["sql"], "o.subtotal + o.tax")
        self.assertEqual([item["columnName"] for item in gross["inputs"]], ["subtotal", "tax"])

    def test_resolves_cte_lineage_to_verified_physical_columns(self):
        result = self.derive("""
            WITH priced AS (SELECT id, subtotal + tax AS gross FROM sales.orders)
            SELECT id, gross FROM priced
        """, ["id", "gross"])

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["outputs"][0]["inputs"][0]["columnName"], "id")
        self.assertEqual([item["columnName"] for item in result["outputs"][1]["inputs"]], ["subtotal", "tax"])

    def test_unverified_source_columns_remain_partial(self):
        result = self.derive("SELECT missing FROM sales.orders", ["missing"])

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["outputs"][0]["mappingStatus"], "partial")
        self.assertEqual(result["outputs"][0]["inputs"], [])
        self.assertEqual(result["outputs"][0]["reason"], "unresolved_source_column")

    def test_parse_failures_and_missing_outputs_are_explicit(self):
        self.assertEqual(self.derive("not a query", ["value"])["status"], "unavailable")
        self.assertEqual(
            derive_column_provenance(
                "SELECT 1", [], [], current_namespace="public", relation_fingerprint="a" * 64,
            ),
            {"status": "unavailable", "reason": "no_outputs"},
        )

    def test_derives_verified_inner_and_left_join_column_predicates(self):
        sources = [
            self.sources[0],
            {
                "profileId": "local", "database": "demo", "namespace": "sales",
                "relation": "order_items", "kind": "table",
                "columns": [
                    {"name": "order_id", "type": "bigint", "ordinal": 1},
                    {"name": "product_id", "type": "bigint", "ordinal": 2},
                    {"name": "active", "type": "boolean", "ordinal": 3},
                ],
            },
            {
                "profileId": "local", "database": "demo", "namespace": "sales",
                "relation": "products", "kind": "table",
                "columns": [{"name": "id", "type": "bigint", "ordinal": 1}],
            },
        ]

        result = derive_join_provenance(
            """
            SELECT o.id
            FROM sales.orders o
            JOIN sales.order_items i ON i.order_id = o.id
            LEFT JOIN sales.products p ON p.id = i.product_id
            """,
            sources, current_namespace="reports", relation_fingerprint="a" * 64,
        )

        self.assertEqual(result["status"], "available")
        self.assertEqual([item["joinType"] for item in result["joins"]], ["inner", "left"])
        self.assertEqual([item["queryScope"] for item in result["joins"]], ["root", "root"])
        first = result["joins"][0]["predicates"][0]
        self.assertEqual((first["left"]["relation"], first["left"]["columnName"]), ("orders", "id"))
        self.assertEqual((first["right"]["relation"], first["right"]["columnName"]), ("order_items", "order_id"))
        self.assertEqual(result["joins"][1]["rightReferenceAlias"], "p")
        self.assertRegex(result["fingerprint"], r"^[0-9a-f]{64}$")

    def test_join_analysis_distinguishes_root_and_nested_query_scopes(self):
        sources = [
            self.sources[0],
            {
                "profileId": "local", "database": "demo", "namespace": "sales",
                "relation": "order_items", "kind": "table",
                "columns": [{"name": "order_id", "type": "bigint", "ordinal": 1}],
            },
            {
                "profileId": "local", "database": "demo", "namespace": "sales",
                "relation": "products", "kind": "table",
                "columns": [{"name": "id", "type": "bigint", "ordinal": 1}],
            },
        ]

        result = derive_join_provenance(
            """
            SELECT o.id
            FROM sales.orders o
            JOIN sales.products p ON p.id = o.id
            WHERE EXISTS (
                SELECT 1 FROM sales.order_items i
                JOIN sales.products nested_product ON nested_product.id = i.order_id
            )
            """,
            sources, current_namespace="reports", relation_fingerprint="a" * 64,
        )

        self.assertEqual([join["queryScope"] for join in result["joins"]], ["root", "nested"])

    def test_join_analysis_keeps_complete_condition_when_only_keys_are_representable(self):
        sources = [
            self.sources[0],
            {
                "profileId": "local", "database": "demo", "namespace": "sales",
                "relation": "order_items", "kind": "table",
                "columns": [
                    {"name": "order_id", "type": "bigint", "ordinal": 1},
                    {"name": "active", "type": "boolean", "ordinal": 2},
                ],
            },
        ]

        result = derive_join_provenance(
            "SELECT o.id FROM sales.orders o LEFT JOIN sales.order_items i ON i.order_id = o.id AND i.active = true",
            sources, current_namespace="reports", relation_fingerprint="a" * 64,
        )

        self.assertEqual(result["status"], "partial")
        join = result["joins"][0]
        self.assertEqual(join["mappingStatus"], "partial")
        self.assertIn("unsupported_predicate_shape", join["reasons"])
        self.assertIn("active", join["condition"]["sql"])
        self.assertEqual(len(join["predicates"]), 1)

    def test_using_is_normalized_and_natural_join_remains_explicitly_partial(self):
        sources = [
            self.sources[0],
            {
                "profileId": "local", "database": "demo", "namespace": "sales",
                "relation": "archived_orders", "kind": "table",
                "columns": [
                    {"name": "id", "type": "bigint", "ordinal": 1},
                    {"name": "subtotal", "type": "numeric", "ordinal": 2},
                ],
            },
        ]
        using = derive_join_provenance(
            "SELECT o.id FROM sales.orders o JOIN sales.archived_orders a USING (id)",
            sources, current_namespace="reports", relation_fingerprint="a" * 64,
        )
        natural = derive_join_provenance(
            "SELECT o.id FROM sales.orders o NATURAL JOIN sales.archived_orders a",
            sources, current_namespace="reports", relation_fingerprint="a" * 64,
        )

        self.assertEqual(using["status"], "available")
        self.assertEqual(using["joins"][0]["conditionKind"], "using")
        self.assertEqual(len(using["joins"][0]["predicates"]), 1)
        self.assertEqual(natural["status"], "partial")
        self.assertIn("unsupported_natural_join", natural["joins"][0]["reasons"])

    def stages(self, sql, *, fingerprint="a" * 64, sources=None):
        return derive_sql_stages(
            sql, self.sources if sources is None else sources,
            current_namespace="reports", relation_fingerprint=fingerprint,
        )

    def test_sql_stages_describe_chained_ctes_and_repeated_reference_aliases(self):
        result = self.stages("""
            WITH base AS (
                SELECT id, subtotal FROM sales.orders WHERE subtotal > 0
            ), priced AS (
                SELECT b.id, b.subtotal FROM base b
            )
            SELECT left_side.id
            FROM priced left_side
            JOIN priced right_side ON right_side.id = left_side.id
        """)

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["orderSemantics"], "syntactic_dependency")
        self.assertEqual(
            [(stage["kind"], stage.get("name")) for stage in result["stages"]],
            [("cte", "base"), ("cte", "priced"), ("query_block", None)],
        )
        base, priced, query_block = result["stages"]
        self.assertEqual(priced["dependsOnStageIds"], [base["stageId"]])
        self.assertEqual(priced["inputs"], [{
            "inputOrdinal": 1,
            "referenceAlias": "b",
            "source": {"type": "stage", "stageId": base["stageId"]},
        }])
        self.assertEqual(base["wherePredicates"][0]["expression"]["sql"], "subtotal > 0")
        self.assertEqual(query_block["dependsOnStageIds"], [priced["stageId"]])
        self.assertEqual(
            [item["referenceAlias"] for item in query_block["inputs"]],
            ["left_side", "right_side"],
        )

    def test_sql_stages_describe_derived_table_outputs_inputs_and_local_predicates(self):
        result = self.stages("""
            SELECT d.id
            FROM (
                SELECT o.id AS order_id, count(*) AS item_count
                FROM sales.orders o
                WHERE o.subtotal > 10
                GROUP BY o.id
                HAVING count(*) > 1
            ) AS d(id, total)
        """)

        self.assertEqual(result["status"], "available")
        stage = result["stages"][0]
        self.assertEqual(stage["kind"], "derived_table")
        self.assertEqual(stage["name"], "d")
        self.assertEqual([column["name"] for column in stage["outputColumns"]], ["id", "total"])
        self.assertTrue(all(column["nameSource"] == "column_alias_list" for column in stage["outputColumns"]))
        self.assertEqual(stage["inputs"][0]["referenceAlias"], "o")
        self.assertEqual(stage["inputs"][0]["source"]["profileId"], "local")
        self.assertEqual(stage["inputs"][0]["source"]["relation"], "orders")
        self.assertIn("subtotal", stage["wherePredicates"][0]["expression"]["sql"])
        self.assertIn("COUNT", stage["havingPredicates"][0]["expression"]["sql"])
        query_block = result["stages"][1]
        self.assertEqual(query_block["kind"], "query_block")
        self.assertEqual(query_block["dependsOnStageIds"], [stage["stageId"]])

    def test_sql_stages_keep_repeated_cte_aliases_on_input_edges(self):
        result = self.stages("""
            WITH base AS (SELECT id FROM sales.orders), paired AS (
                SELECT l.id FROM base l JOIN base r ON l.id = r.id
            )
            SELECT id FROM paired
        """)

        base, paired, query_block = result["stages"]
        self.assertEqual([item["referenceAlias"] for item in paired["inputs"]], ["l", "r"])
        self.assertEqual(
            [item["source"]["stageId"] for item in paired["inputs"]],
            [base["stageId"], base["stageId"]],
        )
        self.assertEqual(paired["joinPredicates"][0]["expression"]["sql"], "l.id = r.id")
        self.assertEqual(query_block["dependsOnStageIds"], [paired["stageId"]])

    def test_sql_stages_root_query_block_owns_three_verified_relation_inputs(self):
        sources = [
            self.sources[0],
            {
                "profileId": "local", "database": "demo", "namespace": "sales",
                "relation": "order_items", "kind": "table",
                "columns": [
                    {"name": "order_id", "type": "bigint", "ordinal": 1},
                    {"name": "product_id", "type": "bigint", "ordinal": 2},
                    {"name": "quantity", "type": "integer", "ordinal": 3},
                ],
            },
            {
                "profileId": "local", "database": "demo", "namespace": "catalog",
                "relation": "products", "kind": "foreign_table",
                "columns": [
                    {"name": "id", "type": "bigint", "ordinal": 1},
                    {"name": "active", "type": "boolean", "ordinal": 2},
                ],
            },
        ]
        result = self.stages("""
            SELECT o.id AS order_id, sum(i.quantity) AS units
            FROM sales.orders AS o
            JOIN sales.order_items AS i ON i.order_id = o.id
            LEFT JOIN catalog.products AS p ON p.id = i.product_id
            WHERE p.active = true AND o.subtotal > 0
            GROUP BY o.id
            HAVING sum(i.quantity) > 1
        """, sources=sources)

        self.assertEqual(result["status"], "available")
        self.assertEqual(len(result["stages"]), 1)
        stage = result["stages"][0]
        self.assertEqual(stage["kind"], "query_block")
        self.assertEqual(stage["parentStageId"], None)
        self.assertEqual(stage["lifetime"], "query")
        self.assertEqual(stage["dependsOnStageIds"], [])
        self.assertEqual([item["referenceAlias"] for item in stage["inputs"]], ["o", "i", "p"])
        self.assertEqual(
            [(item["source"]["namespace"], item["source"]["relation"], item["source"]["kind"])
             for item in stage["inputs"]],
            [("sales", "orders", "table"), ("sales", "order_items", "table"),
             ("catalog", "products", "foreign_table")],
        )
        self.assertEqual(
            [item["expression"]["sql"] for item in stage["joinPredicates"]],
            ["i.order_id = o.id", "p.id = i.product_id"],
        )
        self.assertEqual(
            [item["expression"]["sql"] for item in stage["wherePredicates"]],
            ["p.active = TRUE", "o.subtotal > 0"],
        )
        self.assertEqual(stage["havingPredicates"][0]["expression"]["sql"], "SUM(i.quantity) > 1")
        self.assertEqual(
            [item["expression"]["sql"] for item in stage["outputColumns"]],
            ["o.id", "SUM(i.quantity)"],
        )

    def test_sql_stages_root_query_block_consumes_chained_cte_and_derived_stage(self):
        result = self.stages("""
            WITH base AS (SELECT id, subtotal FROM sales.orders),
                 priced AS (SELECT b.id, b.subtotal FROM base AS b)
            SELECT d.id
            FROM (SELECT p.id FROM priced AS p WHERE p.subtotal > 5) AS d
        """)

        base, priced, derived, query_block = result["stages"]
        self.assertEqual(
            [stage["kind"] for stage in result["stages"]],
            ["cte", "cte", "derived_table", "query_block"],
        )
        self.assertEqual(priced["dependsOnStageIds"], [base["stageId"]])
        self.assertEqual(derived["dependsOnStageIds"], [priced["stageId"]])
        self.assertEqual(query_block["dependsOnStageIds"], [derived["stageId"]])
        self.assertEqual(query_block["inputs"], [{
            "inputOrdinal": 1,
            "referenceAlias": "d",
            "source": {"type": "stage", "stageId": derived["stageId"]},
        }])
        self.assertEqual(query_block["parentStageId"], None)
        self.assertNotIn("WITH base", query_block["sql"]["sql"])
        self.assertIn("FROM (SELECT", query_block["sql"]["sql"])

    def test_sql_stages_query_block_predicates_are_isolated_from_nested_scopes(self):
        result = self.stages("""
            WITH filtered AS (
                SELECT id, subtotal FROM sales.orders WHERE subtotal > 100
            )
            SELECT d.id
            FROM (SELECT f.id, f.subtotal FROM filtered AS f WHERE f.subtotal < 500) AS d
            JOIN sales.orders AS o ON o.id = d.id
            WHERE d.subtotal <> 250
            HAVING count(*) > 0
        """)

        cte, derived, query_block = result["stages"]
        self.assertEqual(cte["wherePredicates"][0]["expression"]["sql"], "subtotal > 100")
        self.assertEqual(derived["wherePredicates"][0]["expression"]["sql"], "f.subtotal < 500")
        self.assertEqual(
            [item["expression"]["sql"] for item in query_block["joinPredicates"]],
            ["o.id = d.id"],
        )
        self.assertEqual(
            [item["expression"]["sql"] for item in query_block["wherePredicates"]],
            ["d.subtotal <> 250"],
        )
        self.assertEqual(
            [item["expression"]["sql"] for item in query_block["havingPredicates"]],
            ["COUNT(*) > 0"],
        )

    def test_sql_stages_simple_select_is_one_query_block(self):
        result = self.stages("SELECT o.id, o.subtotal + o.tax AS total FROM sales.orders AS o")

        self.assertEqual(result["status"], "available")
        self.assertEqual(len(result["stages"]), 1)
        stage = result["stages"][0]
        self.assertEqual(stage["kind"], "query_block")
        self.assertEqual(stage["displayOrdinal"], 1)
        self.assertEqual(stage["parentStageId"], None)
        self.assertEqual(stage["recursive"], False)
        self.assertEqual(stage["inputs"][0]["referenceAlias"], "o")
        self.assertEqual(
            [item["expression"]["sql"] for item in stage["outputColumns"]],
            ["o.id", "o.subtotal + o.tax"],
        )

    def test_sql_stages_recursive_and_unsupported_inputs_are_partial(self):
        recursive = self.stages("""
            WITH RECURSIVE numbers(n) AS (
                SELECT 1 UNION ALL SELECT n + 1 FROM numbers WHERE n < 3
            )
            SELECT n FROM numbers
        """)
        unsupported = self.stages("""
            WITH generated AS (SELECT value FROM generate_series(1, 3) AS value)
            SELECT value FROM generated
        """)

        self.assertEqual(recursive["status"], "partial")
        self.assertTrue(recursive["stages"][0]["recursive"])
        self.assertIn("recursive_query", recursive["stages"][0]["reasons"])
        self.assertEqual(unsupported["status"], "partial")
        self.assertIn("unresolved_relation", unsupported["reasons"])

    def test_sql_stage_ids_and_fingerprint_are_deterministic_and_relation_independent(self):
        sql = "WITH selected AS (SELECT id FROM sales.orders) SELECT id FROM selected"
        first = self.stages(sql, fingerprint="a" * 64)
        repeated = self.stages(sql, fingerprint="a" * 64)
        other_relation = self.stages(sql, fingerprint="b" * 64)

        self.assertEqual(first, repeated)
        self.assertEqual(first["fingerprint"], other_relation["fingerprint"])
        self.assertEqual(first["stages"][0]["stageId"], other_relation["stages"][0]["stageId"])
        self.assertEqual(
            [stage["kind"] for stage in first["stages"]], ["cte", "query_block"],
        )
        self.assertEqual(
            first["stages"][1]["dependsOnStageIds"], [first["stages"][0]["stageId"]],
        )
        self.assertNotEqual(first["relationFingerprint"], other_relation["relationFingerprint"])
        self.assertRegex(first["fingerprint"], r"^[0-9a-f]{64}$")

    def test_sql_stages_parser_failure_is_explicitly_unavailable(self):
        result = self.stages("SELECT (")

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "analysis_failed")
        self.assertEqual(result["stages"], [])
        self.assertRegex(result["fingerprint"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
