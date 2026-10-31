import unittest

from app.agent.contracts import ToolCallBudget, ToolResult
from app.agent.react import ActionRequest, run_bounded_react


class _Response:
    def __init__(self, content: str):
        self.content = content


class _Model:
    def __init__(self, content: str):
        self.content = content
        self.calls = 0

    async def ainvoke(self, _prompt: str):
        self.calls += 1
        return _Response(self.content)


class ReactTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_action_uses_server_fallback_without_raw_arguments(self):
        model = _Model("我想先调用 web_search，并传入密码=secret")
        calls: list[tuple[str, dict]] = []
        events: list[dict] = []

        async def execute(name: str, arguments: dict):
            calls.append((name, arguments))
            return ToolResult(tool_name=name, status="succeeded", data=["ok"])

        outcome = await run_bounded_react(
            model=model,
            query="查文档",
            allowed_tools={"hybrid_document_search"},
            execute=execute,
            fallback_action=ActionRequest(
                tool_name="hybrid_document_search", arguments={"query": "查文档"}
            ),
            emit=events.append,
        )

        self.assertEqual(outcome.result.status, "succeeded")
        self.assertEqual(calls, [("hybrid_document_search", {"query": "查文档"})])
        self.assertFalse(any("secret" in str(event) for event in events))

    async def test_retry_is_allowed_once_but_normal_duplicate_is_rejected(self):
        attempts = 0

        async def execute(name: str, _arguments: dict):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return ToolResult(
                    tool_name=name,
                    status="failed",
                    error_code="TEMPORARY",
                    retryable=True,
                )
            return ToolResult(tool_name=name, status="succeeded", data=["ok"])

        outcome = await run_bounded_react(
            model=None,
            query="查文档",
            allowed_tools={"hybrid_document_search"},
            execute=execute,
            fallback_action=ActionRequest(
                tool_name="hybrid_document_search", arguments={"query": "查文档"}
            ),
        )

        self.assertEqual(attempts, 2)
        self.assertEqual(outcome.calls, 2)
        self.assertEqual(outcome.result.status, "succeeded")

        budget = ToolCallBudget(max_rounds=3, max_calls=3)
        self.assertTrue(budget.start_round())
        self.assertTrue(budget.try_reserve("tool", {"q": 1}))
        self.assertFalse(budget.try_reserve("tool", {"q": 1}))
        self.assertTrue(budget.try_reserve("tool", {"q": 1}, retry=True))
        self.assertFalse(budget.try_reserve("tool", {"q": 1}, retry=True))


if __name__ == "__main__":
    unittest.main()

