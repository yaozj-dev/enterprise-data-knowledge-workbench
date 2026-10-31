/** 企业数据分析与制度知识问答工作台主界面：登录、会话管理、路由展示和 SSE 对话。 */
import {
  Activity,
  ArrowUpRight,
  BarChart3,
  BookOpen,
  Check,
  CircleUserRound,
  Clock3,
  Eraser,
  History,
  LayoutDashboard,
  LogOut,
  Menu,
  MessageSquarePlus,
  Gauge,
  Server,
  ShieldCheck,
  Sparkles,
  Upload,
  X,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Composer } from "./components/Composer";
import { AdminConsole } from "./components/AdminConsole";
import { EmptyState } from "./components/EmptyState";
import { KnowledgeBase } from "./components/KnowledgeBase";
import { MessageBubble } from "./components/MessageBubble";
import { QualityConsole } from "./components/QualityConsole";
import {
  createConversation,
  createAdminUser,
  deleteAdminUser,
  deleteDocument,
  getMe,
  getQualityOverview,
  listConversations,
  listAdminUsers,
  listAuditLogs,
  listDocuments,
  listQualityFeedback,
  listRegressionCases,
  listMessages,
  listMcpAuditLogs,
  listMemory,
  login,
  register,
  reindexDocument,
  reviewAgentFeedback,
  retryAgentRun,
  streamConversationQuery,
  submitAgentFeedback,
  confirmMemory,
  deleteMemory,
  uploadDocument,
} from "./lib/agentApi";
import { cn } from "./lib/format";
import type {
  AgentEvent,
  AgentTimelineItem,
  AgentFeedback,
  AgentFeedbackPage,
  AdminUser,
  AuditLogPage,
  ChatMessage,
  Conversation,
  ServerMessage,
  StepState,
  User,
  DocumentView,
  LoginResponse,
  MemoryCandidate,
  MCPToolAuditPage,
  QualityOverview,
  QueryMode,
  RegressionCase,
} from "./types/agent";

const examples = [
  "结合销售数据，分析 2025 年 3 月费用情况，并说明差旅报销应如何申请",
  "统计 2025 年第一季度各大区的 GMV，并按 GMV 从高到低排序",
  "统计 2025 年 3 月各商品品类的销量和销售额",
  "报销流程应该如何申请？",
];

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "Vite /api proxy";
const intentLabels: Record<string, string> = {
  data: "数据分析",
  document: "企业文档",
  general: "通用对话",
  knowledge: "企业文档",
  composite: "综合分析",
  clarify: "待澄清",
};

type WorkspaceView = "chat" | "documents" | "admin" | "quality";

