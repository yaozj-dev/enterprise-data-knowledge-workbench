import json
import tempfile
import unittest
from pathlib import Path

from app.services.quality_service import AgentRunTrace, redact_trace_text
from app.storage import app_db
from app.storage.app_db import (
    get_agent_run_for_user,
    get_quality_overview,
    list_quality_feedback,
    list_regression_cases,
    review_agent_feedback,
    upsert_agent_feedback,
)


class AgentQualityStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_db_path = app_db.DB_PATH
        app_db.DB_PATH = Path(self.temporary_directory.name) / "workbench.db"
        app_db.init_db()
        self.user = app_db.create_user("quality-user@example.com", "Quality User", "password-123")
        self.admin = app_db.create_user("quality-admin@example.com", "Quality Admin", "password-123", "admin")
        self.conversation = app_db.create_conversation(self.user["id"], "quality trace")

    def tearDown(self):
        app_db.DB_PATH = self.original_db_path
        self.temporary_directory.cleanup()

    def test_trace_feedback_review_and_regression_case_form_a_closed_loop(self):
        trace = AgentRunTrace(
            user_id=self.user["id"],
            conversation_id=self.conversation["id"],
            query="请分析本季度经营表现，联系 qa@example.com，token=secret-value",
        )
        trace.observe({"type": "progress", "step": "识别业务意图", "status": "running"})
        trace.observe({"type": "progress", "step": "识别业务意图", "status": "success"})
        trace.observe(
            {
                "type": "route",
                "intent": "data",
                "confidence": 0.91,
                "strategy": "llm_structured",
                "policy_version": "hybrid-router-v1",
            }
        )
        trace.observe({"type": "progress", "step": "召回指标信息", "status": "running"})
        trace.observe({"type": "progress", "step": "召回指标信息", "status": "success"})
        trace.observe({"type": "result", "data": [{"销售额": 100.0}], "sql": "SELECT 1"})
        trace.finish()

        run = get_agent_run_for_user(trace.run_id, self.user["id"])
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run["route"], "data")
        self.assertEqual(run["status"], "completed")
        self.assertGreaterEqual(run["total_latency_ms"], 0)
        self.assertIn("[email]", run["query_preview"])
        self.assertIn("[secret]", run["query_preview"])

        feedback = upsert_agent_feedback(
            trace.run_id,
            self.user["id"],
            -1,
            "wrong_route",
            "我希望该问题按数据分析处理。",
        )
        reviewed = review_agent_feedback(
            feedback["id"],
            self.admin["id"],
            "accepted",
            "确认补充到路由回归集。",
            "data",
            "统计本季度经营指标",
            True,
        )
        self.assertEqual(reviewed["review_status"], "accepted")
        self.assertEqual(len(list_regression_cases()), 1)

        page = list_quality_feedback(status="accepted")
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["items"][0]["regression_case_id"] is not None, True)
        overview = get_quality_overview(14)
        self.assertEqual(overview["summary"]["run_count"], 1)
        self.assertEqual(overview["summary"]["negative_feedback_count"], 1)
        self.assertEqual(overview["by_route"][0]["route"], "data")

    def test_rejected_feedback_cannot_be_promoted(self):
        trace = AgentRunTrace(
            user_id=self.user["id"],
            conversation_id=self.conversation["id"],
            query="查询销售额",
        )
        trace.observe({"type": "route", "intent": "data", "confidence": 1, "strategy": "rules"})
        trace.observe({"type": "result", "data": []})
        trace.finish()
        feedback = upsert_agent_feedback(trace.run_id, self.user["id"], -1, "other", "需要检查")

        with self.assertRaises(ValueError):
            review_agent_feedback(
                feedback["id"],
                self.admin["id"],
                "rejected",
                promote_to_regression=True,
            )

    def test_trace_persists_tool_budget_metrics(self):
        trace = AgentRunTrace(
            user_id=self.user["id"],
            conversation_id=self.conversation["id"],
            query="结合销售数据和企业制度分析费用",
        )
        trace.observe({"type": "plan", "plan": {"max_iterations": 3}})
        trace.observe(
            {"type": "agent_started", "agent_name": "data", "task_id": "task-1", "status": "running"}
        )
        trace.observe(
            {
                "type": "tool_started",
                "agent_name": "data",
                "task_id": "task-1",
                "tool_name": "safe_readonly_query",
                "status": "running",
                "round": 1,
            }
        )
        trace.observe(
            {
                "type": "tool_completed",
                "agent_name": "data",
                "task_id": "task-1",
                "tool_name": "safe_readonly_query",
                "status": "succeeded",
                "latency_ms": 25,
                "round": 1,
            }
        )
        trace.finish()

        run = get_agent_run_for_user(trace.run_id, self.user["id"])
        self.assertIsNotNone(run)
        assert run is not None and run["trace_summary"] is not None
        summary = json.loads(run["trace_summary"])
        self.assertEqual(summary["agent_count"], 1)
        self.assertEqual(
            summary["budget"],
            {
                "max_rounds": 3,
                "max_calls": 6,
                "round_count": 0,
                "call_count": 1,
                "completed_count": 1,
                "failed_count": 0,
                "latency_ms": 25,
                "max_observed_round": 1,
                "exhausted": False,
            },
        )

    def test_trace_redaction_masks_common_sensitive_identifiers(self):
        preview = redact_trace_text("请联系 a.b@example.com 或 13800138000，api_key=abc123456789")
        self.assertNotIn("a.b@example.com", preview)
        self.assertNotIn("13800138000", preview)
        self.assertIn("[email]", preview)
        self.assertIn("[phone]", preview)
        self.assertIn("[secret]", preview)


if __name__ == "__main__":
    unittest.main()
