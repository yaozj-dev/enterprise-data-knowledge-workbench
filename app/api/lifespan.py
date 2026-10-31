"""
FastAPI 应用生命周期管理

负责在服务启动时初始化外部客户端，在服务关闭时释放连接资源。
这些客户端是应用级资源，适合在 lifespan 中创建一次并复用，而不是每个请求
重复初始化。
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import (
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.mcp.business_tools import bind_default_business_tools
from app.observability.tracing import configure_tracing
from app.services.runtime_state import runtime_state_store
from app.storage.app_db import init_db, purge_expired_quality_data


@asynccontextmanager
async def lifespan(app: FastAPI):
    """管理应用启动和关闭两个阶段的外部资源"""

    # 应用自身的账号、会话和审计数据先初始化；它不依赖 Docker 中的业务数据库
    init_db()
    configure_tracing()
    # Trace is useful for iteration but should not become an unbounded copy of
    # user activity. The default is intentionally finite and configurable.
    try:
        purge_expired_quality_data(int(os.getenv("QUALITY_TRACE_RETENTION_DAYS", "90")))
    except ValueError:
        purge_expired_quality_data(90)

    # 启动阶段：先建立各类外部服务客户端，后续依赖函数会从 manager 中取已初始化对象
    qdrant_client_manager.init()
    embedding_client_manager.init()
    es_client_manager.init()
    meta_mysql_client_manager.init()
    dw_mysql_client_manager.init()
    bind_default_business_tools()

    # FastAPI does not automatically run the lifespan of a mounted Starlette
    # app. The MCP SDK session manager must be active for Streamable HTTP
    # sessions, including the in-process endpoint used by local development.
    from app.mcp.sdk_server import mcp_sdk_app

    async with mcp_sdk_app.router.lifespan_context(mcp_sdk_app):
        # yield 之前是启动逻辑，yield 之后是关闭逻辑；中间阶段由 FastAPI 正常处理请求
        yield

    # 关闭阶段：按应用级资源统一释放连接，避免进程退出前留下未关闭的网络连接
    await qdrant_client_manager.close()
    await es_client_manager.close()
    await meta_mysql_client_manager.close()
    await dw_mysql_client_manager.close()
    await runtime_state_store.close()
