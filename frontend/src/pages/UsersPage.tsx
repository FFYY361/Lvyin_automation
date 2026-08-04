import { useCallback, useEffect, useMemo, useState } from "react";
import { KeyRound, Pencil, Search, UserCheck, UserX } from "lucide-react";
import { api, errorMessage, jsonBody } from "../api";
import { Alert, Badge, Button, EmptyState, Field, LoadingScreen, Modal, PageHeader, Panel } from "../components";
import type { AdminUser } from "../types";
import { formatDateTime } from "../utils";

type UserAction = { kind: "edit" | "toggle" | "reset"; user: AdminUser } | null;

export function UsersPage() {
  const [items, setItems] = useState<AdminUser[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [action, setAction] = useState<UserAction>(null);
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try { setItems((await api<{ items: AdminUser[] }>("/api/admin/users")).items); }
    catch (value) { setError(errorMessage(value)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { document.title = "用户管理 · 前瞻管理"; void load(); }, [load]);
  const filtered = useMemo(() => {
    const value = query.trim().toLocaleLowerCase();
    return value ? items.filter((item) => `${item.username} ${item.display_name}`.toLocaleLowerCase().includes(value)) : items;
  }, [items, query]);

  const open = (kind: NonNullable<UserAction>["kind"], user: AdminUser) => {
    setAction({ kind, user }); setDisplayName(user.display_name); setPassword(""); setConfirmation(""); setError(null); setSuccess(null);
  };
  const submit = async () => {
    if (!action) return;
    if (action.kind === "reset" && password !== confirmation) { setError("两次输入的新密码不一致"); return; }
    setSaving(true); setError(null);
    try {
      if (action.kind === "edit") await api(`/api/admin/users/${action.user.id}`, { method: "PATCH", ...jsonBody({ display_name: displayName }) });
      if (action.kind === "toggle") await api(`/api/admin/users/${action.user.id}`, { method: "PATCH", ...jsonBody({ is_active: !action.user.is_active }) });
      if (action.kind === "reset") await api(`/api/admin/users/${action.user.id}/reset-password`, { method: "POST", ...jsonBody({ new_password: password }) });
      setSuccess(action.kind === "edit" ? "展示名称已更新。" : action.kind === "toggle" ? `账号已${action.user.is_active ? "停用" : "启用"}。` : "密码已重置，用户旧会话已经失效。");
      setAction(null); await load();
    } catch (value) { setError(errorMessage(value)); } finally { setSaving(false); }
  };

  if (loading) return <LoadingScreen label="正在读取用户" />;
  return (
    <>
      <PageHeader eyebrow="管理员" title="用户管理" description="管理普通用户资料、启用状态和登录密码。管理员账号只读。" />
      {error ? <Alert tone="danger" onDismiss={() => setError(null)}>{error}</Alert> : null}
      {success ? <Alert tone="success" onDismiss={() => setSuccess(null)}>{success}</Alert> : null}
      <Panel className="user-search"><Search size={17} /><input aria-label="搜索用户" placeholder="搜索用户名或展示名称" value={query} onChange={(event) => setQuery(event.target.value)} /></Panel>
      {!filtered.length ? <Panel><EmptyState title="没有匹配的用户" /></Panel> : <Panel className="user-list">
        <div className="user-list__header" aria-hidden="true"><span>用户</span><span>身份与状态</span><span>领取任务</span><span>最近更新</span><span>操作</span></div>
        {filtered.map((item) => (
          <article className="user-row" key={item.id}>
            <div className="user-row__identity"><strong>{item.display_name}</strong><span>@{item.username}</span></div>
            <div className="user-row__badges"><Badge tone={item.role === "admin" ? "info" : "neutral"}>{item.role === "admin" ? "管理员" : "普通用户"}</Badge><Badge tone={item.is_active ? "success" : "warning"}>{item.is_active ? "已启用" : "已停用"}</Badge></div>
            <div className="user-row__metric"><span>领取任务</span><strong>{item.claimed_task_count}</strong></div>
            <div className="user-row__metric"><span>最近更新</span><strong>{formatDateTime(item.updated_at)}</strong></div>
            {item.role === "admin" ? <p className="user-row__readonly muted">管理员账号不能在此修改。</p> : <div className="user-row__actions"><Button variant="quiet" onClick={() => open("edit", item)}><Pencil size={15} />修改名称</Button><Button variant="quiet" onClick={() => open("toggle", item)}>{item.is_active ? <UserX size={15} /> : <UserCheck size={15} />}{item.is_active ? "停用" : "启用"}</Button><Button variant="quiet" onClick={() => open("reset", item)}><KeyRound size={15} />重置密码</Button></div>}
          </article>
        ))}
      </Panel>}
      {action?.kind === "edit" ? <Modal title="修改展示名称" onClose={() => setAction(null)} actions={<><Button onClick={() => setAction(null)}>取消</Button><Button variant="primary" loading={saving} onClick={() => void submit()}>保存</Button></>}><Field label="展示名称"><input maxLength={100} value={displayName} onChange={(event) => setDisplayName(event.target.value)} required /></Field></Modal> : null}
      {action?.kind === "toggle" ? <Modal title={`${action.user.is_active ? "停用" : "启用"}用户`} onClose={() => setAction(null)} actions={<><Button onClick={() => setAction(null)}>取消</Button><Button variant={action.user.is_active ? "danger" : "primary"} loading={saving} onClick={() => void submit()}>确认{action.user.is_active ? "停用" : "启用"}</Button></>}><Alert tone="warning">切换启用状态会使该用户的旧会话失效，但不会自动释放其已认领任务。</Alert><p>{action.user.display_name}（@{action.user.username}）</p></Modal> : null}
      {action?.kind === "reset" ? <Modal title="重置用户密码" onClose={() => setAction(null)} actions={<><Button onClick={() => setAction(null)}>取消</Button><Button variant="danger" loading={saving} disabled={password.length < 8 || confirmation.length < 8} onClick={() => void submit()}>确认重置</Button></>}><Alert tone="warning">重置后该用户的全部旧会话会立即失效。</Alert><div className="stack"><Field label="新密码"><input type="password" minLength={8} maxLength={128} autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} /></Field><Field label="确认新密码"><input type="password" minLength={8} maxLength={128} autoComplete="new-password" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /></Field></div></Modal> : null}
    </>
  );
}
