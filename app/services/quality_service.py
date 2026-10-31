"""Runtime quality telemetry for the enterprise data workbench.

The collector intentionally records execution metadata instead of full prompts
or model outputs. It gives operators evidence for latency and failure analysis
while keeping the chat transcript as the user-scoped source of record.
"""

import hashlib
import json
import re
import secrets
from collections.abc import Mapping
from time import perf_counter
from typing import Any

from app.core.log import logger
from app.storage.app_db import (
    add_agent_run_step,
    create_agent_run,
    finish_agent_run,
    update_agent_run_route,
)

EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
SECRET_PATTERN = re.compile(
    r"(?i)\b(?:sk-[a-z0-9_-]{12,}|(?:api[_ -]?key|token|password)\s*[:=]\s*\S+)"
)


def redact_trace_text(value: str, limit: int = 800) -> str:
    """Return a reviewable but deliberately low-risk trace preview."""

    normalized = " ".join(value.split())
    normalized = EMAIL_PATTERN.sub("[email]", normalized)
    normalized = PHONE_PATTERN.sub("[phone]", normalized)
    normalized = SECRET_PATTERN.sub("[secret]", normalized)
    if len(normalized) > limit:
        return f"{normalized[: max(0, limit - 1)]}…"
    return normalized


def parse_sse_payload(chunk: str) -> dict[str, Any] | None:
    """Parse one internal SSE chunk without leaking malformed payloads to telemetry."""

    if not chunk.startswith("data: "):
        return None
    try:
        payload = json.loads(chunk[6:].strip())
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def step_category(step_name: str) -> str:
    """Map human-readable node names to stable dashboard categories."""

    if "业务意图" in step_name or "路由" in step_name:
        return "routing"
    if "检索" in step_name or "召回" in step_name:
        return "retrieval"
    if "生成SQL" in step_name or "校验SQL" in step_name or "执行SQL" in step_name:
        return "sql"
    if any(keyword in step_name for keyword in ("生成", "抽取", "过滤", "合并", "修正")):
        return "model"
    return "other"


