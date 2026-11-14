import { NavLink, Route, Routes } from "react-router-dom";
import { ModeBadge } from "./components/ModeBadge";
import AdminPage from "./pages/AdminPage";
import DashboardPage from "./pages/DashboardPage";
import HealthPage from "./pages/HealthPage";
import NewsPage from "./pages/NewsPage";
import TradesPage from "./pages/TradesPage";

export default function App() {
  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">Options Portal</div>
        <nav className="nav">
          <NavLink to="/" end>
            Signals
          </NavLink>
          <NavLink to="/news">News</NavLink>
          <NavLink to="/trades">Trades</NavLink>
          <NavLink to="/health">Health</NavLink>
          <NavLink to="/admin">Admin</NavLink>
        </nav>
        <div className="topbar-right">
          <span className="muted">IST · 15m</span>
          <ModeBadge mode="dry_run" />
        </div>
      </header>
      <main className="main">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/news" element={<NewsPage />} />
          <Route path="/trades" element={<TradesPage />} />
          <Route path="/health" element={<HealthPage />} />
          <Route path="/admin" element={<AdminPage />} />
        </Routes>
      </main>
    </div>
  );
}
