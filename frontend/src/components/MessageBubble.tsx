/**
 * 聊天消息气泡组件
 * 组合展示用户问题、智能体回复、执行流程和结果表格
 */
import { Activity, AlertTriangle, Bot, Check, CheckCircle2, Copy, FileText, RotateCcw, Send, ShieldCheck, Sparkles, ThumbsDown, ThumbsUp, Trash2, UserRound } from "lucide-react";
import { useState } from "react";
import { ResultTable } from "./ResultTable";
import { StepRail } from "./StepRail";
import { getCurrentAgentRun } from "../lib/agentApi";
import { cn, formatTime, toClipboardText } from "../lib/format";
import type { ChatMessage, MemoryCandidate } from "../types/agent";

type FeedbackPayload = {
  rating: 1 | -1;
  issue_type?: "wrong_answer" | "retrieval_miss" | "wrong_route" | "slow_response" | "unsafe_or_unexpected_sql" | "other";
  comment?: string;
};

export function MessageBubble({
  message,
  onSubmitFeedback,
  onRetryRun,
  retryingRunId,
  pendingMemoryCandidates,
  onConfirmMemory,
  onDeleteMemory,
}: {
  message: ChatMessage;
  onSubmitFeedback?: (runId: string, payload: FeedbackPayload) => Promise<void>;
  onRetryRun?: (runId: string) => Promise<void>;
  retryingRunId?: string | null;
  pendingMemoryCandidates?: MemoryCandidate[];
  onConfirmMemory?: (candidate: MemoryCandidate) => Promise<void>;
  onDeleteMemory?: (candidate: MemoryCandidate) => Promise<void>;
}) {
  const isUser = message.role === "user";
  const [negativeOpen, setNegativeOpen] = useState(false);
  const [issueType, setIssueType] = useState<NonNullable<FeedbackPayload["issue_type"]>>("wrong_answer");
  const [comment, setComment] = useState("");
  const [feedbackError, setFeedbackError] = useState("");
  const [submittingFeedback, setSubmittingFeedback] = useState(false);
  const [runDetail, setRunDetail] = useState<string | null>(null);
  const [runDetailLoading, setRunDetailLoading] = useState(false);
  const [memoryError, setMemoryError] = useState("");
  const [memoryActionId, setMemoryActionId] = useState<string | null>(null);

  const copy = async () => {
    const text = message.result ? toClipboardText(message.result) : message.content;
    await navigator.clipboard.writeText(text);
  };

  const visibleCandidates = message.memoryCandidates?.length
    ? message.memoryCandidates
    : pendingMemoryCandidates ?? [];

  const actOnMemory = async (candidate: MemoryCandidate, action: "confirm" | "delete") => {
    const handler = action === "confirm" ? onConfirmMemory : onDeleteMemory;
    if (!handler || memoryActionId) return;
    setMemoryActionId(candidate.id);
    setMemoryError("");
    try {
      await handler(candidate);
    } catch (error) {
      setMemoryError(error instanceof Error ? error.message : "记忆操作失败");
    } finally {
      setMemoryActionId(null);
    }
  };

  const submitFeedback = async (payload: FeedbackPayload) => {
    if (!message.traceId || !onSubmitFeedback || submittingFeedback) return;
    setSubmittingFeedback(true);
    setFeedbackError("");
    try {
      await onSubmitFeedback(message.traceId, payload);
      if (payload.rating === -1) setNegativeOpen(false);
    } catch (error) {
      setFeedbackError(error instanceof Error ? error.message : "反馈提交失败");
    } finally {
      setSubmittingFeedback(false);
    }
  };

  const loadRunDetail = async () => {
    if (!message.traceId || runDetailLoading) return;
    setRunDetailLoading(true);
    try {
      const snapshot = await getCurrentAgentRun(message.traceId);
      setRunDetail(JSON.stringify(snapshot, null, 2));
    } catch (error) {
      setRunDetail(error instanceof Error ? error.message : "执行详情加载失败");
    } finally {
      setRunDetailLoading(false);
    }
  };

  if (isUser) {
    return (
      <article data-testid="user-message" className="flex justify-end gap-3">
        <div className="max-w-[760px]">
          <div className="border border-ink bg-ink px-4 py-3 text-white shadow-line">
            <p className="whitespace-pre-wrap text-sm leading-6">{message.content}</p>
            <div className="mt-2 text-right text-[11px] text-white/45">{formatTime(message.createdAt)}</div>
          </div>
        </div>
        <div className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center bg-moss text-white"><UserRound className="h-4 w-4" aria-hidden="true" /></div>
      </article>
    );
  }

  return (
    <article
      data-testid="assistant-message"
      data-trace-id={message.traceId}
      className="flex gap-3"
    >
      <div className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center bg-moss/10 text-moss"><Bot className="h-4 w-4" aria-hidden="true" /></div>
      <div className="min-w-0 flex-1">
        <div className="border border-ink/10 bg-white shadow-line">
          <div className="flex items-center justify-between gap-3 border-b border-ink/10 px-4 py-3">
            <div className="flex min-w-0 items-center gap-2">
              <span className="text-xs font-semibold text-ink">工作台 Agent</span>
              {message.route && <span className="truncate border border-moss/20 bg-moss/5 px-2 py-1 text-[10px] font-semibold text-moss">{message.route === "data" ? "数据分析" : message.route === "document" ? "企业文档" : message.route === "general" ? "通用对话" : "待澄清"}</span>}
              {message.status === "streaming" && <span className="text-[10px] text-brass">处理中</span>}
            </div>
            {message.status !== "streaming" && <button type="button" onClick={copy} className="grid h-7 w-7 shrink-0 place-items-center text-ink/35 transition hover:bg-moss/10 hover:text-moss" title="复制回答" aria-label="复制回答"><Copy className="h-3.5 w-3.5" aria-hidden="true" /></button>}
          </div>

          <div className="px-4 py-4 sm:px-5">
            <p className="whitespace-pre-wrap text-sm leading-7 text-ink">{message.content}</p>
            {message.error && <div className="mt-4 border border-tomato/25 bg-[#fff4f2] px-3 py-2.5 text-sm text-tomato">{message.error}</div>}
            {message.degradations && message.degradations.length > 0 && (
              <div className="mt-4 border border-brass/25 bg-brass/10 px-3 py-2.5 text-xs text-ink/65">
                <div className="flex items-center gap-2 font-semibold text-brass"><AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />本次请求启用了降级路径</div>
                <div className="mt-1.5 grid gap-1 text-ink/55">
                  {message.degradations.map((item) => <span key={`${item.service}-${item.reason}`}>{item.service}：{item.fallback}（{item.reason}）</span>)}
                </div>
              </div>
            )}
            {message.verification && (
              <div className={cn("mt-4 flex flex-wrap items-center gap-2 border px-3 py-2.5 text-xs", message.verification.degraded ? "border-brass/25 bg-brass/10 text-ink/65" : "border-moss/20 bg-moss/5 text-ink/65")}>
                <ShieldCheck className={cn("h-3.5 w-3.5", message.verification.degraded ? "text-brass" : "text-moss")} aria-hidden="true" />
                <span>证据校验：{message.verification.decision === "pass" && !message.verification.degraded ? "通过" : "部分通过"}</span>
                {message.verification.modelStatus && <span className="text-ink/45">模型校验 {message.verification.modelStatus}</span>}
                {message.verification.modelConfidence !== null && message.verification.modelConfidence !== undefined && <span className="text-ink/45">置信度 {Math.round(message.verification.modelConfidence * 100)}%</span>}
              </div>
            )}
            {message.status === "done" && !message.error && <div className="mt-3 flex items-center gap-1.5 text-[11px] text-moss"><CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />已完成 · {formatTime(message.createdAt)}</div>}
            {message.traceId && (
              <details className="mt-3 border border-ink/10 bg-[#f7faf9] px-3 py-2.5">
                <summary className="cursor-pointer text-xs font-semibold text-ink/65">查看执行详情</summary>
                <div className="mt-3 border-t border-ink/10 pt-3">
                  <button type="button" onClick={() => void loadRunDetail()} disabled={runDetailLoading} className="flex h-8 items-center gap-2 border border-ink/15 bg-white px-3 text-xs font-medium text-ink transition hover:border-moss/45 hover:bg-moss/5 hover:text-moss disabled:opacity-45">
                    <Activity className="h-3.5 w-3.5" aria-hidden="true" />
                    {runDetailLoading ? "正在加载快照..." : "加载运行快照"}
                  </button>
                  {runDetail && <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap break-words border-t border-ink/10 pt-3 font-mono text-[11px] leading-5 text-ink/70">{runDetail}</pre>}
                </div>
              </details>
            )}
            <StepRail steps={message.steps} plan={message.plan} timeline={message.timeline} partial={message.partial} executionStatus={message.executionStatus} />
            {message.result !== undefined && <ResultTable data={message.result} />}
            {message.sql && <details className="mt-4 border border-ink/10 bg-[#f7faf9] px-3 py-2.5"><summary className="cursor-pointer text-xs font-semibold text-ink/65">查看已执行 SQL</summary><pre className="mt-3 max-h-56 overflow-auto whitespace-pre-wrap break-words border-t border-ink/10 pt-3 font-mono text-xs leading-5 text-ink/70">{message.sql}</pre></details>}
            {message.executionStatus === "failed" && message.traceId && onRetryRun && (
              <div className="mt-4 border-t border-ink/10 pt-4">
                <button type="button" onClick={() => void onRetryRun(message.traceId!)} disabled={retryingRunId !== null && retryingRunId !== undefined} className="flex h-9 items-center gap-2 border border-ink/15 bg-white px-3 text-xs font-medium text-ink transition hover:border-moss/45 hover:bg-moss/5 hover:text-moss disabled:cursor-not-allowed disabled:opacity-45">
                  <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
                  {retryingRunId === message.traceId ? "正在创建重试..." : "重新运行"}
                </button>
              </div>
            )}

            {message.citations && message.citations.length > 0 && (
              <div className="mt-5 border-t border-ink/10 pt-4">
                <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-ink/60"><FileText className="h-3.5 w-3.5 text-moss" aria-hidden="true" />引用来源</div>
                <div className="flex flex-wrap gap-2">{message.citations.map((citation) => <span key={`${citation.index}-${citation.filename}`} className="border border-ink/10 bg-parchment px-2.5 py-1.5 text-xs text-ink/60">[{citation.index}] {citation.filename}{citation.page_number ? ` · 第${citation.page_number}页` : ""}</span>)}</div>
              </div>
            )}

            {visibleCandidates.length > 0 && onConfirmMemory && onDeleteMemory && (
              <section className="mt-5 border-t border-ink/10 pt-4">
                <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-ink/60"><Sparkles className="h-3.5 w-3.5 text-moss" aria-hidden="true" />建议保存的记忆</div>
                <p className="mb-2 text-xs leading-5 text-ink/45">只有你确认后，偏好或事实才会写入长期记忆。</p>
                <div className="grid gap-2">
                  {visibleCandidates.map((candidate) => (
                    <div key={candidate.id} className="flex items-start gap-3 border border-ink/10 bg-[#f7faf9] px-3 py-2.5">
                      <p className="min-w-0 flex-1 text-xs leading-5 text-ink/75">{candidate.content}</p>
                      <div className="flex shrink-0 gap-1">
                        <button type="button" onClick={() => void actOnMemory(candidate, "confirm")} disabled={memoryActionId !== null} className="grid h-7 w-7 place-items-center border border-moss/25 text-moss transition hover:bg-moss/10 disabled:opacity-40" title="确认保存" aria-label="确认保存"><Check className="h-3.5 w-3.5" aria-hidden="true" /></button>
                        <button type="button" onClick={() => void actOnMemory(candidate, "delete")} disabled={memoryActionId !== null} className="grid h-7 w-7 place-items-center border border-ink/15 text-ink/45 transition hover:border-tomato/30 hover:bg-tomato/5 hover:text-tomato disabled:opacity-40" title="删除候选" aria-label="删除候选"><Trash2 className="h-3.5 w-3.5" aria-hidden="true" /></button>
                      </div>
                    </div>
                  ))}
                </div>
                {memoryError && <p className="mt-2 text-xs text-tomato">{memoryError}</p>}
              </section>
            )}

            {message.status === "done" && !message.error && message.traceId && onSubmitFeedback && (
              <section className="mt-5 flex flex-wrap items-center gap-2 border-t border-ink/10 pt-4" aria-label="回答反馈">
                <span className="mr-1 text-xs text-ink/45">这次结果有帮助吗？</span>
                <button type="button" onClick={() => void submitFeedback({ rating: 1 })} disabled={submittingFeedback || message.feedbackRating === 1} className="grid h-8 w-8 place-items-center border border-ink/10 text-ink/45 transition hover:border-moss/45 hover:bg-moss/5 hover:text-moss disabled:cursor-not-allowed disabled:opacity-45" title="有帮助" aria-label="有帮助"><ThumbsUp className="h-3.5 w-3.5" aria-hidden="true" /></button>
                <button type="button" onClick={() => { setFeedbackError(""); setNegativeOpen((value) => !value); }} disabled={submittingFeedback || message.feedbackRating === -1} className="grid h-8 w-8 place-items-center border border-ink/10 text-ink/45 transition hover:border-tomato/45 hover:bg-tomato/5 hover:text-tomato disabled:cursor-not-allowed disabled:opacity-45" title="需要改进" aria-label="需要改进"><ThumbsDown className="h-3.5 w-3.5" aria-hidden="true" /></button>
                {message.feedbackRating === 1 && <span className="text-xs text-moss">已记录为有帮助</span>}
                {message.feedbackRating === -1 && <span className="text-xs text-ink/55">已进入质量审核队列</span>}
                {negativeOpen && message.feedbackRating !== -1 && (
                  <div className="mt-2 grid w-full gap-2 border border-ink/10 bg-[#f7faf9] p-3 sm:grid-cols-[160px_minmax(0,1fr)_auto]">
                    <label className="sr-only" htmlFor={`feedback-type-${message.id}`}>问题类型</label>
                    <select id={`feedback-type-${message.id}`} value={issueType} onChange={(event) => setIssueType(event.target.value as NonNullable<FeedbackPayload["issue_type"]>)} className="h-9 border border-ink/15 bg-white px-2 text-xs text-ink outline-none focus:border-moss">
                      <option value="wrong_answer">回答不正确</option>
                      <option value="retrieval_miss">没有检索到资料</option>
                      <option value="wrong_route">路由不正确</option>
                      <option value="slow_response">响应太慢</option>
                      <option value="unsafe_or_unexpected_sql">SQL 不符合预期</option>
                      <option value="other">其他</option>
                    </select>
                    <label className="sr-only" htmlFor={`feedback-comment-${message.id}`}>补充说明</label>
                    <input id={`feedback-comment-${message.id}`} value={comment} onChange={(event) => setComment(event.target.value)} maxLength={2000} placeholder="补充期望结果，帮助后续回归测试" className="h-9 min-w-0 border border-ink/15 bg-white px-2 text-xs outline-none focus:border-moss" />
                    <button type="button" onClick={() => void submitFeedback({ rating: -1, issue_type: issueType, comment: comment.trim() || undefined })} disabled={submittingFeedback} className="flex h-9 items-center justify-center gap-1.5 bg-ink px-3 text-xs font-medium text-white transition hover:bg-soot disabled:opacity-45"><Send className="h-3.5 w-3.5" aria-hidden="true" />提交</button>
                  </div>
                )}
                {feedbackError && <p className="w-full text-xs text-tomato">{feedbackError}</p>}
              </section>
            )}
          </div>
        </div>
      </div>
    </article>
  );
}
