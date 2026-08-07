import { useEffect, useState } from "react";
import { Link, NavLink, Route, Routes } from "react-router-dom";
import { api } from "./api/client";
import { ComingSoon } from "./components/ComingSoon";
import Github from "./pages/Github";
import Repos from "./pages/Repos";
import Settings from "./pages/Settings";
import TaskDetail from "./pages/TaskDetail";
import Tasks from "./pages/Tasks";

function JalebiMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <path
        d="M12 9.5a2.5 2.5 0 1 1-2.5 2.5 3.5 3.5 0 1 1 3.5-3.5 4.5 4.5 0 1 1-4.5 4.5 5.5 5.5 0 1 1 5.5-5.5 6.5 6.5 0 1 1-6.5 6.5"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

const NAV_ITEMS = [
  { to: "/", label: "Tasks", end: true },
  { to: "/repos", label: "Repos", end: false },
  { to: "/github", label: "GitHub", end: false },
  { to: "/settings", label: "Settings", end: false },
];

const SOON_ITEMS = [
  { to: "/screenings", label: "Screenings" },
  { to: "/agents", label: "Agents" },
  { to: "/triggers", label: "Triggers" },
];

function navClass({ isActive }: { isActive: boolean }): string {
  return `relative rounded-lg px-3 py-1.5 text-sm transition-colors ${
    isActive
      ? "bg-ink-850 text-ink-100 ring-1 ring-inset ring-ink-700"
      : "text-ink-400 hover:text-ink-100"
  }`;
}

function HealthDot() {
  const [up, setUp] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    const check = () => {
      api
        .getHealth()
        .then(() => !cancelled && setUp(true))
        .catch(() => !cancelled && setUp(false));
    };
    check();
    const timer = setInterval(check, 15000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const color =
    up === null ? "bg-ink-600" : up ? "bg-green-400" : "bg-red-400";
  const title = up === null ? "Checking API…" : up ? "API online" : "API unreachable";
  return <span className={`h-2 w-2 rounded-full ${color}`} title={title} />;
}

function App() {
  return (
    <div className="min-h-screen font-sans text-ink-200">
      <header className="sticky top-0 z-10 border-b border-ink-800/80 bg-ink-950/80 backdrop-blur-md">
        <div className="mx-auto flex max-w-6xl items-center gap-1 px-6 py-3">
          <Link to="/" className="mr-6 flex items-center gap-2.5">
            <JalebiMark className="h-7 w-7 text-syrup-500" />
            <span className="text-xl font-bold tracking-tight text-ink-100">Jalebi</span>
          </Link>

          <nav className="flex items-center gap-1">
            {NAV_ITEMS.map((item) => (
              <NavLink key={item.to} to={item.to} end={item.end} className={navClass}>
                {item.label}
              </NavLink>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-1">
            {SOON_ITEMS.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  `rounded-lg px-3 py-1.5 text-sm text-ink-600 transition-colors hover:text-ink-400 ${
                    isActive ? "bg-ink-850 text-ink-400" : ""
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
            <span className="ml-3 flex items-center gap-2 rounded-full border border-ink-800 px-3 py-1.5">
              <HealthDot />
              <span className="font-mono text-[11px] text-ink-500">api:3456</span>
            </span>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-6 py-8">
        <Routes>
          <Route path="/" element={<Tasks />} />
          <Route path="/tasks/:id" element={<TaskDetail />} />
          <Route path="/repos" element={<Repos />} />
          <Route path="/github" element={<Github />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/screenings" element={<ComingSoon feature="Screenings" />} />
          <Route path="/agents" element={<ComingSoon feature="Agents" />} />
          <Route path="/triggers" element={<ComingSoon feature="Triggers" />} />
          <Route
            path="*"
            element={
              <div className="surface flex flex-col items-start gap-3 p-6">
                <h2 className="panel-title">Page not found</h2>
                <p className="text-sm text-ink-400">
                  That URL doesn&apos;t exist.{" "}
                  <Link to="/" className="link">
                    Back to tasks
                  </Link>
                  .
                </p>
              </div>
            }
          />
        </Routes>
      </main>
    </div>
  );
}

export default App;
