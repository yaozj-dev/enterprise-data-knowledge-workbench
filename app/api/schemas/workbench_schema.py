"""Request and response models for the enterprise data workbench."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=160)
    password: str = Field(min_length=8, max_length=200)


class RegisterRequest(BaseModel):
    """Self-registration never accepts a caller-selected role."""

    email: str = Field(min_length=3, max_length=160)
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=8, max_length=200)


class UserView(BaseModel):
    id: int
    email: str
    display_name: str
    role: str


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=160)
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=8, max_length=200)
    role: Literal["admin", "user"] = "user"


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserView


class ConversationCreate(BaseModel):
    title: str = Field(default="新会话", max_length=120)


class WorkbenchQuery(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    mode: Literal["auto", "data", "knowledge", "general", "composite"] = "auto"


class DocumentView(BaseModel):
    id: str
    filename: str
    content_type: str
    size_bytes: int
    status: str
    error_message: str | None = None
    created_at: str
    updated_at: str
    chunk_count: int | None = None


class MemoryCandidateCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2_000)
    kind: Literal["preference", "fact", "instruction"] = "preference"


class MemoryCandidateView(BaseModel):
    id: str
    user_id: int
    conversation_id: str | None = None
    kind: str
    content: str
    source_run_id: str | None = None
    status: str
    created_at: Any | None = None
    confirmed_at: Any | None = None


class AuditLogView(BaseModel):
    id: int
    user_id: int | None = None
    user_email: str | None = None
    user_display_name: str | None = None
    action: str
    detail: str | None = None
    created_at: str


class AuditLogPage(BaseModel):
    items: list[AuditLogView]
    total: int
    limit: int
    offset: int


class AgentFeedbackCreate(BaseModel):
    rating: Literal[-1, 1]
    issue_type: Literal[
        "wrong_answer",
        "retrieval_miss",
        "wrong_route",
        "slow_response",
        "unsafe_or_unexpected_sql",
        "other",
    ] | None = None
    comment: str | None = Field(default=None, max_length=2_000)


class AgentFeedbackReview(BaseModel):
    review_status: Literal["accepted", "rejected"]
    reviewer_note: str | None = Field(default=None, max_length=2_000)
    expected_route: Literal["data", "document", "general", "clarify"] | None = None
    regression_query: str | None = Field(default=None, max_length=4_000)
    promote_to_regression: bool = False


class AgentFeedbackView(BaseModel):
    id: int
    run_id: str
    rating: int
    issue_type: str | None = None
    comment: str | None = None
    review_status: str
    expected_route: str | None = None
    reviewer_note: str | None = None
    reviewed_at: str | None = None
    created_at: str
    updated_at: str
    query_preview: str | None = None
    route: str | None = None
    route_strategy: str | None = None
    route_confidence: float | None = None
    policy_version: str | None = None
    run_status: str | None = None
    total_latency_ms: int | None = None
    retrieval_latency_ms: int | None = None
    model_latency_ms: int | None = None
    sql_latency_ms: int | None = None
    citation_count: int | None = None
    error_code: str | None = None
    user_email: str | None = None
    user_display_name: str | None = None
    reviewer_display_name: str | None = None
    regression_case_id: str | None = None


class AgentFeedbackPage(BaseModel):
    items: list[AgentFeedbackView]
    total: int
    limit: int
    offset: int


class QualityOverview(BaseModel):
    window_days: int
    summary: dict[str, Any]
    by_route: list[dict[str, Any]]
    slow_steps: list[dict[str, Any]]


class RegressionCaseView(BaseModel):
    id: str
    feedback_id: int
    query: str
    expected_route: str
    source_issue_type: str | None = None
    created_at: str
    created_by_name: str | None = None


class AgentRunView(BaseModel):
    """User-scoped persisted run metadata plus bounded runtime state."""

    id: str
    user_id: int
    conversation_id: str | None = None
    query_preview: str
    route: str | None = None
    route_strategy: str | None = None
    route_confidence: float | None = None
    policy_version: str
    status: str
    first_event_ms: int | None = None
    total_latency_ms: int | None = None
    retrieval_latency_ms: int | None = None
    model_latency_ms: int | None = None
    sql_latency_ms: int | None = None
    citation_count: int = 0
    result_row_count: int | None = None
    error_code: str | None = None
    trace_summary: str | None = None
    parent_run_id: str | None = None
    started_at: str
    completed_at: str | None = None
    runtime: dict[str, Any]


class MCPToolAuditView(BaseModel):
    id: int
    user_id: int
    conversation_id: str | None = None
    run_id: str | None = None
    agent_name: str | None = None
    tool_name: str
    source: str
    status: str
    error_code: str | None = None
    retryable: bool
    duration_ms: int
    created_at: Any


class MCPToolAuditPage(BaseModel):
    items: list[MCPToolAuditView]
    total: int
    limit: int
    offset: int
