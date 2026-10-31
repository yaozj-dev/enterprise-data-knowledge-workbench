/**
 * Agent 执行轨迹
 * 用紧凑时间线展示 LangGraph 节点状态，避免在聊天消息中占用整块画布。
 */
import { Bot, Check, Circle, GitBranch, LoaderCircle, Wrench, X } from "lucide-react";
import { cn } from "../lib/format";
import type { AgentTimelineItem, ChatMessage, StepState } from "../types/agent";

type FlowStatus = StepState["status"] | "pending";

function NodeIcon({ status }: { status: FlowStatus }) {
  if (status === "running") return <LoaderCircle className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />;
  if (status === "success") return <Check className="h-3.5 w-3.5" aria-hidden="true" />;
  if (status === "error") return <X className="h-3.5 w-3.5" aria-hidden="true" />;
  return <Circle className="h-3.5 w-3.5" aria-hidden="true" />;
}

function statusLabel(status: FlowStatus | string) {
  if (status === "running") return "进行中";
  if (status === "success") return "完成";
  if (status === "succeeded") return "完成";
  if (status === "timed_out") return "超时";
  if (status === "partial") return "部分完成";
  if (status === "failed" || status === "error") return "异常";
  if (status === "skipped") return "跳过";
  if (status === "error") return "异常";
  return "等待";
}

function timelineLabel(item: AgentTimelineItem) {
  const labels: Record<string, string> = {
    data: "数据 Agent",
    knowledge: "知识 Agent",
    general: "通用 Agent",
    report: "报告校验 Agent",
    clarification: "澄清 Agent",
    supervisor: "Supervisor",
    hybrid_document_search: "混合文档检索",
    data_catalog_lookup: "数据目录检索",
    safe_readonly_query: "只读 SQL 查询",
    verify_evidence: "证据校验",
  };
  return labels[item.name] ?? item.name;
}

function TimelineItem({ item }: { item: AgentTimelineItem }) {
  const isAgent = item.kind === "agent";
  const icon = item.status === "running"
    ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
    : isAgent
      ? <Bot className="h-3.5 w-3.5" aria-hidden="true" />
      : <Wrench className="h-3.5 w-3.5" aria-hidden="true" />;
  return (
    <div className="flex min-w-0 items-center gap-2.5 border border-ink/8 bg-white px-3 py-2">
      <span className={cn(
        "grid h-6 w-6 shrink-0 place-items-center",
        item.status === "running" && "bg-brass/15 text-brass",
        (item.status === "succeeded" || item.status === "success") && "bg-moss/10 text-moss",
        (item.status === "failed" || item.status === "error" || item.status === "timed_out") && "bg-tomato/10 text-tomato",
        item.status === "partial" && "bg-brass/15 text-brass",
      )}>{icon}</span>
      <span className="min-w-0 flex-1 truncate text-xs text-ink/75">{timelineLabel(item)}</span>
      {item.latencyMs !== undefined && <span className="shrink-0 text-[10px] text-ink/35">{item.latencyMs}ms</span>}
      <span className={cn(
        "shrink-0 text-[10px]",
        item.status === "running" && "text-brass",
        (item.status === "succeeded" || item.status === "success") && "text-moss",
        (item.status === "failed" || item.status === "error" || item.status === "timed_out") && "text-tomato",
        item.status === "partial" && "text-brass",
      )}>{statusLabel(item.status)}</span>
    </div>
  );
}

export function StepRail({
  steps = [],
  plan,
  timeline = [],
  partial = false,
  executionStatus,
}: {
  steps?: StepState[];
  plan?: ChatMessage["plan"];
  timeline?: AgentTimelineItem[];
  partial?: boolean;
  executionStatus?: string;
}) {
  if (steps.length === 0 && !plan && timeline.length === 0 && !partial) return null;

  return (
    <section className="mt-5 border-t border-ink/10 pt-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-xs font-semibold text-ink/65"><GitBranch className="h-3.5 w-3.5 text-moss" aria-hidden="true" />执行轨迹</div>
        <span className="text-[10px] uppercase tracking-[0.14em] text-ink/35">Supervisor · LangGraph</span>
      </div>
      {plan && (
        <div className="mb-3 border border-moss/15 bg-moss/5 px-3 py-2.5">
          <div className="flex flex-wrap items-center gap-2 text-xs text-ink/65">
            <span className="font-semibold text-moss">执行计划</span>
            <span>{plan.tasks.length} 个任务</span>
            <span>·</span>
            <span>置信度 {Math.round(plan.confidence * 100)}%</span>
            <span>·</span>
            <span>最多 {plan.max_iterations} 轮</span>
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {plan.tasks.map((task) => <span key={task.task_id} className="border border-ink/10 bg-white px-2 py-1 text-[10px] text-ink/60">{task.agent_name} · {task.required ? "必需" : "可选"}</span>)}
          </div>
        </div>
      )}
      {timeline.length > 0 && (
        <div className="mb-3 grid gap-2 sm:grid-cols-2">
          {timeline.map((item) => <TimelineItem key={item.id} item={item} />)}
        </div>
      )}
      <div className="grid gap-2 sm:grid-cols-2">
        {steps.map((step) => (
          <div key={step.step} className="flex min-w-0 items-center gap-2.5 border border-ink/8 bg-parchment px-3 py-2">
            <span className={cn("grid h-6 w-6 shrink-0 place-items-center", step.status === "running" && "bg-brass/15 text-brass", step.status === "success" && "bg-moss/10 text-moss", step.status === "error" && "bg-tomato/10 text-tomato")}><NodeIcon status={step.status} /></span>
            <span className="min-w-0 flex-1 truncate text-xs text-ink/70">{step.step}</span>
            <span className={cn("shrink-0 text-[10px]", step.status === "running" && "text-brass", step.status === "success" && "text-moss", step.status === "error" && "text-tomato")}>{statusLabel(step.status)}</span>
          </div>
        ))}
      </div>
      {partial && <div className="mt-3 border border-brass/25 bg-brass/10 px-3 py-2 text-xs text-ink/65">本次流程{executionStatus === "clarified" ? "需要补充信息" : "已部分完成"}，页面只展示已通过校验的结果。</div>}
    </section>
  );
}
