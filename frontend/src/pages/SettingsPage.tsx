import { useEffect, useMemo, useState, type FormEvent } from "react";
import { ChevronRight, Database, KeyRound, Save, Search, Users } from "lucide-react";
import { Link } from "react-router-dom";
import { api, errorMessage, jsonBody } from "../api";
import { Alert, Badge, Button, Field, LoadingScreen, NameInput, PageHeader, Panel, SectionTitle } from "../components";
import { competitionLabels, type CredentialStatus, type EditorialDefaults, type InstitutionSummary } from "../types";
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

  useEffect(() => {
    document.title = "资料管理 · 绿茵宣传部";
    Promise.all([
      api<CredentialStatus>("/api/settings/thufootball-credentials"),
      api<EditorialDefaults>("/api/editorial-defaults"),
      api<{ items: InstitutionSummary[] }>("/api/admin/institutions"),
    ]).then(([credentialValue, defaultValue, institutionValue]) => {
      setCredentials(credentialValue);
      setDefaults(defaultValue);
      setInstitutions(institutionValue.items);
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

  if (loading) return <LoadingScreen label="正在读取资料管理" />;
  return (
    <>
      <PageHeader eyebrow="系统" title="资料管理" description="维护默认人员、球队与球员人工资料，以及底层查询凭据。" />
      {error ? <Alert tone="danger" onDismiss={() => setError(null)}>{error}</Alert> : null}
      {success ? <Alert tone="success" onDismiss={() => setSuccess(null)}>{success}</Alert> : null}

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
