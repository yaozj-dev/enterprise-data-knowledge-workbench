/**
 * 聊天输入区组件
 * 处理问题输入、发送和停止当前流式请求
 */
import { ArrowUp, Paperclip, Square, Sparkles } from "lucide-react";
import { FormEvent, KeyboardEvent, useRef } from "react";
import { cn } from "../lib/format";
import type { QueryMode } from "../types/agent";

type ComposerProps = {
    value: string;
    disabled: boolean;
    isStreaming: boolean;
    onChange: (value: string) => void;
    onSubmit: () => void;
    onStop: () => void;
    onAttach: () => void;
    uploading: boolean;
    mode: QueryMode;
    onModeChange: (mode: QueryMode) => void;
};

export function Composer({
    value,
    disabled,
    isStreaming,
    onChange,
    onSubmit,
    onStop,
    onAttach,
    uploading,
    mode,
    onModeChange,
}: ComposerProps) {
    const textareaRef = useRef<HTMLTextAreaElement | null>(null);

    const submit = (event: FormEvent) => {
        event.preventDefault();
        if (!disabled) onSubmit();
    };

    const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            if (!disabled) onSubmit();
        }
    };

    return (
        <form
            onSubmit={submit}
            className="border-t border-ink/10 bg-white px-4 py-3 sm:px-7 sm:py-4"
        >
            <div className="mx-auto flex max-w-5xl items-end gap-2 border border-ink/15 bg-[#f7faf9] p-2 shadow-line transition focus-within:border-moss/50 focus-within:bg-white">
                <button type="button" onClick={onAttach} disabled={uploading || isStreaming} className="grid h-10 w-10 shrink-0 place-items-center text-ink/45 transition hover:bg-moss/10 hover:text-moss disabled:cursor-not-allowed disabled:opacity-30" title="上传文档" aria-label="上传文档"><Paperclip className="h-4 w-4" aria-hidden="true" /></button>
                <textarea
                    ref={textareaRef}
                    value={value}
                    onChange={(event) => onChange(event.target.value)}
                    onKeyDown={onKeyDown}
                    rows={1}
                    placeholder="输入业务问题，按 Enter 发送，Shift + Enter 换行"
                    className="max-h-36 min-h-10 flex-1 resize-none bg-transparent px-2 py-2.5 text-sm leading-6 text-ink outline-none placeholder:text-ink/35"
                />
                <div className="hidden items-center gap-1 px-2 pb-2 text-[10px] text-ink/30 sm:flex"><Sparkles className="h-3 w-3" aria-hidden="true" />Agent</div>
                <label className="sr-only" htmlFor="query-mode">执行模式</label>
                <select
                    id="query-mode"
                    value={mode}
                    onChange={(event) => onModeChange(event.target.value as QueryMode)}
                    disabled={isStreaming}
                    className="mb-1 hidden h-8 max-w-[118px] border border-ink/10 bg-white px-2 text-[11px] text-ink/60 outline-none focus:border-moss sm:block"
                    title="选择 Agent 执行模式"
                >
                    <option value="auto">自动路由</option>
                    <option value="data">数据分析</option>
                    <option value="knowledge">知识库</option>
                    <option value="general">通用对话</option>
                    <option value="composite">综合分析</option>
                </select>
                <button
                    type={isStreaming ? "button" : "submit"}
                    onClick={isStreaming ? onStop : undefined}
                    disabled={!isStreaming && disabled}
                    className={cn(
                        "grid h-10 w-10 shrink-0 place-items-center text-white transition focus:outline-none focus:ring-2 focus:ring-moss/40 focus:ring-offset-2",
                        isStreaming
                            ? "bg-tomato hover:bg-tomato/90"
                            : "bg-ink hover:bg-soot disabled:cursor-not-allowed disabled:bg-ink/25",
                    )}
                    title={isStreaming ? "停止" : "发送"}
                    aria-label={isStreaming ? "停止" : "发送"}
                >
                    {isStreaming ? (
                        <Square
                            className="h-4 w-4 fill-current"
                            aria-hidden="true"
                        />
                    ) : (
                        <ArrowUp className="h-5 w-5" aria-hidden="true" />
                    )}
                </button>
            </div>
        </form>
    );
}
