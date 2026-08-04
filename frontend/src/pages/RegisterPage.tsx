import { useEffect, useState, type FormEvent } from "react";
import { UserPlus } from "lucide-react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { errorMessage } from "../api";
import { useAuth } from "../auth";
import { Alert, Button, Field, LoadingScreen } from "../components";

export function RegisterPage() {
  const { user, loading, register } = useAuth();
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => { document.title = "注册 · 前瞻协作"; }, []);
  if (loading) return <LoadingScreen label="正在检查登录状态" />;
  if (user) return <Navigate to={user.role === "admin" ? "/batches" : "/tasks"} replace />;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (password !== confirmation) {
      setError("两次输入的密码不一致");
      return;
    }
    setSubmitting(true); setError(null);
    try {
      await register(username, displayName, password);
      navigate("/tasks", { replace: true });
    } catch (value) {
      setError(errorMessage(value));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="login-page">
      <div className="login-card">
        <div className="login-icon"><UserPlus size={24} /></div>
        <p className="eyebrow">MEMBER SIGN UP</p>
        <h1>注册普通用户</h1>
        <p className="login-subtitle">注册后即可领取比赛前瞻任务</p>
        {error ? <Alert tone="danger">{error}</Alert> : null}
        <form className="stack stack--large" onSubmit={submit}>
          <Field label="用户名" hint="1–64 个字符，区分大小写且不能包含空白。"><input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required autoFocus /></Field>
          <Field label="展示名称"><input autoComplete="name" maxLength={100} value={displayName} onChange={(event) => setDisplayName(event.target.value)} required /></Field>
          <Field label="密码" hint="至少 8 个字符。"><input type="password" autoComplete="new-password" minLength={8} maxLength={128} value={password} onChange={(event) => setPassword(event.target.value)} required /></Field>
          <Field label="确认密码"><input type="password" autoComplete="new-password" minLength={8} maxLength={128} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} required /></Field>
          <Button variant="primary" loading={submitting} type="submit" className="button--full">注册并登录</Button>
        </form>
        <p className="auth-switch">已有账号？<Link to="/login">返回登录</Link></p>
      </div>
    </main>
  );
}
