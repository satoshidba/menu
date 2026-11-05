import { ReactNode, useEffect, useState } from "react";
import { Link, NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import Icon from "./components/Icon";
import { ToastProvider } from "./components/ui";
import { AuthProvider, useAuth } from "./lib/auth";
import Login from "./screens/Login";
import Today from "./screens/Today";
import Recipes from "./screens/Recipes";
import RecipeDetail from "./screens/RecipeDetail";
import RecipeNew from "./screens/RecipeNew";
import CookMode from "./screens/CookMode";
import Pantry from "./screens/Pantry";
import Scan from "./screens/Scan";
import Plan from "./screens/Plan";
import Shopping from "./screens/Shopping";
import Receipts from "./screens/Receipts";
import ReceiptReview from "./screens/ReceiptReview";
import Nutrition from "./screens/Nutrition";
import Settings from "./screens/Settings";
import DesignSystem from "./screens/DesignSystem";
import Health from "./screens/Health";
import Stocktake from "./screens/Stocktake";

export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <Gate />
      </ToastProvider>
    </AuthProvider>
  );
}

function Gate() {
  const { me, loading } = useAuth();
  const loc = useLocation();
  if (loc.pathname === "/design") return <DesignSystem />;
  if (loading) return <div className="main"><p className="muted">Opening the larder…</p></div>;
  if (!me) return <Login />;
  return (
    <Routes>
      <Route path="/recipes/:id/cook" element={<CookMode />} />
      <Route path="*" element={<Shell><AppRoutes /></Shell>} />
    </Routes>
  );
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Today />} />
      <Route path="/recipes" element={<Recipes />} />
      <Route path="/recipes/new" element={<RecipeNew />} />
      <Route path="/recipes/:id" element={<RecipeDetail />} />
      <Route path="/pantry" element={<Pantry />} />
      <Route path="/pantry/scan" element={<Scan />} />
      <Route path="/pantry/stocktake" element={<Stocktake />} />
      <Route path="/plan" element={<Plan />} />
      <Route path="/shopping" element={<Shopping />} />
      <Route path="/receipts" element={<Receipts />} />
      <Route path="/receipts/:id" element={<ReceiptReview />} />
      <Route path="/nutrition" element={<Nutrition />} />
      <Route path="/health" element={<Health />} />
      <Route path="/settings" element={<Settings />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

const NAV = [
  { to: "/", label: "Today", icon: "today", end: true },
  { to: "/recipes", label: "Recipes", icon: "book" },
  { to: "/pantry", label: "Pantry", icon: "jar" },
  { to: "/plan", label: "Plan", icon: "plan" },
];
const MORE = [
  { to: "/shopping", label: "Shopping list", icon: "cart" },
  { to: "/receipts", label: "Receipts", icon: "receipt" },
  { to: "/nutrition", label: "Nutrition", icon: "chart" },
  { to: "/health", label: "Medicines & vitamins", icon: "pill" },
  { to: "/settings", label: "Settings", icon: "settings" },
];

export function Wordmark() {
  return <Link to="/" className="wordmark" aria-label="Larder home"><Icon name="leaf" size={22} />Larder</Link>;
}

function contextualAction(path: string): { to: string; label: string; icon: string } | null {
  if (path.startsWith("/recipes/new") || path.startsWith("/pantry/scan") || path.startsWith("/pantry/stocktake") || /^\/receipts\/.+/.test(path)) return null;
  if (path.startsWith("/recipes")) return { to: "/recipes/new", label: "Add recipe", icon: "plus" };
  if (path.startsWith("/receipts")) return { to: "/receipts?upload=1", label: "Review receipt", icon: "receipt" };
  if (path.startsWith("/shopping") || path.startsWith("/plan")) return { to: "/pantry/scan", label: "Scan item", icon: "scan" };
  return { to: "/pantry/scan", label: "Scan item", icon: "scan" };
}

function Shell({ children }: { children: ReactNode }) {
  const loc = useLocation();
  const { me, signOut } = useAuth();
  const [online, setOnline] = useState(navigator.onLine);
  useEffect(() => {
    const on = () => setOnline(true), off = () => setOnline(false);
    window.addEventListener("online", on); window.addEventListener("offline", off);
    return () => { window.removeEventListener("online", on); window.removeEventListener("offline", off); };
  }, []);
  useEffect(() => { window.scrollTo(0, 0); }, [loc.pathname]);
  const action = contextualAction(loc.pathname);
  return (
    <div className="shell">
      <a href="#main" className="sr-only">Skip to content</a>
      <nav className="sidebar" aria-label="Main">
        <Wordmark />
        {NAV.map((n) => <NavLink key={n.to} to={n.to} end={n.end} className="nav"><Icon name={n.icon} />{n.label}</NavLink>)}
        <div className="nav-group eyebrow">Household</div>
        {MORE.map((n) => <NavLink key={n.to} to={n.to} className="nav"><Icon name={n.icon} />{n.label}</NavLink>)}
        <div className="spacer" />
        <div className="small muted" style={{ padding: "0 12px" }}>{me?.household.name}</div>
        <button className="btn ghost small" style={{ justifyContent: "flex-start" }} onClick={signOut}>Sign out {me?.user.display_name}</button>
      </nav>
      <div>
        {!online && <div className="offline-bar">You're offline. Showing what was last saved on this device; changes won't be sent until you reconnect.</div>}
        <header className="topbar">
          <Wordmark />
          <div className="row">
            <NavLink to="/health" className="btn ghost icon-btn" aria-label="Medicines and vitamins"><Icon name="pill" /></NavLink>
            <NavLink to="/shopping" className="btn ghost icon-btn" aria-label="Shopping list"><Icon name="cart" /></NavLink>
            <NavLink to="/receipts" className="btn ghost icon-btn" aria-label="Receipts"><Icon name="receipt" /></NavLink>
            <NavLink to="/settings" className="btn ghost icon-btn" aria-label="Settings and more"><Icon name="settings" /></NavLink>
          </div>
        </header>
        <main id="main" className="main">{children}</main>
      </div>
      {action && <Link to={action.to} className="fab"><Icon name={action.icon} />{action.label}</Link>}
      <nav className="tabbar" aria-label="Main">
        {NAV.map((n) => <NavLink key={n.to} to={n.to} end={n.end}><Icon name={n.icon} />{n.label}</NavLink>)}
      </nav>
    </div>
  );
}
