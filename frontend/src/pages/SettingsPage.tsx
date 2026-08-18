import { useEffect, useState, type FormEvent } from "react";
import { KeyRound, Save, Users } from "lucide-react";
import { api, errorMessage, jsonBody } from "../api";
import { Alert, Badge, Button, Field, LoadingScreen, NameInput, PageHeader, Panel, SectionTitle } from "../components";
import type { CredentialStatus, EditorialDefaults } from "../types";
import { namesText, parseNames } from "../utils";

export function SettingsPage() {
  const [credentials, setCredentials] = useState<CredentialStatus | null>(null);
  const [defaults, setDefaults] = useState<EditorialDefaults | null>(null);
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
    document.title = "设置 · 绿茵宣传部";
    Promise.all([
      api<CredentialStatus>("/api/settings/thufootball-credentials"),
      api<EditorialDefaults>("/api/editorial-defaults"),
    ]).then(([credentialValue, defaultValue]) => {
      setCredentials(credentialValue);
      setDefaults(defaultValue);
      setEditors(namesText(defaultValue.editors));
      setReviewers(namesText(defaultValue.reviewers));
      setApprovers(namesText(defaultValue.approvers));
    }).catch((value) => setError(errorMessage(value))).finally(() => setLoading(false));
  }, []);

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

  if (loading) return <LoadingScreen label="正在读取设置" />;
  return (
    <>
      <PageHeader eyebrow="系统" title="设置" description="管理数据查询凭据和新建批次使用的默认人员。" />
      {error ? <Alert tone="danger" onDismiss={() => setError(null)}>{error}</Alert> : null}
      {success ? <Alert tone="success" onDismiss={() => setSuccess(null)}>{success}</Alert> : null}
      <div className="settings-grid">
        <Panel>
          <SectionTitle title="THUFootball 凭据" description="提交前会通过只读接口验证。" actions={<KeyRound size={20} />} />
          <div className="credential-status">
            <Badge tone={credentials?.configured ? "success" : "warning"}>{credentials?.configured ? "已配置" : "未配置"}</Badge>
            {credentials?.configured ? <span>openid {credentials.openid_masked} · session {credentials.session_key_masked}</span> : <span>查询比赛前需要补充凭据</span>}
          </div>
          <form className="stack stack--large" onSubmit={saveCredentials}>
            <Field label="OpenID" htmlFor="openid"><input id="openid" type="password" autoComplete="off" value={openid} onChange={(event) => setOpenid(event.target.value)} required /></Field>
            <Field label="Session Key" htmlFor="session-key"><input id="session-key" type="password" autoComplete="off" value={sessionKey} onChange={(event) => setSessionKey(event.target.value)} required /></Field>
            <Button variant="primary" loading={saving === "credentials"} type="submit">验证并更新</Button>
          </form>
        </Panel>
        <Panel>
          <SectionTitle title="默认人员" description="只影响后续新建批次。" actions={<Users size={20} />} />
          <form className="stack stack--large" onSubmit={saveDefaults}>
            <Field label="编辑"><NameInput value={editors} onChange={setEditors} /></Field>
            <Field label="责编"><NameInput value={reviewers} onChange={setReviewers} /></Field>
            <Field label="审核"><NameInput value={approvers} onChange={setApprovers} /></Field>
            <Button variant="primary" loading={saving === "defaults"} type="submit"><Save size={16} />保存默认人员</Button>
          </form>
          {defaults ? <p className="footnote">上次更新：{new Date(defaults.updated_at).toLocaleString("zh-CN")}</p> : null}
        </Panel>
      </div>
    </>
  );
}
