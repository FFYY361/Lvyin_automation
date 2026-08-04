import { useEffect, useState, type FormEvent } from "react";
import { KeyRound, Save, UserRound } from "lucide-react";
import { api, errorMessage, jsonBody } from "../api";
import { useAuth } from "../auth";
import { Alert, Button, Field, PageHeader, Panel, SectionTitle } from "../components";

export function AccountPage() {
  const { user, updateProfile } = useAuth();
  const [displayName, setDisplayName] = useState(user?.display_name ?? "");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [saving, setSaving] = useState<"profile" | "password" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => { document.title = "个人设置 · 前瞻协作"; }, []);

  const saveProfile = async (event: FormEvent) => {
    event.preventDefault(); setSaving("profile"); setError(null); setSuccess(null);
    try {
      await updateProfile(displayName);
      setSuccess("展示名称已更新，已有比赛署名不会改变。 ");
    } catch (value) { setError(errorMessage(value)); } finally { setSaving(null); }
  };
  const changePassword = async (event: FormEvent) => {
    event.preventDefault();
    if (newPassword !== confirmation) { setError("两次输入的新密码不一致"); return; }
    setSaving("password"); setError(null); setSuccess(null);
    try {
      await api<void>("/api/auth/change-password", { method: "POST", ...jsonBody({ current_password: currentPassword, new_password: newPassword }) });
      setCurrentPassword(""); setNewPassword(""); setConfirmation("");
      setSuccess("密码已修改，其他设备上的旧会话已经失效。");
    } catch (value) { setError(errorMessage(value)); } finally { setSaving(null); }
  };

  return (
    <>
      <PageHeader eyebrow="账号" title="个人设置" description={`当前账号：@${user?.username ?? ""}`} />
      {error ? <Alert tone="danger" onDismiss={() => setError(null)}>{error}</Alert> : null}
      {success ? <Alert tone="success" onDismiss={() => setSuccess(null)}>{success}</Alert> : null}
      <div className="settings-grid">
        <Panel>
          <SectionTitle title="展示名称" description="用于认领任务时生成署名，不会回写已有署名。" actions={<UserRound size={20} />} />
          <form className="stack" onSubmit={saveProfile}>
            <Field label="展示名称"><input maxLength={100} value={displayName} onChange={(event) => setDisplayName(event.target.value)} required /></Field>
            <Button variant="primary" loading={saving === "profile"} type="submit"><Save size={16} />保存名称</Button>
          </form>
        </Panel>
        <Panel>
          <SectionTitle title="修改密码" description="修改后当前会话继续有效，其他旧会话失效。" actions={<KeyRound size={20} />} />
          <form className="stack" onSubmit={changePassword}>
            <Field label="当前密码"><input type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required /></Field>
            <Field label="新密码"><input type="password" autoComplete="new-password" minLength={8} maxLength={128} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required /></Field>
            <Field label="确认新密码"><input type="password" autoComplete="new-password" minLength={8} maxLength={128} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} required /></Field>
            <Button variant="primary" loading={saving === "password"} type="submit"><KeyRound size={16} />修改密码</Button>
          </form>
        </Panel>
      </div>
    </>
  );
}
