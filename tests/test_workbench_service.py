import unittest
from datetime import datetime
from unittest.mock import patch

from app.services.workbench_service import (
    build_document_context,
    classify_intent,
    current_time_answer,
    format_conversation_history,
    has_valid_citation,
    is_current_time_question,
    relevant_document_hits,
    resolve_intent,
)


class IntentRoutingTests(unittest.TestCase):
    def test_routes_specialized_questions_first(self):
        self.assertEqual(classify_intent("统计 2025 年的销售额"), "data")
        self.assertEqual(classify_intent("公司的报销流程是什么"), "document")

    def test_routes_open_ended_questions_to_general_agent(self):
        self.assertEqual(classify_intent("什么是 Python 后端开发"), "general")

    def test_clarifies_short_vague_questions(self):
        self.assertEqual(classify_intent("看一下"), "clarify")

    def test_detects_current_time_questions(self):
        self.assertTrue(is_current_time_question("请告诉我今天的日期"))
        self.assertTrue(is_current_time_question("现在几点"))
        self.assertFalse(is_current_time_question("统计今天的销售额"))

    def test_formats_current_date_without_llm(self):
        fixed_time = datetime.fromisoformat("2026-10-19T09:05:00+08:00")
        self.assertEqual(
            current_time_answer("请告诉我今天的日期", fixed_time),
            "今天是2026年10月19日。",
        )
        self.assertEqual(
            current_time_answer("今天星期几", fixed_time),
            "今天是2026年10月19日，星期一。",
        )

    def test_history_keeps_recent_messages_and_enforces_a_character_budget(self):
        history = format_conversation_history(
            [
                {"role": "assistant", "content": "x" * 8_000},
                {"role": "user", "content": "早期问题"},
                {"role": "system", "content": "不应传入模型"},
                {"role": "assistant", "content": "早期回答"},
                {"role": "user", "content": "最新追问"},
            ]
        )
        self.assertNotIn("不应传入模型", history)
        self.assertIn("用户：最新追问", history)
        self.assertLessEqual(len(history), 4_000)


class _FakeModelResponse:
    content = "测试回答"


class _FakeLLM:
    def __init__(self):
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str):
        self.prompts.append(prompt)
        return _FakeModelResponse()


class ConversationContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_orchestrator_switch_reaches_compatibility_flow(self):
        from app.services.workbench_service import WorkbenchService

        class FakeQueryService:
            async def query(self, _query: str):
                yield 'data: {"type":"result","data":[{"销售额":100}]}\n\n'

        service = WorkbenchService(FakeQueryService())
        with (
            patch("app.conf.app_config.get_orchestrator_mode", return_value="legacy"),
            patch(
                "app.services.supervisor_workbench_service.SupervisorWorkbenchService.stream",
                side_effect=AssertionError("Supervisor must not be used in legacy mode"),
            ),
        ):
            events = [
                item
                async for item in service.stream("统计销售额", user_id=3, state_store=None)
            ]

        self.assertTrue(any('"type":"result"' in item for item in events))

    async def test_supervisor_orchestrator_switch_is_the_default_workbench_path(self):
        from app.services.workbench_service import WorkbenchService

        async def fake_stream(*_args):
            yield 'data: {"type":"done","execution_status":"completed"}\n\n'

        service = WorkbenchService(query_service=None)
        with (
            patch("app.conf.app_config.get_orchestrator_mode", return_value="supervisor"),
            patch(
                "app.services.supervisor_workbench_service.SupervisorWorkbenchService.stream",
                fake_stream,
            ),
        ):
            events = [
                item
                async for item in service.stream("统计销售额", user_id=3, state_store=None)
            ]

        self.assertEqual(len(events), 1)
        self.assertIn('"execution_status":"completed"', events[0])

    async def test_ambiguous_business_question_uses_structured_router(self):
        class RouterResponse:
            content = '{"intent":"data","confidence":0.88}'

        class RouterModel:
            async def ainvoke(self, _prompt: str):
                return RouterResponse()

        with patch("app.services.workbench_service.llm", RouterModel()):
            decision = await resolve_intent("请帮我看看本季度经营表现")

        self.assertEqual(decision.intent, "data")
        self.assertEqual(decision.strategy, "llm_structured")
        self.assertGreaterEqual(decision.confidence, 0.72)

    async def test_low_confidence_router_result_falls_back_to_clarification(self):
        class RouterResponse:
            content = '{"intent":"document","confidence":0.3}'

        class RouterModel:
            async def ainvoke(self, _prompt: str):
                return RouterResponse()

        with patch("app.services.workbench_service.llm", RouterModel()):
            decision = await resolve_intent("请帮我看看本季度经营表现")

        self.assertEqual(decision.intent, "clarify")
        self.assertEqual(decision.strategy, "llm_low_confidence")

    async def test_general_agent_receives_bounded_persisted_history(self):
        from app.services.workbench_service import WorkbenchService

        fake_llm = _FakeLLM()
        service = WorkbenchService(query_service=None)
        history = [
            {"role": "user", "content": "我正在准备 Python 后端实习"},
            {"role": "assistant", "content": "可以先完善 API 和测试。"},
        ]

        with patch("app.services.workbench_service.llm", fake_llm):
            events = [
                chunk
                async for chunk in service.stream(
                    "下一步应该学什么", user_id=7, conversation_history=history
                )
            ]

        self.assertEqual(len(fake_llm.prompts), 1)
        self.assertIn("我正在准备 Python 后端实习", fake_llm.prompts[0])
        self.assertIn("下一步应该学什么", fake_llm.prompts[0])
        self.assertTrue(any('"type": "answer"' in event for event in events))


class RetrievalSafetyTests(unittest.TestCase):
    def test_weak_or_duplicate_hits_are_not_passed_to_the_rag_prompt(self):
        hits = relevant_document_hits(
            [
                {"document_id": "a", "chunk_index": 0, "score": 0.88, "content": "有效内容"},
                {"document_id": "a", "chunk_index": 0, "score": 0.87, "content": "重复内容"},
                {"document_id": "b", "chunk_index": 0, "score": 0.01, "content": "弱相关内容"},
            ]
        )
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["content"], "有效内容")

    def test_context_budget_and_citation_validation_are_enforced(self):
        original_context = "x" * 7_000
        context = build_document_context(
            [{"index": 1, "filename": "policy.md", "page_number": None}],
            [{"content": original_context}],
        )
        self.assertLessEqual(len(context), 6_000)
        self.assertTrue(has_valid_citation("报销需要审批。[1]", 1))
        self.assertFalse(has_valid_citation("报销需要审批。", 1))
        self.assertFalse(has_valid_citation("报销需要审批。[2]", 1))


if __name__ == "__main__":
    unittest.main()
