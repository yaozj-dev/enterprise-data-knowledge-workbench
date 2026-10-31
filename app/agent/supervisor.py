"""Deterministic Supervisor planning and validation.

The Supervisor is intentionally conservative: explicit routes are decided by
rules, while an eventual model planner can be plugged in through
``validate_model_plan``.  Both paths must produce the same ``ExecutionPlan``
before any specialist is invoked.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import ValidationError

from app.agent.contracts import ExecutionPlan, TaskSpec

RouteIntent = Literal["data", "knowledge", "general", "composite", "clarify"]
ROUTER_POLICY_VERSION = "supervisor-v1"
COMPOSITE_DATA_TIMEOUT_MS = min(
    90_000,
    max(20_000, int(os.getenv("COMPOSITE_DATA_TIMEOUT_MS", "45000"))),
)
DATA_AGENT_TIMEOUT_MS = min(
    120_000,
    max(20_000, int(os.getenv("DATA_AGENT_TIMEOUT_MS", "45000"))),
)

DATA_WORDS = (
    "销售",
    "订单",
    "销量",
    "销售额",
    "gmv",
    "商品",
    "客户",
    "区域",
    "会员",
    "统计",
    "同比",
    "趋势",
    "排名",
    "金额",
)
KNOWLEDGE_WORDS = (
    "制度",
    "流程",
    "规范",
    "文档",
    "说明",
    "报销",
    "请假",
    "权限",
    "政策",
    "办公用品",
    "知识库",
)
GENERAL_WORDS = ("你好", "你是谁", "介绍一下你", "谢谢", "天气", "日期", "时间")
COMPOSITE_WORDS = ("结合", "同时", "对照", "综合", "分别从数据和", "根据制度分析")
VAGUE_WORDS = ("帮我处理", "看一下", "处理一下", "分析一下")


@dataclass(frozen=True)
class RoutingDecision:
    intent: RouteIntent
    confidence: float
    strategy: str
    policy_version: str = ROUTER_POLICY_VERSION


def classify_intent(query: str, mode: str = "auto") -> RoutingDecision:
    """Return a bounded route decision without calling an LLM."""

    normalized = query.strip().lower()
    if mode not in {"auto", "data", "knowledge", "document", "general", "composite", "clarify"}:
        mode = "auto"
    if mode == "document":
        mode = "knowledge"
    if mode != "auto":
        return RoutingDecision(mode, 1.0, "mode_override")  # type: ignore[arg-type]

    has_data = any(word.lower() in normalized for word in DATA_WORDS)
    has_knowledge = any(word.lower() in normalized for word in KNOWLEDGE_WORDS)
    if (has_data and has_knowledge) or any(word in normalized for word in COMPOSITE_WORDS):
        return RoutingDecision("composite", 0.94, "rules_composite")
    if has_knowledge:
        return RoutingDecision("knowledge", 0.98, "rules_knowledge")
    if has_data:
        return RoutingDecision("data", 0.98, "rules_data")
    if any(word.lower() in normalized for word in GENERAL_WORDS):
        return RoutingDecision("general", 0.95, "rules_general")
    if len(normalized) < 4 or any(word in normalized for word in VAGUE_WORDS):
        return RoutingDecision("clarify", 0.95, "rules_clarify")
    return RoutingDecision("general", 0.55, "rules_ambiguous")


def _task(
    task_id: str,
    agent_name: str,
    goal: str,
    tools: list[str],
    *,
    dependencies: list[str] | None = None,
    required: bool = True,
    timeout_ms: int = 20_000,
) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        agent_name=agent_name,
        goal=goal,
        dependencies=dependencies or [],
        allowed_tools=tools,
        required=required,
        timeout_ms=timeout_ms,
    )


def build_execution_plan(query: str, mode: str = "auto") -> tuple[RoutingDecision, ExecutionPlan]:
    """Build the server-owned plan used for the first Supervisor phase."""

    decision = classify_intent(query, mode)
    if decision.intent == "data":
        tasks = [
            _task(
                "data-1",
                "data",
                "回答用户的数据分析问题",
                ["data_catalog_lookup", "safe_readonly_query"],
                timeout_ms=DATA_AGENT_TIMEOUT_MS,
            )
        ]
        evidence = ["readonly_sql_result"]
    elif decision.intent == "knowledge":
        tasks = [_task("knowledge-1", "knowledge", "依据用户隔离的企业文档回答问题", ["hybrid_document_search"])]
        evidence = ["document_citation"]
    elif decision.intent == "general":
        tasks = [_task("general-1", "general", "回答不依赖企业数据的通用问题", [])]
        evidence = []
    elif decision.intent == "composite":
        tasks = [
            _task(
                "data-1",
                "data",
                "提取问题所需的结构化业务数据",
                ["data_catalog_lookup", "safe_readonly_query"],
                required=False,
                timeout_ms=COMPOSITE_DATA_TIMEOUT_MS,
            ),
            _task("knowledge-1", "knowledge", "检索与问题相关的企业文档证据", ["hybrid_document_search"], required=False),
            _task("report-1", "report", "合并结构化数据与文档证据并检查一致性", ["verify_evidence"], dependencies=["data-1", "knowledge-1"]),
        ]
        evidence = ["readonly_sql_result", "document_citation"]
    else:
        tasks = []
        evidence = []
    plan = ExecutionPlan(
        intent=decision.intent,
        tasks=tasks,
        required_evidence=evidence,
        confidence=decision.confidence,
        max_iterations=3,
    )
    return decision, plan


def validate_model_plan(
    payload: Any,
    *,
    repair: Callable[[Any], Any] | None = None,
) -> ExecutionPlan | None:
    """Validate a model plan and allow exactly one explicit repair attempt."""

    try:
        return ExecutionPlan.model_validate(payload)
    except (ValidationError, TypeError, ValueError):
        if repair is None:
            return None
    try:
        return ExecutionPlan.model_validate(repair(payload))
    except (ValidationError, TypeError, ValueError):
        return None


def public_plan(plan: ExecutionPlan) -> dict[str, Any]:
    """Return the plan fields safe for an SSE timeline and browser storage."""

    return plan.model_dump(mode="json")

