"""Authenticated workbench APIs."""

import asyncio
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from starlette.responses import StreamingResponse

from app.api.dependencies import (
    get_document_service,
    get_memory_repository,
    get_meta_session,
    get_optional_memory_repository,
    get_query_service,
)
from app.api.schemas.workbench_schema import (
    AgentFeedbackCreate,
    AgentFeedbackPage,
    AgentFeedbackReview,
    AgentFeedbackView,
    AgentRunView,
    AuditLogPage,
    ConversationCreate,
    DocumentView,
    LoginRequest,
    LoginResponse,
    MCPToolAuditPage,
    MemoryCandidateCreate,
    MemoryCandidateView,
    QualityOverview,
    RegisterRequest,
    RegressionCaseView,
    UserCreate,
    UserView,
    WorkbenchQuery,
)
from app.core.serialization import json_default
from app.mcp.audit import list_tool_audit_logs
from app.observability.tracing import set_span_status, span
from app.repositories.mysql.meta.memory_repository import MemoryRepository
from app.security import create_access_token, get_current_user, require_admin
from app.services.document_service import (
    DocumentNotFoundError,
    DocumentService,
    DuplicateDocumentError,
)
from app.services.quality_service import AgentRunTrace, parse_sse_payload
from app.services.query_service import QueryService
from app.services.runtime_state import runtime_state_store
from app.services.workbench_service import WorkbenchService
from app.storage.app_db import (
    add_message,
    count_active_admins,
    create_conversation,
    create_user,
    delete_user,
    get_agent_run_for_user,
    get_conversation,
    get_quality_overview,
    get_user,
    get_user_by_email,
    list_audit_logs,
    list_conversations,
    list_documents,
    list_messages,
    list_quality_feedback,
    list_recent_messages,
    list_regression_cases,
    list_users,
    review_agent_feedback,
    upsert_agent_feedback,
    verify_password,
    write_audit,
)

workbench_router = APIRouter(prefix="/api/workbench", tags=["workbench"])
logger = logging.getLogger(__name__)
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
ALLOWED_DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".md", ".txt"}


def self_registration_enabled() -> bool:
    """Make local onboarding simple while allowing shared deployments to close signup."""

    value = os.getenv("APP_ALLOW_SELF_REGISTRATION", "true").strip().lower()
    return value in {"1", "true", "yes", "on"}


@workbench_router.post("/auth/login", response_model=LoginResponse)
async def login(payload: LoginRequest):
    user = get_user_by_email(payload.email)
    if not user or not user["is_active"] or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    write_audit(user["id"], "login")
    return LoginResponse(
        access_token=create_access_token(user),
        user=UserView(**{key: user[key] for key in ("id", "email", "display_name", "role")}),
    )


@workbench_router.post("/auth/register", response_model=LoginResponse, status_code=201)
async def register(payload: RegisterRequest):
    """Create a normal user and immediately issue a session token."""

    if not self_registration_enabled():
        raise HTTPException(status_code=403, detail="当前部署已关闭自助注册，请联系管理员")
    try:
        user = create_user(payload.email, payload.display_name, payload.password, role="user")
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="该邮箱已经注册") from exc
    write_audit(user["id"], "self_register", user["email"])
    return LoginResponse(
        access_token=create_access_token(user),
        user=UserView(**{key: user[key] for key in ("id", "email", "display_name", "role")}),
    )


@workbench_router.get("/auth/me", response_model=UserView)
async def current_user(user: Annotated[dict, Depends(get_current_user)]):
    return UserView(**{key: user[key] for key in ("id", "email", "display_name", "role")})


@workbench_router.get("/admin/users")
async def admin_users(admin: Annotated[dict, Depends(require_admin)]):
    write_audit(admin["id"], "list_users")
    return {"items": list_users()}


@workbench_router.post("/admin/users", response_model=UserView, status_code=201)
async def admin_create_user(
    payload: UserCreate,
    admin: Annotated[dict, Depends(require_admin)],
):
    try:
        user = create_user(payload.email, payload.display_name, payload.password, payload.role)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="该邮箱已经注册") from exc
    write_audit(admin["id"], "create_user", user["email"])
    return UserView(**user)


