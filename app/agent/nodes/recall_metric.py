"""
指标召回节点

负责根据用户问题从指标向量知识库中召回候选指标
它帮助 Agent 把“销售额 转化率 客单价”等业务表达映射到已定义指标
实现路径和字段召回类似：关键词扩展 -> Embedding -> Qdrant 相似度检索 -> MetricInfo 去重
"""

import asyncio

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import json_llm
from app.agent.state import DataAgentState
from app.core.log import logger
from app.entities.metric_info import MetricInfo
from app.prompt.prompt_loader import load_prompt


async def recall_metric(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """召回和用户问题语义相关的业务指标"""

    writer = runtime.stream_writer
    step = "召回指标信息"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        # query 用于让 LLM 生成指标层检索词，keywords 来自上游的通用关键词抽取
        query = state["query"]
        keywords = state["keywords"]
        # 指标召回使用向量检索，需要 Embedding 客户端和指标 Qdrant 仓储配合
        embedding_client = runtime.context["embedding_client"]
        metric_qdrant_repository = runtime.context["metric_qdrant_repository"]

        # 用 LLM 把用户问法扩展成“指标概念”列表，例如“销售总额”可扩展出“GMV”“成交额”
        prompt = PromptTemplate(
            template=load_prompt("extend_keywords_for_metric_recall"),
            input_variables=["query"],
        )
        # 指标扩展 prompt 要求只输出 JSON 数组，解析后 result 就是 list[str]
        output_parser = JsonOutputParser()
        # LCEL 管道：填充提示词 -> 调用模型 -> 解析 JSON
        chain = prompt | json_llm | output_parser

        result = await chain.ainvoke({"query": query})

        # 通用关键词和指标扩展词都参与召回，提升同义指标的命中率
        recall_keywords = list(set(keywords + result))

        # Embed all expanded terms in one bounded request instead of paying one
        # HTTP round trip per keyword before reaching Qdrant.
        embeddings = await embedding_client.aembed_documents(recall_keywords)

        # 用指标 id 做唯一键，避免多个关键词命中同一个指标时重复写入 state
        metric_info_map: dict[str, MetricInfo] = {}
        search_results = await asyncio.gather(
            *(
                metric_qdrant_repository.search(embedding)
                for embedding in embeddings
            ),
            return_exceptions=True,
        )
        for keyword, current in zip(recall_keywords, search_results):
            if isinstance(current, BaseException):
                logger.warning(f"指标检索关键词 {keyword} 失败: {current}")
                continue
            current_metric_infos: list[MetricInfo] = current
            for metric_info in current_metric_infos:
                if metric_info.id not in metric_info_map:
                    metric_info_map[metric_info.id] = metric_info

        # 写回 state 的是业务实体列表，后续过滤节点不需要关心 Qdrant 原始 point 结构
        retrieved_metric_infos: list[MetricInfo] = list(metric_info_map.values())
        logger.info(f"检索到指标信息：{list(metric_info_map.keys())}")
        writer({"type": "progress", "step": step, "status": "success"})
        return {"retrieved_metric_infos": retrieved_metric_infos}
    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise
