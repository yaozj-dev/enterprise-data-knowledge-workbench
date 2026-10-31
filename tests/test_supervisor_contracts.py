import unittest

from pydantic import ValidationError

from app.agent.contracts import ExecutionPlan, TaskSpec, ToolCallBudget
from app.agent.supervisor import build_execution_plan, validate_model_plan
from app.agent.supervisor_graph import build_supervisor_plan


class SupervisorContractTests(unittest.TestCase):
    def test_supervisor_graph_returns_a_validated_plan(self):
        async def check():
            decision, plan = await build_supervisor_plan(
                "结合销售数据和报销制度分析本月费用",
                "auto",
            )
            self.assertEqual(decision.intent, "composite")
            self.assertEqual(plan.intent, "composite")
            self.assertEqual(len(plan.tasks), 3)

        import asyncio

        asyncio.run(check())

    def test_composite_plan_has_parallel_specialists_and_report_dependency(self):
        decision, plan = build_execution_plan("结合销售数据和报销制度分析本月费用", "auto")

        self.assertEqual(decision.intent, "composite")
        self.assertEqual(plan.intent, "composite")
        self.assertEqual(len(plan.tasks), 3)
        report = next(task for task in plan.tasks if task.agent_name == "report")
        self.assertEqual(set(report.dependencies), {"data-1", "knowledge-1"})

    def test_plan_rejects_cycle_and_unauthorized_tool(self):
        with self.assertRaises(ValidationError):
            ExecutionPlan(
                intent="composite",
                confidence=0.9,
                tasks=[
                    TaskSpec(
                        task_id="a",
                        agent_name="data",
                        goal="a",
                        dependencies=["b"],
                        allowed_tools=["safe_readonly_query"],
                    ),
                    TaskSpec(
                        task_id="b",
                        agent_name="knowledge",
                        goal="b",
                        dependencies=["a"],
                        allowed_tools=["hybrid_document_search"],
                    ),
                ],
            )
        with self.assertRaises(ValidationError):
            TaskSpec(
                task_id="data-1",
                agent_name="data",
                goal="read",
                allowed_tools=["web_search"],
            )

    def test_extra_identity_fields_cannot_be_smuggled_into_plan(self):
        self.assertIsNone(
            validate_model_plan(
                {
                    "intent": "data",
                    "confidence": 0.9,
                    "tasks": [],
                    "required_evidence": [],
                    "max_iterations": 3,
                    "user_id": 99,
                }
            )
        )

    def test_budget_rejects_duplicate_calls_and_limits_retries(self):
        budget = ToolCallBudget(max_rounds=3, max_calls=2, max_retries_per_call=1)
        self.assertTrue(budget.start_round())
        self.assertTrue(budget.try_reserve("safe_readonly_query", {"sql": "select 1"}))
        self.assertFalse(budget.try_reserve("safe_readonly_query", {"sql": "select 1"}))
        self.assertTrue(budget.try_reserve("data_catalog_lookup", {"query": "sales"}))
        self.assertFalse(budget.try_reserve("third_tool", {}))
        self.assertTrue(budget.can_retry("safe_readonly_query", {"sql": "select 1"}))
        self.assertFalse(budget.can_retry("safe_readonly_query", {"sql": "select 1"}))


if __name__ == "__main__":
    unittest.main()

