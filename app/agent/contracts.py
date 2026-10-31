"""Stable contracts shared by the Supervisor and specialist Agents.

The model is allowed to choose a business plan, but it is never allowed to
choose an identity, database credential, or permission boundary.  Those values
remain request-scoped server state.  Keeping this contract independent from
LangGraph also makes it straightforward to validate plans in unit tests and at
an MCP boundary.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AGENT_NAMES = frozenset(
    {"supervisor", "data", "knowledge", "general", "report", "clarification"}
)

AGENT_TOOL_ALLOWLIST: dict[str, frozenset[str]] = {
    "supervisor": frozenset(),
    "data": frozenset({"data_catalog_lookup", "safe_readonly_query"}),
    "knowledge": frozenset({"hybrid_document_search"}),
    "general": frozenset(),
    "report": frozenset({"verify_evidence"}),
    "clarification": frozenset(),
}

PLAN_INTENTS = frozenset({"data", "knowledge", "general", "composite", "clarify"})
TASK_STATUSES = frozenset(
    {"planned", "running", "succeeded", "failed", "timed_out", "partial", "skipped", "unsupported"}
)
EXECUTION_STATUSES = frozenset(
    {"planned", "executing", "verifying", "completed", "partial", "clarified", "failed", "cancelled"}
)


class TaskSpec(BaseModel):
    """A bounded unit of work assigned to one specialist Agent."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    agent_name: str = Field(min_length=1, max_length=32)
    goal: str = Field(min_length=1, max_length=1_000)
    dependencies: list[str] = Field(default_factory=list, max_length=3)
    allowed_tools: list[str] = Field(default_factory=list, max_length=4)
    required: bool = True
    timeout_ms: int = Field(default=20_000, ge=100, le=120_000)

    @field_validator("agent_name")
    @classmethod
    def validate_agent_name(cls, value: str) -> str:
        if value not in AGENT_NAMES:
            raise ValueError(f"unknown agent: {value}")
        return value

    @field_validator("dependencies", "allowed_tools")
    @classmethod
    def remove_duplicate_values(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("duplicate dependency or tool is not allowed")
        return values

    @model_validator(mode="after")
    def validate_tool_boundary(self) -> "TaskSpec":
        allowed = AGENT_TOOL_ALLOWLIST[self.agent_name]
        unauthorized = set(self.allowed_tools) - allowed
        if unauthorized:
            names = ", ".join(sorted(unauthorized))
            raise ValueError(f"tools are not allowed for {self.agent_name}: {names}")
        return self


class ExecutionPlan(BaseModel):
    """Validated Supervisor output.

    A plan is deliberately small.  Three tasks are enough for the current
    composite path: Data, Knowledge, and Report/Verifier.  More elaborate
    workflows should be represented as another bounded run, not an unbounded
    model-generated graph.
    """

    model_config = ConfigDict(extra="forbid")

    intent: Literal["data", "knowledge", "general", "composite", "clarify"]
    tasks: list[TaskSpec] = Field(default_factory=list, max_length=3)
    required_evidence: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(ge=0.0, le=1.0)
    max_iterations: int = Field(default=3, ge=1, le=3)

    @model_validator(mode="after")
    def validate_graph(self) -> "ExecutionPlan":
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task_id values must be unique")
        task_map = {task.task_id: task for task in self.tasks}
        for task in self.tasks:
            missing = set(task.dependencies) - task_map.keys()
            if missing:
                raise ValueError(f"unknown task dependencies: {sorted(missing)}")
            if task.task_id in task.dependencies:
                raise ValueError(f"task cannot depend on itself: {task.task_id}")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise ValueError("task dependencies must be acyclic")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in task_map[task_id].dependencies:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in task_ids:
            visit(task_id)

        if self.intent == "composite" and len(self.tasks) < 2:
            raise ValueError("composite plans require at least two tasks")
        if self.intent != "composite" and len(self.tasks) > 1:
            raise ValueError("non-composite plans may contain only one task")
        return self


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=1)
    filename: str = Field(min_length=1, max_length=255)
    page_number: int | None = Field(default=None, ge=1)
    score: float | None = None
    document_id: str | None = Field(default=None, max_length=128)
    chunk_index: int | None = Field(default=None, ge=0)

    @classmethod
    def from_document_hit(cls, index: int, hit: dict[str, Any]) -> "Citation":
        return cls(
            index=index,
            filename=str(hit.get("filename", "未命名文档")),
            page_number=hit.get("page_number"),
            score=hit.get("score"),
            document_id=hit.get("document_id"),
            chunk_index=hit.get("chunk_index"),
        )


