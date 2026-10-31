/**
 * 首页空状态组件
 * 展示产品入口信息和可点击的示例问数问题
 */
import { ArrowUpRight, BookOpen, Database, FileText, Sparkles } from "lucide-react";

type EmptyStateProps = {
  examples: string[];
  onUseExample: (example: string) => void;
};

const highlights = [
  { label: "数据分析", detail: "自然语言生成 SQL 并返回可读结果", icon: Database },
  { label: "企业知识库", detail: "检索文档内容并展示引用来源", icon: BookOpen },
  { label: "Agent 流程", detail: "实时查看路由、检索和校验状态", icon: FileText },
];

export function EmptyState({ examples, onUseExample }: EmptyStateProps) {
  return (
    <div className="mx-auto flex min-h-full w-full max-w-5xl flex-col justify-center px-4 py-10 sm:px-6 lg:px-10">
      <div className="mb-9 max-w-2xl">
        <div className="mb-5 inline-flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-moss"><Sparkles className="h-4 w-4" aria-hidden="true" />企业数据分析与制度知识问答工作台</div>
        <h1 className="text-3xl font-semibold leading-tight tracking-tight text-ink sm:text-5xl">今天要解决什么问题？</h1>
        <p className="mt-4 max-w-xl text-sm leading-7 text-ink/55">从业务数据、制度文档到开放式问题，选择一个入口开始，让 Agent 给出可追踪的处理结果。</p>
      </div>

      <div className="grid gap-3 md:grid-cols-3">
        {highlights.map((item) => {
          const Icon = item.icon;
          return (
            <div key={item.label} className="border border-ink/10 bg-white px-4 py-4 shadow-line">
              <div className="mb-7 flex h-9 w-9 items-center justify-center bg-moss/10 text-moss"><Icon className="h-4 w-4" aria-hidden="true" /></div>
              <div className="text-sm font-semibold text-ink">{item.label}</div>
              <div className="mt-1.5 text-xs leading-5 text-ink/50">{item.detail}</div>
            </div>
          );
        })}
      </div>

      <div className="mt-8">
        <div className="mb-3 flex items-center justify-between"><div className="text-xs font-semibold uppercase tracking-[0.16em] text-ink/45">快速开始</div><div className="text-xs text-ink/35">选择一个问题直接运行</div></div>
        <div className="grid gap-2 md:grid-cols-2">
          {examples.map((example, index) => (
          <button
            key={example}
            type="button"
            onClick={() => onUseExample(example)}
            className="group flex min-h-16 items-center justify-between gap-4 border border-ink/10 bg-white px-4 py-3.5 text-left text-sm leading-6 text-ink transition hover:border-moss/45 hover:bg-moss/[0.03] focus:outline-none focus:ring-2 focus:ring-moss/35"
          >
            <span className="min-w-0">{example}</span><ArrowUpRight className="h-4 w-4 shrink-0 text-ink/25 transition group-hover:text-moss" aria-hidden="true" />
          </button>
          ))}
        </div>
      </div>
    </div>
  );
}
