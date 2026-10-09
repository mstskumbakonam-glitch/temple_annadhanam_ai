import { useEffect, useState } from "react";
import { ApiError, api, getApiKey, setApiKey } from "./lib/api.js";
import { SessionContext, makeSession, useRoute } from "./lib/app.js";
import { useNow } from "./lib/hooks.js";
import Calendar from "./pages/Calendar.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Halls, { HallDetail } from "./pages/Halls.jsx";
import Monitoring from "./pages/Monitoring.jsx";
import Reports from "./pages/Reports.jsx";
import Seats from "./pages/Seats.jsx";
import Settings from "./pages/Settings.jsx";
import Staff from "./pages/Staff.jsx";
import Temples, { TempleDetail } from "./pages/Temples.jsx";
import { ToastProvider } from "./ui.jsx";

const NAV = [
  ["", "Dashboard", "M3 13h8V3H3zm10 8h8V11h-8zM3 21h8v-6H3zm10-18v6h8V3z"],
  ["temples", "Temples", "M12 2 4 7v2h16V7zM5 10v8H3v3h18v-3h-2v-8h-3v8h-3v-8h-2v8H8v-8z"],
  ["halls", "Annadhanam halls", "M3 5h18v4H3zm2 6h14v8H5zm3 2v4h3v-4zm5 0v4h3v-4z"],
  ["seats", "Seat management", "M7 4h10v7H7zM5 12h14v3H5zm1 4h2v4H6zm10 0h2v4h-2z"],
  ["calendar", "Calendar & sessions", "M7 2v2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-2V2h-2v2H9V2zM5 9h14v11H5z"],
  ["staff", "Staff management", "M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm8 0a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM1 21v-2c0-3 4-5 8-5s8 2 8 5v2zm17 0v-2c0-1.5-.6-2.8-1.7-3.8 3 .4 5.7 1.8 5.7 3.8v2z"],
  ["reports", "Reports", "M5 3h10l4 4v14H5zm9 1v4h4M8 12h8M8 16h8M8 8h4"],
  ["monitoring", "AI / CCTV monitoring", "M4 7h11l5-3v12l-5-3H4zm0 8v5h4v-5"],
  ["settings", "Settings", "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zm9 4-2.2.8-.5 1.3 1 2.1-1.6 1.6-2.1-1-1.3.5L13.5 21h-3l-.8-2.2-1.3-.5-2.1 1-1.6-1.6 1-2.1-.5-1.3L3 12v-2.3l2.2-.8.5-1.3-1-2.1 1.6-1.6 2.1 1 1.3-.5L10.5 2h3l.8 2.2 1.3.5 2.1-1 1.6 1.6-1 2.1.5 1.3 2.2.8z"],
];

const ROLE_LABEL = { viewer: "Viewer", operator: "Operator", admin: "Administrator" };

function SignIn({ onSignedIn, rejected }) {
  const [value, setValue] = useState("");
  return (
    <main className="signin">
      <form
        className="signin-card"
        onSubmit={(e) => {
          e.preventDefault();
          setApiKey(value.trim());
          onSignedIn();
        }}
      >
        <Mark />
        <h1>Annadhanam hall management</h1>
        <p className="muted">Sign in with the access key from your administrator.</p>
        {rejected && <p className="field-error" role="alert">That key was not accepted. Check it and try again.</p>}
        <label htmlFor="key">Access key</label>
        <input id="key" type="password" autoComplete="current-password" value={value}
               onChange={(e) => setValue(e.target.value)} autoFocus />
        <button type="submit" className="btn btn-primary" disabled={!value.trim()}>Sign in</button>
        <p className="hint">The key is kept only in this browser tab and is cleared when you close it.</p>
      </form>
    </main>
  );
}

function Mark() {
  // A stylised gopuram: tiers narrowing upward.
  return (
    <svg className="mark" viewBox="0 0 32 32" aria-hidden="true">
      <path d="M13 3h6v3h-6zM11 7h10v4H11zM9 12h14v5H9zM7 18h18v6H7zM5 25h22v4H5z" />
    </svg>
  );
}

