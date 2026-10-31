"""
表信息过滤节点

负责在合并后的候选表结构中筛选出当前问题真正需要的表和字段
这里让大模型只返回“保留哪些表和字段”的选择结果，真正的结构裁剪仍由程序完成
"""

import copy
import os

import yaml
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import json_llm
from app.agent.state import DataAgentState, TableInfoState
from app.core.log import logger
from app.prompt.prompt_loader import load_prompt

# SQL 生成阶段才需要完整示例值；过滤节点只需要少量示例就能识别相关字段。
FILTER_TABLE_MAX_EXAMPLES = max(0, int(os.getenv("FILTER_TABLE_MAX_EXAMPLES", "3")))


async def filter_table(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """根据用户问题裁剪候选表结构上下文"""

    writer = runtime.stream_writer
    step = "过滤表信息"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        query = state["query"]
        table_infos: list[TableInfoState] = state["table_infos"]
        # 复制后再截断示例，避免原地修改 LangGraph 状态或影响 SQL 生成阶段。
        filter_input = copy.deepcopy(table_infos)
        for table_info in filter_input:
            for column_info in table_info["columns"]:
                column_info["examples"] = column_info["examples"][
                    :FILTER_TABLE_MAX_EXAMPLES
                ]

        # table_infos 是嵌套结构，转成 YAML 后更适合放进提示词，也保留中文字段说明
        prompt = PromptTemplate(
            template=load_prompt("filter_table_info"),
            input_variables=["query", "table_infos"],
        )
        # filter_table_info prompt 要求模型只输出 JSON 对象：表名 -> 字段名列表
        output_parser = JsonOutputParser()
        # LCEL 管道：填充提示词 -> 调用模型 -> 解析 JSON
        chain = prompt | json_llm | output_parser

        result = await chain.ainvoke(
            {
                "query": query,
                "table_infos": yaml.dump(
                    filter_input, allow_unicode=True, sort_keys=False
                ),
            }
        )
        # 模型只负责选择，程序根据选择结果从原始 TableInfoState 中裁剪，避免模型重写复杂结构出错
        filtered_table_infos: list[TableInfoState] = []
        for table_info in table_infos:
            if table_info["name"] in result:
                table_info["columns"] = [
                    column_info
                    for column_info in table_info["columns"]
                    if column_info["name"] in result[table_info["name"]]
                ]
                filtered_table_infos.append(table_info)

        logger.info(
            f"过滤后的表信息：{[filtered_table_info['name'] for filtered_table_info in filtered_table_infos]}"
        )
        writer({"type": "progress", "step": step, "status": "success"})
        return {"table_infos": filtered_table_infos}

    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise
