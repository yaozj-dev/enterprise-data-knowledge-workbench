import { Activity, Filter, RefreshCw, ShieldCheck, Trash2, UserPlus, UsersRound } from "lucide-react";
import { FormEvent, useState } from "react";
import type { AdminUser, AuditLogPage, MCPToolAuditPage } from "../types/agent";

type AdminConsoleProps = {
  users: AdminUser[];
  currentUserId: number;
  auditPage: AuditLogPage | null;
  mcpAuditPage: MCPToolAuditPage | null;
  loading: boolean;
  creating: boolean;
  deletingUserId: number | null;
  actionFilter: string;
  onActionFilterChange: (value: string) => void;
  onApplyFilter: () => void;
  onLoadPage: (offset: number) => void;
  onRefresh: () => void;
  onDeleteUser: (user: AdminUser) => void;
  onCreateUser: (payload: {
    email: string;
    display_name: string;
    password: string;
    role: "admin" | "user";
  }) => Promise<void>;
};

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "--" : date.toLocaleString("zh-CN", { hour12: false });
}

export function AdminConsole({
  users,
  currentUserId,
  auditPage,
  mcpAuditPage,
  loading,
  creating,
  deletingUserId,
  actionFilter,
  onActionFilterChange,
  onApplyFilter,
  onLoadPage,
  onRefresh,
  onDeleteUser,
  onCreateUser,
}: AdminConsoleProps) {
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<"admin" | "user">("user");
  const [formError, setFormError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setFormError("");
    try {
      await onCreateUser({ email: email.trim(), display_name: displayName.trim(), password, role });
      setEmail("");
      setDisplayName("");
      setPassword("");
      setRole("user");
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "创建用户失败");
    }
  };

  const previousOffset = Math.max(0, (auditPage?.offset ?? 0) - (auditPage?.limit ?? 20));
  const nextOffset = (auditPage?.offset ?? 0) + (auditPage?.limit ?? 20);
  const hasNextPage = auditPage ? nextOffset < auditPage.total : false;

  return (
    <section className="mx-auto w-full max-w-7xl px-4 py-7 sm:px-6 lg:px-10 lg:py-9">
      <div className="flex flex-col gap-5 border-b border-ink/10 pb-7 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-moss">Administration</p>
          <h1 className="text-2xl font-semibold">管理员控制台</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-ink/55">在后端角色校验的基础上管理账号，并按时间追踪关键工作台操作。</p>
        </div>
        <button type="button" onClick={onRefresh} disabled={loading || creating} className="flex h-10 shrink-0 items-center justify-center gap-2 border border-ink/15 bg-white px-3 text-sm font-medium text-ink/70 transition hover:border-moss/40 hover:text-moss disabled:cursor-not-allowed disabled:opacity-45">
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} aria-hidden="true" />刷新数据
        </button>
      </div>

      <div className="grid border-b border-ink/10 sm:grid-cols-2">
        <div className="border-r border-ink/10 py-5 sm:pr-5"><div className="text-xl font-semibold">{users.length}</div><div className="mt-1 text-xs text-ink/45">账号总数</div></div>
        <div className="py-5 sm:pl-5"><div className="text-xl font-semibold">{auditPage?.total ?? 0}</div><div className="mt-1 text-xs text-ink/45">当前筛选下的审计记录</div></div>
      </div>

      <div className="grid gap-8 py-8 xl:grid-cols-[minmax(0,1fr)_360px]">
        <section>
          <div className="mb-3 flex items-center justify-between"><h2 className="text-sm font-semibold">用户账号</h2><span className="text-xs text-ink/45">JWT 角色由后端验证</span></div>
          <div className="overflow-x-auto border border-ink/10 bg-white">
            <table className="min-w-full text-left text-sm">
              <thead className="border-b border-ink/10 bg-[#f7faf9] text-xs text-ink/45"><tr><th className="px-4 py-3 font-medium">用户</th><th className="px-4 py-3 font-medium">角色</th><th className="px-4 py-3 font-medium">状态</th><th className="px-4 py-3 font-medium">创建时间</th><th className="px-4 py-3 text-right font-medium">操作</th></tr></thead>
              <tbody>
                {users.map((user) => <tr key={user.id} className="border-b border-ink/10 last:border-b-0"><td className="px-4 py-3"><div className="font-medium">{user.display_name}</div><div className="mt-0.5 text-xs text-ink/45">{user.email}</div></td><td className="px-4 py-3"><span className="border border-moss/20 bg-moss/10 px-2 py-0.5 text-xs text-moss">{user.role === "admin" ? "管理员" : "普通用户"}</span></td><td className="px-4 py-3 text-xs text-ink/60">{user.is_active ? "启用" : "已停用"}</td><td className="whitespace-nowrap px-4 py-3 text-xs text-ink/45">{formatTime(user.created_at)}</td><td className="px-4 py-3 text-right"><button type="button" onClick={() => onDeleteUser(user)} disabled={loading || creating || deletingUserId !== null || user.id === currentUserId} className="grid h-8 w-8 place-items-center border border-ink/10 text-ink/45 transition hover:border-tomato/45 hover:text-tomato disabled:cursor-not-allowed disabled:opacity-30" title={user.id === currentUserId ? "不能删除当前登录账号" : "删除账号"} aria-label={`删除 ${user.email}`}><Trash2 className={`h-3.5 w-3.5 ${deletingUserId === user.id ? "animate-pulse" : ""}`} aria-hidden="true" /></button></td></tr>)}
                {users.length === 0 && <tr><td colSpan={5} className="px-4 py-10 text-center text-sm text-ink/45">没有可显示的用户</td></tr>}
              </tbody>
            </table>
          </div>
        </section>

        <section className="border border-ink/10 bg-white p-5">
          <div className="mb-5 flex items-center gap-2"><UserPlus className="h-4 w-4 text-moss" aria-hidden="true" /><h2 className="text-sm font-semibold">创建账号</h2></div>
          <form className="space-y-4" onSubmit={(event) => void submit(event)}>
            <label className="block text-xs font-medium text-ink/65">姓名<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} required maxLength={80} className="mt-1.5 h-10 w-full border border-ink/15 bg-[#f7faf9] px-3 text-sm outline-none focus:border-moss focus:ring-2 focus:ring-moss/15" /></label>
            <label className="block text-xs font-medium text-ink/65">邮箱<input value={email} onChange={(event) => setEmail(event.target.value)} type="email" required maxLength={160} className="mt-1.5 h-10 w-full border border-ink/15 bg-[#f7faf9] px-3 text-sm outline-none focus:border-moss focus:ring-2 focus:ring-moss/15" /></label>
            <label className="block text-xs font-medium text-ink/65">初始密码<input value={password} onChange={(event) => setPassword(event.target.value)} type="password" required minLength={8} maxLength={200} className="mt-1.5 h-10 w-full border border-ink/15 bg-[#f7faf9] px-3 text-sm outline-none focus:border-moss focus:ring-2 focus:ring-moss/15" /></label>
            <label className="block text-xs font-medium text-ink/65">角色<select value={role} onChange={(event) => setRole(event.target.value as "admin" | "user")} className="mt-1.5 h-10 w-full border border-ink/15 bg-[#f7faf9] px-3 text-sm outline-none focus:border-moss focus:ring-2 focus:ring-moss/15"><option value="user">普通用户</option><option value="admin">管理员</option></select></label>
            {formError && <p className="border border-tomato/25 bg-tomato/10 px-3 py-2 text-xs text-tomato">{formError}</p>}
            <button type="submit" disabled={creating} className="flex h-10 w-full items-center justify-center gap-2 bg-ink text-sm font-semibold text-white transition hover:bg-soot disabled:cursor-not-allowed disabled:opacity-45"><UsersRound className="h-4 w-4" aria-hidden="true" />{creating ? "创建中..." : "创建用户"}</button>
          </form>
        </section>
      </div>

      <section className="border-t border-ink/10 pt-7">
        <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><div><h2 className="text-sm font-semibold">操作审计</h2><p className="mt-1 text-xs text-ink/45">登录、查询、文档处理和管理员操作都会记录。</p></div><div className="flex gap-2"><label className="sr-only" htmlFor="audit-action">操作类型</label><input id="audit-action" value={actionFilter} onChange={(event) => onActionFilterChange(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") onApplyFilter(); }} placeholder="例如 upload_document" className="h-9 w-48 border border-ink/15 bg-white px-3 text-xs outline-none focus:border-moss sm:w-56" /><button type="button" onClick={onApplyFilter} disabled={loading} className="grid h-9 w-9 place-items-center border border-ink/15 bg-white text-ink/55 transition hover:border-moss/40 hover:text-moss disabled:opacity-45" title="应用筛选" aria-label="应用审计筛选"><Filter className="h-4 w-4" aria-hidden="true" /></button></div></div>
        <div className="overflow-x-auto border border-ink/10 bg-white"><table className="min-w-full text-left text-sm"><thead className="border-b border-ink/10 bg-[#f7faf9] text-xs text-ink/45"><tr><th className="px-4 py-3 font-medium">时间</th><th className="px-4 py-3 font-medium">操作人</th><th className="px-4 py-3 font-medium">操作</th><th className="px-4 py-3 font-medium">详情</th></tr></thead><tbody>{auditPage?.items.map((item) => <tr key={item.id} className="border-b border-ink/10 last:border-b-0"><td className="whitespace-nowrap px-4 py-3 text-xs text-ink/45">{formatTime(item.created_at)}</td><td className="px-4 py-3 text-xs text-ink/70">{item.user_display_name || item.user_email || "系统"}</td><td className="px-4 py-3"><code className="bg-[#f1f5f3] px-1.5 py-0.5 text-xs text-ink/70">{item.action}</code></td><td className="max-w-[420px] truncate px-4 py-3 text-xs text-ink/55" title={item.detail || ""}>{item.detail || "--"}</td></tr>)}{!auditPage?.items.length && <tr><td colSpan={4} className="px-4 py-10 text-center text-sm text-ink/45">没有符合条件的审计记录</td></tr>}</tbody></table></div>
        <div className="mt-4 flex items-center justify-end gap-2"><span className="mr-auto text-xs text-ink/45">{auditPage ? `第 ${auditPage.offset + 1}-${Math.min(auditPage.offset + auditPage.items.length, auditPage.total)} 条，共 ${auditPage.total} 条` : "正在加载"}</span><button type="button" onClick={() => onLoadPage(previousOffset)} disabled={loading || !auditPage || auditPage.offset === 0} className="h-9 border border-ink/15 bg-white px-3 text-xs text-ink/65 transition hover:border-moss/40 hover:text-moss disabled:cursor-not-allowed disabled:opacity-40">上一页</button><button type="button" onClick={() => onLoadPage(nextOffset)} disabled={loading || !hasNextPage} className="h-9 border border-ink/15 bg-white px-3 text-xs text-ink/65 transition hover:border-moss/40 hover:text-moss disabled:cursor-not-allowed disabled:opacity-40">下一页</button></div>
      </section>

      <section className="border-t border-ink/10 pt-7">
        <div className="mb-4 flex items-end justify-between gap-4">
          <div>
            <div className="flex items-center gap-2"><Activity className="h-4 w-4 text-moss" aria-hidden="true" /><h2 className="text-sm font-semibold">MCP 工具审计</h2></div>
            <p className="mt-1 text-xs text-ink/45">只展示运行元数据，不保存工具参数、Prompt 或业务内容。</p>
          </div>
          <span className="text-xs text-ink/45">{mcpAuditPage ? `共 ${mcpAuditPage.total} 条` : "暂不可用"}</span>
        </div>
        <div className="overflow-x-auto border border-ink/10 bg-white">
          <table className="min-w-full text-left text-sm">
            <thead className="border-b border-ink/10 bg-[#f7faf9] text-xs text-ink/45"><tr><th className="px-4 py-3 font-medium">时间</th><th className="px-4 py-3 font-medium">Agent</th><th className="px-4 py-3 font-medium">工具</th><th className="px-4 py-3 font-medium">来源</th><th className="px-4 py-3 font-medium">状态</th><th className="px-4 py-3 font-medium">耗时</th><th className="px-4 py-3 font-medium">错误码</th></tr></thead>
            <tbody>
              {mcpAuditPage?.items.map((item) => <tr key={item.id} className="border-b border-ink/10 last:border-b-0"><td className="whitespace-nowrap px-4 py-3 text-xs text-ink/45">{formatTime(item.created_at)}</td><td className="px-4 py-3 text-xs text-ink/65">{item.agent_name || "--"}</td><td className="px-4 py-3"><code className="bg-[#f1f5f3] px-1.5 py-0.5 text-xs text-ink/70">{item.tool_name}</code></td><td className="px-4 py-3 text-xs text-ink/55">{item.source}</td><td className="px-4 py-3 text-xs text-ink/65">{item.status}{item.retryable ? " · 可重试" : ""}</td><td className="whitespace-nowrap px-4 py-3 text-xs text-ink/55">{item.duration_ms} ms</td><td className="px-4 py-3 text-xs text-tomato/75">{item.error_code || "--"}</td></tr>)}
              {!mcpAuditPage?.items.length && <tr><td colSpan={7} className="px-4 py-10 text-center text-sm text-ink/45">暂无 MCP 工具审计记录</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
    </section>
  );
}
