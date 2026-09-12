import { Fragment, useEffect, useState } from "react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { api } from "./api/client";
import Agents from "./pages/Agents";
import Github from "./pages/Github";
import Repos from "./pages/Repos";
import Screenings, { SCREENS_SEEN_KEY } from "./pages/Screenings";
import Settings from "./pages/Settings";
import Skills from "./pages/Skills";
import TaskDetail from "./pages/TaskDetail";
import Tasks from "./pages/Tasks";
import Triggers from "./pages/Triggers";
import NotificationBell from "./components/NotificationBell";

function JalebiMark({ className }: { className?: string }) {
  return (
    <img
      src="/jalebi-logo.svg"
      alt=""
      aria-hidden="true"
      draggable={false}
      className={className}
    />
  );
}

const NAV_ITEMS = [
  { to: "/", label: "Tasks", end: true, group: "tasks" },
  { to: "/repos", label: "Repos", end: false, group: "repos" },
  { to: "/github", label: "GitHub", end: false, group: "repos" },
  { to: "/agents", label: "Agents", end: false, group: "agents" },
  { to: "/skills", label: "Skills", end: false, group: "agents" },
  { to: "/screenings", label: "Screenings", end: false, group: "screenings" },
  { to: "/triggers", label: "Triggers", end: false, group: "screenings" },
  { to: "/settings", label: "Settings", end: false, group: "settings" },
];

const SOON_ITEMS: { to: string; label: string }[] = [];

function useScreeningsUnread(): number {
  const [unread, setUnread] = useState(0);
  const location = useLocation();
  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        const screens = await api.getScreens();
        const seen = Number(localStorage.getItem(SCREENS_SEEN_KEY) || 0);
        const n = screens.filter((s) => {
          const lr = s.latest_run;
          if (!lr || lr.status !== "done" || !lr.finding_total) return false;
          const t = Date.parse(lr.finished_at || lr.started_at || "");
          return !Number.isNaN(t) && t > seen;
        }).length;
        if (!cancelled) setUnread(n);
      } catch {
        // Badge is best-effort; a failed check keeps the previous count.
      }
    };
    check();
    const timer = setInterval(check, 60000);
    // location.pathname in deps: visiting the tab sets the seen timestamp,
    // and the badge must clear immediately rather than at the next poll.
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [location.pathname]);
  return unread;
}

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

  const color = up === null ? "bg-ink-600" : up ? "bg-green-400" : "bg-red-400";
  const title = up === null ? "Checking API…" : up ? "API online" : "API unreachable";
  return <span className={`h-2 w-2 rounded-full ${color}`} title={title} />;
}

function App() {
  const screeningsUnread = useScreeningsUnread();
  return (
    <div className="min-h-screen font-sans text-ink-200">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-3 focus:left-3 focus:z-50 focus:rounded-md focus:bg-ink-850 focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:text-ink-100 focus:ring-2 focus:ring-syrup-500 focus:outline-none"
      >
        Skip to main content
      </a>
      <header className="sticky top-0 z-10 border-b border-ink-800/80 bg-ink-950/80 backdrop-blur-md">
        <div className="mx-auto flex max-w-6xl items-center gap-1 px-6 py-3">
          <Link to="/" className="mr-6 flex items-center gap-2.5">
            <JalebiMark className="h-7 w-7" />
            <span className="text-xl font-bold tracking-tight text-ink-100">Jalebi</span>
          </Link>

          <nav className="flex items-center gap-1">
            {NAV_ITEMS.map((item, idx) => {
              const showSeparator = idx > 0 && item.group !== NAV_ITEMS[idx - 1].group;
              return (
                <Fragment key={item.to}>
                  {showSeparator && (
                    <span aria-hidden="true" className="mx-1 h-5 w-px bg-ink-800" />
                  )}
                  <NavLink to={item.to} end={item.end} className={navClass}>
                    {item.label}
                    {item.to === "/screenings" && screeningsUnread > 0 && (
                      <span
                        title={`${screeningsUnread} screen(s) with new findings`}
                        className="ml-1.5 inline-flex min-h-5 min-w-5 items-center justify-center rounded-full bg-syrup-500 px-1 text-[11px] font-semibold text-ink-950"
                      >
                        {screeningsUnread}
                      </span>
                    )}
                  </NavLink>
                </Fragment>
              );
            })}
          </nav>

          <div className="ml-auto flex items-center gap-1">
            {SOON_ITEMS.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  `rounded-lg px-3 py-1.5 text-sm text-ink-500 transition-colors hover:text-ink-400 ${
                    isActive ? "bg-ink-850 text-ink-400" : ""
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
            <NotificationBell />
            <span className="ml-3 flex items-center gap-2 rounded-full border border-ink-800 px-3 py-1.5">
              <HealthDot />
              <span className="font-mono text-[11px] text-ink-500">
                api:{window.location.port || "2052"}
              </span>
            </span>
          </div>
        </div>
      </header>

      <main id="main-content" tabIndex={-1} className="mx-auto max-w-6xl px-6 py-8">
        <Routes>
          <Route path="/" element={<Tasks />} />
          <Route path="/tasks/:id" element={<TaskDetail />} />
          <Route path="/repos" element={<Repos />} />
          <Route path="/github" element={<Github />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/skills" element={<Skills />} />
          <Route path="/screenings" element={<Screenings />} />
          <Route path="/triggers" element={<Triggers />} />
          <Route path="/settings" element={<Settings />} />
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
