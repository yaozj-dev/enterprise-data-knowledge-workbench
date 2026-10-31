/**
 * 企业数据分析与制度知识问答工作台 API 客户端
 * 统一处理登录、会话数据和鉴权 SSE 流，组件层不直接拼接 HTTP 请求。
 */
import type {
  AgentFeedback,
  AgentFeedbackPage,
  AgentRunSnapshot,
  AdminUser,
  AgentEvent,
  AuditLogPage,
  Conversation,
  DocumentView,
  LoginResponse,
  MemoryCandidate,
  MCPToolAuditPage,
  QueryMode,
  QualityOverview,
  RegressionCase,
  ServerMessage,
} from "../types/agent";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") ?? "";

type QueryOptions = {
  signal?: AbortSignal;
  onEvent: (event: AgentEvent) => void;
  mode?: QueryMode;
};

async function request<T>(path: string, token?: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    let detail = `接口请求失败：HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // 非 JSON 错误响应仍保留 HTTP 状态码提示。
    }
    throw new Error(detail);
  }

  return (await response.json()) as T;
}

export function login(email: string, password: string) {
  return request<LoginResponse>("/api/workbench/auth/login", undefined, {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function register(payload: { email: string; display_name: string; password: string }) {
  return request<LoginResponse>("/api/workbench/auth/register", undefined, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getMe(token: string) {
  return request<LoginResponse["user"]>("/api/workbench/auth/me", token);
}

export function listAdminUsers(token: string) {
  return request<{ items: AdminUser[] }>("/api/workbench/admin/users", token);
}

export function createAdminUser(
  token: string,
  payload: { email: string; display_name: string; password: string; role: "admin" | "user" },
) {
  return request<AdminUser>("/api/workbench/admin/users", token, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function deleteAdminUser(token: string, userId: number) {
  return request<AdminUser>(`/api/workbench/admin/users/${userId}`, token, {
    method: "DELETE",
  });
}

export function listAuditLogs(
  token: string,
  options: { limit?: number; offset?: number; action?: string } = {},
) {
  const search = new URLSearchParams({
    limit: String(options.limit ?? 20),
    offset: String(options.offset ?? 0),
  });
  if (options.action?.trim()) search.set("action", options.action.trim());
  return request<AuditLogPage>(`/api/workbench/admin/audit-logs?${search.toString()}`, token);
}

export function listMcpAuditLogs(token: string, options: { limit?: number; offset?: number } = {}) {
  const search = new URLSearchParams({
    limit: String(options.limit ?? 50),
    offset: String(options.offset ?? 0),
  });
  return request<MCPToolAuditPage>(`/api/workbench/admin/mcp-audit-logs?${search.toString()}`, token);
}

export function getQualityOverview(token: string, windowDays = 14) {
  return request<QualityOverview>(
    `/api/workbench/admin/quality/overview?window_days=${encodeURIComponent(String(windowDays))}`,
    token,
  );
}

export function listQualityFeedback(
  token: string,
  options: { status?: "pending" | "accepted" | "rejected"; limit?: number; offset?: number } = {},
) {
  const search = new URLSearchParams({
    limit: String(options.limit ?? 20),
    offset: String(options.offset ?? 0),
  });
  if (options.status) search.set("status", options.status);
  return request<AgentFeedbackPage>(
    `/api/workbench/admin/quality/feedback?${search.toString()}`,
    token,
  );
}

export function reviewAgentFeedback(
  token: string,
  feedbackId: number,
  payload: {
    review_status: "accepted" | "rejected";
    reviewer_note?: string;
    expected_route?: "data" | "document" | "general" | "clarify";
    regression_query?: string;
    promote_to_regression: boolean;
  },
) {
  return request<AgentFeedback>(
    `/api/workbench/admin/quality/feedback/${feedbackId}/review`,
    token,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

export function listRegressionCases(token: string) {
  return request<RegressionCase[]>("/api/workbench/admin/quality/regression-cases", token);
}

export function submitAgentFeedback(
  token: string,
  runId: string,
  payload: {
    rating: 1 | -1;
    issue_type?: "wrong_answer" | "retrieval_miss" | "wrong_route" | "slow_response" | "unsafe_or_unexpected_sql" | "other";
    comment?: string;
  },
) {
  return request<AgentFeedback>(
    `/api/workbench/agent-runs/${encodeURIComponent(runId)}/feedback`,
    token,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

export function getAgentRun(token: string | undefined, runId: string) {
  return request<AgentRunSnapshot>(`/api/workbench/agent-runs/${encodeURIComponent(runId)}`, token);
}

export function getCurrentAgentRun(runId: string) {
  return getAgentRun(localStorage.getItem("workbench_token") ?? undefined, runId);
}

export function retryAgentRun(token: string, runId: string) {
  return request<AgentRunSnapshot>(
    `/api/workbench/agent-runs/${encodeURIComponent(runId)}/retry`,
    token,
    { method: "POST" },
  );
}

export function listConversations(token: string) {
  return request<{ items: Conversation[] }>("/api/workbench/conversations", token);
}

export function createConversation(token: string, title = "新会话") {
  return request<Conversation>("/api/workbench/conversations", token, {
    method: "POST",
    body: JSON.stringify({ title }),
  });
}

export function listMessages(token: string, conversationId: string) {
  return request<{ items: ServerMessage[] }>(
    `/api/workbench/conversations/${encodeURIComponent(conversationId)}/messages`,
    token,
  );
}

export function listDocuments(token: string) {
  return request<DocumentView[]>("/api/workbench/documents", token);
}

export function listMemory(token: string, status?: "pending" | "confirmed" | "deleted") {
  const suffix = status ? `?status=${encodeURIComponent(status)}` : "";
  return request<MemoryCandidate[]>(`/api/workbench/memory${suffix}`, token);
}

export function confirmMemory(token: string, memoryId: string) {
  return request<MemoryCandidate>(
    `/api/workbench/memory/${encodeURIComponent(memoryId)}/confirm`,
    token,
    { method: "POST" },
  );
}

export function deleteMemory(token: string, memoryId: string) {
  return request<void>(`/api/workbench/memory/${encodeURIComponent(memoryId)}`, token, {
    method: "DELETE",
  });
}

export async function uploadDocument(token: string, file: File) {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(`${API_BASE_URL}/api/workbench/documents`, {
    method: "POST",
    headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
    body: formData,
  });
  if (!response.ok) {
    let detail = `文档上传失败：HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // 保留状态码错误。
    }
    throw new Error(detail);
  }
  return (await response.json()) as DocumentView;
}

