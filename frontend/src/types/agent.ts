/** 工作台使用的 API、SSE 和界面状态类型。 */
export type ProgressStatus = "running" | "success" | "error";

export type ProgressEvent = {
  type: "progress";
  step: string;
  status: ProgressStatus;
  agent_name?: string;
  task_id?: string;
};

export type RouteEvent = {
  type: "route";
  intent: "data" | "document" | "knowledge" | "general" | "composite" | "clarify" | string;
  agent_intent?: string;
  confidence?: number;
  strategy?: string;
  policy_version?: string;
};

export type TraceEvent = {
  type: "trace";
  run_id: string;
};

export type ResultEvent = {
  type: "result";
  data: unknown;
  sql?: string;
};

export type AnswerEvent = {
  type: "answer";
  content: string;
  citations?: Citation[];
};

export type ClarificationEvent = {
  type: "clarification";
  message: string;
};

export type ErrorEvent = {
  type: "error";
  message: string;
  agent_name?: string;
  task_id?: string;
};

export type DegradedEvent = {
  type: "degraded";
  service: string;
  status: "degraded";
  fallback: string;
  reason: string;
};

export type PlanEvent = {
  type: "plan";
  plan: {
    intent: string;
    confidence: number;
    max_iterations: number;
    required_evidence: string[];
    tasks: Array<{
      task_id: string;
      agent_name: string;
      goal: string;
      dependencies: string[];
      allowed_tools: string[];
      required: boolean;
      timeout_ms: number;
    }>;
  };
};

export type AgentLifecycleEvent = {
  type: "agent_started" | "agent_completed";
  agent_name: string;
  task_id: string;
  status: string;
  summary?: string;
  latency_ms?: number;
  verification?: string;
  verification_meta?: Record<string, unknown>;
};

export type ReplanEvent = {
  type: "replan";
  from_agent?: string;
  to_agent?: string;
  reason?: string;
  replan_count?: number;
};

export type ToolLifecycleEvent = {
  type: "tool_started" | "tool_completed";
  agent_name?: string;
  task_id?: string;
  tool_name: string;
  status?: string;
  summary?: string;
  latency_ms?: number;
  decision?: string;
  reasons?: string[];
  verification?: string;
  verification_meta?: Record<string, unknown>;
};

export type EvidenceEvent = {
  type: "evidence";
  agent_name?: string;
  citations?: Citation[];
  data?: unknown;
};

export type PartialResultEvent = {
  type: "partial_result";
  agent_name?: string;
  status?: string;
  summary?: string;
  data?: unknown;
};

export type MemoryCandidateEvent = {
  type: "memory_candidate";
  candidate: MemoryCandidate;
};

export type DoneEvent = {
  type: "done";
  status: string;
  execution_status: string;
};

export type AgentEvent =
  | ProgressEvent
  | RouteEvent
  | TraceEvent
  | ResultEvent
  | AnswerEvent
  | ClarificationEvent
  | ErrorEvent
  | DegradedEvent
  | PlanEvent
  | AgentLifecycleEvent
  | ReplanEvent
  | ToolLifecycleEvent
  | EvidenceEvent
  | PartialResultEvent
  | MemoryCandidateEvent
  | DoneEvent;

export type StepState = {
  step: string;
  status: ProgressStatus;
  updatedAt: number;
};

export type User = {
  id: number;
  email: string;
  display_name: string;
  role: string;
};

export type AdminUser = User & {
  is_active: boolean;
  created_at: string;
};

export type AuditLog = {
  id: number;
  user_id?: number | null;
  user_email?: string | null;
  user_display_name?: string | null;
  action: string;
  detail?: string | null;
  created_at: string;
};

export type AuditLogPage = {
  items: AuditLog[];
  total: number;
  limit: number;
  offset: number;
};

export type MCPToolAudit = {
  id: number;
  user_id: number;
  conversation_id?: string | null;
  run_id?: string | null;
  agent_name?: string | null;
  tool_name: string;
  source: string;
  status: string;
  error_code?: string | null;
  retryable: boolean;
  duration_ms: number;
  created_at: string;
};

export type MCPToolAuditPage = {
  items: MCPToolAudit[];
  total: number;
  limit: number;
  offset: number;
};

export type LoginResponse = {
  access_token: string;
  token_type: string;
  user: User;
};

export type Conversation = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
};

