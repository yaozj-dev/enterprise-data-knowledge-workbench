import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.scripts.evaluate_sql_results import (
    evaluate,
    normalized_rows,
    parse_sse_events,
    result_matches,
    write_report,
)


class SQLEvaluationTests(unittest.TestCase):
    def test_parse_sse_events_and_compare_result_values_without_column_aliases(self):
        events = parse_sse_events(
            'data: {"type":"progress","step":"生成SQL","status":"success"}\n\n'
            'data: {"type":"result","data":[{"销售额":279159.5}],'
            '"sql":"SELECT SUM(order_amount) FROM fact_order"}\n\n'
        )

        self.assertEqual(events[-1]["type"], "result")
        self.assertTrue(result_matches([279159.5], events[-1]["data"]))
        self.assertFalse(result_matches([115], events[-1]["data"]))

    def test_row_comparison_keeps_dimension_and_metric_paired(self):
        expected = [["华东", 107373.0], ["华南", 70202.0]]
        correct_rows_with_different_aliases = [
            {"区域": "华南", "销售额": 70202},
            {"region": "华东", "amount": 107373.0},
        ]
        values_attached_to_wrong_dimensions = [
            {"区域": "华东", "销售额": 70202.0},
            {"区域": "华南", "销售额": 107373.0},
        ]

        self.assertTrue(result_matches(expected, correct_rows_with_different_aliases))
        self.assertFalse(result_matches(expected, values_attached_to_wrong_dimensions))
        self.assertEqual(
            normalized_rows(correct_rows_with_different_aliases),
            normalized_rows(expected),
        )

    def test_missing_sql_result_is_recorded_with_public_error_context(self):
        events = parse_sse_events(
            'data: {"type":"progress","step":"生成SQL","status":"error"}\n\n'
            'data: {"type":"error","message":"数据分析执行失败，请调整问题后重试或联系管理员。"}\n\n'
        )
        public_events = [
            {
                key: event[key]
                for key in ("type", "step", "status", "message")
                if key in event
            }
            for event in events
            if event.get("type") in {"error", "clarification", "progress"}
            and (event.get("type") != "progress" or event.get("status") == "error")
        ]

        self.assertEqual(
            public_events,
            [
                {"type": "progress", "step": "生成SQL", "status": "error"},
                {"type": "error", "message": "数据分析执行失败，请调整问题后重试或联系管理员。"},
            ],
        )

    def test_case_window_rejects_invalid_values(self):
        with self.assertRaisesRegex(ValueError, "offset"):
            asyncio.run(evaluate(offset=-1))

    def test_write_report_preserves_metrics_for_repeated_runs(self):
        metrics = {"total": 30, "correct": 28, "failures": []}
        with TemporaryDirectory() as directory:
            report_path = Path(directory) / "sql-run.json"
            write_report(metrics, report_path)
            self.assertEqual(json.loads(report_path.read_text(encoding="utf-8")), metrics)


if __name__ == "__main__":
    unittest.main()
