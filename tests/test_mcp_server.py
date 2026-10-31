import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import (
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.mcp.server import app


class MCPServerTests(unittest.TestCase):
    def test_standalone_service_lifespan_and_health_endpoint(self):
        with (
            patch.dict(os.environ, {"MCP_SERVICE_TOKEN": "test-token"}, clear=False),
            patch.object(qdrant_client_manager, "init") as qdrant_init,
            patch.object(embedding_client_manager, "init") as embedding_init,
            patch.object(es_client_manager, "init") as es_init,
            patch.object(meta_mysql_client_manager, "init") as meta_init,
            patch.object(dw_mysql_client_manager, "init") as dw_init,
            patch.object(qdrant_client_manager, "close", new_callable=AsyncMock),
            patch.object(es_client_manager, "close", new_callable=AsyncMock),
            patch.object(meta_mysql_client_manager, "close", new_callable=AsyncMock),
            patch.object(dw_mysql_client_manager, "close", new_callable=AsyncMock),
            patch("app.mcp.server.bind_default_business_tools") as bind_tools,
        ):
            with TestClient(app) as client:
                health = client.get("/health")
                unauthorized = client.post(
                    "/mcp",
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                )
                headers = {
                    "host": "127.0.0.1:8000",
                    "accept": "application/json, text/event-stream",
                    "x-mcp-service-token": "test-token",
                    "x-agent-name": "data",
                    "x-user-id": "42",
                }
                initialized = client.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-03-26",
                            "capabilities": {},
                            "clientInfo": {"name": "test", "version": "1"},
                        },
                    },
                )
                headers["mcp-session-id"] = initialized.headers["mcp-session-id"]
                ready = client.post(
                    "/mcp",
                    headers=headers,
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                )
                listed = client.post(
                    "/mcp",
                    headers=headers,
                    json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
                )

            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json(), {"status": "ok"})
            self.assertEqual(unauthorized.status_code, 401)
            self.assertEqual(initialized.status_code, 200)
            self.assertEqual(ready.status_code, 202)
            self.assertEqual(listed.status_code, 200)
            self.assertIn(
                "safe_readonly_query",
                {tool["name"] for tool in listed.json()["result"]["tools"]},
            )
            for initialize in (
                qdrant_init,
                embedding_init,
                es_init,
                meta_init,
                dw_init,
            ):
                initialize.assert_called_once_with()
            bind_tools.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
