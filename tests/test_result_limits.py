import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.result_limits import ResultLimitError, ResultLimiter, ResultLimits, fit_serialized_envelope, json_utf8_size, truncate_utf8


class ResultLimitTests(unittest.TestCase):
    def test_envelope_fitting_counts_exact_serialized_metadata_and_rows(self):
        rows = [["x" * 20], ["y" * 20], ["z" * 20]]
        envelope = lambda values: {"metadata": "m" * 20, "rows": values, "count": len(values)}
        maximum = json_utf8_size(envelope(rows[:2]))
        fitted, document = fit_serialized_envelope(rows, envelope, maximum)
        self.assertEqual(fitted, rows[:2])
        self.assertEqual(json_utf8_size(document), maximum)

        with self.assertRaises(ResultLimitError) as caught:
            fit_serialized_envelope([], lambda values: {"metadata": "too large", "rows": values}, 10)
        self.assertEqual(caught.exception.code, "result_metadata_too_large")

    def test_utf8_truncation_never_splits_a_code_point(self):
        self.assertEqual(truncate_utf8("a🙂b", 5), "a🙂")
        limiter = ResultLimiter(ResultLimits(max_cell_bytes=8, max_row_bytes=32, max_result_bytes=64))
        events = []
        value = limiter.cell("🙂🙂🙂", events=events)
        self.assertEqual(value, "🙂")
        self.assertLessEqual(json_utf8_size(value), 8)
        self.assertEqual(events[0]["policy"], "truncate")
        self.assertEqual(events[0]["actual"], 14)

        events = []
        escaped = limiter.cell("\\" * 10, events=events)
        self.assertEqual(escaped, "\\" * 3)
        self.assertLessEqual(json_utf8_size(escaped), 8)

    def test_collection_cardinality_is_truncated_with_structured_event(self):
        limiter = ResultLimiter(ResultLimits(max_collection_items=2))
        events = []
        self.assertEqual(limiter.cell([1, 2, 3], events=events), [1, 2])
        self.assertEqual(events, [{
            "code": "result_collection_truncated", "policy": "truncate", "path": "$",
            "limit": 2, "actual": 3,
        }])

    def test_nesting_cell_and_row_overflow_are_structured_rejections(self):
        nesting = ResultLimiter(ResultLimits(max_nesting=2))
        with self.assertRaises(ResultLimitError) as caught:
            nesting.cell([[[1]]])
        self.assertEqual(caught.exception.to_dict()["code"], "result_nesting_too_deep")
        self.assertEqual(caught.exception.details["policy"], "reject")

        cell = ResultLimiter(ResultLimits(max_cell_bytes=5, max_row_bytes=100, max_result_bytes=100))
        with self.assertRaises(ResultLimitError) as caught:
            cell.cell({"key": 1})
        self.assertEqual(caught.exception.code, "result_cell_too_large")

        row = ResultLimiter(ResultLimits(max_cell_bytes=20, max_row_bytes=8, max_result_bytes=100))
        with self.assertRaises(ResultLimitError) as caught:
            row.row(["abc", "def"])
        self.assertEqual(caught.exception.code, "result_row_too_large")

    def test_total_and_row_count_limits_truncate_whole_rows(self):
        limiter = ResultLimiter(ResultLimits(max_cell_bytes=20, max_row_bytes=30, max_result_bytes=11))
        result = limiter.rows([{"v": "one"}, {"v": "two"}], ["v"], max_rows=5)
        self.assertEqual(result["rows"], [["one"]])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["limitEvents"][-1]["code"], "result_total_bytes_truncated")

        result = ResultLimiter().rows([(1,), (2,)], ["v"], max_rows=1)
        self.assertEqual(result["rows"], [[1]])
        self.assertEqual(result["limitEvents"][-1]["code"], "result_row_count_truncated")
        with self.assertRaises(ValueError):
            ResultLimiter().rows([], [], max_rows=0)

    def test_record_results_preserve_names_and_share_all_limits(self):
        limiter = ResultLimiter(ResultLimits(max_collection_items=2, max_result_bytes=100))
        result = limiter.records([{"payload": [1, 2, 3]}], ["payload"], max_rows=1)
        self.assertEqual(result["rows"], [{"payload": [1, 2]}])
        self.assertTrue(result["limitEvents"])
        self.assertEqual(result["limitEvents"][0]["code"], "result_collection_truncated")

    def test_incremental_accounting_never_serializes_the_accumulated_row_list(self):
        import schemii.result_limits as limits_module

        measured = limits_module.json_utf8_size

        def assert_incremental(value):
            if isinstance(value, list) and len(value) > 1 and all(isinstance(item, list) for item in value):
                raise AssertionError("accumulated rows were copied and serialized")
            return measured(value)

        limiter = ResultLimiter(ResultLimits(max_result_bytes=1024))
        with patch("schemii.result_limits.json_utf8_size", side_effect=assert_incremental):
            result = limiter.rows([(index,) for index in range(20)], ["value"], max_rows=20)
        self.assertEqual(len(result["rows"]), 20)
        self.assertLessEqual(json_utf8_size(result["rows"]), limiter.limits.max_result_bytes)

    def test_record_byte_accounting_includes_field_names(self):
        alias = "field_name_that_is_larger_than_the_value"
        limiter = ResultLimiter(ResultLimits(max_cell_bytes=100, max_row_bytes=100, max_result_bytes=40))
        result = limiter.records([(1,), (2,)], [alias], max_rows=2)
        self.assertEqual(result["rows"], [])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["limitEvents"][-1]["code"], "result_total_bytes_truncated")


if __name__ == "__main__":
    unittest.main()
