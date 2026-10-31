import unittest
from unittest.mock import patch

from app.mcp import business_tools
from app.mcp.tool_registry import ToolContext


class _Documents:
    def __init__(self):
        self.calls: list[tuple[int, str, int]] = []

    async def search(self, user_id: int, query: str, limit: int = 5) -> list[dict]:
        self.calls.append((user_id, query, limit))
        return [
            {
                "document_id": "doc-1",
                "chunk_index": 0,
                "filename": "policy.md",
                "page_number": 2,
                "score": 0.91,
                "content": "审批流程",
            }
        ]


class MCPBusinessToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_document_handler_uses_server_owned_user_identity(self):
        documents = _Documents()
        with patch("app.mcp.business_tools._document_service", return_value=documents):
            result = await business_tools._hybrid_document_search(
                {"query": "报销流程"},
                ToolContext(user_id=42, source="mcp", agent_name="knowledge"),
            )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.source, "mcp")
        self.assertEqual(result.citations[0].filename, "policy.md")
        self.assertEqual(result.citations[0].document_id, "doc-1")
        self.assertEqual(result.citations[0].page_number, 2)
        self.assertEqual(documents.calls, [(42, "报销流程", 5)])

    async def test_readonly_handler_rejects_write_sql_before_database_access(self):
        result = await business_tools._safe_readonly_query(
            {"sql": "DELETE FROM fact_order"},
            ToolContext(user_id=42, source="mcp", agent_name="data"),
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "SQL_NOT_ALLOWED")

    async def test_evidence_handler_blocks_unverified_report(self):
        result = await business_tools._verify_evidence(
            {
                "evidence": {
                    "answer": "制度要求先审批。[2]",
                    "result": None,
                    "citations": [{"index": 1, "filename": "policy.md"}],
                    "data_succeeded": False,
                    "knowledge_succeeded": True,
                }
            },
            ToolContext(user_id=42, source="mcp", agent_name="report"),
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "EVIDENCE_BLOCKED")
