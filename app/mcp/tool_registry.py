"""Tool registry shared by the MCP transport and local fallback adapters."""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Any, Awaitable, Callable

from app.agent.contracts import ToolResult
from app.observability.tracing import set_span_status, span

ToolHandler = Callable[[dict[str, Any], "ToolContext"], Awaitable[ToolResult]]
AuditSink = Callable[[ToolResult, "ToolContext", int], Awaitable[None]]


def _matches_schema(value: Any, schema: dict[str, Any]) -> bool:
    """Validate the small JSON Schema subset used by internal tools."""

    expected_type = schema.get("type")
    if expected_type == "object" and not isinstance(value, dict):
        return False
    if expected_type == "string" and not isinstance(value, str):
        return False
    if expected_type == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
        return False
    if expected_type == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
        return False
    if expected_type == "boolean" and not isinstance(value, bool):
        return False

    if expected_type == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if any(name not in value for name in required):
            return False
        if schema.get("additionalProperties") is False and any(
            name not in properties for name in value
        ):
            return False
        return all(
            name not in value or _matches_schema(value[name], child_schema)
            for name, child_schema in properties.items()
            if isinstance(child_schema, dict)
        )
    return True


@dataclass(frozen=True)
class ToolContext:
    """Server-owned identity and request metadata; never model supplied."""

    user_id: int
    run_id: str = ""
    conversation_id: str = ""
    source: str = "mcp"
    agent_name: str = ""


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    allowed_agents: frozenset[str]
    handler: ToolHandler


