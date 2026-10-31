"""Standalone internal MCP service entry point for Docker Compose."""

from __future__ import annotations

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
from app.mcp.sdk_server import create_mcp_sdk_app


@asynccontextmanager
async def _tool_service_lifespan(_app: FastAPI):
    """Initialize only the clients required by the internal tool service."""

    managers = (
        qdrant_client_manager,
        embedding_client_manager,
        es_client_manager,
        meta_mysql_client_manager,
        dw_mysql_client_manager,
    )
    initialized = []
    try:
        for manager in managers:
            manager.init()
            initialized.append(manager)
        bind_default_business_tools()
        async with sdk_app.router.lifespan_context(sdk_app):
            yield
    finally:
        for manager in reversed(initialized):
            close = getattr(manager, "close", None)
            if close is not None:
                await close()

sdk_app = create_mcp_sdk_app()
app = FastAPI(lifespan=_tool_service_lifespan)
app.mount("/", sdk_app)
