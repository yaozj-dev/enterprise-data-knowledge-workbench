import {
  AlertTriangle,
  Database,
  FileText,
  RefreshCw,
  Trash2,
  Upload,
} from "lucide-react";
import type { DocumentView } from "../types/agent";

type KnowledgeBaseProps = {
  documents: DocumentView[];
  uploading: boolean;
  activeDocumentId: string | null;
  onUpload: () => void;
  onReindex: (document: DocumentView) => void;
  onDelete: (document: DocumentView) => void;
};

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "--" : date.toLocaleString("zh-CN", { hour12: false });
}

function statusStyle(status: DocumentView["status"]) {
  if (status === "ready") return "border-moss/20 bg-moss/10 text-moss";
  if (status === "failed") return "border-tomato/25 bg-tomato/10 text-tomato";
  return "border-amber-500/25 bg-amber-50 text-amber-700";
}

function statusLabel(status: DocumentView["status"]) {
  if (status === "ready") return "可检索";
  if (status === "failed") return "处理失败";
  return "处理中";
}

export function KnowledgeBase({
  documents,
  uploading,
  activeDocumentId,
  onUpload,
  onReindex,
  onDelete,
}: KnowledgeBaseProps) {
  const readyCount = documents.filter((document) => document.status === "ready").length;
  const chunkCount = documents.reduce((sum, document) => sum + (document.chunk_count ?? 0), 0);

  return (
    <section className="mx-auto w-full max-w-6xl px-4 py-7 sm:px-6 lg:px-10 lg:py-9">
      <div className="flex flex-col gap-5 border-b border-ink/10 pb-7 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-moss">Knowledge Base</p>
          <h1 className="text-2xl font-semibold">企业知识库</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-ink/55">上传后会解析、切块并写入向量库。检索时只会召回当前账号自己的文档。</p>
        </div>
        <button type="button" onClick={onUpload} disabled={uploading || Boolean(activeDocumentId)} className="flex h-11 shrink-0 items-center justify-center gap-2 bg-moss px-4 text-sm font-semibold text-white transition hover:bg-[#118278] disabled:cursor-not-allowed disabled:opacity-45">
          <Upload className="h-4 w-4" aria-hidden="true" />
          {uploading ? "正在上传并建立索引" : "上传文档"}
        </button>
      </div>

      <div className="grid grid-cols-1 border-b border-ink/10 sm:grid-cols-3">
        {[
          { label: "已上传文档", value: documents.length },
          { label: "可检索文档", value: readyCount },
          { label: "已保存分块", value: chunkCount },
        ].map((item) => (
          <div key={item.label} className="border-r border-ink/10 px-0 py-5 last:border-r-0 sm:px-5 first:sm:pl-0">
            <div className="text-xl font-semibold text-ink">{item.value}</div>
            <div className="mt-1 text-xs text-ink/45">{item.label}</div>
          </div>
        ))}
      </div>

      <div className="mt-7">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold">文档列表</h2>
          <span className="text-xs text-ink/45">单个文件最大 10 MB</span>
        </div>

        {documents.length === 0 ? (
          <div className="border border-dashed border-ink/20 bg-white px-6 py-14 text-center">
            <Database className="mx-auto h-6 w-6 text-ink/30" aria-hidden="true" />
            <p className="mt-3 text-sm font-medium text-ink/70">还没有可检索的企业文档</p>
            <p className="mt-1 text-xs text-ink/45">支持 PDF、DOCX、Markdown 和 TXT。</p>
          </div>
        ) : (
          <div className="overflow-hidden border border-ink/10 bg-white">
            {documents.map((document) => {
              const isActing = activeDocumentId === document.id;
              return (
                <article key={document.id} className="flex flex-col gap-4 border-b border-ink/10 p-4 last:border-b-0 sm:flex-row sm:items-center sm:gap-5 sm:px-5">
                  <div className="grid h-10 w-10 shrink-0 place-items-center bg-[#eef4f2] text-moss"><FileText className="h-4 w-4" aria-hidden="true" /></div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="max-w-full truncate text-sm font-medium text-ink">{document.filename}</h3>
                      <span className={`border px-2 py-0.5 text-[11px] ${statusStyle(document.status)}`}>{statusLabel(document.status)}</span>
                    </div>
                    <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink/45">
                      <span>{formatBytes(document.size_bytes)}</span>
                      <span>{document.chunk_count ?? 0} 个分块</span>
                      <span>更新于 {formatTime(document.updated_at)}</span>
                    </div>
                    {document.error_message && <p className="mt-2 flex items-center gap-1.5 text-xs text-tomato"><AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />{document.error_message}</p>}
                  </div>
                  <div className="flex shrink-0 items-center gap-2 self-end sm:self-auto">
                    <button type="button" onClick={() => onReindex(document)} disabled={isActing || uploading} className="grid h-9 w-9 place-items-center border border-ink/10 text-ink/55 transition hover:border-moss/40 hover:text-moss disabled:cursor-not-allowed disabled:opacity-35" title="重建向量索引" aria-label={`重建 ${document.filename} 的向量索引`}>
                      <RefreshCw className={`h-4 w-4 ${isActing ? "animate-spin" : ""}`} aria-hidden="true" />
                    </button>
                    <button type="button" onClick={() => onDelete(document)} disabled={isActing || uploading} className="grid h-9 w-9 place-items-center border border-ink/10 text-ink/55 transition hover:border-tomato/45 hover:text-tomato disabled:cursor-not-allowed disabled:opacity-35" title="删除文档" aria-label={`删除 ${document.filename}`}>
                      <Trash2 className="h-4 w-4" aria-hidden="true" />
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </div>
    </section>
  );
}
