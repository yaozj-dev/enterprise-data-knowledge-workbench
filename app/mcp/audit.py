"""Durable, low-cardinality audit records for internal tool calls.

Only operational metadata is stored here. Tool arguments, prompts, document
contents and credentials never cross this boundary. The recorder is fail-open
so an audit database outage cannot turn a read-only answer into a user-facing
failure.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import text

from app.agent.contracts import ToolResult
from app.clients.mysql_client_manager import meta_mysql_client_manager
from app.core.log import logger
from app.mcp.tool_registry import ToolContext

MCP_AUDIT_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS mcp_tool_audit_logs (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    conversation_id VARCHAR(128),
    run_id VARCHAR(128),
    agent_name VARCHAR(40),
    tool_name VARCHAR(80) NOT NULL,
    source VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL,
    error_code VARCHAR(80),
    retryable BOOLEAN NOT NULL DEFAULT FALSE,
    duration_ms INT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_mcp_audit_user_time (user_id, created_at),
    INDEX idx_mcp_audit_tool_time (tool_name, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_schema_lock = asyncio.Lock()
_schema_ready = False


async def _ensure_table(session: Any) -> None:
    global _schema_ready
    if _schema_ready:
        return
    async with _schema_lock:
        if _schema_ready:
            return
        await session.execute(text(MCP_AUDIT_TABLE_DDL))
        await session.commit()
        _schema_ready = True


async def record_tool_call(
    result: ToolResult,
    context: ToolContext,
    duration_ms: int,
) -> None:
    """Persist one tool outcome while keeping the answer path fail-open."""

    factory = meta_mysql_client_manager.session_factory
    if factory is None:
        return
    try:
        async with factory() as session:
            await _ensure_table(session)
            await session.execute(
                text(
                    "INSERT INTO mcp_tool_audit_logs "
                    "(user_id, conversation_id, run_id, agent_name, tool_name, "
                    "source, status, error_code, retryable, duration_ms) "
                    "VALUES (:user_id, :conversation_id, :run_id, :agent_name, "
                    ":tool_name, :source, :status, :error_code, :retryable, :duration_ms)"
                ),
                {
                    "user_id": max(0, int(context.user_id)),
                    "conversation_id": (context.conversation_id or "")[:128] or None,
                    "run_id": (context.run_id or "")[:128] or None,
                    "agent_name": (context.agent_name or "")[:40] or None,
                    "tool_name": result.tool_name[:80],
                    "source": result.source,
                    "status": result.status,
                    "error_code": result.error_code,
                    "retryable": bool(result.retryable),
                    "duration_ms": max(0, int(duration_ms)),
                },
            )
            await session.commit()
    except Exception:
        logger.warning(
            "Unable to persist MCP tool audit record",
            tool_name=result.tool_name,
            status=result.status,
        )


async def list_tool_audit_logs(
    session: Any,
    *,
    limit: int = 50,
    offset: int = 0,
    user_id: int | None = None,
) -> dict[str, Any]:
    """Read bounded operational audit data for an administrator dashboard."""

    await _ensure_table(session)
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    condition = "WHERE user_id = :user_id" if user_id is not None else ""
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if user_id is not None:
        params["user_id"] = user_id
    total_result = await session.execute(
        text(f"SELECT COUNT(*) AS total FROM mcp_tool_audit_logs {condition}"),
        params,
    )
    total = int(total_result.scalar_one())
    result = await session.execute(
        text(
            "SELECT id, user_id, conversation_id, run_id, agent_name, tool_name, "
            "source, status, error_code, retryable, duration_ms, created_at "
            f"FROM mcp_tool_audit_logs {condition} "
            "ORDER BY id DESC LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    return {
        "items": [dict(row) for row in result.mappings().all()],
        "total": total,
        "limit": limit,
        "offset": offset,
    }