class ToolResult(BaseModel):
    """Canonical output for MCP and local fallback adapters."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(min_length=1, max_length=80)
    status: Literal["succeeded", "failed", "timed_out", "partial"]
    data: Any = None
    citations: list[Citation] = Field(default_factory=list)
    error_code: str | None = Field(default=None, max_length=80)
    retryable: bool = False
    source: Literal["mcp", "local", "system"] = "local"


class AgentResult(BaseModel):
    """Specialist result exposed to the report layer, without hidden thoughts."""

    model_config = ConfigDict(extra="forbid")

    agent_name: str
    task_id: str
    status: Literal[
        "succeeded",
        "failed",
        "timed_out",
        "partial",
        "skipped",
        "unsupported",
    ]
    answer: str = ""
    data: Any = None
    citations: list[Citation] = Field(default_factory=list)
    error_code: str | None = Field(default=None, max_length=80)
    retryable: bool = False
    latency_ms: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentEvent(BaseModel):
    """Safe event schema for the UI and telemetry stream."""

    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "agent_started",
        "agent_completed",
        "tool_started",
        "tool_completed",
        "replan",
        "evidence",
        "partial_result",
        "memory_candidate",
        "degraded",
        "done",
    ]
    agent_name: str | None = None
    task_id: str | None = None
    tool_name: str | None = None
    status: str | None = None
    summary: str | None = Field(default=None, max_length=800)
    latency_ms: int | None = Field(default=None, ge=0)
    data: Any = None
    service: str | None = None
    fallback: str | None = None
    reason: str | None = None


class ExecutionState(BaseModel):
    """Small state machine used by the Supervisor and persisted in traces."""

    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "planned",
        "executing",
        "verifying",
        "completed",
        "partial",
        "clarified",
        "failed",
        "cancelled",
    ] = "planned"
    task_status: dict[str, str] = Field(default_factory=dict)
    replans: int = Field(default=0, ge=0, le=1)

    def transition(self, next_status: str) -> None:
        transitions = {
            "planned": {"executing", "clarified", "cancelled", "failed"},
            "executing": {"verifying", "partial", "completed", "clarified", "failed", "cancelled"},
            "verifying": {"completed", "partial", "clarified", "failed", "cancelled"},
            "partial": {"verifying", "completed", "clarified", "cancelled"},
            "completed": set(),
            "clarified": set(),
            "failed": set(),
            "cancelled": set(),
        }
        if next_status not in EXECUTION_STATUSES:
            raise ValueError(f"unknown execution status: {next_status}")
        if next_status not in transitions[self.status]:
            raise ValueError(f"invalid execution transition: {self.status} -> {next_status}")
        self.status = next_status  # type: ignore[assignment]

    def set_task_status(self, task_id: str, status: str) -> None:
        if status not in TASK_STATUSES:
            raise ValueError(f"unknown task status: {status}")
        self.task_status[task_id] = status


class BudgetExceeded(RuntimeError):
    """Raised only by callers that want an exception instead of a boolean."""


@dataclass
class ToolCallBudget:
    """Bounded ReAct budget with duplicate-call and retry protection."""

    max_rounds: int = 3
    max_calls: int = 6
    max_retries_per_call: int = 1
    round_count: int = 0
    call_count: int = 0
    _calls: set[str] = field(default_factory=set)
    _failures: dict[str, int] = field(default_factory=dict)

    def start_round(self) -> bool:
        if self.round_count >= self.max_rounds:
            return False
        self.round_count += 1
        return True

    @staticmethod
    def _key(tool_name: str, arguments: dict[str, Any]) -> str:
        return json.dumps(
            {"tool_name": tool_name, "arguments": arguments},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )

    def try_reserve(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        retry: bool = False,
    ) -> bool:
        if self.call_count >= self.max_calls:
            return False
        key = self._key(tool_name, arguments)
        if key in self._calls and not retry:
            return False
        if retry:
            retry_key = f"retry:{key}"
            if retry_key in self._calls:
                return False
            self._calls.add(retry_key)
        else:
            self._calls.add(key)
        self.call_count += 1
        return True

    def can_retry(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        key = self._key(tool_name, arguments)
        failures = self._failures.get(key, 0)
        if failures >= self.max_retries_per_call:
            return False
        self._failures[key] = failures + 1
        return True

    @property
    def exhausted(self) -> bool:
        return self.round_count >= self.max_rounds or self.call_count >= self.max_calls

