"""MCP-first tool gateway with a schema-identical local fallback.

The Supervisor never sends user identity as model-controlled arguments.  The
gateway attaches it to ``ToolContext`` and, for an internal HTTP call, to
trusted service headers.  Both paths return the same ``ToolResult`` contract.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from typing import Any

from app.agent.contracts import Citation, ToolResult
from app.mcp.tool_registry import ToolContext, ToolRegistry, default_registry
from app.observability.tracing import set_span_status, span


@dataclass(frozen=True)
class GatewayConfig:
    endpoint: str = ""
    service_token: str = ""
    timeout_seconds: float = 8.0

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        try:
            timeout = max(1.0, float(os.getenv("MCP_TIMEOUT_SECONDS", "8")))
        except ValueError:
            timeout = 8.0
        return cls(
            endpoint=os.getenv("MCP_TOOLS_URL", "").strip().rstrip("/"),
            service_token=os.getenv("MCP_SERVICE_TOKEN", "").strip(),
            timeout_seconds=timeout,
        )


def _document_citations(hits: list[dict[str, Any]]) -> list[Citation]:
    return [
        Citation.from_document_hit(index, hit)
        for index, hit in enumerate(hits, start=1)
    ]


class ToolGateway:
    """Route a bounded call to MCP and keep local behavior available."""

    def __init__(
        self,
        *,
        document_service: Any = None,
        config: GatewayConfig | None = None,
    ):
        self.document_service = document_service
        self.config = config or GatewayConfig.from_env()
        self.local_registry = ToolRegistry(service_token=secrets.token_urlsafe(16))
        # Local fallback calls are audited with the same safe operational
        # fields as remote MCP calls. Import lazily to keep unit-test startup
        # independent from database initialization.
        from app.mcp.audit import record_tool_call

        self.local_registry.set_audit_sink(record_tool_call)
        self._register_local_tools()
        self._local_tool_names = {
            tool["name"] for tool in self.local_registry.definitions()
        }

    def _register_local_tools(self) -> None:
        async def search_documents(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
            if self.document_service is None:
                return ToolResult(
                    tool_name="hybrid_document_search",
                    status="failed",
                    error_code="TOOL_UNAVAILABLE",
                    retryable=True,
                    source="local",
                )
            query = arguments.get("query")
            if not isinstance(query, str) or not query.strip():
                return ToolResult(
                    tool_name="hybrid_document_search",
                    status="failed",
                    error_code="INVALID_PARAMS",
                    source="local",
                )
            hits = await self.document_service.search(context.user_id, query.strip())
            return ToolResult(
                tool_name="hybrid_document_search",
                status="succeeded",
                data=hits,
                citations=_document_citations(hits),
                source="local",
            )

        async def verify_evidence(arguments: dict[str, Any], _context: ToolContext) -> ToolResult:
            evidence = arguments.get("evidence")
            if not isinstance(evidence, dict):
                return ToolResult(
                    tool_name="verify_evidence",
                    status="failed",
                    error_code="INVALID_PARAMS",
                    source="local",
                )
            from app.services.evidence_verifier import verify_report_programmatically

            check = verify_report_programmatically(
                str(evidence.get("answer", "")),
                result=evidence.get("result"),
                citations=evidence.get("citations", []),
                data_succeeded=bool(evidence.get("data_succeeded")),
                knowledge_succeeded=bool(evidence.get("knowledge_succeeded")),
                document_evidence=evidence.get("document_evidence"),
            )
            blocked = check.decision == "block"
            return ToolResult(
                tool_name="verify_evidence",
                status="failed" if blocked else "succeeded",
                data={
                    "decision": check.decision,
                    "reasons": list(check.reasons),
                },
                error_code="EVIDENCE_BLOCKED" if blocked else None,
                source="local",
            )

        self.local_registry.register(
            "hybrid_document_search",
            "Search user-owned documents with vector and keyword retrieval.",
            {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            search_documents,
            allowed_agents={"knowledge", "report"},
        )
        self.local_registry.register(
            "verify_evidence",
            "Run deterministic evidence checks before report generation.",
            {"type": "object", "properties": {"evidence": {"type": "object"}}, "required": ["evidence"]},
            verify_evidence,
            allowed_agents={"report"},
        )

    async def call(
        self,
        name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        async with span("agent.tool", {"tool.name": name, "tool.agent": context.agent_name}) as current_span:
            if self.config.endpoint and self.config.service_token:
                try:
                    result = await self._call_remote(name, arguments, context)
                    # Only transport/service failures are eligible for local
                    # degradation. Deterministic business rejections must not
                    # be silently replaced by another execution path.
                    if result.status == "succeeded" or not result.retryable:
                        set_span_status(current_span, "ok", **{"tool.source": "mcp", "tool.status": result.status})
                        return result
                except Exception:
                    # The local adapter is the explicit degradation path. The
                    # Agent sees only the shared ToolResult shape.
                    pass
            local_context = ToolContext(
                user_id=context.user_id,
                run_id=context.run_id,
                conversation_id=context.conversation_id,
                source="local",
                agent_name=context.agent_name,
            )
            if name in self._local_tool_names:
                result = await self.local_registry.call(name, arguments, local_context)
            else:
                result = await default_registry.call(name, arguments, local_context)
            set_span_status(current_span, "ok", **{"tool.source": "local", "tool.status": result.status})
            return result

    async def _call_remote(
        self,
        name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        import httpx

        headers = {
            "Accept": "application/json, text/event-stream",
            "X-MCP-Service-Token": self.config.service_token,
            "X-Agent-Name": context.agent_name,
            "X-User-Id": str(context.user_id),
            "X-Run-Id": context.run_id,
            "X-Conversation-Id": context.conversation_id,
        }
        async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
            initialize = await client.post(
                self.config.endpoint,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {
                            "name": "enterprise-data-knowledge-workbench",
                            "version": "1.0",
                        },
                    },
                },
            )
            initialize.raise_for_status()
            initialize_payload = initialize.json()
            if initialize_payload.get("error"):
                return self._remote_error(name, initialize_payload["error"])
            if not isinstance(initialize_payload.get("result"), dict):
                raise RuntimeError("MCP initialize response is malformed")
            session_id = initialize.headers.get("Mcp-Session-Id")
            if session_id:
                headers["Mcp-Session-Id"] = session_id

            # Complete the protocol handshake before discovering capabilities.
            # Some Streamable HTTP servers answer notifications with 202/204;
            # their body is intentionally ignored.
            initialized = await client.post(
                self.config.endpoint,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                },
            )
            initialized.raise_for_status()

            listed = await client.post(
                self.config.endpoint,
                headers=headers,
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            )
            listed.raise_for_status()
            listed_payload = listed.json()
            if listed_payload.get("error"):
                return self._remote_error(name, listed_payload["error"])
            listed_result = listed_payload.get("result") or {}
            advertised_tools = listed_result.get("tools")
            if not isinstance(advertised_tools, list):
                raise RuntimeError("MCP tools/list response is malformed")
            advertised_names = {
                item.get("name")
                for item in advertised_tools
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            }
            if name not in advertised_names:
                return ToolResult(
                    tool_name=name,
                    status="failed",
                    error_code="TOOL_NOT_ALLOWED",
                    retryable=False,
                    source="mcp",
                )

            response = await client.post(
                self.config.endpoint,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
            )
            response.raise_for_status()
            payload = response.json()
        if payload.get("error"):
            return self._remote_error(name, payload["error"])
        result = payload.get("result") or {}
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            try:
                return ToolResult.model_validate(structured).model_copy(
                    update={"source": "mcp", "tool_name": name}
                )
            except Exception as exc:
                raise RuntimeError("MCP structuredContent does not match ToolResult") from exc
        return ToolResult(
            tool_name=name,
            status="failed" if result.get("isError") else "succeeded",
            data=result.get("content"),
            source="mcp",
        )

    @staticmethod
    def _remote_error(name: str, error: Any) -> ToolResult:
        """Map JSON-RPC transport errors to the shared tool contract."""

        if not isinstance(error, dict):
            code = "TOOL_EXECUTION_FAILED"
        else:
            code = {"-32601": "METHOD_NOT_FOUND", "-32602": "INVALID_PARAMS"}.get(
                str(error.get("code")), "TOOL_EXECUTION_FAILED"
            )
        return ToolResult(
            tool_name=name,
            status="failed",
            error_code=code,
            retryable=code == "TOOL_EXECUTION_FAILED",
            source="mcp",
        )


default_tool_gateway = ToolGateway()
