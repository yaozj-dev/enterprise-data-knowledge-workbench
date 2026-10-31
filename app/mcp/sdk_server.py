"""Official MCP SDK transport for the internal tool boundary.

The workbench keeps the small JSON-RPC route in ``mcp_router`` for backwards
compatibility and tests. This module exposes the same registry through the
official MCP Python SDK's Streamable HTTP transport when a deployment wants a
standards-based MCP endpoint.
"""

from __future__ import annotations

import hmac
import json
import os
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.serialization import json_default
from app.mcp.tool_registry import ToolContext, default_registry
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent

DEFAULT_MCP_ALLOWED_HOSTS = frozenset(
    {
        "localhost:*",
        "127.0.0.1:*",
        "[::1]:*",
        "backend:*",
        "enterprise-data-backend:*",
        "mcp:*",
    }
)


def configured_mcp_hosts(raw: str | None = None) -> set[str]:
    """Return a non-empty host allowlist even when an env value is blank."""

    raw_value = os.getenv("MCP_ALLOWED_HOSTS") if raw is None else raw
    configured = {item.strip() for item in (raw_value or "").split(",") if item.strip()}
    return configured or set(DEFAULT_MCP_ALLOWED_HOSTS)


class ServiceTokenMiddleware(BaseHTTPMiddleware):
    """Require the internal service token before the SDK sees a request."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path.rstrip("/") == "/health":
            return await call_next(request)
        expected = os.getenv("MCP_SERVICE_TOKEN", "").strip()
        supplied = request.headers.get("x-mcp-service-token", "")
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            return JSONResponse(
                {"error": "MCP service authentication failed"}, status_code=401
            )
        if request.method == "POST" and request.url.path.rstrip("/") == "/mcp":
            body = await request.body()
            try:
                payload = json.loads(body)
            except (TypeError, ValueError):
                payload = None
            if isinstance(payload, dict) and payload.get("method") == "tools/call":
                params = payload.get("params")
                if not isinstance(params, dict):
                    return JSONResponse(
                        _rpc_error(payload.get("id"), -32602, "Invalid params")
                    )
                name = params.get("name")
                arguments = params.get("arguments", {})
                if not isinstance(name, str):
                    return JSONResponse(
                        _rpc_error(payload.get("id"), -32602, "Invalid params")
                    )
                error_code = default_registry.validate_arguments(name, arguments)
                if error_code == "METHOD_NOT_FOUND":
                    return JSONResponse(
                        _rpc_error(payload.get("id"), -32601, "Method not found")
                    )
                if error_code == "INVALID_PARAMS":
                    return JSONResponse(
                        _rpc_error(payload.get("id"), -32602, "Invalid params")
                    )
            if isinstance(payload, dict) and payload.get("method") == "tools/list":
                # FastMCP registers one stable protocol surface, but discovery
                # must still respect the server-owned Agent boundary.  Filter
                # the advertised capabilities before the request reaches the
                # SDK so a caller cannot use tools/list to enumerate tools it
                # is not allowed to call.
                agent_name = request.headers.get("x-agent-name", "supervisor")
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": payload.get("id"),
                        "result": {
                            "tools": default_registry.definitions(agent_name)
                        },
                    }
                )
        return await call_next(request)


def _request_header(context: Context, name: str, default: str = "") -> str:
    request = context.request_context.request
    headers = getattr(request, "headers", {}) if request is not None else {}
    value = headers.get(name, default)
    return str(value)


def _rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _tool_context(context: Context) -> ToolContext:
    try:
        user_id = int(_request_header(context, "x-user-id", "0"))
    except ValueError:
        user_id = 0
    return ToolContext(
        user_id=user_id,
        run_id=_request_header(context, "x-run-id", "mcp-sdk"),
        conversation_id=_request_header(context, "x-conversation-id"),
        source="mcp",
        agent_name=_request_header(context, "x-agent-name", "supervisor"),
    )


def _register_registry_tools(server: FastMCP) -> None:
    """Expose registry schemas while keeping authorization in the registry."""

    async def call_registry(
        tool_name: str, arguments: dict[str, Any], context: Context
    ) -> CallToolResult:
        result = await default_registry.call(
            tool_name, arguments, _tool_context(context)
        )
        payload = result.model_dump(mode="json")
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(payload, ensure_ascii=False, default=json_default),
                )
            ],
            structuredContent=payload,
            isError=result.status != "succeeded",
        )

    async def hybrid_document_search(query: str, ctx: Context) -> CallToolResult:
        return await call_registry("hybrid_document_search", {"query": query}, ctx)

    async def data_catalog_lookup(query: str, ctx: Context) -> CallToolResult:
        return await call_registry("data_catalog_lookup", {"query": query}, ctx)

    async def safe_readonly_query(sql: str, ctx: Context) -> CallToolResult:
        return await call_registry("safe_readonly_query", {"sql": sql}, ctx)

    async def search_confirmed_memory(query: str, ctx: Context) -> CallToolResult:
        return await call_registry("search_confirmed_memory", {"query": query}, ctx)

    async def propose_memory(content: str, ctx: Context) -> CallToolResult:
        return await call_registry("propose_memory", {"content": content}, ctx)

    async def web_search(query: str, ctx: Context) -> CallToolResult:
        return await call_registry("web_search", {"query": query}, ctx)

    async def verify_evidence(
        evidence: dict[str, Any], ctx: Context
    ) -> CallToolResult:
        return await call_registry("verify_evidence", {"evidence": evidence}, ctx)

    handlers = {
        "hybrid_document_search": hybrid_document_search,
        "data_catalog_lookup": data_catalog_lookup,
        "safe_readonly_query": safe_readonly_query,
        "search_confirmed_memory": search_confirmed_memory,
        "propose_memory": propose_memory,
        "web_search": web_search,
        "verify_evidence": verify_evidence,
    }

    for definition in default_registry.definitions():
        name = definition["name"]
        description = definition["description"]
        handler = handlers.get(name)
        if handler is None:
            continue
        server.tool(
            name=name,
            description=description,
        )(handler)


async def _health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def create_mcp_sdk_app(
    *,
    lifespan: Callable[..., AbstractAsyncContextManager[Any]] | None = None,
):
    """Build the SDK ASGI app mounted by :mod:`main`.

    The endpoint is mounted at ``/internal/mcp-sdk/mcp`` by the FastAPI app.
    It remains unusable until ``MCP_SERVICE_TOKEN`` is configured, so merely
    shipping the endpoint does not create an unauthenticated tool surface.
    """

    configured_hosts = configured_mcp_hosts()
    server = FastMCP(
        name="enterprise-data-knowledge-tools",
        instructions="Internal allowlisted tools for the enterprise data knowledge workbench.",
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=False,
        lifespan=lifespan,
        transport_security=TransportSecuritySettings(
            allowed_hosts=sorted(configured_hosts),
        ),
    )
    _register_registry_tools(server)
    app = server.streamable_http_app()
    app.add_middleware(ServiceTokenMiddleware)
    app.add_route("/health", _health, methods=["GET"])
    return app


mcp_sdk_app = create_mcp_sdk_app()

