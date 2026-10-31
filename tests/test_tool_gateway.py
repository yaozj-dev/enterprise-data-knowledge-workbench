import unittest
from unittest.mock import AsyncMock, patch

from app.agent.contracts import ToolResult
from app.mcp.gateway import GatewayConfig, ToolGateway
from app.mcp.tool_registry import ToolContext, default_registry


class _Documents:
    async def search(self, user_id: int, query: str):
        return [
            {
                "user_id": user_id,
                "document_id": "doc-1",
                "chunk_index": 0,
                "filename": "policy.md",
                "page_number": 2,
                "score": 0.91,
                "content": f"匹配：{query}",
            }
        ]


class _Response:
    def __init__(self, payload, *, headers=None, status_code=200):
        self._payload = payload
        self.headers = headers or {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _AsyncClient:
    responses = []
    requests = []

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, endpoint, *, headers, json):
        self.requests.append((endpoint, json))
        return self.responses.pop(0)


class ToolGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_document_tools_fall_back_through_shared_registry(self):
        gateway = ToolGateway()
        expected = ToolResult(
            tool_name="data_catalog_lookup",
            status="succeeded",
            data=[{"name": "fact_order"}],
            source="local",
        )
        with patch.object(
            default_registry, "call", new_callable=AsyncMock, return_value=expected
        ) as local_call:
            result = await gateway.call(
                "data_catalog_lookup",
                {"query": "销售"},
                ToolContext(user_id=42, agent_name="data"),
            )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.source, "local")
        local_call.assert_awaited_once()

    async def test_local_fallback_preserves_tool_result_contract_and_identity(self):
        gateway = ToolGateway(
            document_service=_Documents(),
            config=GatewayConfig(endpoint="http://127.0.0.1:1", service_token="secret"),
        )
        result = await gateway.call(
            "hybrid_document_search",
            {"query": "报销"},
            ToolContext(user_id=42, run_id="run-1", agent_name="knowledge"),
        )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.source, "local")
        self.assertEqual(result.data[0]["user_id"], 42)
        self.assertEqual(result.citations[0].filename, "policy.md")

    async def test_local_allowlist_rejects_wrong_agent(self):
        gateway = ToolGateway(document_service=_Documents())
        result = await gateway.call(
            "hybrid_document_search",
            {"query": "报销"},
            ToolContext(user_id=42, agent_name="general"),
        )

        self.assertEqual(result.error_code, "TOOL_NOT_ALLOWED")

    async def test_evidence_tool_runs_deterministic_verification(self):
        gateway = ToolGateway(document_service=_Documents())
        result = await gateway.call(
            "verify_evidence",
            {
                "evidence": {
                    "answer": "报销需要审批。[1]",
                    "result": [{"销售额": 100}],
                    "citations": [{"index": 1}],
                    "data_succeeded": True,
                    "knowledge_succeeded": True,
                }
            },
            ToolContext(user_id=42, agent_name="report"),
        )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.data["decision"], "pass")

    async def test_evidence_tool_blocks_missing_required_evidence(self):
        gateway = ToolGateway(document_service=_Documents())
        result = await gateway.call(
            "verify_evidence",
            {
                "evidence": {
                    "answer": "没有引用",
                    "result": None,
                    "citations": [],
                    "data_succeeded": False,
                    "knowledge_succeeded": False,
                }
            },
            ToolContext(user_id=42, agent_name="report"),
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "EVIDENCE_BLOCKED")
        self.assertEqual(result.data["decision"], "block")

    async def test_remote_handshake_discovers_tools_before_calling(self):
        _AsyncClient.responses = [
            _Response(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"protocolVersion": "2025-03-26"},
                },
                headers={"Mcp-Session-Id": "session-1"},
            ),
            _Response({"jsonrpc": "2.0", "result": {}}),
            _Response(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "tools": [
                            {
                                "name": "hybrid_document_search",
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    },
                }
            ),
            _Response(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "result": {
                        "structuredContent": {
                            "tool_name": "hybrid_document_search",
                            "status": "succeeded",
                            "data": [{"document_id": "doc-1"}],
                            "citations": [],
                            "source": "mcp",
                        }
                    },
                }
            ),
        ]
        _AsyncClient.requests = []
        gateway = ToolGateway(
            document_service=_Documents(),
            config=GatewayConfig(endpoint="http://mcp.test", service_token="secret"),
        )
        with patch("httpx.AsyncClient", _AsyncClient):
            result = await gateway._call_remote(
                "hybrid_document_search",
                {"query": "报销"},
                ToolContext(user_id=42, run_id="run-1", agent_name="knowledge"),
            )

        self.assertEqual(result.source, "mcp")
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(
            [request[1]["method"] for request in _AsyncClient.requests],
            ["initialize", "notifications/initialized", "tools/list", "tools/call"],
        )
        self.assertEqual(_AsyncClient.requests[-1][1]["id"], 3)

    async def test_remote_tool_not_advertised_is_rejected_before_call(self):
        _AsyncClient.responses = [
            _Response(
                {"jsonrpc": "2.0", "id": 1, "result": {}},
                headers={"Mcp-Session-Id": "s"},
            ),
            _Response({"jsonrpc": "2.0", "result": {}}),
            _Response({"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}),
        ]
        _AsyncClient.requests = []
        gateway = ToolGateway(
            config=GatewayConfig(endpoint="http://mcp.test", service_token="secret")
        )
        with patch("httpx.AsyncClient", _AsyncClient):
            result = await gateway._call_remote(
                "safe_readonly_query",
                {"sql": "select 1"},
                ToolContext(user_id=42, run_id="run-1", agent_name="data"),
            )

        self.assertEqual(result.error_code, "TOOL_NOT_ALLOWED")
        self.assertEqual(len(_AsyncClient.requests), 3)


if __name__ == "__main__":
    unittest.main()
