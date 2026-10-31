"""
问数查询服务

负责把 API 层传入的自然语言问题转换成一次 LangGraph 工作流执行：
创建初始 State、组装 Runtime Context、消费 graph.astream 的流式输出，
并统一包装成 SSE 文本返回给路由层。
"""

import inspect
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from time import perf_counter
from typing import Any

from langchain_huggingface import HuggingFaceEndpointEmbeddings

from app.agent.context import DataAgentContext
from app.agent.contracts import AgentResult
from app.agent.graph import graph
from app.agent.state import DataAgentState
from app.core.log import logger
from app.core.serialization import json_default
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository


class QueryService:
    """封装一次问数查询所需的业务编排逻辑"""

    def __init__(
        self,
        meta_mysql_repository: MetaMySQLRepository,
        embedding_client: HuggingFaceEndpointEmbeddings,
        dw_mysql_repository: DWMySQLRepository,
        column_qdrant_repository: ColumnQdrantRepository,
        metric_qdrant_repository: MetricQdrantRepository,
        value_es_repository: ValueESRepository,
    ):
        # MySQL 仓储分别负责元数据补全和真实数仓环境信息读取
        self.meta_mysql_repository = meta_mysql_repository
        self.dw_mysql_repository = dw_mysql_repository

        # 召回链路依赖的向量检索、Embedding 和全文检索能力由依赖层注入
        self.embedding_client = embedding_client
        self.column_qdrant_repository = column_qdrant_repository
        self.metric_qdrant_repository = metric_qdrant_repository
        self.value_es_repository = value_es_repository

    async def events(self, query: str) -> AsyncIterator[dict[str, Any]]:
        """Run the Data Agent graph and yield structured public events."""

        # State 只放会被图节点读写和合并的业务数据，外部工具对象不塞进 State
        state = DataAgentState(query=query)
        # Context 保存本次图执行需要复用的外部依赖，节点通过 runtime.context 读取
        context = DataAgentContext(
            column_qdrant_repository=self.column_qdrant_repository,
            embedding_client=self.embedding_client,
            metric_qdrant_repository=self.metric_qdrant_repository,
            value_es_repository=self.value_es_repository,
            meta_mysql_repository=self.meta_mysql_repository,
            dw_mysql_repository=self.dw_mysql_repository,
        )
        try:
            # stream_mode="custom" 对应节点内部 writer(...) 写出的进度消息
            async for chunk in graph.astream(
                input=state, context=context, stream_mode="custom"
            ):
                if isinstance(chunk, dict):
                    yield chunk
        except Exception as e:
            # 流式接口已经开始返回后不能再改 HTTP 状态码，因此把异常也包装成一条 SSE 消息
            # Full details stay in server logs. Returning database or provider
            # errors verbatim would expose implementation details to clients.
            logger.exception(f"数据分析流程失败：{e}")
            error = {
                "type": "error",
                "message": "数据分析执行失败，请调整问题后重试或联系管理员。",
            }
            yield error

    async def query(self, query: str) -> AsyncIterator[str]:
        """Compatibility SSE stream for the existing API clients."""

        async for payload in self.events(query):
            yield f"data: {json.dumps(payload, ensure_ascii=False, default=json_default)}\n\n"

    async def execute(
        self,
        query: str,
        *,
        task_id: str = "data-1",
        on_event: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    ) -> AgentResult:
        """Run the Data Agent behind the shared structured result contract."""

        started = perf_counter()
        result_payload: dict[str, Any] | None = None
        error_payload: dict[str, Any] | None = None
        event_count = 0
        sql: str | None = None
        async for payload in self.events(query):
            event_count += 1
            if on_event is not None:
                callback_result = on_event(payload)
                if inspect.isawaitable(callback_result):
                    await callback_result
            if payload.get("type") == "result":
                result_payload = payload
                sql = payload.get("sql")
            elif payload.get("type") == "error":
                error_payload = payload

        latency_ms = round((perf_counter() - started) * 1_000)
        if result_payload is not None and error_payload is None:
            return AgentResult(
                agent_name="data",
                task_id=task_id,
                status="succeeded",
                data=result_payload.get("data"),
                latency_ms=latency_ms,
                metadata={"sql": sql, "event_count": event_count},
            )
        if error_payload is not None:
            return AgentResult(
                agent_name="data",
                task_id=task_id,
                status="failed",
                error_code="DATA_AGENT_FAILED",
                retryable=True,
                latency_ms=latency_ms,
                metadata={"event_count": event_count},
            )
        return AgentResult(
            agent_name="data",
            task_id=task_id,
            status="partial",
            error_code="DATA_RESULT_MISSING",
            latency_ms=latency_ms,
            metadata={"event_count": event_count},
        )
