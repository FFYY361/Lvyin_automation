import { useEffect, useState, type FormEvent } from "react";
import { LockKeyhole } from "lucide-react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { errorMessage } from "../api";
import { useAuth } from "../auth";
import { Alert, Button, Field, LoadingScreen } from "../components";

export function LoginPage() {
  const { user, loading, login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => { document.title = "登录 · 前瞻管理"; }, []);
  if (loading) return <LoadingScreen label="正在检查登录状态" />;
  if (user) return <Navigate to="/" replace />;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(username, password);
      const from = (location.state as { from?: string } | null)?.from ?? "/";
      navigate(from, { replace: true });
    } catch (value) {
      setError(errorMessage(value));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="login-page">
      <div className="login-card">
        <div className="login-icon"><LockKeyhole size={24} /></div>
        <p className="eyebrow">ADMIN CONSOLE</p>
        <h1>登录前瞻管理</h1>
        <p className="login-subtitle">使用管理员账号继续</p>
        {error ? <Alert tone="danger">{error}</Alert> : null}
        <form onSubmit={submit} className="stack stack--large">
          <Field label="用户名" htmlFor="username">
            <input id="username" autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required autoFocus />
          </Field>
          <Field label="密码" htmlFor="password">
            <input id="password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required />
          </Field>
          <Button variant="primary" loading={submitting} type="submit" className="button--full">登录</Button>
        </form>
      </div>
    </main>
  );
}
