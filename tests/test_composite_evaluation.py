import unittest

from app.scripts.evaluate_composite_workbench import (
    evaluate_plan_cases,
    load_cases,
    parse_sse_event_line,
    score_live_events,
    select_cases,
    summarize_live_records,
)


class CompositeEvaluationTests(unittest.TestCase):
    def test_reviewed_composite_suite_has_twenty_valid_supervisor_plans(self):
        cases = load_cases()
        metrics = evaluate_plan_cases(cases)

        self.assertGreaterEqual(metrics["total"], 20)
        self.assertEqual(metrics["passed"], metrics["total"])
        self.assertEqual(metrics["composite_route_rate"], 1.0)

    def test_live_event_score_tracks_evidence_and_partial_status(self):
        case = {"id": "case-1", "query": "结合数据和制度分析"}
        events = [
            {"type": "route", "agent_intent": "composite"},
            {"type": "agent_started", "agent_name": "data"},
            {"type": "agent_started", "agent_name": "knowledge"},
            {"type": "agent_started", "agent_name": "report"},
            {"type": "result", "data": [{"销售额": 100}]},
            {"type": "answer", "content": "制度说明[1]", "citations": [{"index": 1}]},
            {"type": "done", "execution_status": "partial"},
        ]
        record = score_live_events(
            case,
            events,
            first_event_ms=42.0,
            total_latency_ms=220.0,
        )
        summary = summarize_live_records([record])

        self.assertTrue(record["orchestration_ok"])
        self.assertTrue(record["evidence_backed"])
        self.assertFalse(record["fully_completed"])
        self.assertEqual(summary["partial"], 1)
        self.assertEqual(
            parse_sse_event_line('data: {"type":"done","execution_status":"completed"}'),
            {"type": "done", "execution_status": "completed"},
        )

    def test_case_window_supports_targeted_live_reruns(self):
        cases = load_cases()
        self.assertEqual(select_cases(cases, offset=3, limit=2), cases[3:5])
        with self.assertRaisesRegex(ValueError, "outside"):
            select_cases(cases, offset=len(cases))
