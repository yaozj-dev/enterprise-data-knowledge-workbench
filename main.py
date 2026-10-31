"""
FastAPI 应用入口

负责创建后端应用实例，注册应用生命周期函数，并把各业务模块中的 router
挂载到同一个 app 上。HTTP 请求会先进入这里创建的 app，再按路由分发到
具体的接口处理函数。
"""

import uuid
from time import perf_counter

from fastapi import FastAPI, Request

from app.api.lifespan import lifespan
from app.api.routers.mcp_router import mcp_router
from app.api.routers.query_router import query_router
from app.api.routers.workbench_router import workbench_router
from app.core.context import request_id_ctx_var
from app.core.log import logger
from app.mcp.sdk_server import mcp_sdk_app
from app.observability.tracing import set_span_status, span

# lifespan 交给 FastAPI 管理，用于在服务启动和关闭时统一初始化与释放外部客户端
app = FastAPI(lifespan=lifespan)

# 把查询路由注册进应用；没有挂载时，/docs 和真实 HTTP 请求都访问不到该接口
app.include_router(query_router)
# 工作台路由提供登录、会话持久化和统一的 Agent 入口
app.include_router(workbench_router)
# Internal JSON-RPC tool boundary.  It is token protected and intended for the
# backend network, not for browser clients.
app.include_router(mcp_router)
# Standards-based Streamable HTTP transport from the official MCP SDK. The
# legacy JSON-RPC route above remains available for existing local clients.
app.mount("/internal/mcp-sdk", mcp_sdk_app)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    """Attach an auditable request ID and structured HTTP lifecycle events."""

    request_id = str(uuid.uuid4())
    token = request_id_ctx_var.set(request_id)
    started_at = perf_counter()
    try:
        async with span(
            "http.request",
            {"http.method": request.method, "http.route": request.url.path},
        ) as request_span:
            response = await call_next(request)
            set_span_status(
                request_span,
                "ok" if response.status_code < 500 else "error",
                **{"http.status_code": response.status_code},
            )
            logger.bind(
                event="http_request_completed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=round((perf_counter() - started_at) * 1_000, 2),
            ).info("HTTP request completed")
            response.headers["X-Request-ID"] = request_id
            return response
    except Exception:
        logger.bind(
            event="http_request_failed",
            method=request.method,
            path=request.url.path,
            duration_ms=round((perf_counter() - started_at) * 1_000, 2),
        ).exception("HTTP request failed")
        raise
    finally:
        request_id_ctx_var.reset(token)