@workbench_router.delete("/admin/users/{user_id}", response_model=UserView)
async def admin_delete_user(
    user_id: int,
    admin: Annotated[dict, Depends(require_admin)],
    document_service: Annotated[DocumentService, Depends(get_document_service)],
):
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="不能删除当前登录的管理员账号")

    target = get_user(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target["role"] == "admin" and target["is_active"] and count_active_admins() <= 1:
        raise HTTPException(status_code=409, detail="至少需要保留一个启用的管理员账号")

    # Qdrant is external to SQLite, so remove each owned vector payload before
    # the database cascade deletes the matching document rows.
    for document in list_documents(target["id"]):
        try:
            await document_service.delete(target["id"], document["id"])
        except DocumentNotFoundError:
            continue
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="无法清理该用户的文档向量，账号未删除",
            ) from exc

    deleted = delete_user(target["id"])
    if deleted is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    write_audit(admin["id"], "delete_user", deleted["email"])
    return UserView(**{key: deleted[key] for key in ("id", "email", "display_name", "role")})


@workbench_router.get("/admin/audit-logs", response_model=AuditLogPage)
async def admin_audit_logs(
    admin: Annotated[dict, Depends(require_admin)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0)] = 0,
    action: Annotated[str | None, Query(max_length=80)] = None,
):
    page = list_audit_logs(limit=limit, offset=offset, action=action)
    write_audit(admin["id"], "list_audit_logs", action or "all")
    return page


