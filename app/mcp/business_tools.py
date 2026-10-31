"""Handlers for the internal MCP registry.

The SDK transport is initialized at import time, while database clients are
initialized by FastAPI's lifespan.  This module binds handlers after startup
so the protocol surface remains stable without creating connections too early.
"""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy import text

from app.agent.contracts import Citation, ToolResult
from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import (
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.mcp.audit import record_tool_call
from app.mcp.tool_registry import ToolContext, default_registry
from app.repositories.es.document_es_repository import DocumentESRepository
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta.memory_repository import MemoryRepository
from app.repositories.qdrant.document_qdrant_repository import DocumentQdrantRepository
from app.services.document_service import DocumentService
from app.services.evidence_verifier import verify_report_programmatically
from app.services.hybrid_retrieval import HttpReranker
from app.services.sql_policy import SQLPolicyError, normalize_read_query


def _source(context: ToolContext) -> str:
    return context.source if context.source in {"mcp", "local"} else "system"


def _identity_error(tool_name: str, context: ToolContext) -> ToolResult | None:
    if context.user_id < 1:
        return ToolResult(
            tool_name=tool_name,
            status="failed",
            error_code="INVALID_IDENTITY",
            retryable=False,
            source=_source(context),
        )
    return None


async def _data_catalog_lookup(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    error = _identity_error("data_catalog_lookup", context)
    query = arguments.get("query")
    if error:
        return error
    if not isinstance(query, str) or not query.strip():
        return ToolResult(tool_name="data_catalog_lookup", status="failed", error_code="INVALID_PARAMS", source=_source(context))
    factory = meta_mysql_client_manager.session_factory
    if factory is None:
        return ToolResult(tool_name="data_catalog_lookup", status="failed", error_code="TOOL_UNAVAILABLE", retryable=True, source=_source(context))
    pattern = f"%{query.strip()[:100]}%"
    async with factory() as session:
        rows: list[dict[str, Any]] = []
        for table_name, kind in (("table_info", "table"), ("column_info", "column"), ("metric_info", "metric")):
            result = await session.execute(
                text(
                    f"SELECT id, name, description FROM {table_name} "
                    "WHERE name LIKE :pattern OR description LIKE :pattern LIMIT 20"
                ),
                {"pattern": pattern},
            )
            rows.extend({"kind": kind, **dict(row)} for row in result.mappings().all())
    return ToolResult(tool_name="data_catalog_lookup", status="succeeded", data=rows[:20], source=_source(context))


async def _safe_readonly_query(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    error = _identity_error("safe_readonly_query", context)
    sql = arguments.get("sql")
    if error:
        return error
    if not isinstance(sql, str) or not sql.strip():
        return ToolResult(tool_name="safe_readonly_query", status="failed", error_code="INVALID_PARAMS", source=_source(context))
    try:
        normalized = normalize_read_query(sql)
    except SQLPolicyError:
        return ToolResult(tool_name="safe_readonly_query", status="failed", error_code="SQL_NOT_ALLOWED", retryable=False, source=_source(context))
    factory = dw_mysql_client_manager.session_factory
    if factory is None:
        return ToolResult(tool_name="safe_readonly_query", status="failed", error_code="TOOL_UNAVAILABLE", retryable=True, source=_source(context))
    try:
        async with factory() as session:
            rows = await DWMySQLRepository(session).run(normalized)
    except TimeoutError:
        return ToolResult(tool_name="safe_readonly_query", status="timed_out", error_code="TOOL_TIMEOUT", retryable=True, source=_source(context))
    except Exception:
        return ToolResult(tool_name="safe_readonly_query", status="failed", error_code="TOOL_EXECUTION_FAILED", retryable=True, source=_source(context))
    return ToolResult(tool_name="safe_readonly_query", status="succeeded", data=rows, source=_source(context))


def _document_service() -> DocumentService:
    reranker_url = os.getenv("RERANKER_URL", "").strip()
    reranker_timeout = float(os.getenv("RERANKER_TIMEOUT_SECONDS", "30"))
    reranker_cooldown = float(os.getenv("RERANKER_FAILURE_COOLDOWN_SECONDS", "60"))
    reranker = (
        HttpReranker(
            reranker_url,
            timeout_seconds=reranker_timeout,
            failure_cooldown_seconds=reranker_cooldown,
        )
        if reranker_url
        else None
    )
    return DocumentService(
        DocumentQdrantRepository(qdrant_client_manager.client),
        embedding_client_manager.client,
        DocumentESRepository(es_client_manager.client),
        reranker,
    )


async def _hybrid_document_search(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    error = _identity_error("hybrid_document_search", context)
    query = arguments.get("query")
    if error:
        return error
    if not isinstance(query, str) or not query.strip():
        return ToolResult(tool_name="hybrid_document_search", status="failed", error_code="INVALID_PARAMS", source=_source(context))
    try:
        hits = await _document_service().search(context.user_id, query.strip(), limit=5)
    except Exception:
        return ToolResult(tool_name="hybrid_document_search", status="failed", error_code="TOOL_EXECUTION_FAILED", retryable=True, source=_source(context))
    citations = [
        Citation.from_document_hit(index, hit)
        for index, hit in enumerate(hits, start=1)
    ]
    return ToolResult(
        tool_name="hybrid_document_search",
        status="succeeded",
        data=hits,
        citations=citations,
        source=_source(context),
    )


async def _search_confirmed_memory(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    error = _identity_error("search_confirmed_memory", context)
    query = arguments.get("query")
    if error:
        return error
    if not isinstance(query, str) or not query.strip():
        return ToolResult(tool_name="search_confirmed_memory", status="failed", error_code="INVALID_PARAMS", source=_source(context))
    factory = meta_mysql_client_manager.session_factory
    if factory is None:
        return ToolResult(tool_name="search_confirmed_memory", status="failed", error_code="TOOL_UNAVAILABLE", retryable=True, source=_source(context))
    async with factory() as session:
        repository = MemoryRepository(session)
        await repository.ensure_tables()
        memories = await repository.search_confirmed(context.user_id, query.strip())
    return ToolResult(tool_name="search_confirmed_memory", status="succeeded", data=memories, source=_source(context))


async def _propose_memory(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    error = _identity_error("propose_memory", context)
    content = arguments.get("content")
    if error:
        return error
    if not isinstance(content, str) or not content.strip() or len(content) > 2_000:
        return ToolResult(tool_name="propose_memory", status="failed", error_code="INVALID_PARAMS", source=_source(context))
    factory = meta_mysql_client_manager.session_factory
    if factory is None:
        return ToolResult(tool_name="propose_memory", status="failed", error_code="TOOL_UNAVAILABLE", retryable=True, source=_source(context))
    async with factory() as session:
        repository = MemoryRepository(session)
        await repository.ensure_tables()
        candidate = await repository.propose(context.user_id, content.strip(), source_run_id=context.run_id)
    return ToolResult(tool_name="propose_memory", status="succeeded", data=candidate, source=_source(context))


async def _verify_evidence(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    evidence = arguments.get("evidence")
    if not isinstance(evidence, dict):
        return ToolResult(tool_name="verify_evidence", status="failed", error_code="INVALID_PARAMS", source=_source(context))
    result = verify_report_programmatically(
        str(evidence.get("answer", "")),
        result=evidence.get("result"),
        citations=evidence.get("citations") if isinstance(evidence.get("citations"), list) else [],
        data_succeeded=bool(evidence.get("data_succeeded")),
        knowledge_succeeded=bool(evidence.get("knowledge_succeeded")),
        document_evidence=evidence.get("document_evidence"),
    )
    blocked = result.decision == "block"
    return ToolResult(
        tool_name="verify_evidence",
        status="failed" if blocked else "succeeded",
        data={"decision": result.decision, "reasons": list(result.reasons)},
        error_code="EVIDENCE_BLOCKED" if blocked else None,
        source=_source(context),
    )


def bind_default_business_tools() -> None:
    """Bind real handlers after external clients are ready."""

    # The registry is shared by the local adapter and the MCP transport. The
    # same sink therefore records both execution paths with one contract.
    default_registry.set_audit_sink(record_tool_call)

    handlers = {
        "hybrid_document_search": _hybrid_document_search,
        "data_catalog_lookup": _data_catalog_lookup,
        "safe_readonly_query": _safe_readonly_query,
        "search_confirmed_memory": _search_confirmed_memory,
        "propose_memory": _propose_memory,
        "verify_evidence": _verify_evidence,
    }
    for name, handler in handlers.items():
        default_registry.bind_handler(name, handler)