class AgentRunTrace:
    """Persist a compact, fail-open trace around a streaming Agent request."""

    def __init__(
        self,
        *,
        user_id: int,
        conversation_id: str | None,
        query: str,
        policy_version: str = "hybrid-router-v1",
    ):
        self.run_id = secrets.token_urlsafe(18)
        self.policy_version = policy_version
        self._started = perf_counter()
        self._first_event_ms: int | None = None
        self._step_started: dict[str, tuple[float, int]] = {}
        self._step_durations: dict[str, list[int]] = {}
        self._step_count = 0
        self._route: str | None = None
        self._route_strategy: str | None = None
        self._route_confidence: float | None = None
        self._terminal_type: str | None = None
        self._execution_status: str | None = None
        self._error_code: str | None = None
        self._citation_count = 0
        self._result_row_count: int | None = None
        self._tool_round_count = 0
        self._tool_call_count = 0
        self._tool_completed_count = 0
        self._tool_failed_count = 0
        self._tool_latency_ms = 0
        self._tool_max_round = 0
        self._plan_max_rounds: int | None = None
        self._agent_started: set[str] = set()
        self._tool_started: set[tuple[str | None, str | None, str | None]] = set()
        self._finished = False
        self._stored = False
        try:
            create_agent_run(
                user_id=user_id,
                conversation_id=conversation_id,
                query_preview=redact_trace_text(query),
                query_hash=hashlib.sha256(query.encode("utf-8")).hexdigest(),
                policy_version=policy_version,
                run_id=self.run_id,
            )
            self._stored = True
        except Exception:
            logger.exception("Unable to create Agent run telemetry")

    @property
    def elapsed_ms(self) -> int:
        return max(0, round((perf_counter() - self._started) * 1_000))

    def observe(self, payload: Mapping[str, Any]) -> None:
        """Observe one canonical SSE payload and update the trace asynchronously by stream order."""

        if self._finished:
            return
        event_type = str(payload.get("type", ""))
        if event_type and self._first_event_ms is None:
            self._first_event_ms = self.elapsed_ms
        if event_type == "route":
            self._route = str(payload.get("intent", "unknown"))
            self._route_strategy = str(payload.get("strategy", "unspecified"))
            confidence = payload.get("confidence")
            self._route_confidence = float(confidence) if isinstance(confidence, (int, float)) else None
            self.policy_version = str(payload.get("policy_version", self.policy_version))
            if self._stored:
                try:
                    update_agent_run_route(
                        self.run_id,
                        self._route,
                        self._route_strategy,
                        self._route_confidence,
                        self.policy_version,
                    )
                except Exception:
                    logger.exception("Unable to persist Agent route telemetry")
            return
        if event_type == "progress":
            self._observe_progress(payload)
            return
        if event_type == "plan":
            plan = payload.get("plan")
            if isinstance(plan, Mapping):
                max_rounds = plan.get("max_iterations")
                if isinstance(max_rounds, int) and max_rounds > 0:
                    self._plan_max_rounds = max_rounds
            return
        if event_type in {"agent_started", "agent_completed"}:
            agent_name = payload.get("agent_name")
            task_id = payload.get("task_id")
            if isinstance(agent_name, str) and agent_name:
                self._agent_started.add(f"{agent_name}:{task_id or ""}")
            return
        if event_type == "tool_started":
            tool_name = payload.get("tool_name")
            if isinstance(tool_name, str) and tool_name:
                agent_name = payload.get("agent_name")
                task_id = payload.get("task_id")
                key = (
                    agent_name if isinstance(agent_name, str) else None,
                    task_id if isinstance(task_id, str) else None,
                    tool_name,
                )
                # A started call is a real budget attempt even if the stream is
                # cancelled before its completion event arrives.
                if key not in self._tool_started:
                    self._tool_started.add(key)
                    self._tool_call_count += 1
                round_value = payload.get("round")
                if isinstance(round_value, int) and round_value > self._tool_max_round:
                    self._tool_max_round = round_value
            return
        if event_type == "tool_completed":
            self._tool_completed_count += 1
            status = str(payload.get("status", ""))
            if status in {"failed", "timed_out"}:
                self._tool_failed_count += 1
            latency = payload.get("latency_ms")
            if isinstance(latency, int) and latency >= 0:
                self._tool_latency_ms += latency
            round_value = payload.get("round")
            if isinstance(round_value, int) and round_value > self._tool_max_round:
                self._tool_max_round = round_value
            return
        if event_type == "answer":
            citations = payload.get("citations")
            self._citation_count = len(citations) if isinstance(citations, list) else 0
            self._terminal_type = "answer"
            return
        if event_type == "result":
            result = payload.get("data")
            self._result_row_count = len(result) if isinstance(result, list) else None
            self._terminal_type = "result"
            return
        if event_type == "clarification":
            self._terminal_type = "clarification"
            return
        if event_type == "error":
            self._terminal_type = "error"
            self._error_code = "agent_error"
            return
        if event_type == "done":
            execution_status = payload.get("execution_status")
            if isinstance(execution_status, str):
                self._execution_status = execution_status
            if execution_status == "clarified":
                self._terminal_type = "clarification"
            elif execution_status == "partial":
                self._terminal_type = "partial"
            elif execution_status == "failed":
                self._terminal_type = "error"

    def _observe_progress(self, payload: Mapping[str, Any]) -> None:
        step_name = str(payload.get("step", "未命名步骤"))[:160]
        status = str(payload.get("status", ""))
        now = perf_counter()
        if status == "running":
            self._step_started[step_name] = (now, self.elapsed_ms)
            return
        if status not in {"success", "error"}:
            return
        started_at, started_offset = self._step_started.pop(step_name, (now, self.elapsed_ms))
        duration_ms = max(0, round((now - started_at) * 1_000))
        category = step_category(step_name)
        self._step_durations.setdefault(category, []).append(duration_ms)
        self._step_count += 1
        if self._stored:
            try:
                add_agent_run_step(
                    self.run_id,
                    self._step_count,
                    step_name,
                    category,
                    status,
                    started_offset,
                    duration_ms,
                )
            except Exception:
                logger.exception("Unable to persist Agent step telemetry")

    def _trace_summary(self) -> dict[str, Any]:
        max_rounds = self._plan_max_rounds or 3
        return {
            "route": self._route,
            "route_strategy": self._route_strategy,
            "terminal_type": self._terminal_type,
            "step_count": self._step_count,
            "agent_count": len(self._agent_started),
            "budget": {
                "max_rounds": max_rounds,
                "max_calls": 6,
                "round_count": self._tool_round_count,
                "call_count": self._tool_call_count,
                "completed_count": self._tool_completed_count,
                "failed_count": self._tool_failed_count,
                "latency_ms": self._tool_latency_ms,
                "max_observed_round": self._tool_max_round,
                "exhausted": self._tool_round_count >= max_rounds or self._tool_call_count >= 6,
            },
        }

    def finish(self, *, cancelled: bool = False, exception: bool = False) -> None:
        """Close unfinished nodes and persist aggregate metrics exactly once."""

        if self._finished:
            return
        self._finished = True
        final_step_status = "cancelled" if cancelled else "error" if exception else "success"
        now = perf_counter()
        for step_name, (started_at, started_offset) in list(self._step_started.items()):
            duration_ms = max(0, round((now - started_at) * 1_000))
            category = step_category(step_name)
            self._step_durations.setdefault(category, []).append(duration_ms)
            self._step_count += 1
            if self._stored:
                try:
                    add_agent_run_step(
                        self.run_id,
                        self._step_count,
                        step_name,
                        category,
                        final_step_status,
                        started_offset,
                        duration_ms,
                    )
                except Exception:
                    logger.exception("Unable to close Agent step telemetry")
        self._step_started.clear()
        if cancelled:
            status = "cancelled"
        elif exception or self._terminal_type == "error":
            status = "failed"
        elif self._terminal_type == "clarification":
            status = "clarified"
        elif self._terminal_type == "partial" or self._execution_status == "partial":
            status = "partial"
        elif self._terminal_type in {"answer", "result"}:
            status = "completed"
        else:
            status = "incomplete"
        if exception and not self._error_code:
            self._error_code = "stream_exception"
        summary = json.dumps(self._trace_summary(), ensure_ascii=False)
        if self._stored:
            try:
                finish_agent_run(
                    self.run_id,
                    status=status,
                    first_event_ms=self._first_event_ms,
                    total_latency_ms=self.elapsed_ms,
                    retrieval_latency_ms=sum(self._step_durations.get("retrieval", [])),
                    model_latency_ms=sum(self._step_durations.get("model", [])),
                    sql_latency_ms=sum(self._step_durations.get("sql", [])),
                    citation_count=self._citation_count,
                    result_row_count=self._result_row_count,
                    error_code=self._error_code,
                    trace_summary=summary,
                )
            except Exception:
                logger.exception("Unable to finish Agent run telemetry")