export function reindexDocument(token: string, documentId: string) {
  return request<DocumentView>(
    `/api/workbench/documents/${encodeURIComponent(documentId)}/reindex`,
    token,
    { method: "POST" },
  );
}

export function deleteDocument(token: string, documentId: string) {
  return request<DocumentView>(
    `/api/workbench/documents/${encodeURIComponent(documentId)}`,
    token,
    { method: "DELETE" },
  );
}

export async function streamConversationQuery(
  token: string,
  conversationId: string,
  query: string,
  options: QueryOptions,
) {
  const response = await fetch(
    `${API_BASE_URL}/api/workbench/conversations/${encodeURIComponent(conversationId)}/query`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify({ query, mode: options.mode ?? "auto" }),
      signal: options.signal,
    },
  );

  if (!response.ok) {
    let detail = `接口请求失败：HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // 流式接口可能返回空响应，使用状态码即可定位问题。
    }
    throw new Error(detail);
  }

  if (!response.body) throw new Error("浏览器未返回可读取的流式响应。");

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split(/\n\n/);
    buffer = chunks.pop() ?? "";
    for (const chunk of chunks) {
      const event = parseSseChunk(chunk);
      if (event) options.onEvent(event);
    }
  }

  buffer += decoder.decode();
  const tail = parseSseChunk(buffer);
  if (tail) options.onEvent(tail);
}

function parseSseChunk(chunk: string): AgentEvent | null {
  const payload = chunk
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.replace(/^data:\s?/, ""))
    .join("\n")
    .trim();

  if (!payload) return null;
  try {
    return JSON.parse(payload) as AgentEvent;
  } catch {
    return { type: "error", message: `无法解析后端事件：${payload}` };
  }
}
