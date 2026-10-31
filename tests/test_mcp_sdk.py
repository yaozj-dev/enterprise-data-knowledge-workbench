import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.mcp.sdk_server import configured_mcp_hosts, create_mcp_sdk_app


class MCPSDKTests(unittest.TestCase):
    def test_blank_host_configuration_uses_internal_defaults(self):
        self.assertIn("mcp:*", configured_mcp_hosts(""))
        self.assertIn("127.0.0.1:*", configured_mcp_hosts("127.0.0.1:*"))

    def _client(self):
        patcher = patch.dict(os.environ, {"MCP_SERVICE_TOKEN": "sdk-secret"}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        return TestClient(create_mcp_sdk_app())

    @staticmethod
    def _headers(agent: str = "data"):
        return {
            "host": "127.0.0.1:8000",
            "accept": "application/json, text/event-stream",
            "x-mcp-service-token": "sdk-secret",
            "x-agent-name": agent,
            "x-user-id": "7",
            "x-run-id": "test-run",
        }

    def _initialize(self, client, headers):
        response = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0"},
                },
            },
        )
        self.assertEqual(response.status_code, 200)
        headers["mcp-session-id"] = response.headers["mcp-session-id"]
        initialized = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
        )
        self.assertEqual(initialized.status_code, 202)

    def test_token_and_tool_schema_are_enforced(self):
        client = self._client()
        health = client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json(), {"status": "ok"})
        bad = client.post(
            "/mcp",
            headers={"host": "127.0.0.1:8000"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        self.assertEqual(bad.status_code, 401)

        with client:
            headers = self._headers()
            self._initialize(client, headers)
            listed = client.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                },
            )
            tools = {item["name"]: item for item in listed.json()["result"]["tools"]}
            self.assertIn("safe_readonly_query", tools)
            self.assertIn("sql", tools["safe_readonly_query"]["inputSchema"]["properties"])
            self.assertNotIn("arguments", tools["safe_readonly_query"]["inputSchema"]["properties"])

    def test_agent_allowlist_returns_protocol_error_result(self):
        client = self._client()
        with client:
            headers = self._headers(agent="general")
            self._initialize(client, headers)
            response = client.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "safe_readonly_query",
                        "arguments": {"sql": "select 1"},
                    },
                },
            )
            body = response.json()["result"]
            self.assertTrue(body["isError"])
            self.assertEqual(body["structuredContent"]["error_code"], "TOOL_NOT_ALLOWED")

    def test_tools_list_only_advertises_agent_allowed_tools(self):
        client = self._client()
        with client:
            data_headers = self._headers(agent="data")
            self._initialize(client, data_headers)
            data_list = client.post(
                "/mcp",
                headers=data_headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/list",
                    "params": {},
                },
            )
            data_names = {item["name"] for item in data_list.json()["result"]["tools"]}
            self.assertIn("safe_readonly_query", data_names)
            self.assertNotIn("hybrid_document_search", data_names)

            general_headers = self._headers(agent="general")
            self._initialize(client, general_headers)
            general_list = client.post(
                "/mcp",
                headers=general_headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "tools/list",
                    "params": {},
                },
            )
            general_names = {
                item["name"] for item in general_list.json()["result"]["tools"]
            }
            self.assertNotIn("safe_readonly_query", general_names)
            self.assertNotIn("hybrid_document_search", general_names)

    def test_invalid_tool_arguments_use_json_rpc_invalid_params(self):
        client = self._client()
        with client:
            headers = self._headers()
            self._initialize(client, headers)
            response = client.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "safe_readonly_query",
                        "arguments": {"sql": 1},
                    },
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["error"]["code"], -32602)


if __name__ == "__main__":
    unittest.main()
