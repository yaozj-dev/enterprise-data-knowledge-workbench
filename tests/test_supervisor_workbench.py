import asyncio
import json
import unittest
from unittest.mock import patch

from app.agent.contracts import AgentResult, ExecutionPlan, TaskSpec
from app.agent.supervisor import RoutingDecision, build_execution_plan
from app.services.supervisor_workbench_service import SupervisorWorkbenchService


class _Response:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    async def ainvoke(self, prompt: str):
        if "报告校验器" in prompt:
            return _Response('{"decision":"pass","confidence":0.95}')
        return _Response("制度要求先审批。[1]")


class _CapturingLLM:
    def __init__(self):
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str):
        self.prompts.append(prompt)
        return _Response("你好，我会用中文回答。")


class _CapturingCitationLLM(_CapturingLLM):
    async def ainvoke(self, prompt: str):
        self.prompts.append(prompt)
        if "报告校验器" in prompt:
            return _Response('{"decision":"pass","confidence":0.95}')
        return _Response("制度要求先审批。[1]")


class _LowConfidenceVerifierLLM:
    async def ainvoke(self, prompt: str):
        if "报告校验器" in prompt:
            return _Response('{"decision":"pass","confidence":0.5}')
        return _Response("销售额为100。")


class _ConfirmedMemoryRepository:
    async def list_for_user(self, user_id: int, status: str | None = None):
        assert status == "confirmed"
        return [
            {
                "user_id": user_id,
                "kind": "preference",
                "content": "请用中文回答",
                "status": "confirmed",
            }
        ]


class _FailingMemoryRepository(_ConfirmedMemoryRepository):
    async def propose(self, *_args, **_kwargs):
        raise OSError("memory write failed")


class _DataService:
    async def query(self, _query: str):
        yield 'data: {"type":"result","data":[{"销售额":100}]}' + "\n\n"


class _StructuredDataService:
    def __init__(self):
        self.execute_calls = 0
        self.query_calls = 0

    async def execute(self, _query: str, *, task_id: str, on_event=None):
        self.execute_calls += 1
        if on_event:
            await on_event(
                {
                    "type": "result",
                    "data": [{"销售额": 100}],
                    "sql": "SELECT 100 AS 销售额",
                }
            )
        return AgentResult(
            agent_name="data",
            task_id=task_id,
            status="succeeded",
            data=[{"销售额": 100}],
            metadata={"sql": "SELECT 100 AS 销售额"},
        )

    async def query(self, _query: str):
        self.query_calls += 1
        yield 'data: {"type":"error","message":"legacy path used"}' + "\n\n"


class _CapturingStructuredDataService(_StructuredDataService):
    def __init__(self):
        super().__init__()
        self.queries: list[str] = []

    async def execute(self, query: str, *, task_id: str, on_event=None):
        self.queries.append(query)
        return await super().execute(query, task_id=task_id, on_event=on_event)


class _StructuredDataServiceWithoutEvent(_StructuredDataService):
    async def execute(self, _query: str, *, task_id: str, on_event=None):
        self.execute_calls += 1
        return AgentResult(
            agent_name="data",
            task_id=task_id,
            status="succeeded",
            data=[],
            metadata={"sql": "SELECT 1 WHERE 0"},
        )


class _SlowDataService:
    async def query(self, _query: str):
        await asyncio.sleep(0.5)
        yield 'data: {"type":"result","data":[{"销售额":100}]}' + "\n\n"


class _EmptyDataService:
    async def query(self, _query: str):
        yield 'data: {"type":"progress","step":"执行SQL","status":"success"}' + "\n\n"


class _DocumentService:
    async def search(self, _user_id: int, _query: str):
        return [
            {
                "document_id": "policy-1",
                "chunk_index": 0,
                "filename": "expense.md",
                "page_number": 1,
                "score": 0.9,
                "content": "费用需要审批。",
            }
        ]


