"""A small, bounded Action/Observation runner for specialist tools.

This is intentionally not a free-form chain-of-thought loop. The model emits
only a structured tool request, the server validates it against an allowlist,
and the UI receives status summaries rather than hidden reasoning or raw
arguments. A deterministic fallback action keeps the business path usable
when a model or MCP service is unavailable.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.contracts import ToolCallBudget, ToolResult


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(min_length=1, max_length=80)
    arguments: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class ReactOutcome:
    result: ToolResult
    rounds: int
    calls: int
    replans: int


def _response_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            item if isinstance(item, str) else str(item.get("text", ""))
            for item in content
            if isinstance(item, (str, dict))
        )
    return str(content)


def parse_action(raw: Any) -> ActionRequest:
    text = _response_text(raw).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("action output is not JSON")
        payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("action output must be a JSON object")
    return ActionRequest.model_validate(payload)


def _action_prompt(query: str, allowed_tools: set[str], observation: str) -> str:
    return (
        "你是受限工具 Agent。只能选择一个服务端白名单工具，"
        "只输出 JSON：{\"tool_name\":\"...\",\"arguments\":{...}}。"
        "不要输出思维过程，不要输出答案，不要传入 user_id、权限、密码或数据库账号。\n"
        f"用户目标：{query}\n"
        f"允许的工具：{json.dumps(sorted(allowed_tools), ensure_ascii=False)}\n"
        f"上一次工具观察摘要：{observation or '无'}"
    )


async def run_bounded_react(
    *,
    model: Any,
    query: str,
    allowed_tools: set[str],
    execute: Callable[[str, dict[str, Any]], Awaitable[ToolResult]],
    fallback_action: ActionRequest,
    emit: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    max_rounds: int = 3,
    max_calls: int = 6,
    tool_timeout_seconds: float = 8.0,
    model_timeout_seconds: float = 4.0,
) -> ReactOutcome:
    """Execute bounded Action/Observation steps with one retry per call."""

    budget = ToolCallBudget(
        max_rounds=max_rounds,
        max_calls=max_calls,
        max_retries_per_call=1,
    )
    observation = ""
    replans = 0
    fallback_used = False
    last_result: ToolResult | None = None
    pending_retry: ActionRequest | None = None

    async def publish(payload: dict[str, Any]) -> None:
        if emit is None:
            return
        value = emit(payload)
        if inspect.isawaitable(value):
            await value

    for _ in range(max_rounds):
        if not budget.start_round():
            break
        action: ActionRequest | None = pending_retry
        is_retry = action is not None
        pending_retry = None
        if action is None and model is not None and not fallback_used:
            try:
                response = await asyncio.wait_for(
                    model.ainvoke(_action_prompt(query, allowed_tools, observation)),
                    timeout=model_timeout_seconds,
                )
                action = parse_action(response)
            except (TimeoutError, ValidationError, ValueError, json.JSONDecodeError):
                replans += 1
            except Exception:
                replans += 1
        if action is None:
            if fallback_used:
                break
            action = fallback_action
            fallback_used = True

        if action.tool_name not in allowed_tools:
            replans += 1
            observation = "tool_not_allowed"
            if fallback_used:
                break
            action = fallback_action
            fallback_used = True

        if not budget.try_reserve(action.tool_name, action.arguments, retry=is_retry):
            observation = "tool_budget_exceeded_or_duplicate"
            break

        await publish(
            {
                "type": "tool_started",
                "tool_name": action.tool_name,
                "status": "running",
                "round": budget.round_count,
            }
        )
        started = asyncio.get_running_loop().time()
        try:
            result = await asyncio.wait_for(
                execute(action.tool_name, action.arguments), timeout=tool_timeout_seconds
            )
        except TimeoutError:
            result = ToolResult(
                tool_name=action.tool_name,
                status="timed_out",
                error_code="TOOL_TIMEOUT",
                retryable=True,
                source="system",
            )
        except Exception:
            result = ToolResult(
                tool_name=action.tool_name,
                status="failed",
                error_code="TOOL_EXECUTION_FAILED",
                retryable=True,
                source="system",
            )
        latency_ms = round(
            (asyncio.get_running_loop().time() - started) * 1_000
        )
        await publish(
            {
                "type": "tool_completed",
                "tool_name": action.tool_name,
                "status": result.status,
                "error_code": result.error_code,
                "latency_ms": latency_ms,
                "summary": "工具观察已接收",
                "round": budget.round_count,
            }
        )
        last_result = result
        if result.status == "succeeded":
            return ReactOutcome(result, budget.round_count, budget.call_count, replans)
        observation = result.error_code or result.status
        if result.retryable and budget.can_retry(action.tool_name, action.arguments):
            replans += 1
            pending_retry = action
            continue
        break

    return ReactOutcome(
        last_result
        or ToolResult(
            tool_name=fallback_action.tool_name,
            status="failed",
            error_code="REACT_BUDGET_EXCEEDED",
            retryable=False,
            source="system",
        ),
        budget.round_count,
        budget.call_count,
        replans,
    )

