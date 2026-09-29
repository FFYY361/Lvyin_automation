import { useEffect, useMemo, useState, type FormEvent } from "react";
import { ChevronRight, Database, Expand, KeyRound, Save, Search, Users, X } from "lucide-react";
import { Link } from "react-router-dom";
import { api, errorMessage, jsonBody } from "../api";
import { Alert, Badge, Button, Field, LoadingScreen, NameInput, PageHeader, Panel, SectionTitle } from "../components";
import { competitionLabels, type CredentialStatus, type EditorialDefaults, type InstitutionSummary, type PromptTemplate } from "../types";
import { namesText, parseNames } from "../utils";

const competitionIdFields = [
  ["male", "male_team_ids"],
  ["female", "female_team_ids"],
  ["futsal", "futsal_team_ids"],
] as const;

export function SettingsPage() {
  const [credentials, setCredentials] = useState<CredentialStatus | null>(null);
  const [defaults, setDefaults] = useState<EditorialDefaults | null>(null);
  const [institutions, setInstitutions] = useState<InstitutionSummary[]>([]);
  const [prompts, setPrompts] = useState<PromptTemplate[]>([]);
  const [query, setQuery] = useState("");
  const [openid, setOpenid] = useState("");
  const [sessionKey, setSessionKey] = useState("");
  const [editors, setEditors] = useState("");
  const [reviewers, setReviewers] = useState("");
  const [approvers, setApprovers] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<"credentials" | "defaults" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [expandedPromptKey, setExpandedPromptKey] = useState<string | null>(null);

  useEffect(() => {
    document.title = "资料管理 · 绿茵宣传部";
    Promise.all([
      api<CredentialStatus>("/api/settings/thufootball-credentials"),
      api<EditorialDefaults>("/api/editorial-defaults"),
      api<{ items: InstitutionSummary[] }>("/api/admin/institutions"),
      api<{ items: PromptTemplate[] }>("/api/admin/prompts"),
    ]).then(([credentialValue, defaultValue, institutionValue, promptValue]) => {
      setCredentials(credentialValue);
      setDefaults(defaultValue);
      setInstitutions(institutionValue.items);
      setPrompts(promptValue.items);
      setEditors(namesText(defaultValue.editors));
      setReviewers(namesText(defaultValue.reviewers));
      setApprovers(namesText(defaultValue.approvers));
    }).catch((value) => setError(errorMessage(value))).finally(() => setLoading(false));
  }, []);

  const filtered = useMemo(() => {
    const value = query.trim().toLocaleLowerCase("zh-CN");
    if (!value) return institutions;
    return institutions.filter((item) => `${item.name} ${item.short_name}`.toLocaleLowerCase("zh-CN").includes(value));
  }, [institutions, query]);

  const saveCredentials = async (event: FormEvent) => {
    event.preventDefault(); setSaving("credentials"); setError(null); setSuccess(null);
    try {
      const value = await api<CredentialStatus>("/api/settings/thufootball-credentials", { method: "PUT", ...jsonBody({ openid, session_key: sessionKey }) });
      setCredentials(value); setOpenid(""); setSessionKey(""); setSuccess("THUFootball 凭据已验证并更新");
    } catch (value) { setError(errorMessage(value)); } finally { setSaving(null); }
  };

  const saveDefaults = async (event: FormEvent) => {
    event.preventDefault(); setSaving("defaults"); setError(null); setSuccess(null);
    try {
      const value = await api<EditorialDefaults>("/api/editorial-defaults", { method: "PUT", ...jsonBody({ editors: parseNames(editors), reviewers: parseNames(reviewers), approvers: parseNames(approvers) }) });
      setDefaults(value); setSuccess("默认人员已保存，新建批次将使用这些人员");
    } catch (value) { setError(errorMessage(value)); } finally { setSaving(null); }
  };

  const savePrompt = async (item: PromptTemplate) => {
    setError(null); setSuccess(null);
    try {
      const value = await api<PromptTemplate>(`/api/admin/prompts/${item.key}`, { method: "PUT", ...jsonBody({ content: item.content, expected_updated_at: item.updated_at }) });
      setPrompts((current) => current.map((entry) => entry.key === value.key ? value : entry));
      setSuccess(`${item.label}已保存`);
    } catch (value) { setError(errorMessage(value)); }
  };

  if (loading) return <LoadingScreen label="正在读取资料管理" />;
  return (
    <>
      <PageHeader eyebrow="系统" title="资料管理" description="维护默认人员、球队与球员人工资料，以及底层查询凭据。" />
      {error ? <Alert tone="danger" onDismiss={() => setError(null)}>{error}</Alert> : null}
      {success ? <Alert tone="success" onDismiss={() => setSuccess(null)}>{success}</Alert> : null}

      <Panel className="data-management-panel">
        <SectionTitle title="AI Prompt 规则" description="当前版本直接用于后续 AI 生成；修改后已有结果会标记为过期。" actions={<Database size={20} />} />
        <div className="prompt-editor-list">
          {prompts.map((item) => <div className="prompt-editor" key={item.key}><Field label={item.label}><textarea rows={9} value={item.content} onChange={(event) => setPrompts((current) => current.map((entry) => entry.key === item.key ? { ...entry, content: event.target.value } : entry))} /></Field><div className="editor-actions"><Button onClick={() => setExpandedPromptKey(item.key)}><Expand size={16} />放大编辑</Button><Button variant="primary" onClick={() => void savePrompt(item)}><Save size={16} />保存</Button><span className="footnote">更新于 {new Date(item.updated_at).toLocaleString("zh-CN")}</span></div></div>)}
        </div>
      </Panel>

      {expandedPromptKey ? (() => { const item = prompts.find((entry) => entry.key === expandedPromptKey); if (!item) return null; return <div className="prompt-modal" role="dialog" aria-modal="true" aria-label={`编辑${item.label}`}><div className="prompt-modal__card"><div className="prompt-modal__heading"><div><p className="eyebrow">AI PROMPT</p><h2>{item.label}</h2></div><button className="icon-button" aria-label="关闭放大编辑" onClick={() => setExpandedPromptKey(null)}><X size={20} /></button></div><textarea autoFocus value={item.content} onChange={(event) => setPrompts((current) => current.map((entry) => entry.key === item.key ? { ...entry, content: event.target.value } : entry))} /><div className="prompt-modal__actions"><span className="footnote">编辑完成后保存当前分区</span><Button onClick={() => setExpandedPromptKey(null)}>关闭</Button><Button variant="primary" onClick={() => { void savePrompt(item); setExpandedPromptKey(null); }}><Save size={16} />保存</Button></div></div></div>; })() : null}

      <Panel className="data-management-panel">
        <SectionTitle title="默认人员" description="只影响后续新建批次。" actions={<Users size={20} />} />
        <form className="admin-settings__grid" onSubmit={saveDefaults}>
          <Field label="编辑"><NameInput value={editors} onChange={setEditors} /></Field>
          <Field label="责编"><NameInput value={reviewers} onChange={setReviewers} /></Field>
          <Field label="审核"><NameInput value={approvers} onChange={setApprovers} /></Field>
          <div className="editor-actions data-management-actions"><Button variant="primary" loading={saving === "defaults"} type="submit"><Save size={16} />保存默认人员</Button></div>
        </form>
        {defaults ? <p className="footnote">上次更新：{new Date(defaults.updated_at).toLocaleString("zh-CN")}</p> : null}
      </Panel>

      <Panel className="data-management-panel">
        <SectionTitle title="院系人工资料" description="进入院系后维护男足、女足、五人制及球员描述。" actions={<Database size={20} />} />
        <div className="institution-search"><Search size={17} /><input aria-label="搜索院系" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索院系名称或简称" /></div>
        <div className="institution-card-grid">
          {filtered.map((item) => (
            <Link className="institution-card" key={item.name} to={`/settings/institutions/${encodeURIComponent(item.name)}`}>
              <div className="institution-card__heading"><div><strong>{item.name}</strong><span>{item.short_name}</span></div><ChevronRight size={18} /></div>
              <div className="institution-competition-status">
                {competitionIdFields
                  .filter(([, idsField]) => item[idsField].length > 0)
                  .map(([competition]) => <Badge key={competition} tone={item.team_descriptions_complete[competition] ? "success" : "warning"}>{competitionLabels[competition]}{item.team_descriptions_complete[competition] ? "已填写" : "待补充"}</Badge>)}
              </div>
              <p>球员描述 {item.player_description_count} / {item.player_count}</p>
            </Link>
          ))}
        </div>
        {!filtered.length ? <p className="history-empty">没有匹配的院系。</p> : null}
      </Panel>

      <Panel className="data-management-panel credential-panel">
        <SectionTitle title="THUFootball 凭据" description="仅在底层查询凭据失效时更新；提交前会通过只读接口验证。" actions={<KeyRound size={20} />} />
        <div className="credential-status"><Badge tone={credentials?.configured ? "success" : "warning"}>{credentials?.configured ? "已配置" : "未配置"}</Badge>{credentials?.configured ? <span>openid {credentials.openid_masked} · session {credentials.session_key_masked}</span> : <span>查询比赛前需要补充凭据</span>}</div>
        <form className="form-grid credential-form" onSubmit={saveCredentials}>
          <Field label="OpenID" htmlFor="openid"><input id="openid" type="password" autoComplete="off" value={openid} onChange={(event) => setOpenid(event.target.value)} required /></Field>
          <Field label="Session Key" htmlFor="session-key"><input id="session-key" type="password" autoComplete="off" value={sessionKey} onChange={(event) => setSessionKey(event.target.value)} required /></Field>
          <div className="editor-actions data-management-actions"><Button variant="primary" loading={saving === "credentials"} type="submit">验证并更新</Button></div>
        </form>
      </Panel>
    </>
  );
}
