"""
问数查询接口路由

负责定义前端访问的 `/api/query` 接口，把 HTTP 请求交给 QueryService，
并把问数智能体执行过程以 SSE 形式持续返回给客户端。
路由层只处理请求体、依赖声明和响应类型，不直接创建 Repository 或执行图节点。
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from starlette.responses import StreamingResponse

from app.api.dependencies import get_document_service, get_query_service
from app.api.schemas.query_schema import QuerySchema
from app.security import get_current_user
from app.services.document_service import DocumentService
from app.services.query_service import QueryService
from app.services.workbench_service import WorkbenchService

# 当前模块只维护查询相关接口，避免后续所有 API 都挤在 main.py 中
query_router = APIRouter()


@query_router.post("/api/query")
async def query_handler(
    # 请求体参数：FastAPI 会把前端 JSON 自动解析成 QuerySchema
    query: QuerySchema,
    # Legacy endpoint remains available for evaluation and integrations, but it
    # must not bypass the workbench authentication boundary.
    user: Annotated[dict, Depends(get_current_user)],
    # 服务依赖：FastAPI 会调用 get_query_service，递归组装它所需的仓储和客户端
    query_service: Annotated[QueryService, Depends(get_query_service)],
    document_service: Annotated[DocumentService, Depends(get_document_service)],
):
    """兼容入口；新增 mode 后也使用同一套 Supervisor 编排。"""

    return StreamingResponse(
        WorkbenchService(query_service, document_service).stream(
            query.query, user["id"], [], query.mode
        ),
        media_type="text/event-stream",
    )
