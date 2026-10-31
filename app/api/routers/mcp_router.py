"""Minimal Streamable-HTTP-compatible MCP JSON-RPC boundary.

The backend uses this route as an internal tool seam.  It implements the
protocol messages needed by the workbench (initialize, tools/list, tools/call)
and keeps transport errors distinct from business ``ToolResult`` errors.
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse

from app.mcp.tool_registry import ToolContext, default_registry

mcp_router = APIRouter(prefix="/internal/mcp", tags=["internal-mcp"])


def _rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


@mcp_router.post("")
async def mcp_message(
    payload: dict[str, Any],
    x_mcp_service_token: str | None = Header(default=None),
    x_agent_name: str = Header(default="supervisor"),
    x_user_id: int = Header(default=0),
    x_run_id: str = Header(default="mcp"),
    x_conversation_id: str = Header(default=""),
    x_mcp_session_id: str | None = Header(default=None),
):
    if not default_registry.authenticate(x_mcp_service_token):
        raise HTTPException(status_code=401, detail="MCP service authentication failed")
    request_id = payload.get("id")
    if payload.get("jsonrpc") != "2.0" or "method" not in payload:
        return _rpc_error(request_id, -32600, "Invalid Request")
    method = payload["method"]
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        return _rpc_error(request_id, -32602, "Invalid params")

    if method == "initialize":
        session_id = x_mcp_session_id or secrets.token_urlsafe(18)
        response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "enterprise-data-knowledge-tools", "version": "1.0"},
            },
        }
        return JSONResponse(response, headers={"Mcp-Session-Id": session_id})
    if method == "notifications/initialized":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": default_registry.definitions(x_agent_name)},
        }
    if method != "tools/call":
        return _rpc_error(request_id, -32601, "Method not found")

    name = params.get("name")
    arguments = params.get("arguments", {})
    if not isinstance(name, str) or not isinstance(arguments, dict):
        return _rpc_error(request_id, -32602, "Invalid params")
    if x_user_id < 1:
        return _rpc_error(request_id, -32602, "Invalid params")
    context = ToolContext(
        user_id=x_user_id,
        run_id=x_run_id,
        conversation_id=x_conversation_id,
        source="mcp",
        agent_name=x_agent_name,
    )
    # Agent identity is supplied by the authenticated backend header and is
    # checked by the registry; user identity is attached by the real backend
    # adapter before a business call, never accepted from model arguments.
    result = await default_registry.call(name, arguments, context)
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "isError": result.status != "succeeded",
            "structuredContent": result.model_dump(mode="json"),
            "content": [{"type": "text", "text": result.model_dump_json()}],
        },
    }

