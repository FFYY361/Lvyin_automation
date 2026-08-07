import { useState, type ReactNode } from "react";
import { CalendarPlus, ClipboardList, FileText, Layers3, LogOut, Menu, Settings, UserCog, UserRound, X } from "lucide-react";
import { Navigate, NavLink, Outlet, createHashRouter, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "./auth";
import { Button, LoadingScreen, cx } from "./components";
import { AccountPage } from "./pages/AccountPage";
import { BatchDetailPage } from "./pages/BatchDetailPage";
import { BatchesPage } from "./pages/BatchesPage";
import { DashboardPage } from "./pages/DashboardPage";
import { LoginPage } from "./pages/LoginPage";
import { MatchPage } from "./pages/MatchPage";
import { PreviewPage } from "./pages/PreviewPage";
import { RegisterPage } from "./pages/RegisterPage";
import { ReportMatchPage } from "./pages/ReportMatchPage";
import { ReportsPage } from "./pages/ReportsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { TasksPage } from "./pages/TasksPage";
import { UsersPage } from "./pages/UsersPage";
import { WechatDraftPage } from "./pages/WechatDraftPage";

const adminNavigation = [
  { to: "/previews", label: "前瞻批次", icon: Layers3 },
  { to: "/reports", label: "战报批次", icon: FileText },
  { to: "/create", label: "创建批次", icon: CalendarPlus },
  { to: "/tasks", label: "任务中心", icon: ClipboardList },
  { to: "/wechat-drafts", label: "微信草稿", icon: FileText },
  { to: "/users", label: "用户管理", icon: UserCog },
  { to: "/settings", label: "系统设置", icon: Settings },
];
const userNavigation = [
  { to: "/tasks", label: "任务中心", icon: ClipboardList },
  { to: "/previews", label: "前瞻批次", icon: Layers3 },
  { to: "/reports", label: "战报批次", icon: FileText },
  { to: "/account", label: "个人设置", icon: UserRound },
];

function ProtectedLayout() {
  const { user, loading, logout } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  if (loading) return <LoadingScreen label="正在确认登录状态" />;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  const navigation = user.role === "admin" ? adminNavigation : userNavigation;
  const handleLogout = async () => { await logout(); navigate("/login", { replace: true }); };
  return (
    <div className="app-shell">
      <aside className={cx("sidebar", menuOpen && "sidebar--open")}>
        <div className="brand"><div className="brand__mark">绿</div><div><strong>绿茵宣传部</strong><span>Media Department</span></div><button className="icon-button sidebar__close" onClick={() => setMenuOpen(false)} aria-label="关闭导航"><X size={18} /></button></div>
        <nav className="nav" aria-label="主导航">{navigation.map(({ to, label, icon: Icon }) => <NavLink key={to} to={to} onClick={() => setMenuOpen(false)} className={({ isActive }) => cx("nav__item", isActive && "nav__item--active")}><Icon size={18} aria-hidden />{label}</NavLink>)}</nav>
        <div className="sidebar__footer">
          <NavLink to="/account" className="user-card"><span className="user-card__avatar">{user.display_name.slice(0, 1)}</span><div><strong>{user.display_name}</strong><span>@{user.username} · {user.role === "admin" ? "管理员" : "普通用户"}</span></div></NavLink>
          <Button variant="quiet" onClick={handleLogout} className="logout-button"><LogOut size={17} />退出</Button>
        </div>
      </aside>
      {menuOpen ? <button className="sidebar-scrim" onClick={() => setMenuOpen(false)} aria-label="关闭导航" /> : null}
      <main className="main"><button className="mobile-menu" onClick={() => setMenuOpen(true)} aria-label="打开导航"><Menu size={20} /></button><div className="main__inner"><Outlet /></div></main>
    </div>
  );
}

function RoleHome() {
  const { user } = useAuth();
  return <Navigate to={user?.role === "admin" ? "/previews" : "/tasks"} replace />;
}
function AdminOnly({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  return user?.role === "admin" ? children : <Navigate to="/tasks" replace />;
}

export const router = createHashRouter([
  { path: "/login", element: <LoginPage /> },
  { path: "/register", element: <RegisterPage /> },
  {
    path: "/", element: <ProtectedLayout />, children: [
      { index: true, element: <RoleHome /> },
      { path: "tasks", element: <TasksPage /> },
      { path: "account", element: <AccountPage /> },
      { path: "previews", element: <BatchesPage /> },
      { path: "previews/:batchId", element: <BatchDetailPage /> },
      { path: "previews/:batchId/matches/:gameId", element: <MatchPage /> },
      { path: "previews/:batchId/article", element: <PreviewPage /> },
      { path: "reports", element: <ReportsPage /> },
      { path: "reports/:batchId/matches/:gameId", element: <ReportMatchPage /> },
      { path: "reports/:batchId/article", element: <PreviewPage articleType="report" /> },
      { path: "create", element: <AdminOnly><DashboardPage /></AdminOnly> },
      { path: "wechat-drafts", element: <AdminOnly><WechatDraftPage /></AdminOnly> },
      { path: "users", element: <AdminOnly><UsersPage /></AdminOnly> },
      { path: "settings", element: <AdminOnly><SettingsPage /></AdminOnly> },
    ],
  },
  { path: "*", element: <Navigate to="/" replace /> },
]);