function makeId() {
  return crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function upsertStep(steps: StepState[] = [], event: Extract<AgentEvent, { type: "progress" }>) {
  const next = steps.filter((item) => item.step !== event.step);
  next.push({ step: event.step, status: event.status, updatedAt: Date.now() });
  return next;
}

function upsertTimeline(
  items: AgentTimelineItem[] = [],
  event: Extract<AgentEvent, { type: "agent_started" | "agent_completed" }>,
) {
  const id = `agent:${event.task_id}`;
  const next = items.filter((item) => item.id !== id);
  next.push({
    id,
    kind: "agent",
    name: event.agent_name,
    status: event.status,
    summary: event.summary,
    latencyMs: event.latency_ms,
    taskId: event.task_id,
  });
  return next;
}

function upsertToolTimeline(
  items: AgentTimelineItem[] = [],
  event: Extract<AgentEvent, { type: "tool_started" | "tool_completed" }>,
) {
  const id = `tool:${event.agent_name ?? "agent"}:${event.tool_name}`;
  const next = items.filter((item) => item.id !== id);
  next.push({
    id,
    kind: "tool",
    name: event.tool_name,
    status: event.status ?? "running",
    summary: event.summary,
    latencyMs: event.latency_ms,
    taskId: event.task_id,
  });
  return next;
}

function fromServerMessage(message: ServerMessage): ChatMessage | null {
  if (message.role === "system") return null;
  let metadata: {
    result?: unknown;
    sql?: string;
    citations?: ChatMessage["citations"];
    trace_id?: string;
    route?: string;
    route_strategy?: string;
    plan?: ChatMessage["plan"];
    execution_status?: string;
    timeline?: ChatMessage["timeline"];
    partial?: boolean;
    memory_candidates?: ChatMessage["memoryCandidates"];
    degradations?: ChatMessage["degradations"];
    verification?: {
      decision?: string;
      meta?: Record<string, unknown>;
    };
  } = {};
  if (message.event_data) {
    try {
      metadata = JSON.parse(message.event_data) as typeof metadata;
    } catch {
      // 兼容早期只保存文本的会话记录。
    }
  }
  return {
    id: `server-${message.id}`,
    role: message.role,
    content: message.content,
    createdAt: Date.parse(message.created_at) || Date.now(),
    status: "done",
    result: metadata.result,
    sql: metadata.sql,
    citations: metadata.citations,
    traceId: metadata.trace_id,
    route: metadata.route,
    routeStrategy: metadata.route_strategy,
    plan: metadata.plan,
    executionStatus: metadata.execution_status,
    timeline: metadata.timeline,
    partial: metadata.partial,
    memoryCandidates: metadata.memory_candidates,
    degradations: metadata.degradations?.map((item) => ({
      ...item,
      type: "degraded" as const,
      status: "degraded" as const,
    })),
    verification: metadata.verification
      ? {
          decision: metadata.verification.decision,
          modelStatus: typeof metadata.verification.meta?.model_status === "string"
            ? metadata.verification.meta.model_status
            : undefined,
          modelConfidence: typeof metadata.verification.meta?.model_confidence === "number"
            ? metadata.verification.meta.model_confidence
            : null,
          degraded: metadata.verification.meta?.degraded === true,
        }
      : undefined,
  };
}

type AuthMode = "login" | "register";

function LoginScreen({
  onLogin,
  onRegister,
}: {
  onLogin: (email: string, password: string) => Promise<void>;
  onRegister: (displayName: string, email: string, password: string) => Promise<void>;
}) {
  const [mode, setMode] = useState<AuthMode>("login");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const isRegistering = mode === "register";

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (submitting) return;
    setError("");
    if (isRegistering && password !== confirmPassword) {
      setError("两次输入的密码不一致");
      return;
    }
    setSubmitting(true);
    try {
      if (isRegistering) {
        await onRegister(displayName.trim(), email.trim(), password);
      } else {
        await onLogin(email.trim(), password);
      }
    } catch (authError) {
      setError(authError instanceof Error ? authError.message : isRegistering ? "注册失败" : "登录失败");
    } finally {
      setSubmitting(false);
    }
  };

  const switchMode = (nextMode: AuthMode) => {
    setMode(nextMode);
    setError("");
    setConfirmPassword("");
  };

  return (
    <main className="grid min-h-dvh bg-[#e8efed] text-ink lg:grid-cols-[1.05fr_0.95fr]">
      <section className="relative hidden overflow-hidden bg-ink p-12 text-white lg:flex lg:flex-col lg:justify-between">
        <div className="relative">
          <div className="mb-14 flex items-center gap-3">
            <div className="grid h-11 w-11 place-items-center bg-moss text-white"><BarChart3 className="h-5 w-5" aria-hidden="true" /></div>
            <div><div className="text-base font-semibold">企业数据分析与制度知识问答工作台</div><div className="text-xs text-white/45">AGENT OPERATIONS</div></div>
          </div>
          <div className="max-w-xl">
            <p className="mb-5 text-xs font-semibold uppercase tracking-[0.22em] text-moss/90">统一智能入口</p>
            <h1 className="text-5xl font-semibold leading-[1.08] tracking-tight">让企业问题，<br />进入可追踪的执行流程。</h1>
            <p className="mt-6 max-w-md text-sm leading-7 text-white/60">连接数据分析、企业知识库与通用对话，让每一次回答都能看见路由、证据和结果。</p>
          </div>
        </div>
        <div className="relative grid max-w-xl grid-cols-3 gap-3">
          {[{ label: "数据分析", value: "SQL" }, { label: "知识检索", value: "RAG" }, { label: "流程编排", value: "Graph" }].map((item) => <div key={item.label} className="border border-white/10 bg-white/[0.04] px-3 py-3"><div className="text-sm font-semibold text-white/85">{item.value}</div><div className="mt-1 text-[11px] text-white/45">{item.label}</div></div>)}
        </div>
      </section>

      <section className="flex items-center justify-center px-4 py-10 sm:px-8">
      <div className="w-full max-w-md border border-ink/10 bg-white p-7 shadow-panel sm:p-9">
        <div className="mb-8 flex items-center gap-3 lg:hidden">
          <div className="grid h-11 w-11 place-items-center bg-ink text-white"><Sparkles className="h-5 w-5" aria-hidden="true" /></div>
          <div><h1 className="text-lg font-semibold">企业数据分析与制度知识问答工作台</h1><p className="text-xs text-ink/45">Agent Workspace</p></div>
        </div>
        <div className="mb-8">
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-moss">{isRegistering ? "创建账号" : "欢迎回来"}</p>
          <h2 className="text-2xl font-semibold tracking-tight">{isRegistering ? "注册工作台账号" : "登录工作台"}</h2>
          <p className="mt-2 text-sm leading-6 text-ink/55">{isRegistering ? "填写信息后即可开始使用。" : "使用你的工作台账号登录。"}</p>
        </div>

        <form className="space-y-4" onSubmit={submit}>
          {isRegistering && (
            <label className="block text-sm font-medium text-ink/75">
              姓名
              <input
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                type="text"
                autoComplete="name"
                required
                maxLength={80}
                className="mt-2 h-12 w-full border border-ink/15 bg-[#f7faf9] px-3 text-sm outline-none transition placeholder:text-ink/30 focus:border-moss focus:ring-2 focus:ring-moss/15"
                placeholder="你的姓名"
              />
            </label>
          )}
          <label className="block text-sm font-medium text-ink/75">
            邮箱地址
            <input
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              type="email"
              autoComplete="email"
              required
              className="mt-2 h-12 w-full border border-ink/15 bg-[#f7faf9] px-3 text-sm outline-none transition placeholder:text-ink/30 focus:border-moss focus:ring-2 focus:ring-moss/15"
              placeholder="admin@example.com"
            />
          </label>
          <label className="block text-sm font-medium text-ink/75">
            密码
            <input
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              autoComplete={isRegistering ? "new-password" : "current-password"}
              required
              minLength={8}
              className="mt-2 h-12 w-full border border-ink/15 bg-[#f7faf9] px-3 text-sm outline-none transition placeholder:text-ink/30 focus:border-moss focus:ring-2 focus:ring-moss/15"
              placeholder="请输入密码"
            />
          </label>
          {isRegistering && (
            <label className="block text-sm font-medium text-ink/75">
              确认密码
              <input
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
                type="password"
                autoComplete="new-password"
                required
                minLength={8}
                className="mt-2 h-12 w-full border border-ink/15 bg-[#f7faf9] px-3 text-sm outline-none transition placeholder:text-ink/30 focus:border-moss focus:ring-2 focus:ring-moss/15"
                placeholder="再次输入密码"
              />
            </label>
          )}
          {error && <p className="border border-tomato/25 bg-tomato/10 px-3 py-2 text-sm text-tomato">{error}</p>}
          <button
            type="submit"
            disabled={submitting}
            className="flex h-12 w-full items-center justify-center gap-2 bg-moss text-sm font-semibold text-white transition hover:bg-soot disabled:cursor-not-allowed disabled:bg-ink/35"
          >
            <ShieldCheck className="h-4 w-4" aria-hidden="true" />
            {submitting ? isRegistering ? "注册中..." : "登录中..." : isRegistering ? "创建账号" : "登录工作台"}
          </button>
          <p className="pt-1 text-center text-sm text-ink/55">
            {isRegistering ? "已有账号？" : "还没有账号？"}
            <button
              type="button"
              onClick={() => switchMode(isRegistering ? "login" : "register")}
              className="ml-1 font-medium text-moss underline decoration-moss/40 underline-offset-4 transition hover:text-soot"
            >
              {isRegistering ? "去登录" : "注册账号"}
            </button>
          </p>
        </form>
      </div>
      </section>
    </main>
  );
}