function Shell({ session, onSignOut }) {
  const route = useRoute();
  const now = useNow(1000);
  const [menuOpen, setMenuOpen] = useState(false);
  const section = route.parts[0] || "";
  useEffect(() => setMenuOpen(false), [route.path]);

  let page;
  switch (section) {
    case "":
      page = <Dashboard />;
      break;
    case "temples":
      page = route.parts[1] ? <TempleDetail code={route.parts[1]} /> : <Temples params={route.params} />;
      break;
    case "halls":
      page = route.parts[1] ? <HallDetail code={route.parts[1]} /> : <Halls params={route.params} />;
      break;
    case "seats":
      page = <Seats params={route.params} />;
      break;
    case "calendar":
      page = <Calendar params={route.params} />;
      break;
    case "staff":
      page = <Staff params={route.params} />;
      break;
    case "reports":
      page = <Reports />;
      break;
    case "monitoring":
      page = <Monitoring />;
      break;
    case "settings":
      page = <Settings onSignOut={onSignOut} />;
      break;
    default:
      page = <p className="state">This page does not exist. <a href="#/">Go to the dashboard</a>.</p>;
  }

  const date = now.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: session.tz });
  const time = now.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: session.tz });

  return (
    <div className={`shell ${menuOpen ? "menu-open" : ""}`}>
      <a href="#main" className="skip">Skip to content</a>
      <aside className="sidebar" aria-label="Main navigation">
        <div className="brand">
          <Mark />
          <div>
            <strong>Annadhanam</strong>
            <span>Hall management</span>
          </div>
        </div>
        <nav>
          <ul>
            {NAV.map(([slug, label, icon]) => (
              <li key={slug}>
                <a href={`#/${slug}`} aria-current={section === slug ? "page" : undefined}>
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d={icon} /></svg>
                  {label}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      </aside>
      <div className="main-col">
        <header className="topbar">
          <button type="button" className="icon-btn menu-btn" aria-label="Open navigation" aria-expanded={menuOpen}
                  onClick={() => setMenuOpen((v) => !v)}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18v2H3zm0 5h18v2H3zm0 5h18v2H3z" /></svg>
          </button>
          <p className="today">
            <time dateTime={now.toISOString()}>{date}</time>
            <span className="clock">{time} IST</span>
          </p>
          <div className="profile">
            {session.demoMode && <span className="badge badge-demo">Demo mode</span>}
            {!session.authRequired && <span className="badge badge-warn" title="No access keys are configured on the server">Open access</span>}
            <span className="who" aria-label={`Signed in as ${ROLE_LABEL[session.role]}`}>
              <span className="avatar" aria-hidden="true">{ROLE_LABEL[session.role][0]}</span>
              {ROLE_LABEL[session.role]}
            </span>
            {session.authRequired && (
              <button type="button" className="btn btn-quiet" onClick={onSignOut}>Sign out</button>
            )}
          </div>
        </header>
        <main id="main" className="content" tabIndex={-1}>{page}</main>
      </div>
      <button type="button" className="scrim" aria-label="Close navigation" tabIndex={-1} onClick={() => setMenuOpen(false)} />
    </div>
  );
}

export default function App() {
  const [state, setState] = useState({ phase: "checking" });

  const check = async () => {
    try {
      const me = await api.me();
      setState({ phase: "in", session: makeSession(me) });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setState({ phase: "out", rejected: Boolean(getApiKey()) });
      } else {
        setState({ phase: "error", error: err });
      }
    }
  };

  useEffect(() => {
    check();
  }, []);

  const signOut = () => {
    setApiKey("");
    setState({ phase: "out", rejected: false });
  };

  if (state.phase === "checking") return <p className="state state-loading boot">Connecting…</p>;
  if (state.phase === "error") {
    return (
      <main className="signin">
        <div className="signin-card">
          <h1>Server not reachable</h1>
          <p>{state.error.message} Make sure the backend is running, then try again.</p>
          <button type="button" className="btn btn-primary" onClick={check}>Try again</button>
        </div>
      </main>
    );
  }
  if (state.phase === "out") return <SignIn onSignedIn={check} rejected={state.rejected} />;
  return (
    <SessionContext.Provider value={state.session}>
      <ToastProvider>
        <Shell session={state.session} onSignOut={signOut} />
      </ToastProvider>
    </SessionContext.Provider>
  );
}
