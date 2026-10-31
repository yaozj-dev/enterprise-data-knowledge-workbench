import unittest
from unittest.mock import patch

from app.services.query_service import QueryService


class QueryServiceErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_internal_graph_error_is_not_exposed_to_sse_client(self):
        async def broken_astream(*_args, **_kwargs):
            raise RuntimeError("mysql://admin:secret@internal-host:3306/dw")
            yield  # pragma: no cover

        fake_graph = type("BrokenGraph", (), {"astream": broken_astream})()
        service = QueryService(None, None, None, None, None, None)

        with patch("app.services.query_service.graph", fake_graph):
            events = [event async for event in service.query("统计销售额")]

        self.assertEqual(len(events), 1)
        self.assertIn("数据分析执行失败", events[0])
        self.assertNotIn("internal-host", events[0])
        self.assertNotIn("secret", events[0])

    async def test_structured_execution_returns_result_and_sql_metadata(self):
        async def successful_astream(*_args, **_kwargs):
            yield {"type": "progress", "step": "执行SQL", "status": "running"}
            yield {
                "type": "result",
                "data": [{"销售额": 100}],
                "sql": "SELECT SUM(amount) FROM fact_order",
            }

        fake_graph = type("SuccessfulGraph", (), {"astream": successful_astream})()
        service = QueryService(None, None, None, None, None, None)
        seen: list[dict] = []

        with patch("app.services.query_service.graph", fake_graph):
            result = await service.execute("统计销售额", on_event=seen.append)

        self.assertEqual(result.agent_name, "data")
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.data, [{"销售额": 100}])
        self.assertEqual(result.metadata["sql"], "SELECT SUM(amount) FROM fact_order")
        self.assertEqual(len(seen), 2)

    async def test_structured_execution_marks_missing_evidence_as_partial(self):
        async def progress_only_astream(*_args, **_kwargs):
            yield {"type": "progress", "step": "生成SQL", "status": "success"}

        fake_graph = type("ProgressOnlyGraph", (), {"astream": progress_only_astream})()
        service = QueryService(None, None, None, None, None, None)

        with patch("app.services.query_service.graph", fake_graph):
            result = await service.execute("统计销售额")

        self.assertEqual(result.status, "partial")
        self.assertEqual(result.error_code, "DATA_RESULT_MISSING")


if __name__ == "__main__":
    unittest.main()
