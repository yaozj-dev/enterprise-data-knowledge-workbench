import json
import unittest

from app.agent.planner import build_model_plan, parse_plan_payload
from app.agent.supervisor import build_execution_plan


class _Response:
    def __init__(self, content: str):
        self.content = content


class _SequenceModel:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls = 0

    async def ainvoke(self, _prompt: str):
        self.calls += 1
        return _Response(self.responses.pop(0))


class PlannerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _, self.baseline = build_execution_plan(
            "结合销售数据和报销制度分析本月费用", "auto"
        )

    def test_parser_accepts_json_code_fence(self):
        payload = parse_plan_payload("```json\n{\"intent\": \"data\"}\n```")
        self.assertEqual(payload["intent"], "data")

    async def test_valid_model_plan_keeps_server_boundaries(self):
        payload = self.baseline.model_dump(mode="json")
        payload["tasks"][0]["goal"] = "只读取销售数据并按问题范围聚合"
        model = _SequenceModel([json.dumps(payload, ensure_ascii=False)])

        outcome = await build_model_plan("复合问题", self.baseline, model)

        self.assertEqual(outcome.strategy, "llm_structured_plan")
        self.assertEqual(outcome.attempts, 1)
        self.assertEqual(model.calls, 1)
        self.assertEqual(outcome.plan.confidence, self.baseline.confidence)
        self.assertEqual(
            {task.agent_name for task in outcome.plan.tasks},
            {"data", "knowledge", "report"},
        )

    async def test_invalid_first_plan_is_repaired_once(self):
        valid = self.baseline.model_dump(mode="json")
        model = _SequenceModel(
            [
                json.dumps({"intent": "composite", "tasks": [{"agent_name": "web"}]}),
                json.dumps(valid, ensure_ascii=False),
            ]
        )

        outcome = await build_model_plan("复合问题", self.baseline, model)

        self.assertEqual(outcome.strategy, "llm_structured_plan")
        self.assertEqual(outcome.attempts, 2)
        self.assertEqual(model.calls, 2)

    async def test_unsafe_plan_falls_back_after_one_repair(self):
        unsafe = self.baseline.model_dump(mode="json")
        unsafe["user_id"] = 42
        unsafe["tasks"][0]["allowed_tools"] = ["web_search"]
        model = _SequenceModel([json.dumps(unsafe), json.dumps(unsafe)])

        outcome = await build_model_plan("复合问题", self.baseline, model)

        self.assertEqual(outcome.strategy, "llm_plan_fallback")
        self.assertEqual(outcome.attempts, 2)
        self.assertEqual(outcome.plan, self.baseline)
        self.assertIsNotNone(outcome.fallback_reason)


if __name__ == "__main__":
    unittest.main()

