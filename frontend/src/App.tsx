import { useState } from "react";
import {
  CalendarPlus,
  FileText,
  Layers3,
  LogOut,
  Menu,
  Settings,
  X,
} from "lucide-react";
import {
  Navigate,
  NavLink,
  Outlet,
  createHashRouter,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { useAuth } from "./auth";
import { Button, LoadingScreen, cx } from "./components";
import { BatchDetailPage } from "./pages/BatchDetailPage";
import { BatchesPage } from "./pages/BatchesPage";
import { DashboardPage } from "./pages/DashboardPage";
import { LoginPage } from "./pages/LoginPage";
import { MatchPage } from "./pages/MatchPage";
import { PreviewPage } from "./pages/PreviewPage";
import { SettingsPage } from "./pages/SettingsPage";
import { WechatDraftPage } from "./pages/WechatDraftPage";

const navigation = [
  { to: "/", label: "创建批次", icon: CalendarPlus, end: true },
  { to: "/batches", label: "批次管理", icon: Layers3 },
  { to: "/wechat-drafts", label: "微信草稿", icon: FileText },
  { to: "/settings", label: "设置", icon: Settings },
];

function ProtectedLayout() {
  const { user, loading, logout } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();

  if (loading) return <LoadingScreen label="正在确认登录状态" />;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;

  const handleLogout = async () => {
    await logout();
    navigate("/login", { replace: true });
  };

  return (
    <div className="app-shell">
      <aside className={cx("sidebar", menuOpen && "sidebar--open")}>
        <div className="brand">
          <div className="brand__mark">P</div>
          <div>
            <strong>前瞻管理</strong>
            <span>Preview Console</span>
          </div>
          <button className="icon-button sidebar__close" onClick={() => setMenuOpen(false)} aria-label="关闭导航">
            <X size={18} />
          </button>
        </div>
        <nav className="nav" aria-label="主导航">
          {navigation.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              onClick={() => setMenuOpen(false)}
              className={({ isActive }) => cx("nav__item", isActive && "nav__item--active")}
            >
              <Icon size={18} aria-hidden />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar__footer">
          <div className="user-card">
            <span className="user-card__avatar">{user.display_name.slice(0, 1)}</span>
            <div>
              <strong>{user.display_name}</strong>
              <span>@{user.username}</span>
            </div>
          </div>
          <Button variant="quiet" onClick={handleLogout} className="logout-button">
            <LogOut size={17} />退出
          </Button>
        </div>
      </aside>
      {menuOpen ? <button className="sidebar-scrim" onClick={() => setMenuOpen(false)} aria-label="关闭导航" /> : null}
      <main className="main">
        <button className="mobile-menu" onClick={() => setMenuOpen(true)} aria-label="打开导航">
          <Menu size={20} />
        </button>
        <div className="main__inner"><Outlet /></div>
      </main>
    </div>
  );
}

export const router = createHashRouter([
  { path: "/login", element: <LoginPage /> },
  {
    path: "/",
    element: <ProtectedLayout />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "batches", element: <BatchesPage /> },
      { path: "batches/:batchId", element: <BatchDetailPage /> },
      { path: "batches/:batchId/matches/:gameId", element: <MatchPage /> },
      { path: "batches/:batchId/preview", element: <PreviewPage /> },
      { path: "wechat-drafts", element: <WechatDraftPage /> },
      { path: "settings", element: <SettingsPage /> },
    ],
  },
  { path: "*", element: <Navigate to="/" replace /> },
]);