export default function App() {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem("workbench_token"));
  const [user, setUser] = useState<User | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [workspaceError, setWorkspaceError] = useState("");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [documents, setDocuments] = useState<DocumentView[]>([]);
  const [uploading, setUploading] = useState(false);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [activeController, setActiveController] = useState<AbortController | null>(null);
  const [queryMode, setQueryMode] = useState<QueryMode>("auto");
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [activeView, setActiveView] = useState<WorkspaceView>("chat");
  const [activeDocumentId, setActiveDocumentId] = useState<string | null>(null);
  const [adminUsers, setAdminUsers] = useState<AdminUser[]>([]);
  const [auditPage, setAuditPage] = useState<AuditLogPage | null>(null);
  const [mcpAuditPage, setMcpAuditPage] = useState<MCPToolAuditPage | null>(null);
  const [auditActionFilter, setAuditActionFilter] = useState("");
  const [adminLoading, setAdminLoading] = useState(false);
  const [adminCreating, setAdminCreating] = useState(false);
  const [adminDeletingUserId, setAdminDeletingUserId] = useState<number | null>(null);
  const [qualityOverview, setQualityOverview] = useState<QualityOverview | null>(null);
  const [qualityFeedbackPage, setQualityFeedbackPage] = useState<AgentFeedbackPage | null>(null);
  const [regressionCases, setRegressionCases] = useState<RegressionCase[]>([]);
  const [qualityLoading, setQualityLoading] = useState(false);
  const [reviewingFeedbackId, setReviewingFeedbackId] = useState<number | null>(null);
  const [memoryCandidates, setMemoryCandidates] = useState<MemoryCandidate[]>([]);
  const [retryingRunId, setRetryingRunId] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const documentInputRef = useRef<HTMLInputElement | null>(null);

  const isStreaming = Boolean(activeController);
  const canSubmit = draft.trim().length > 0 && !isStreaming && Boolean(activeConversationId);
  const completedCount = useMemo(
    () => messages.filter((message) => message.role === "assistant" && message.status === "done").length,
    [messages],
  );
  const activeConversation = conversations.find((conversation) => conversation.id === activeConversationId);
  const latestAssistantId = [...messages].reverse().find((message) => message.role === "assistant")?.id;

  const refreshMemoryCandidates = async (accessToken: string) => {
    try {
      setMemoryCandidates(await listMemory(accessToken, "pending"));
    } catch {
      // Memory is an optional enhancement. A database outage must not block chat.
      setMemoryCandidates([]);
    }
  };

  const hydrateWorkspace = async (accessToken: string, preferredId?: string) => {
    const [response, documentResponse] = await Promise.all([
      listConversations(accessToken),
      listDocuments(accessToken),
    ]);
    setDocuments(documentResponse);
    let items = response.items;
    if (items.length === 0) {
      items = [await createConversation(accessToken)];
    }
    setConversations(items);
    const selectedId = preferredId && items.some((item) => item.id === preferredId) ? preferredId : items[0].id;
    setActiveConversationId(selectedId);
    const history = await listMessages(accessToken, selectedId);
    setMessages(history.items.map(fromServerMessage).filter((item): item is ChatMessage => item !== null));
    await refreshMemoryCandidates(accessToken);
  };

  useEffect(() => {
    const savedToken = localStorage.getItem("workbench_token");
    if (!savedToken) {
      setAuthLoading(false);
      return;
    }

    void (async () => {
      try {
        const currentUser = await getMe(savedToken);
        setToken(savedToken);
        setUser(currentUser);
        await hydrateWorkspace(savedToken);
      } catch {
        localStorage.removeItem("workbench_token");
        setToken(null);
      } finally {
        setAuthLoading(false);
      }
    })();
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const completeAuthentication = async (response: LoginResponse) => {
    localStorage.setItem("workbench_token", response.access_token);
    setToken(response.access_token);
    setUser(response.user);
    setWorkspaceError("");
    await hydrateWorkspace(response.access_token);
  };

  const handleLogin = async (email: string, password: string) => {
    await completeAuthentication(await login(email, password));
  };

  const handleRegister = async (displayName: string, email: string, password: string) => {
    await completeAuthentication(await register({ display_name: displayName, email, password }));
  };

  const logout = () => {
    activeController?.abort();
    localStorage.removeItem("workbench_token");
    setToken(null);
    setUser(null);
    setConversations([]);
    setActiveConversationId(null);
    setMessages([]);
    setAdminUsers([]);
    setAuditPage(null);
    setMcpAuditPage(null);
    setAdminDeletingUserId(null);
    setQualityOverview(null);
    setQualityFeedbackPage(null);
    setRegressionCases([]);
    setReviewingFeedbackId(null);
    setMemoryCandidates([]);
  };

  const selectConversation = async (conversationId: string) => {
    if (!token || isStreaming) return;
    setWorkspaceError("");
    setActiveView("chat");
    setActiveConversationId(conversationId);
    setMobileNavOpen(false);
    try {
      const history = await listMessages(token, conversationId);
      setMessages(history.items.map(fromServerMessage).filter((item): item is ChatMessage => item !== null));
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "无法加载会话");
    }
  };

  const newConversation = async () => {
    if (!token || isStreaming) return;
    try {
      const conversation = await createConversation(token);
      setConversations((current) => [conversation, ...current]);
      setActiveConversationId(conversation.id);
      setActiveView("chat");
      setMessages([]);
      setDraft("");
      setMobileNavOpen(false);
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "无法创建会话");
    }
  };

  const upload = async (file: File) => {
    if (!token || uploading) return;
    setUploading(true);
    setWorkspaceError("");
    try {
      const document = await uploadDocument(token, file);
      setDocuments((current) => [document, ...current.filter((item) => item.id !== document.id)]);
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "文档上传失败");
    } finally {
      setUploading(false);
    }
  };

  const reindex = async (document: DocumentView) => {
    if (!token || activeDocumentId) return;
    setActiveDocumentId(document.id);
    setWorkspaceError("");
    try {
      const updated = await reindexDocument(token, document.id);
      setDocuments((current) => current.map((item) => item.id === updated.id ? updated : item));
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "文档索引重建失败");
    } finally {
      setActiveDocumentId(null);
    }
  };

  const remove = async (document: DocumentView) => {
    if (!token || activeDocumentId) return;
    const approved = window.confirm(`确认删除“${document.filename}”？该操作会移除文档分块和向量索引。`);
    if (!approved) return;
    setActiveDocumentId(document.id);
    setWorkspaceError("");
    try {
      await deleteDocument(token, document.id);
      setDocuments((current) => current.filter((item) => item.id !== document.id));
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "文档删除失败");
    } finally {
      setActiveDocumentId(null);
    }
  };

  const loadAdmin = async (offset = 0, action = auditActionFilter) => {
    if (!token || user?.role !== "admin") return;
    setAdminLoading(true);
    setWorkspaceError("");
    try {
      const [usersResponse, auditResponse, mcpResponse] = await Promise.all([
        listAdminUsers(token),
        listAuditLogs(token, { limit: 20, offset, action }),
        listMcpAuditLogs(token).catch(() => null),
      ]);
      setAdminUsers(usersResponse.items);
      setAuditPage(auditResponse);
      setMcpAuditPage(mcpResponse);
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "无法加载管理员数据");
    } finally {
      setAdminLoading(false);
    }
  };

  const openAdmin = () => {
    if (user?.role !== "admin") return;
    setActiveView("admin");
    setMobileNavOpen(false);
    void loadAdmin(0);
  };

  const loadQuality = async () => {
    if (!token || user?.role !== "admin") return;
    setQualityLoading(true);
    setWorkspaceError("");
    try {
      const [overview, feedback, cases] = await Promise.all([
        getQualityOverview(token),
        listQualityFeedback(token, { limit: 30, offset: 0 }),
        listRegressionCases(token),
      ]);
      setQualityOverview(overview);
      setQualityFeedbackPage(feedback);
      setRegressionCases(cases);
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "无法加载质量运营数据");
    } finally {
      setQualityLoading(false);
    }
  };

  const openQuality = () => {
    if (user?.role !== "admin") return;
    setActiveView("quality");
    setMobileNavOpen(false);
    void loadQuality();
  };

  const createUserFromAdmin = async (payload: {
    email: string;
    display_name: string;
    password: string;
    role: "admin" | "user";
  }) => {
    if (!token) return;
    setAdminCreating(true);
    try {
      await createAdminUser(token, payload);
      await loadAdmin(auditPage?.offset ?? 0);
    } finally {
      setAdminCreating(false);
    }
  };

  const deleteUserFromAdmin = async (target: AdminUser) => {
    if (!token || target.id === user?.id || adminDeletingUserId !== null) return;
    const approved = window.confirm(`确认删除账号“${target.email}”？该操作会同时删除其会话和已上传文档。`);
    if (!approved) return;
    setAdminDeletingUserId(target.id);
    setWorkspaceError("");
    try {
      await deleteAdminUser(token, target.id);
      await loadAdmin(auditPage?.offset ?? 0);
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "删除用户失败");
    } finally {
      setAdminDeletingUserId(null);
    }
  };

  const submitFeedbackForMessage = async (
    runId: string,
    payload: {
      rating: 1 | -1;
      issue_type?: "wrong_answer" | "retrieval_miss" | "wrong_route" | "slow_response" | "unsafe_or_unexpected_sql" | "other";
      comment?: string;
    },
  ) => {
    if (!token) throw new Error("登录状态已失效，请重新登录");
    setMessages((current) => current.map((message) => message.traceId === runId ? { ...message, feedbackSubmitting: true } : message));
    try {
      await submitAgentFeedback(token, runId, payload);
      setMessages((current) => current.map((message) => message.traceId === runId ? { ...message, feedbackRating: payload.rating, feedbackSubmitting: false } : message));
    } catch (error) {
      setMessages((current) => current.map((message) => message.traceId === runId ? { ...message, feedbackSubmitting: false } : message));
      throw error;
    }
  };

  const confirmMemoryCandidate = async (candidate: MemoryCandidate) => {
    if (!token) throw new Error("登录状态已失效，请重新登录");
    await confirmMemory(token, candidate.id);
    setMemoryCandidates((current) => current.filter((item) => item.id !== candidate.id));
    setMessages((current) => current.map((message) => ({
      ...message,
      memoryCandidates: message.memoryCandidates?.filter((item) => item.id !== candidate.id),
    })));
  };

  const deleteMemoryCandidate = async (candidate: MemoryCandidate) => {
    if (!token) throw new Error("登录状态已失效，请重新登录");
    await deleteMemory(token, candidate.id);
    setMemoryCandidates((current) => current.filter((item) => item.id !== candidate.id));
    setMessages((current) => current.map((message) => ({
      ...message,
      memoryCandidates: message.memoryCandidates?.filter((item) => item.id !== candidate.id),
    })));
  };

  const reviewFeedbackFromQuality = async (
    feedback: AgentFeedback,
    payload: {
      review_status: "accepted" | "rejected";
      reviewer_note?: string;
      expected_route?: "data" | "document" | "general" | "clarify";
      regression_query?: string;
      promote_to_regression: boolean;
    },
  ) => {
    if (!token) throw new Error("登录状态已失效，请重新登录");
    setReviewingFeedbackId(feedback.id);
    try {
      await reviewAgentFeedback(token, feedback.id, payload);
      await loadQuality();
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "审核反馈失败");
      throw error;
    } finally {
      setReviewingFeedbackId(null);
    }
  };

  const retryAssistantRun = async (runId: string) => {
    if (!token || retryingRunId) return;
    setRetryingRunId(runId);
    setWorkspaceError("");
    try {
      await retryAgentRun(token, runId);
      if (activeConversationId) await selectConversation(activeConversationId);
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "重试失败");
    } finally {
      setRetryingRunId(null);
    }
  };

  const startQuery = async (rawQuery = draft) => {
    const query = rawQuery.trim();
    if (!query || isStreaming || !token || !activeConversationId) return;

    setActiveView("chat");

    const userMessage: ChatMessage = {
      id: makeId(),
      role: "user",
      content: query,
      createdAt: Date.now(),
    };
    const assistantId = makeId();
    const assistantMessage: ChatMessage = {
      id: assistantId,
      role: "assistant",
      content: "正在分析你的问题...",
      createdAt: Date.now(),
      status: "streaming",
      steps: [],
    };
    const controller = new AbortController();
    setActiveController(controller);
    setDraft("");
    setMessages((current) => [...current, userMessage, assistantMessage]);

    const onEvent = (event: AgentEvent) => {
      setMessages((current) => current.map((message) => {
        if (message.id !== assistantId) return message;
        if (event.type === "trace") {
          return { ...message, traceId: event.run_id };
        }
        if (event.type === "degraded") {
          const existing = message.degradations ?? [];
          const next = existing.some(
            (item) => item.service === event.service && item.reason === event.reason,
          )
            ? existing
            : [...existing, event];
          return {
            ...message,
            degradations: next,
            content: message.content || "流程正在以降级模式运行",
          };
        }
        if (event.type === "route") {
          return {
            ...message,
            route: event.intent,
            routeStrategy: event.strategy,
            routeConfidence: event.confidence,
            content: `已路由至：${intentLabels[event.intent] ?? event.intent}`,
          };
        }
        if (event.type === "plan") {
          return {
            ...message,
            plan: event.plan,
            content: `已生成执行计划：${intentLabels[event.plan.intent] ?? event.plan.intent}`,
          };
        }
        if (event.type === "replan") {
          const replanItem: AgentTimelineItem = {
            id: `replan:${event.replan_count ?? Date.now()}`,
            kind: "agent",
            name: "supervisor",
            status: "partial",
            summary: `已重新规划：${event.reason ?? "任务能力不匹配"}`,
          };
          return {
            ...message,
            timeline: [...(message.timeline ?? []), replanItem],
            content: `Supervisor 正在重新规划（第 ${event.replan_count ?? 1} 次）`,
          };
        }
        if (event.type === "progress") {
          return {
            ...message,
            content: event.status === "running" ? `正在执行：${event.step}` : message.content,
            steps: upsertStep(message.steps, event),
          };
        }
        if (event.type === "agent_started" || event.type === "agent_completed") {
          const nextTimeline = upsertTimeline(message.timeline, event);
          const verification = event.verification_meta;
          return {
            ...message,
            timeline: nextTimeline,
            verification: verification || event.verification
              ? {
                  decision: event.verification,
                  modelStatus: typeof verification?.model_status === "string" ? verification.model_status : undefined,
                  modelConfidence: typeof verification?.model_confidence === "number" ? verification.model_confidence : null,
                  degraded: verification?.degraded === true,
                }
              : message.verification,
            content: event.status === "running"
              ? `正在执行 ${event.agent_name} Agent`
              : message.content,
          };
        }
        if (event.type === "tool_started" || event.type === "tool_completed") {
          const verification = event.verification_meta;
          return {
            ...message,
            timeline: upsertToolTimeline(message.timeline, event),
            verification: verification
              ? {
                  decision: event.verification,
                  modelStatus: typeof verification.model_status === "string" ? verification.model_status : undefined,
                  modelConfidence: typeof verification.model_confidence === "number" ? verification.model_confidence : null,
                  degraded: verification.degraded === true,
                }
              : message.verification,
          };
        }
        if (event.type === "evidence") {
          return {
            ...message,
            citations: event.citations ?? message.citations,
          };
        }
        if (event.type === "partial_result") {
          return {
            ...message,
            partial: true,
            executionStatus: event.status ?? "partial",
            result: event.data ?? message.result,
            content: event.summary ?? "已返回部分结果，仍有步骤未完成。",
          };
        }
        if (event.type === "memory_candidate") {
          const existing = message.memoryCandidates ?? [];
          const nextCandidates = existing.some((item) => item.id === event.candidate.id)
            ? existing
            : [...existing, event.candidate];
          return { ...message, memoryCandidates: nextCandidates };
        }
        if (event.type === "result") {
          return { ...message, content: "数据分析结果已生成", result: event.data, sql: event.sql };
        }
        if (event.type === "answer") {
          return { ...message, content: event.content, citations: event.citations };
        }
        if (event.type === "clarification") {
          return { ...message, content: event.message, executionStatus: "clarified" };
        }
        if (event.type === "done") {
          const isPartial = event.execution_status === "partial" || event.status === "partial";
          const isFailed = event.execution_status === "failed" || event.status === "failed";
          return {
            ...message,
            status: isFailed ? "error" : "done",
            partial: isPartial,
            executionStatus: event.execution_status,
            content: isFailed ? message.content : message.content || (isPartial ? "流程已部分完成" : "流程已完成"),
          };
        }
        if (event.type === "error") {
          return { ...message, status: "error", content: "这次请求没有成功。", error: event.message };
        }
        return message;
      }));
    };

    try {
      await streamConversationQuery(token, activeConversationId, query, {
        signal: controller.signal,
        onEvent,
        mode: queryMode,
      });
      setMessages((current) => current.map((message) => (
        message.id === assistantId && message.status === "streaming"
          ? { ...message, status: "done", content: "流程已结束，后端未返回最终内容。" }
          : message
      )));
      const refreshed = await listConversations(token);
      setConversations(refreshed.items);
      await refreshMemoryCandidates(token);
    } catch (error) {
      const isAbort = error instanceof DOMException && error.name === "AbortError";
      setMessages((current) => current.map((message) => (
        message.id === assistantId
          ? {
              ...message,
              status: isAbort ? "done" : "error",
              content: isAbort ? "已停止本次请求。" : "无法连接工作台接口。",
              error: isAbort ? undefined : error instanceof Error ? error.message : String(error),
            }
          : message
      )));
    } finally {
      setActiveController(null);
    }
  };

  if (authLoading) {
    return <main className="grid min-h-dvh place-items-center bg-parchment text-sm text-ink/55">正在加载工作台...</main>;
  }
  if (!token || !user) return <LoginScreen onLogin={handleLogin} onRegister={handleRegister} />;

  return (
    <div className="flex h-dvh overflow-hidden bg-parchment text-ink">
      <div
        className={cn("fixed inset-0 z-30 bg-ink/45 transition lg:hidden", mobileNavOpen ? "opacity-100" : "pointer-events-none opacity-0")}
        onClick={() => setMobileNavOpen(false)}
        aria-hidden="true"
      />

      <aside className={cn("fixed inset-y-0 left-0 z-40 flex w-[292px] -translate-x-full flex-col bg-ink text-white transition-transform duration-200 lg:static lg:translate-x-0", mobileNavOpen && "translate-x-0")}>
        <div className="flex h-16 shrink-0 items-center justify-between border-b border-white/10 px-5">
          <div className="flex items-center gap-3">
            <div className="grid h-9 w-9 place-items-center bg-moss text-white"><BarChart3 className="h-4 w-4" aria-hidden="true" /></div>
            <div><div className="text-sm font-semibold">企业数据分析与制度知识问答工作台</div><div className="text-[10px] uppercase tracking-[0.18em] text-white/40">Agent Operations</div></div>
          </div>
          <button type="button" onClick={() => setMobileNavOpen(false)} className="grid h-8 w-8 place-items-center text-white/45 transition hover:bg-white/10 hover:text-white lg:hidden" title="关闭导航" aria-label="关闭导航"><X className="h-4 w-4" aria-hidden="true" /></button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-3 py-5">
          <button type="button" onClick={newConversation} disabled={isStreaming} className="flex h-11 w-full items-center justify-center gap-2 bg-moss px-3 text-sm font-semibold text-white transition hover:bg-[#118278] disabled:cursor-not-allowed disabled:opacity-40">
            <MessageSquarePlus className="h-4 w-4" aria-hidden="true" /> 新建会话
          </button>

          <nav className="mt-6 space-y-1" aria-label="工作台导航">
            <button type="button" onClick={() => { setActiveView("chat"); setMobileNavOpen(false); }} className={cn("flex w-full items-center gap-3 px-3 py-2.5 text-left text-sm transition", activeView === "chat" ? "bg-white/10 font-medium text-white" : "text-white/55 hover:bg-white/[0.06] hover:text-white")}><LayoutDashboard className={cn("h-4 w-4", activeView === "chat" && "text-[#a7d8c0]")} aria-hidden="true" />对话工作台</button>
            <button type="button" onClick={() => { setActiveView("documents"); setMobileNavOpen(false); }} className={cn("flex w-full items-center gap-3 px-3 py-2.5 text-left text-sm transition", activeView === "documents" ? "bg-white/10 font-medium text-white" : "text-white/55 hover:bg-white/[0.06] hover:text-white")}><BookOpen className={cn("h-4 w-4", activeView === "documents" && "text-[#a7d8c0]")} aria-hidden="true" />企业知识库<span className="ml-auto text-[10px] text-white/30">{documents.length}</span></button>
            {user.role === "admin" && <button type="button" onClick={openAdmin} className={cn("flex w-full items-center gap-3 px-3 py-2.5 text-left text-sm transition", activeView === "admin" ? "bg-white/10 font-medium text-white" : "text-white/55 hover:bg-white/[0.06] hover:text-white")}><ShieldCheck className={cn("h-4 w-4", activeView === "admin" && "text-[#a7d8c0]")} aria-hidden="true" />管理员控制台</button>}
            {user.role === "admin" && <button type="button" onClick={openQuality} className={cn("flex w-full items-center gap-3 px-3 py-2.5 text-left text-sm transition", activeView === "quality" ? "bg-white/10 font-medium text-white" : "text-white/55 hover:bg-white/[0.06] hover:text-white")}><Gauge className={cn("h-4 w-4", activeView === "quality" && "text-[#a7d8c0]")} aria-hidden="true" />质量运营<span className="ml-auto text-[10px] text-white/30">{qualityOverview?.summary.pending_feedback_count ?? 0}</span></button>}
          </nav>

          <section className="mt-8">
            <div className="mb-3 flex items-center justify-between px-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-white/35"><span className="inline-flex items-center gap-2"><History className="h-3.5 w-3.5" aria-hidden="true" />最近会话</span><span>{conversations.length}</span></div>
            <div className="space-y-1">
              {conversations.length === 0 && <div className="px-2 py-3 text-xs leading-5 text-white/35">还没有会话</div>}
              {conversations.map((conversation) => (
                <button key={conversation.id} type="button" disabled={isStreaming} onClick={() => void selectConversation(conversation.id)} className={cn("group flex w-full items-center gap-2 px-3 py-2.5 text-left text-sm transition disabled:cursor-not-allowed disabled:opacity-40", activeConversationId === conversation.id ? "bg-white/12 text-white" : "text-white/55 hover:bg-white/[0.06] hover:text-white")}>
                  <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", activeConversationId === conversation.id ? "bg-[#a7d8c0]" : "bg-white/20")} />
                  <span className="min-w-0 flex-1 truncate">{conversation.title}</span>
                  <ArrowUpRight className="h-3.5 w-3.5 shrink-0 opacity-0 transition group-hover:opacity-50" aria-hidden="true" />
                </button>
              ))}
            </div>
          </section>

          <section className="mt-8">
            <div className="mb-3 flex items-center gap-2 px-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-white/35"><Clock3 className="h-3.5 w-3.5" aria-hidden="true" />快速入口</div>
            <div className="space-y-1">
              {examples.map((example) => <button key={example} type="button" disabled={isStreaming} onClick={() => void startQuery(example)} className="w-full px-2 py-2 text-left text-xs leading-5 text-white/55 transition hover:bg-white/[0.06] hover:text-white disabled:cursor-not-allowed disabled:opacity-40">{example}</button>)}
            </div>
          </section>
        </div>

        <div className="shrink-0 border-t border-white/10 p-4">
          <div className="mb-3 flex items-center gap-3"><div className="grid h-8 w-8 shrink-0 place-items-center bg-[#a7d8c0] text-ink"><CircleUserRound className="h-4 w-4" aria-hidden="true" /></div><div className="min-w-0"><div className="truncate text-xs font-medium text-white/80">{user.display_name}</div><div className="truncate text-[11px] text-white/35">{user.email}</div></div></div>
          <button type="button" onClick={logout} className="flex h-9 w-full items-center justify-center gap-2 border border-white/15 text-xs text-white/55 transition hover:border-white/35 hover:text-white"><LogOut className="h-3.5 w-3.5" aria-hidden="true" />退出登录</button>
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <header className="flex h-16 shrink-0 items-center justify-between border-b border-ink/10 bg-white px-4 lg:px-7">
          <div className="flex min-w-0 items-center gap-3">
            <button type="button" onClick={() => setMobileNavOpen(true)} className="grid h-9 w-9 shrink-0 place-items-center border border-ink/10 text-ink/60 transition hover:border-moss/40 hover:text-moss lg:hidden" title="打开导航" aria-label="打开导航"><Menu className="h-4 w-4" aria-hidden="true" /></button>
            <div className="min-w-0"><div className="truncate text-sm font-semibold">{activeView === "documents" ? "企业知识库" : activeView === "admin" ? "管理员控制台" : activeView === "quality" ? "Agent 质量运营" : activeConversation?.title || "对话工作台"}</div><div className="mt-0.5 truncate text-xs text-ink/45">{activeView === "documents" ? "文档解析 · 向量索引 · 用户隔离" : activeView === "admin" ? "角色权限 · 用户管理 · 操作审计" : activeView === "quality" ? "运行追踪 · 用户反馈 · 回归评测" : "统一入口 · 数据分析 · 企业文档 · 通用对话"}</div></div>
          </div>
          <div className="flex items-center gap-2">
            <div className="hidden items-center gap-2 border border-moss/20 bg-moss/5 px-2.5 py-1.5 text-xs text-moss sm:flex"><span className="h-1.5 w-1.5 rounded-full bg-moss" />系统在线</div>
            <button type="button" onClick={() => { setActiveView("documents"); documentInputRef.current?.click(); }} disabled={uploading || Boolean(activeDocumentId)} className="grid h-9 w-9 place-items-center border border-ink/10 text-ink/55 transition hover:border-moss/40 hover:text-moss disabled:opacity-35" title="上传文档" aria-label="上传文档"><Upload className="h-4 w-4" aria-hidden="true" /></button>
            {activeView === "chat" && <button type="button" onClick={() => setMessages([])} disabled={messages.length === 0 || isStreaming} className="grid h-9 w-9 place-items-center border border-ink/10 text-ink/55 transition hover:border-moss/40 hover:text-moss disabled:cursor-not-allowed disabled:opacity-35" title="清空当前视图" aria-label="清空当前视图"><Eraser className="h-4 w-4" aria-hidden="true" /></button>}
          </div>
        </header>

        <input ref={documentInputRef} type="file" accept=".pdf,.docx,.md,.txt" className="hidden" onChange={(event) => { const file = event.target.files?.[0]; if (file) void upload(file); event.currentTarget.value = ""; }} />
        {workspaceError && <div className="border-b border-tomato/20 bg-[#fff4f2] px-4 py-2.5 text-sm text-tomato lg:px-7">{workspaceError}</div>}
        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto bg-parchment">
          {activeView === "documents" ? <KnowledgeBase documents={documents} uploading={uploading} activeDocumentId={activeDocumentId} onUpload={() => documentInputRef.current?.click()} onReindex={(document) => void reindex(document)} onDelete={(document) => void remove(document)} /> : activeView === "admin" ? <AdminConsole users={adminUsers} currentUserId={user?.id ?? -1} auditPage={auditPage} mcpAuditPage={mcpAuditPage} loading={adminLoading} creating={adminCreating} deletingUserId={adminDeletingUserId} actionFilter={auditActionFilter} onActionFilterChange={setAuditActionFilter} onApplyFilter={() => void loadAdmin(0)} onLoadPage={(offset) => void loadAdmin(offset)} onRefresh={() => void loadAdmin(auditPage?.offset ?? 0)} onDeleteUser={(target) => void deleteUserFromAdmin(target)} onCreateUser={createUserFromAdmin} /> : activeView === "quality" ? <QualityConsole overview={qualityOverview} feedbackPage={qualityFeedbackPage} regressionCases={regressionCases} loading={qualityLoading} reviewingFeedbackId={reviewingFeedbackId} onRefresh={() => void loadQuality()} onReview={reviewFeedbackFromQuality} /> : messages.length === 0 ? <EmptyState examples={examples} onUseExample={(example) => void startQuery(example)} /> : <div className="mx-auto flex max-w-5xl flex-col gap-5 px-4 py-6 sm:px-6 lg:px-10 lg:py-8">{messages.map((message) => <MessageBubble key={message.id} message={message} onSubmitFeedback={submitFeedbackForMessage} onRetryRun={retryAssistantRun} retryingRunId={retryingRunId} pendingMemoryCandidates={message.id === latestAssistantId ? memoryCandidates : undefined} onConfirmMemory={confirmMemoryCandidate} onDeleteMemory={deleteMemoryCandidate} />)}</div>}
        </div>

        <div className="flex shrink-0 items-center justify-between border-t border-ink/10 bg-white px-4 py-2 text-[11px] text-ink/45 sm:px-7"><span className="inline-flex items-center gap-2"><Activity className="h-3.5 w-3.5 text-moss" aria-hidden="true" />{activeView === "documents" ? `知识库中有 ${documents.length} 份文档` : activeView === "admin" ? "管理员权限已验证" : activeView === "quality" ? `待审核 ${qualityOverview?.summary.pending_feedback_count ?? 0} 条反馈` : isStreaming ? "Agent 正在运行" : `本会话完成 ${completedCount} 次请求`}</span><span className="hidden items-center gap-1.5 sm:inline-flex"><Server className="h-3.5 w-3.5 text-moss" aria-hidden="true" />API 已连接 · {API_BASE_URL}</span><span className="inline-flex items-center gap-1.5 text-moss"><Check className="h-3.5 w-3.5" aria-hidden="true" />已鉴权</span></div>
        {activeView === "chat" && <Composer value={draft} disabled={!canSubmit} isStreaming={isStreaming} onChange={setDraft} onSubmit={() => void startQuery()} onStop={() => activeController?.abort()} onAttach={() => documentInputRef.current?.click()} uploading={uploading} mode={queryMode} onModeChange={setQueryMode} />}
      </main>
    </div>
  );
}