export type Citation = {
  index: number;
  filename: string;
  page_number?: number | null;
  score?: number;
};

export type QueryMode = "auto" | "data" | "knowledge" | "general" | "composite";

export type AgentTimelineItem = {
  id: string;
  kind: "agent" | "tool";
  name: string;
  status: string;
  summary?: string;
  latencyMs?: number;
  taskId?: string;
};

export type AgentRunSnapshot = {
  id: string;
  status: string;
  route?: string | null;
  route_strategy?: string | null;
  route_confidence?: number | null;
  first_event_ms?: number | null;
  total_latency_ms?: number | null;
  citation_count?: number | null;
  result_row_count?: number | null;
  error_code?: string | null;
  parent_run_id?: string | null;
  trace_summary?: string | null;
  runtime: {
    plan?: Record<string, unknown> | null;
    source?: string;
    degraded?: boolean;
    events: Array<Record<string, unknown>>;
  };
};

export type MemoryCandidate = {
  id: string;
  user_id: number;
  conversation_id?: string | null;
  kind: string;
  content: string;
  source_run_id?: string | null;
  status: string;
  created_at?: string | null;
  confirmed_at?: string | null;
};

export type DocumentView = {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  status: "processing" | "ready" | "failed" | string;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
  chunk_count?: number | null;
};

export type ServerMessage = {
  id: number;
  role: "user" | "assistant" | "system";
  content: string;
  event_data?: string | null;
  created_at: string;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: number;
  status?: "streaming" | "done" | "error";
  steps?: StepState[];
  route?: string;
  routeStrategy?: string;
  routeConfidence?: number;
  traceId?: string;
  feedbackRating?: 1 | -1;
  feedbackSubmitting?: boolean;
  citations?: Citation[];
  result?: unknown;
  sql?: string;
  error?: string;
  plan?: PlanEvent["plan"];
  timeline?: AgentTimelineItem[];
  partial?: boolean;
  executionStatus?: string;
  memoryCandidates?: MemoryCandidate[];
  degradations?: DegradedEvent[];
  verification?: {
    decision?: string;
    modelStatus?: string;
    modelConfidence?: number | null;
    degraded?: boolean;
  };
};

export type AgentFeedback = {
  id: number;
  run_id: string;
  rating: 1 | -1;
  issue_type?: string | null;
  comment?: string | null;
  review_status: "pending" | "accepted" | "rejected" | string;
  expected_route?: string | null;
  reviewer_note?: string | null;
  reviewed_at?: string | null;
  created_at: string;
  updated_at: string;
  query_preview?: string | null;
  route?: string | null;
  route_strategy?: string | null;
  route_confidence?: number | null;
  policy_version?: string | null;
  run_status?: string | null;
  total_latency_ms?: number | null;
  retrieval_latency_ms?: number | null;
  model_latency_ms?: number | null;
  sql_latency_ms?: number | null;
  citation_count?: number | null;
  error_code?: string | null;
  user_email?: string | null;
  user_display_name?: string | null;
  reviewer_display_name?: string | null;
  regression_case_id?: string | null;
};

export type AgentFeedbackPage = {
  items: AgentFeedback[];
  total: number;
  limit: number;
  offset: number;
};

export type QualityLatency = {
  count: number;
  p50_ms?: number | null;
  p95_ms?: number | null;
  average_ms?: number | null;
};

export type QualityOverview = {
  window_days: number;
  summary: {
    run_count: number;
    successful_run_count: number;
    success_rate?: number | null;
    feedback_count: number;
    positive_feedback_count: number;
    negative_feedback_count: number;
    helpful_rate?: number | null;
    pending_feedback_count: number;
    latency: QualityLatency;
    time_to_first_event: QualityLatency;
  };
  by_route: Array<{
    route: string;
    run_count: number;
    success_rate?: number | null;
    feedback_count: number;
    positive_feedback_count: number;
    p50_ms?: number | null;
    p95_ms?: number | null;
    average_ms?: number | null;
  }>;
  slow_steps: Array<{
    step_name: string;
    category: string;
    error_count: number;
    count: number;
    p50_ms?: number | null;
    p95_ms?: number | null;
    average_ms?: number | null;
  }>;
};

export type RegressionCase = {
  id: string;
  feedback_id: number;
  query: string;
  expected_route: string;
  source_issue_type?: string | null;
  created_at: string;
  created_by_name?: string | null;
};
