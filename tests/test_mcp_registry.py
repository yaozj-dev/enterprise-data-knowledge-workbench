import unittest
from unittest.mock import AsyncMock

from app.agent.contracts import ToolResult
from app.mcp.tool_registry import ToolContext, ToolRegistry


async def _success(arguments, _context):
    return ToolResult(tool_name="private_tool", status="succeeded", data=arguments)


class MCPRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_allowlist_and_protocol_error_codes_are_stable(self):
        registry = ToolRegistry(service_token="secret")
        registry.register(
            "private_tool",
            "test",
            {
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            _success,
            allowed_agents={"data"},
        )
        self.assertTrue(registry.authenticate("secret"))
        self.assertFalse(registry.authenticate("wrong"))
        self.assertEqual(
            (await registry.call("missing", {}, ToolContext(1, agent_name="data"))).error_code,
            "METHOD_NOT_FOUND",
        )
        self.assertEqual(
            (
                await registry.call(
                    "private_tool", {}, ToolContext(1, agent_name="data")
                )
            ).error_code,
            "INVALID_PARAMS",
        )
        self.assertEqual(
            (
                await registry.call(
                    "private_tool", {"value": 1}, ToolContext(1, agent_name="knowledge")
                )
            ).error_code,
            "TOOL_NOT_ALLOWED",
        )
        self.assertEqual(
            (
                await registry.call(
                    "private_tool",
                    {"value": 1, "user_id": 999},
                    ToolContext(1, agent_name="data"),
                )
            ).error_code,
            "INVALID_PARAMS",
        )
        self.assertEqual(
            (
                await registry.call(
                    "private_tool",
                    {"value": "wrong"},
                    ToolContext(1, agent_name="data"),
                )
            ).error_code,
            "INVALID_PARAMS",
        )

    async def test_audit_sink_receives_outcome_without_tool_arguments(self):
        registry = ToolRegistry(service_token="secret")
        registry.register(
            "private_tool",
            "test",
            {"type": "object", "properties": {}, "additionalProperties": False},
            _success,
            allowed_agents={"data"},
        )
        sink = AsyncMock()
        registry.set_audit_sink(sink)

        result = await registry.call(
            "private_tool", {}, ToolContext(1, run_id="run-1", agent_name="data")
        )

        self.assertEqual(result.status, "succeeded")
        sink.assert_awaited_once()
        audited_result, context, duration_ms = sink.await_args.args
        self.assertEqual(audited_result.tool_name, "private_tool")
        self.assertEqual(context.run_id, "run-1")
        self.assertGreaterEqual(duration_ms, 0)
        self.assertFalse(hasattr(audited_result, "arguments"))


if __name__ == "__main__":
    unittest.main()