@workbench_router.get("/admin/mcp-audit-logs", response_model=MCPToolAuditPage)
async def admin_mcp_audit_logs(
    admin: Annotated[dict, Depends(require_admin)],
    session: Annotated[Any, Depends(get_meta_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Expose tool outcomes without exposing arguments or prompt contents."""

    try:
        page = await list_tool_audit_logs(session, limit=limit, offset=offset)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="MCP 审计存储暂时不可用") from exc
    write_audit(admin["id"], "list_mcp_audit_logs", str(offset))
    return page


@workbench_router.get("/admin/quality/overview", response_model=QualityOverview)
async def admin_quality_overview(
    admin: Annotated[dict, Depends(require_admin)],
    window_days: Annotated[int, Query(ge=1, le=90)] = 14,
):
    overview = get_quality_overview(window_days)
    write_audit(admin["id"], "view_quality_overview", str(window_days))
    return overview


@workbench_router.get("/admin/quality/feedback", response_model=AgentFeedbackPage)
async def admin_quality_feedback(
    admin: Annotated[dict, Depends(require_admin)],
    status: Annotated[str | None, Query(pattern="^(pending|accepted|rejected)$")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    page = list_quality_feedback(status=status, limit=limit, offset=offset)
    write_audit(admin["id"], "view_quality_feedback", status or "all")
    return page


@workbench_router.post(
    "/admin/quality/feedback/{feedback_id}/review", response_model=AgentFeedbackView
)
async def admin_review_quality_feedback(
    feedback_id: int,
    payload: AgentFeedbackReview,
    admin: Annotated[dict, Depends(require_admin)],
):
    try:
        feedback = review_agent_feedback(
            feedback_id,
            admin["id"],
            payload.review_status,
            payload.reviewer_note,
            payload.expected_route,
            payload.regression_query,
            payload.promote_to_regression,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if feedback is None:
        raise HTTPException(status_code=404, detail="反馈不存在")
    write_audit(admin["id"], "review_agent_feedback", str(feedback_id))
    return AgentFeedbackView(**feedback)


@workbench_router.get("/admin/quality/regression-cases", response_model=list[RegressionCaseView])
async def admin_regression_cases(admin: Annotated[dict, Depends(require_admin)]):
    write_audit(admin["id"], "list_regression_cases")
    return list_regression_cases()


@workbench_router.post("/conversations")
async def create_chat(
    payload: ConversationCreate,
    user: Annotated[dict, Depends(get_current_user)],
):
    return create_conversation(user["id"], payload.title)


@workbench_router.get("/conversations")
async def conversations(user: Annotated[dict, Depends(get_current_user)]):
    return {"items": list_conversations(user["id"])}


@workbench_router.get("/documents", response_model=list[DocumentView])
async def documents(user: Annotated[dict, Depends(get_current_user)]):
    return list_documents(user["id"])


@workbench_router.get("/memories", response_model=list[MemoryCandidateView])
@workbench_router.get("/memory", response_model=list[MemoryCandidateView])
async def list_memory(
    user: Annotated[dict, Depends(get_current_user)],
    repository: Annotated[MemoryRepository, Depends(get_memory_repository)],
    status: str | None = Query(default=None, pattern="^(pending|confirmed|deleted)$"),
):
    return await repository.list_for_user(user["id"], status)


@workbench_router.post("/memories/candidates", response_model=MemoryCandidateView, status_code=201)
@workbench_router.post("/memory/candidates", response_model=MemoryCandidateView, status_code=201)
async def propose_memory(
    payload: MemoryCandidateCreate,
    user: Annotated[dict, Depends(get_current_user)],
    repository: Annotated[MemoryRepository, Depends(get_memory_repository)],
):
    candidate = await repository.propose(user["id"], payload.content, kind=payload.kind)
    write_audit(user["id"], "propose_memory", candidate["id"])
    return candidate


@workbench_router.post("/memories/{memory_id}/confirm", response_model=MemoryCandidateView)
@workbench_router.post("/memory/{memory_id}/confirm", response_model=MemoryCandidateView)
async def confirm_memory(
    memory_id: str,
    user: Annotated[dict, Depends(get_current_user)],
    repository: Annotated[MemoryRepository, Depends(get_memory_repository)],
):
    candidate = await repository.confirm(user["id"], memory_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="记忆候选不存在、已处理或无权操作")
    write_audit(user["id"], "confirm_memory", memory_id)
    return candidate


@workbench_router.delete("/memories/{memory_id}", status_code=204)
@workbench_router.delete("/memory/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: str,
    user: Annotated[dict, Depends(get_current_user)],
    repository: Annotated[MemoryRepository, Depends(get_memory_repository)],
):
    if not await repository.delete(user["id"], memory_id):
        raise HTTPException(status_code=404, detail="记忆不存在或无权操作")
    write_audit(user["id"], "delete_memory", memory_id)


@workbench_router.post("/documents", response_model=DocumentView)
async def upload_document(
    file: Annotated[UploadFile, File(...)],
    user: Annotated[dict, Depends(get_current_user)],
    document_service: Annotated[DocumentService, Depends(get_document_service)],
):
    filename = Path(file.filename or "").name
    extension = Path(filename).suffix.lower()
    if not filename or extension not in ALLOWED_DOCUMENT_EXTENSIONS:
        raise HTTPException(status_code=415, detail="仅支持 PDF、DOCX、Markdown 和 TXT 文件")
    content = await file.read(MAX_DOCUMENT_BYTES + 1)
    if len(content) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="文档大小不能超过 10 MB")
    try:
        document = await document_service.ingest(
            user["id"], filename, file.content_type or "application/octet-stream", content
        )
    except DuplicateDocumentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Document ingestion failed for %s", filename)
        raise HTTPException(
            status_code=500,
            detail=f"文档处理失败：{str(exc)[:240] or '解析或向量服务不可用'}",
        ) from exc
    write_audit(user["id"], "upload_document", filename)
    return document


@workbench_router.post("/documents/{document_id}/reindex", response_model=DocumentView)
async def reindex_document(
    document_id: str,
    user: Annotated[dict, Depends(get_current_user)],
    document_service: Annotated[DocumentService, Depends(get_document_service)],
):
    try:
        document = await document_service.reindex(user["id"], document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="文档索引重建失败，请检查向量服务") from exc
    write_audit(user["id"], "reindex_document", document["filename"])
    return document


@workbench_router.delete("/documents/{document_id}", response_model=DocumentView)
async def remove_document(
    document_id: str,
    user: Annotated[dict, Depends(get_current_user)],
    document_service: Annotated[DocumentService, Depends(get_document_service)],
):
    try:
        document = await document_service.delete(user["id"], document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="文档删除失败，请检查向量服务") from exc
    write_audit(user["id"], "delete_document", document["filename"])
    return document


@workbench_router.get("/conversations/{conversation_id}/messages")
async def messages(conversation_id: str, user: Annotated[dict, Depends(get_current_user)]):
    if get_conversation(conversation_id, user["id"]) is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"items": list_messages(conversation_id, user["id"])}


@workbench_router.post("/agent-runs/{run_id}/feedback", response_model=AgentFeedbackView)
async def submit_agent_feedback(
    run_id: str,
    payload: AgentFeedbackCreate,
    user: Annotated[dict, Depends(get_current_user)],
):
    if get_agent_run_for_user(run_id, user["id"]) is None:
        raise HTTPException(status_code=404, detail="执行记录不存在或无权反馈")
    feedback = upsert_agent_feedback(
        run_id,
        user["id"],
        payload.rating,
        payload.issue_type,
        payload.comment,
    )
    write_audit(user["id"], "submit_agent_feedback", run_id)
    return AgentFeedbackView(**feedback)


@workbench_router.get("/agent-runs/{run_id}", response_model=AgentRunView)
async def agent_run(
    run_id: str,
    user: Annotated[dict, Depends(get_current_user)],
):
    """Return a bounded, user-owned execution snapshot for refresh/recovery UI."""

    run = get_agent_run_for_user(run_id, user["id"])
    if run is None:
        raise HTTPException(status_code=404, detail="执行记录不存在或无权查看")
    conversation_id = run.get("conversation_id") or ""
    runtime = await runtime_state_store.get_run(user["id"], conversation_id, run_id)
    return AgentRunView(**run, runtime=runtime)


def _update_parent_run(run_id: str, parent_run_id: str) -> None:
    from app.storage.app_db import update_agent_run_parent

    update_agent_run_parent(run_id, parent_run_id)


@workbench_router.post("/agent-runs/{run_id}/retry", response_model=AgentRunView)
async def retry_agent_run(
    run_id: str,
    user: Annotated[dict, Depends(get_current_user)],
):
    """Start a new, user-scoped run from the original server-side question."""

    original = get_agent_run_for_user(run_id, user["id"])
    if original is None:
        raise HTTPException(status_code=404, detail="执行记录不存在或无权重试")
    if original["conversation_id"] is None or get_conversation(
        original["conversation_id"], user["id"]
    ) is None:
        raise HTTPException(status_code=409, detail="原始会话不存在，不能重试")
    if original["status"] not in {"failed", "cancelled", "incomplete"}:
        raise HTTPException(status_code=409, detail="只有失败或中断的执行可以重试")

    # Re-read the original user-owned message. Retry never trusts a replayed
    # query, plan, tool name or identity from the client.
    conversation_id = original["conversation_id"]
    messages = list_messages(conversation_id, user["id"])
    query = next(
        (
            message["content"]
            for message in reversed(messages)
            if message["role"] == "user"
        ),
        "",
    )
    if not query:
        raise HTTPException(status_code=409, detail="原始问题缺失，不能重试")

    add_message(conversation_id, "user", query)
    trace = AgentRunTrace(
        user_id=user["id"],
        conversation_id=conversation_id,
        query=query,
    )
    try:
        _update_parent_run(trace.run_id, run_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="重试执行创建失败") from exc

    write_audit(user["id"], "retry_agent_run", f"{run_id}:{trace.run_id}")
    retry_run = get_agent_run_for_user(trace.run_id, user["id"])
    assert retry_run is not None
    return AgentRunView(
        **retry_run,
        runtime={"plan": None, "events": [], "source": "not_started", "degraded": False},
    )


@workbench_router.post("/conversations/{conversation_id}/query")
async def query(
    conversation_id: str,
    payload: WorkbenchQuery,
    user: Annotated[dict, Depends(get_current_user)],
    query_service: Annotated[QueryService, Depends(get_query_service)],
    document_service: Annotated[DocumentService, Depends(get_document_service)],
    memory_repository: Annotated[MemoryRepository | None, Depends(get_optional_memory_repository)],
):
    if get_conversation(conversation_id, user["id"]) is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    # Read history before recording this question, so the current query appears
    # exactly once in the downstream prompt.
    conversation_history = list_recent_messages(conversation_id, user["id"], limit=8)
    add_message(conversation_id, "user", payload.query)
    trace = AgentRunTrace(
        user_id=user["id"],
        conversation_id=conversation_id,
        query=payload.query,
    )

    async def stream():
        collected: list[str] = []
        event_data: dict = {"trace_id": trace.run_id}
        cancelled = False
        unexpected_exception = False
        # The trace identifier is intentionally separate from the request ID:
        # one HTTP request owns one Agent run, while proxy retries can have
        # different request IDs in a production deployment.
        yield f"data: {json.dumps({'type': 'trace', 'run_id': trace.run_id}, ensure_ascii=False, default=json_default)}\n\n"
        try:
            async with span(
                "agent.run",
                {"agent.user_id": user["id"], "agent.conversation_id": conversation_id, "agent.mode": payload.mode},
            ) as run_span:
                async for chunk in WorkbenchService(query_service, document_service).stream(
                    payload.query,
                    user["id"],
                    conversation_history,
                    payload.mode,
                    trace.run_id,
                    conversation_id,
                    runtime_state_store,
                    memory_repository,
                ):
                    data = parse_sse_payload(chunk)
                    if data is not None:
                        trace.observe(data)
                        if data.get("type") == "route":
                            event_data["route"] = data.get("intent")
                            event_data["route_strategy"] = data.get("strategy")
                            set_span_status(run_span, "ok", **{"agent.route": str(data.get("intent", "unknown"))})
                        if data.get("type") == "plan":
                            event_data["plan"] = data.get("plan")
                        if data.get("type") in {"agent_started", "agent_completed", "tool_started", "tool_completed"}:
                            timeline = event_data.setdefault("timeline", [])
                            kind = "agent" if data.get("type", "").startswith("agent_") else "tool"
                            item_id = (
                                f"{kind}:{data.get('task_id', '')}:{data.get('tool_name', '')}"
                            )
                            item = {
                                "id": item_id,
                                "kind": kind,
                                "name": data.get("agent_name") if kind == "agent" else data.get("tool_name"),
                                "status": data.get("status", "running"),
                                "summary": data.get("summary"),
                                "latencyMs": data.get("latency_ms"),
                                "taskId": data.get("task_id"),
                            }
                            timeline[:] = [existing for existing in timeline if existing.get("id") != item_id]
                            timeline.append(item)
                        if data.get("type") == "partial_result":
                            event_data["partial"] = True
                        if data.get("type") == "degraded":
                            degradations = event_data.setdefault("degradations", [])
                            degradation = {
                                key: data.get(key)
                                for key in ("service", "fallback", "reason")
                                if data.get(key) is not None
                            }
                            if degradation and degradation not in degradations:
                                degradations.append(degradation)
                        if data.get("type") == "memory_candidate" and data.get("candidate"):
                            candidates = event_data.setdefault("memory_candidates", [])
                            if data["candidate"] not in candidates:
                                candidates.append(data["candidate"])
                        if data.get("type") in {"answer", "clarification", "error"}:
                            collected.append(data.get("content") or data.get("message", ""))
                        if data.get("type") == "result":
                            event_data["result"] = data.get("data")
                            event_data["sql"] = data.get("sql")
                            collected.append(json.dumps(data.get("data"), ensure_ascii=False, default=json_default))
                        if data.get("type") == "answer" and data.get("citations"):
                            event_data["citations"] = data["citations"]
                        if data.get("type") == "agent_completed" and data.get("verification"):
                            event_data["verification"] = {
                                "decision": data.get("verification"),
                                "meta": data.get("verification_meta") or {},
                            }
                        if data.get("type") == "done":
                            event_data["execution_status"] = data.get("execution_status")
                            set_span_status(
                                run_span,
                                "error" if data.get("execution_status") == "failed" else "ok",
                                **{"agent.execution_status": str(data.get("execution_status", "unknown"))},
                            )
                    yield chunk
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception:
            unexpected_exception = True
            error_event = {"type": "error", "message": "Agent 执行失败，请查看后端日志。"}
            trace.observe(error_event)
            yield f"data: {json.dumps(error_event, ensure_ascii=False, default=json_default)}\n\n"
            collected.append("Agent 执行失败，请查看后端日志。")
        finally:
            trace.finish(cancelled=cancelled, exception=unexpected_exception)
            if not cancelled:
                add_message(
                    conversation_id,
                    "assistant",
                    "\n".join(collected) or "流程已结束",
                    json.dumps(event_data, ensure_ascii=False, default=json_default),
                )

    write_audit(user["id"], "query", trace.run_id)
    return StreamingResponse(stream(), media_type="text/event-stream")