class SupervisorWorkbenchTests(unittest.IsolatedAsyncioTestCase):
    async def test_low_confidence_verifier_keeps_answer_partial(self):
        service = SupervisorWorkbenchService(None)
        data = AgentResult(
            agent_name="data",
            task_id="data-1",
            status="succeeded",
            data=[{"销售额": 100}],
        )
        knowledge = AgentResult(
            agent_name="knowledge",
            task_id="knowledge-1",
            status="partial",
        )
        with patch("app.services.workbench_service.llm", _LowConfidenceVerifierLLM()):
            answer, _citations, verification, metadata = await service._report(
                "统计销售额", data, knowledge
            )

        self.assertEqual(answer, "销售额为100。")
        self.assertEqual(verification, "partial")
        self.assertTrue(metadata["degraded"])
        self.assertEqual(metadata["model_status"], "low_confidence")

    async def test_runtime_and_memory_degradation_are_visible_in_sse(self):
        class DegradedState:
            def __init__(self):
                self.notice_available = True

            async def append_event(self, *_args, **_kwargs):
                return None

            def consume_degradation_notice(self):
                if not self.notice_available:
                    return None
                self.notice_available = False
                return {
                    "service": "redis",
                    "status": "degraded",
                    "fallback": "in_process",
                    "reason": "REDIS_UNAVAILABLE",
                }

        class UnavailableMemory:
            async def list_for_user(self, *_args, **_kwargs):
                raise OSError("memory store unavailable")

        service = SupervisorWorkbenchService(_DataService())
        with patch("app.services.workbench_service.llm", _CapturingLLM()):
            chunks = [
                item
                async for item in service.stream(
                    "你好",
                    user_id=7,
                    history=[],
                    mode="general",
                    state_store=DegradedState(),
                    memory_repository=UnavailableMemory(),
                )
            ]

        payloads = [json.loads(item[6:].strip()) for item in chunks]
        degradations = [item for item in payloads if item.get("type") == "degraded"]
        self.assertEqual({item["service"] for item in degradations}, {"redis", "mysql_memory"})
        self.assertEqual(payloads[-1]["execution_status"], "completed")

    async def test_memory_candidate_write_failure_is_visible_without_blocking_answer(self):
        service = SupervisorWorkbenchService(_DataService())
        with patch("app.services.workbench_service.llm", _CapturingLLM()):
            chunks = [
                item
                async for item in service.stream(
                    "请记住以后用中文回答",
                    user_id=7,
                    history=[],
                    mode="general",
                    state_store=None,
                    memory_repository=_FailingMemoryRepository(),
                )
            ]

        payloads = [json.loads(item[6:].strip()) for item in chunks]
        notices = [item for item in payloads if item.get("type") == "degraded"]
        self.assertTrue(
            any(
                item.get("service") == "mysql_memory"
                and item.get("reason") == "MYSQL_MEMORY_WRITE_FAILED"
                for item in notices
            )
        )
        self.assertFalse(any(item.get("type") == "memory_candidate" for item in payloads))
        self.assertTrue(any(item.get("type") == "answer" for item in payloads))
        self.assertEqual(payloads[-1]["execution_status"], "completed")

    async def test_confirmed_user_memory_is_added_to_general_agent_prompt(self):
        llm = _CapturingLLM()
        service = SupervisorWorkbenchService(_DataService())
        with patch("app.services.workbench_service.llm", llm):
            chunks = [
                item
                async for item in service.stream(
                    "你好",
                    user_id=7,
                    history=[],
                    mode="general",
                    state_store=None,
                    memory_repository=_ConfirmedMemoryRepository(),
                )
            ]

        payloads = [json.loads(item[6:].strip()) for item in chunks]
        self.assertEqual(len(llm.prompts), 1)
        self.assertIn("请用中文回答", llm.prompts[0])
        self.assertIn("不可信", llm.prompts[0])
        self.assertEqual(payloads[-1]["execution_status"], "completed")

    async def test_confirmed_memory_reaches_knowledge_and_composite_report_prompts(self):
        llm = _CapturingCitationLLM()
        service = SupervisorWorkbenchService(_DataService(), _DocumentService())
        with patch("app.services.workbench_service.llm", llm):
            chunks = [
                item
                async for item in service.stream(
                    "结合销售数据和报销制度分析本月费用",
                    user_id=7,
                    history=[],
                    mode="auto",
                    state_store=None,
                    memory_repository=_ConfirmedMemoryRepository(),
                )
            ]

        prompts = llm.prompts
        knowledge_prompt = next(prompt for prompt in prompts if "文档片段" in prompt)
        report_prompt = next(prompt for prompt in prompts if "结构化数据" in prompt)
        payloads = [json.loads(item[6:].strip()) for item in chunks]
        self.assertIn("请用中文回答", knowledge_prompt)
        self.assertIn("请用中文回答", report_prompt)
        self.assertEqual(payloads[-1]["execution_status"], "completed")

    async def test_supervisor_uses_structured_data_result_instead_of_sse_adapter(self):
        data_service = _StructuredDataService()
        service = SupervisorWorkbenchService(data_service, _DocumentService())
        with patch("app.services.workbench_service.llm", _FakeLLM()):
            chunks = [
                item
                async for item in service.stream(
                    "统计销售额",
                    user_id=7,
                    history=[],
                    mode="data",
                    state_store=None,
                )
            ]

        payloads = [json.loads(item[6:].strip()) for item in chunks]
        self.assertEqual(data_service.execute_calls, 1)
        self.assertEqual(data_service.query_calls, 0)
        self.assertTrue(
            any(
                item.get("type") == "result"
                and item.get("data") == [{"销售额": 100}]
                for item in payloads
            )
        )
        self.assertEqual(payloads[-1]["execution_status"], "completed")

    async def test_supervisor_repairs_missing_result_event_from_structured_contract(self):
        data_service = _StructuredDataServiceWithoutEvent()
        service = SupervisorWorkbenchService(data_service, _DocumentService())
        with patch("app.services.workbench_service.llm", _FakeLLM()):
            chunks = [
                item
                async for item in service.stream(
                    "统计销售额",
                    user_id=7,
                    history=[],
                    mode="data",
                    state_store=None,
                )
            ]

        payloads = [json.loads(item[6:].strip()) for item in chunks]
        result_events = [item for item in payloads if item.get("type") == "result"]
        self.assertEqual(len(result_events), 1)
        self.assertEqual(result_events[0]["data"], [])
        self.assertEqual(result_events[0]["sql"], "SELECT 1 WHERE 0")
        self.assertTrue(result_events[0]["repaired_from_contract"])
        self.assertEqual(payloads[-1]["execution_status"], "completed")

    async def test_composite_run_exposes_parallel_agents_and_report(self):
        service = SupervisorWorkbenchService(_DataService(), _DocumentService())
        with patch("app.services.workbench_service.llm", _FakeLLM()):
            chunks = [
                item
                async for item in service.stream(
                    "结合销售数据和报销制度分析本月费用",
                    user_id=7,
                    history=[],
                    mode="auto",
                    state_store=None,
                )
            ]
        payloads = [json.loads(item[6:].strip()) for item in chunks]
        started = {item.get("agent_name") for item in payloads if item.get("type") == "agent_started"}
        self.assertEqual(started, {"data", "knowledge", "report"})
        self.assertTrue(any(item.get("type") == "result" for item in payloads))
        self.assertTrue(any(item.get("type") == "answer" for item in payloads))
        self.assertEqual(payloads[-1]["type"], "done")
        self.assertEqual(payloads[-1]["execution_status"], "completed")

    async def test_composite_specialists_receive_bounded_subqueries(self):
        data_service = _CapturingStructuredDataService()
        service = SupervisorWorkbenchService(data_service, _DocumentService())
        with patch("app.services.workbench_service.llm", _FakeLLM()):
            chunks = [
                item
                async for item in service.stream(
                    "结合2025年第一季度各区域销售额和费用报销制度，分析区域差异并说明报销需要提交哪些材料。",
                    user_id=7,
                    history=[],
                    mode="composite",
                    state_store=None,
                )
            ]

        payloads = [json.loads(item[6:].strip()) for item in chunks]
        self.assertEqual(len(data_service.queries), 1)
        self.assertIn("销售额", data_service.queries[0])
        self.assertNotIn("报销制度", data_service.queries[0])
        self.assertEqual(payloads[-1]["execution_status"], "completed")

    async def test_composite_timeout_returns_knowledge_evidence_as_partial_result(self):
        decision = RoutingDecision("composite", 0.94, "test")
        plan = ExecutionPlan(
            intent="composite",
            confidence=0.94,
            tasks=[
                TaskSpec(
                    task_id="data-1",
                    agent_name="data",
                    goal="slow analytics",
                    allowed_tools=["data_catalog_lookup", "safe_readonly_query"],
                    required=False,
                    timeout_ms=100,
                ),
                TaskSpec(
                    task_id="knowledge-1",
                    agent_name="knowledge",
                    goal="policy evidence",
                    allowed_tools=["hybrid_document_search"],
                    required=False,
                    timeout_ms=1_000,
                ),
                TaskSpec(
                    task_id="report-1",
                    agent_name="report",
                    goal="merge evidence",
                    dependencies=["data-1", "knowledge-1"],
                    allowed_tools=["verify_evidence"],
                ),
            ],
        )
        service = SupervisorWorkbenchService(_SlowDataService(), _DocumentService())
        with (
            patch(
                "app.services.supervisor_workbench_service.build_supervisor_plan",
                return_value=(decision, plan),
            ),
            patch("app.services.workbench_service.llm", _FakeLLM()),
        ):
            chunks = [
                item
                async for item in service.stream(
                    "结合销售数据和报销制度分析本月费用",
                    user_id=7,
                    history=[],
                    mode="composite",
                    state_store=None,
                )
            ]

        payloads = [json.loads(item[6:].strip()) for item in chunks]
        self.assertTrue(
            any(
                item.get("type") == "partial_result"
                and item.get("agent_name") == "data"
                and item.get("status") == "timed_out"
                for item in payloads
            )
        )
        self.assertTrue(
            any(
                item.get("type") == "answer" and item.get("citations")
                for item in payloads
            )
        )
        self.assertEqual(payloads[-1]["execution_status"], "partial")

    async def test_composite_without_data_evidence_is_not_marked_completed(self):
        service = SupervisorWorkbenchService(_EmptyDataService(), _DocumentService())
        with patch("app.services.workbench_service.llm", _FakeLLM()):
            chunks = [
                item
                async for item in service.stream(
                    "结合销售数据和报销制度分析本月费用",
                    user_id=7,
                    history=[],
                    mode="auto",
                    state_store=None,
                )
            ]

        payloads = [json.loads(item[6:].strip()) for item in chunks]
        data_completion = next(
            item
            for item in payloads
            if item.get("type") == "agent_completed" and item.get("agent_name") == "data"
        )
        self.assertEqual(data_completion["status"], "partial")
        self.assertEqual(payloads[-1]["execution_status"], "partial")

    async def test_unsupported_agent_is_replanned_once_then_completed(self):
        invalid_plan = ExecutionPlan(
            intent="general",
            confidence=0.9,
            tasks=[
                TaskSpec(
                    task_id="unsupported-1",
                    agent_name="report",
                    goal="非法部署任务",
                    allowed_tools=["verify_evidence"],
                )
            ],
        )
        valid_decision = RoutingDecision("general", 0.95, "rules_general")
        _, valid_plan = build_execution_plan("你好", "general")
        service = SupervisorWorkbenchService(_DataService())
        with (
            patch(
                "app.services.supervisor_workbench_service.build_supervisor_plan",
                side_effect=[(valid_decision, invalid_plan), (valid_decision, valid_plan)],
            ),
            patch("app.services.workbench_service.llm", _CapturingLLM()),
        ):
            chunks = [
                item
                async for item in service.stream(
                    "你好",
                    user_id=7,
                    history=[],
                    mode="auto",
                    state_store=None,
                )
            ]

        payloads = [json.loads(item[6:].strip()) for item in chunks]
        self.assertTrue(any(item.get("type") == "replan" for item in payloads))
        self.assertTrue(any(item.get("type") == "answer" for item in payloads))
        self.assertEqual(payloads[-1]["execution_status"], "completed")


if __name__ == "__main__":
    unittest.main()