class ToolRegistry:
    """Small allowlisted registry with protocol-independent error semantics."""

    def __init__(self, service_token: str | None = None):
        self.service_token = service_token or os.getenv("MCP_SERVICE_TOKEN", "")
        self._definitions: dict[str, ToolDefinition] = {}
        self._audit_sink: AuditSink | None = None

    def set_audit_sink(self, sink: AuditSink | None) -> None:
        """Install a deployment-owned audit sink without changing tool handlers."""

        self._audit_sink = sink

    async def _finish(
        self,
        result: ToolResult,
        context: ToolContext,
        started_at: float,
    ) -> ToolResult:
        if self._audit_sink is not None:
            try:
                await self._audit_sink(
                    result,
                    context,
                    round((perf_counter() - started_at) * 1_000),
                )
            except Exception:
                # Audit persistence must never become a hidden business failure.
                pass
        return result

    def register(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: ToolHandler,
        *,
        allowed_agents: frozenset[str] | set[str],
    ) -> None:
        if not name or name in self._definitions:
            raise ValueError("tool name must be non-empty and unique")
        self._definitions[name] = ToolDefinition(
            name=name,
            description=description,
            input_schema=input_schema,
            allowed_agents=frozenset(allowed_agents),
            handler=handler,
        )

    def bind_handler(self, name: str, handler: ToolHandler) -> None:
        """Attach a deployment handler to a predeclared, allowlisted tool."""

        definition = self._definitions.get(name)
        if definition is None:
            raise KeyError(f"unknown tool: {name}")
        self._definitions[name] = replace(definition, handler=handler)

    def definitions(self, agent_name: str | None = None) -> list[dict[str, Any]]:
        values = self._definitions.values()
        if agent_name:
            values = [item for item in values if agent_name in item.allowed_agents]
        return [
            {
                "name": item.name,
                "description": item.description,
                "inputSchema": item.input_schema,
            }
            for item in values
        ]

    def validate_arguments(self, name: str, arguments: Any) -> str | None:
        """Return a protocol error code for a tool call before handler execution."""

        definition = self._definitions.get(name)
        if definition is None:
            return "METHOD_NOT_FOUND"
        if not _matches_schema(arguments, definition.input_schema):
            return "INVALID_PARAMS"
        return None

    def authenticate(self, supplied_token: str | None) -> bool:
        return bool(
            self.service_token
            and supplied_token
            and hmac.compare_digest(self.service_token, supplied_token)
        )

    async def call(
        self,
        name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        started_at = perf_counter()

        async def finish(result: ToolResult) -> ToolResult:
            return await self._finish(result, context, started_at)

        definition = self._definitions.get(name)
        if definition is None:
            return await finish(ToolResult(
                tool_name=name,
                status="failed",
                error_code="METHOD_NOT_FOUND",
                source="mcp",
            ))
        if context.source not in {"mcp", "local"}:
            return await finish(ToolResult(
                tool_name=name,
                status="failed",
                error_code="TOOL_NOT_ALLOWED",
                source="mcp",
            ))
        # The agent identity is carried out-of-band in the context by the
        # backend.  It is intentionally not read from arbitrary tool arguments.
        agent_name = getattr(context, "agent_name", "")
        if agent_name and agent_name not in definition.allowed_agents:
            return await finish(ToolResult(
                tool_name=name,
                status="failed",
                error_code="TOOL_NOT_ALLOWED",
                source="mcp",
            ))
        if not isinstance(arguments, dict):
            return await finish(ToolResult(
                tool_name=name,
                status="failed",
                error_code="INVALID_PARAMS",
                source="mcp",
            ))
        if not _matches_schema(arguments, definition.input_schema):
            return await finish(ToolResult(
                tool_name=name,
                status="failed",
                error_code="INVALID_PARAMS",
                source="mcp",
            ))
        async with span("mcp.tool_call", {"tool.name": name, "agent.name": agent_name}) as tool_span:
            try:
                result = await definition.handler(arguments, context)
                if result.tool_name != name:
                    result = result.model_copy(update={"tool_name": name})
                set_span_status(tool_span, "ok", **{"tool.status": result.status})
                return await finish(result)
            except TimeoutError:
                set_span_status(tool_span, "error", **{"tool.status": "timed_out"})
                return await finish(ToolResult(
                    tool_name=name,
                    status="timed_out",
                    error_code="TOOL_TIMEOUT",
                    retryable=True,
                    source="mcp",
                ))
            except Exception:
                set_span_status(tool_span, "error", **{"tool.status": "failed"})
                return await finish(ToolResult(
                    tool_name=name,
                    status="failed",
                    error_code="TOOL_EXECUTION_FAILED",
                    retryable=True,
                    source="mcp",
                ))


async def unavailable_tool(_arguments: dict[str, Any], _context: ToolContext) -> ToolResult:
    return ToolResult(
        tool_name="unavailable",
        status="failed",
        error_code="TOOL_UNAVAILABLE",
        retryable=True,
        source="local",
    )


default_registry = ToolRegistry()


def _register_builtin_tool_placeholders() -> None:
    """Expose stable schemas even before a deployment injects service handlers.

    The local adapters and the MCP transport share these names.  A deployment
    can replace a placeholder through a separate registry instance without
    changing any Agent plan or frontend contract.
    """

    definitions = (
        (
            "hybrid_document_search",
            "Search user-owned documents with vector and keyword retrieval.",
            {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            {"knowledge", "report"},
        ),
        (
            "data_catalog_lookup",
            "Look up allowlisted analytics metadata.",
            {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            {"data"},
        ),
        (
            "safe_readonly_query",
            "Execute one validated read-only analytics query.",
            {
                "type": "object",
                "properties": {"sql": {"type": "string"}},
                "required": ["sql"],
                "additionalProperties": False,
            },
            {"data"},
        ),
        (
            "search_confirmed_memory",
            "Search user-confirmed long-term memory.",
            {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            {"general", "report"},
        ),
        (
            "propose_memory",
            "Propose a memory candidate for explicit user confirmation.",
            {
                "type": "object",
                "properties": {"content": {"type": "string"}},
                "required": ["content"],
                "additionalProperties": False,
            },
            {"general", "report"},
        ),
        (
            "web_search",
            "Optional external search; disabled by default.",
            {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            set(),
        ),
        (
            "verify_evidence",
            "Run deterministic evidence checks before report generation.",
            {
                "type": "object",
                "properties": {"evidence": {"type": "object"}},
                "required": ["evidence"],
                "additionalProperties": False,
            },
            {"report"},
        ),
    )
    for name, description, schema, agents in definitions:
        default_registry.register(name, description, schema, unavailable_tool, allowed_agents=agents)


_register_builtin_tool_placeholders()

