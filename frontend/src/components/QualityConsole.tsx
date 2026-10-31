import { Activity, CheckCircle2, Clock3, Gauge, RefreshCw, ShieldCheck, ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";
import type { AgentFeedback, AgentFeedbackPage, QualityOverview, RegressionCase } from "../types/agent";

type ReviewPayload = {
  review_status: "accepted" | "rejected";
  reviewer_note?: string;
  expected_route?: "data" | "document" | "general" | "clarify";
  regression_query?: string;
  promote_to_regression: boolean;
};

type QualityConsoleProps = {
  overview: QualityOverview | null;
  feedbackPage: AgentFeedbackPage | null;
  regressionCases: RegressionCase[];
  loading: boolean;
  reviewingFeedbackId: number | null;
  onRefresh: () => void;
  onReview: (feedback: AgentFeedback, payload: ReviewPayload) => Promise<void>;
};

const routeLabels: Record<string, string> = {
  data: "数据分析",
  document: "企业文档",
  general: "通用对话",
  clarify: "待澄清",
  unknown: "未识别",
};

const issueLabels: Record<string, string> = {
  wrong_answer: "回答不正确",
  retrieval_miss: "检索未命中",
  wrong_route: "路由不正确",
  slow_response: "响应过慢",
  unsafe_or_unexpected_sql: "SQL 不符合预期",
  other: "其他",
};

function percent(value: number | null | undefined) {
  return value === null || value === undefined ? "--" : `${Math.round(value * 100)}%`;
}

function milliseconds(value: number | null | undefined) {
  if (value === null || value === undefined) return "--";
  return value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${Math.round(value)}ms`;
}

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "--" : date.toLocaleString("zh-CN", { hour12: false });
}

function Metric({ icon: Icon, label, value, detail }: { icon: typeof Activity; label: string; value: string | number; detail: string }) {
  return (
    <div className="border-r border-ink/10 py-4 last:border-r-0 sm:px-4 sm:first:pl-0">
      <div className="flex items-center gap-2 text-xs text-ink/45"><Icon className="h-3.5 w-3.5 text-moss" aria-hidden="true" />{label}</div>
      <div className="mt-2 text-2xl font-semibold text-ink">{value}</div>
      <div className="mt-1 text-[11px] text-ink/40">{detail}</div>
    </div>
  );
}

function FeedbackReviewRow({ feedback, reviewing, onReview }: { feedback: AgentFeedback; reviewing: boolean; onReview: (payload: ReviewPayload) => Promise<void> }) {
  const [expectedRoute, setExpectedRoute] = useState<"data" | "document" | "general" | "clarify">((feedback.expected_route as "data" | "document" | "general" | "clarify") || "general");
  const [regressionQuery, setRegressionQuery] = useState("");
  const [reviewerNote, setReviewerNote] = useState("");
  const [promote, setPromote] = useState(false);
  const [error, setError] = useState("");

  const submit = async (reviewStatus: "accepted" | "rejected") => {
    setError("");
    try {
      await onReview({
        review_status: reviewStatus,
        reviewer_note: reviewerNote.trim() || undefined,
        expected_route: reviewStatus === "accepted" ? expectedRoute : undefined,
        regression_query: promote ? regressionQuery.trim() : undefined,
        promote_to_regression: reviewStatus === "accepted" && promote,
      });
    } catch (reviewError) {
      setError(reviewError instanceof Error ? reviewError.message : "审核失败");
    }
  };

  const isPending = feedback.review_status === "pending";
  return (
    <article
      data-testid="quality-feedback-row"
      data-feedback-id={feedback.id}
      data-run-id={feedback.run_id}
      className="border-b border-ink/10 py-5 last:border-b-0"
    >
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2"><span className={feedback.rating === 1 ? "inline-flex items-center gap-1 text-xs font-medium text-moss" : "inline-flex items-center gap-1 text-xs font-medium text-tomato"}>{feedback.rating === 1 ? <ThumbsUp className="h-3.5 w-3.5" aria-hidden="true" /> : <ThumbsDown className="h-3.5 w-3.5" aria-hidden="true" />}{feedback.rating === 1 ? "有帮助" : "需改进"}</span><span className="border border-ink/10 bg-[#f7faf9] px-2 py-0.5 text-[10px] text-ink/55">{issueLabels[feedback.issue_type || ""] || "未分类"}</span><span className="text-[11px] text-ink/40">{formatTime(feedback.created_at)}</span></div>
          <p className="mt-2 max-w-3xl whitespace-pre-wrap text-sm leading-6 text-ink">{feedback.query_preview || "该记录未保存问题摘要"}</p>
          {feedback.comment && <p className="mt-2 border-l-2 border-moss/35 pl-3 text-xs leading-5 text-ink/60">{feedback.comment}</p>}
          <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink/45"><span>实际路由：{routeLabels[feedback.route || "unknown"] || feedback.route}</span><span>策略：{feedback.route_strategy || "--"}</span><span>总耗时：{milliseconds(feedback.total_latency_ms)}</span><span>检索：{milliseconds(feedback.retrieval_latency_ms)}</span><span>模型：{milliseconds(feedback.model_latency_ms)}</span></div>
        </div>
        <div className={isPending ? "border border-brass/25 bg-brass/5 px-2 py-1 text-xs text-brass" : "border border-moss/20 bg-moss/5 px-2 py-1 text-xs text-moss"}>{isPending ? "待审核" : feedback.review_status === "accepted" ? "已接受" : "已拒绝"}</div>
      </div>

      {isPending && (
        <div className="mt-4 grid gap-2 border-t border-ink/10 pt-4 lg:grid-cols-[150px_minmax(0,1fr)_auto]">
          <label className="sr-only" htmlFor={`route-${feedback.id}`}>期望路由</label>
          <select id={`route-${feedback.id}`} value={expectedRoute} onChange={(event) => setExpectedRoute(event.target.value as typeof expectedRoute)} className="h-9 border border-ink/15 bg-white px-2 text-xs outline-none focus:border-moss"><option value="data">期望：数据分析</option><option value="document">期望：企业文档</option><option value="general">期望：通用对话</option><option value="clarify">期望：待澄清</option></select>
          <label className="sr-only" htmlFor={`review-note-${feedback.id}`}>审核说明</label>
          <input id={`review-note-${feedback.id}`} value={reviewerNote} onChange={(event) => setReviewerNote(event.target.value)} maxLength={2000} placeholder="审核结论或下一步优化方向" className="h-9 min-w-0 border border-ink/15 bg-white px-2 text-xs outline-none focus:border-moss" />
          <div className="flex gap-2"><button type="button" onClick={() => void submit("accepted")} disabled={reviewing} className="h-9 border border-moss/30 bg-moss px-3 text-xs font-medium text-white transition hover:bg-soot disabled:opacity-45">接受</button><button type="button" onClick={() => void submit("rejected")} disabled={reviewing} className="h-9 border border-ink/15 bg-white px-3 text-xs text-ink/60 transition hover:border-ink/35 disabled:opacity-45">拒绝</button></div>
          <label className="col-span-full flex items-center gap-2 text-xs text-ink/60"><input type="checkbox" checked={promote} onChange={(event) => setPromote(event.target.checked)} className="h-3.5 w-3.5 accent-moss" />纳入回归集（需要填写脱敏后的问题）</label>
          {promote && <input value={regressionQuery} onChange={(event) => setRegressionQuery(event.target.value)} maxLength={4000} placeholder="脱敏后的回归问题，例如：统计本季度各区域销售额" className="col-span-full h-9 min-w-0 border border-ink/15 bg-white px-2 text-xs outline-none focus:border-moss" />}
          {error && <p className="col-span-full text-xs text-tomato">{error}</p>}
        </div>
      )}
    </article>
  );
}

export function QualityConsole({ overview, feedbackPage, regressionCases, loading, reviewingFeedbackId, onRefresh, onReview }: QualityConsoleProps) {
  const summary = overview?.summary;
  return (
    <section className="mx-auto w-full max-w-7xl px-4 py-7 sm:px-6 lg:px-10 lg:py-9">
      <div className="flex flex-col gap-5 border-b border-ink/10 pb-7 sm:flex-row sm:items-end sm:justify-between">
        <div><p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-moss">Quality Operations</p><h1 className="text-2xl font-semibold">Agent 质量运营</h1><p className="mt-2 max-w-3xl text-sm leading-6 text-ink/55">查看近 {overview?.window_days || 14} 天的执行质量，将审核后的失败样本沉淀为下一次发布前的回归测试。</p></div>
        <button type="button" onClick={onRefresh} disabled={loading} className="flex h-10 shrink-0 items-center justify-center gap-2 border border-ink/15 bg-white px-3 text-sm font-medium text-ink/70 transition hover:border-moss/40 hover:text-moss disabled:cursor-not-allowed disabled:opacity-45"><RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} aria-hidden="true" />刷新指标</button>
      </div>

      <div className="grid border-b border-ink/10 sm:grid-cols-2 lg:grid-cols-4">
        <Metric icon={Activity} label="有效执行" value={summary?.run_count ?? 0} detail={`成功率 ${percent(summary?.success_rate)}`} />
        <Metric icon={Clock3} label="P95 响应" value={milliseconds(summary?.latency.p95_ms)} detail={`首事件 P95 ${milliseconds(summary?.time_to_first_event.p95_ms)}`} />
        <Metric icon={ThumbsUp} label="用户反馈" value={summary?.feedback_count ?? 0} detail={`有帮助率 ${percent(summary?.helpful_rate)}`} />
        <Metric icon={ShieldCheck} label="待审核样本" value={summary?.pending_feedback_count ?? 0} detail={`负反馈 ${summary?.negative_feedback_count ?? 0} 条`} />
      </div>

      <div className="grid gap-8 py-8 xl:grid-cols-[minmax(0,1.25fr)_minmax(320px,0.75fr)]">
        <section>
          <div className="mb-3 flex items-center gap-2"><Gauge className="h-4 w-4 text-moss" aria-hidden="true" /><h2 className="text-sm font-semibold">按路由观察</h2></div>
          <div className="overflow-x-auto border border-ink/10 bg-white"><table className="min-w-full text-left text-sm"><thead className="border-b border-ink/10 bg-[#f7faf9] text-xs text-ink/45"><tr><th className="px-4 py-3 font-medium">路由</th><th className="px-4 py-3 font-medium">请求</th><th className="px-4 py-3 font-medium">成功率</th><th className="px-4 py-3 font-medium">P50</th><th className="px-4 py-3 font-medium">P95</th><th className="px-4 py-3 font-medium">反馈</th></tr></thead><tbody>{overview?.by_route.map((item) => <tr key={item.route} className="border-b border-ink/10 last:border-b-0"><td className="px-4 py-3 font-medium">{routeLabels[item.route] || item.route}</td><td className="px-4 py-3 text-ink/60">{item.run_count}</td><td className="px-4 py-3 text-ink/60">{percent(item.success_rate)}</td><td className="px-4 py-3 text-ink/60">{milliseconds(item.p50_ms)}</td><td className="px-4 py-3 text-ink/60">{milliseconds(item.p95_ms)}</td><td className="px-4 py-3 text-ink/60">{item.feedback_count}</td></tr>)}{!overview?.by_route.length && <tr><td colSpan={6} className="px-4 py-10 text-center text-sm text-ink/45">尚无运行数据，发起一次对话后会显示指标。</td></tr>}</tbody></table></div>
        </section>
        <section>
          <div className="mb-3 flex items-center gap-2"><Clock3 className="h-4 w-4 text-moss" aria-hidden="true" /><h2 className="text-sm font-semibold">慢节点</h2></div>
          <div className="border border-ink/10 bg-white">{overview?.slow_steps.map((step) => <div key={`${step.category}-${step.step_name}`} className="flex items-center justify-between gap-3 border-b border-ink/10 px-4 py-3 last:border-b-0"><div className="min-w-0"><div className="truncate text-sm text-ink">{step.step_name}</div><div className="mt-1 text-[11px] text-ink/40">{step.category} · {step.count} 次 · 错误 {step.error_count}</div></div><div className="shrink-0 text-right"><div className="text-sm font-medium">{milliseconds(step.p95_ms)}</div><div className="mt-1 text-[10px] text-ink/40">P95</div></div></div>)}{!overview?.slow_steps.length && <div className="px-4 py-10 text-center text-sm text-ink/45">暂无节点耗时数据</div>}</div>
        </section>
      </div>

      <section className="border-t border-ink/10 pt-7">
        <div className="mb-4 flex flex-wrap items-end justify-between gap-3"><div><h2 className="text-sm font-semibold">反馈审核队列</h2><p className="mt-1 text-xs text-ink/45">审核通过并脱敏后，才能进入回归集；系统不会在线自动改写 Prompt 或路由规则。</p></div><span className="text-xs text-ink/45">共 {feedbackPage?.total ?? 0} 条</span></div>
        <div className="border border-ink/10 bg-white px-4 sm:px-5">{feedbackPage?.items.map((feedback) => <FeedbackReviewRow key={feedback.id} feedback={feedback} reviewing={reviewingFeedbackId === feedback.id} onReview={(payload) => onReview(feedback, payload)} />)}{!feedbackPage?.items.length && <div className="py-12 text-center text-sm text-ink/45">暂无用户反馈。完成一次 Agent 回答后，可在消息下方提交评价。</div>}</div>
      </section>

      <section className="border-t border-ink/10 pt-7">
        <div className="mb-4 flex items-center justify-between"><div><h2 className="text-sm font-semibold">已沉淀回归样本</h2><p className="mt-1 text-xs text-ink/45">这些样本可导出到离线评测，在修改路由、Prompt 或检索策略后执行回归。</p></div><span className="text-xs text-ink/45">{regressionCases.length} 条</span></div>
        <div className="overflow-x-auto border border-ink/10 bg-white"><table className="min-w-full text-left text-sm"><thead className="border-b border-ink/10 bg-[#f7faf9] text-xs text-ink/45"><tr><th className="px-4 py-3 font-medium">脱敏问题</th><th className="px-4 py-3 font-medium">期望路由</th><th className="px-4 py-3 font-medium">问题类型</th><th className="px-4 py-3 font-medium">创建时间</th></tr></thead><tbody>{regressionCases.map((item) => <tr key={item.id} className="border-b border-ink/10 last:border-b-0"><td className="max-w-xl px-4 py-3 text-ink">{item.query}</td><td className="px-4 py-3 text-ink/60">{routeLabels[item.expected_route] || item.expected_route}</td><td className="px-4 py-3 text-ink/60">{issueLabels[item.source_issue_type || ""] || "--"}</td><td className="whitespace-nowrap px-4 py-3 text-xs text-ink/45">{formatTime(item.created_at)}</td></tr>)}{regressionCases.length === 0 && <tr><td colSpan={4} className="px-4 py-10 text-center text-sm text-ink/45">尚未有审核通过的回归样本</td></tr>}</tbody></table></div>
      </section>
    </section>
  );
}
