import { useEffect, useState } from "react";
import AlertsPanel from "./components/AlertsPanel.jsx";
import CameraCard from "./components/CameraCard.jsx";
import TrendChart from "./components/TrendChart.jsx";
import { clock, num } from "./format.js";
import { usePolling, useNow } from "./hooks.js";
import { api, getApiKey, setApiKey } from "./services/api.js";

// Every number on this page comes from the backend API. Nothing is simulated in
// the browser: when a value is unknown the API returns null and we show a dash.

const MODE_COPY = {
  live: { label: "LIVE", note: null },
  demo: {
    label: "DEMO · RECORDED VIDEO",
    note: "Every camera on this page is playing a recorded video through the real AI. These are not live camera readings.",
  },
  mixed: {
    label: "LIVE + RECORDED",
    note: "Some cameras are playing recorded demo video. Cards marked RECORDED are not live readings.",
  },
  idle: { label: "AI IDLE", note: null },
};

function KeyPrompt({ onSaved, rejected }) {
  const [value, setValue] = useState("");
  return (
    <main className="keygate">
      <form
        className="card"
        onSubmit={(e) => {
          e.preventDefault();
          setApiKey(value.trim());
          onSaved();
        }}
      >
        <h1>Annadhanam Crowd Monitor</h1>
        <p className="muted">This server requires an API key. Ask the system administrator for a viewer key.</p>
        {rejected && <p className="inline-warning">That key was not accepted.</p>}
        <label htmlFor="key">API key</label>
        <input id="key" type="password" autoComplete="off" value={value} onChange={(e) => setValue(e.target.value)} />
        <button type="submit" disabled={!value.trim()}>Continue</button>
        <p className="muted small">Kept only for this browser tab.</p>
      </form>
    </main>
  );
}

function Kpi({ label, value, hint, tone }) {
  return (
    <div className={`kpi ${tone || ""}`}>
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
      {hint && <span className="kpi-hint">{hint}</span>}
    </div>
  );
}

function SystemHealth({ live, db, health }) {
  const items = [
    ["API", health ? `v${health.version} (${health.environment})` : "—", !!health],
    ["Database", db.data ? `PostgreSQL ${db.data.server_version}` : db.error ? "unreachable" : "…", !!db.data],
    ["AI pipeline", live ? live.ai_state.replace("_", " ") : "…", live?.ai_state === "running"],
    ["Model", live?.model_name ? `${live.model_name} on ${live.device}` : "not loaded", !!live?.model_name],
    ["Auth", health ? (health.auth_required ? "API keys required" : "OPEN (development)") : "…", !!health?.auth_required],
  ];
  return (
    <section className="card health" aria-labelledby="health-title">
      <h2 id="health-title">System health</h2>
      <ul>
        {items.map(([k, v, ok]) => (
          <li key={k}>
            <i className={ok ? "dot ok" : "dot warn"} aria-hidden="true" />
            <span className="muted">{k}</span>
            <span>{v}</span>
          </li>
        ))}
      </ul>
      {live?.ai_detail && <p className="inline-warning small">{live.ai_detail}</p>}
    </section>
  );
}

export default function App() {
  const now = useNow(1000);
  const [authVersion, setAuthVersion] = useState(0);
  const [health, setHealth] = useState(null);
  const [needsKey, setNeedsKey] = useState(false);
  const [rejected, setRejected] = useState(false);
  const [range, setRange] = useState(180);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  const live = usePolling(() => api.live(), 2000, [authVersion]);
  const alerts = usePolling(() => api.alerts({ pageSize: 15 }), 5000, [authVersion]);
  const history = usePolling(
    () => api.history({ minutes: range, bucketMinutes: range > 360 ? 15 : range > 120 ? 5 : 1 }),
    30000,
    [authVersion, range],
  );
  const db = usePolling(() => api.databaseHealth(), 15000, [authVersion]);

  useEffect(() => {
    if (live.error?.status === 401) {
      setRejected(Boolean(getApiKey()));
      setNeedsKey(true);
    }
  }, [live.error]);

  if (needsKey) {
    return (
      <KeyPrompt
        rejected={rejected}
        onSaved={() => {
          setNeedsKey(false);
          setAuthVersion((v) => v + 1);
        }}
      />
    );
  }

  const data = live.data;
  const tz = data?.site_timezone;
  const mode = MODE_COPY[data?.mode] || MODE_COPY.idle;

  return (
    <div className={`app mode-${data?.mode || "idle"}`}>
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">◉</span>
          <div>
            <h1>Annadhanam Crowd Monitor</h1>
            <p className="muted">Anonymous people counting · no face recognition</p>
          </div>
        </div>
        <div className="topbar-right">
          <span className={`mode-badge mode-${data?.mode || "idle"}`}>{mode.label}</span>
          <span className="clock">{clock(now, tz)}</span>
          {getApiKey() && (
            <button type="button" className="link" onClick={() => { setApiKey(""); setNeedsKey(true); setRejected(false); }}>
              Sign out
            </button>
          )}
        </div>
      </header>

      {mode.note && <div className="demo-banner" role="status">{mode.note}</div>}
      {live.error && live.error.status !== 401 && (
        <div className="error-banner" role="alert">
          Lost contact with the server ({live.error.message}). Showing the last values received
          {live.updatedAt ? ` at ${clock(live.updatedAt, tz)}` : ""}.
        </div>
      )}

      <main className="layout">
        <section className="kpis" aria-label="Headline figures">
          <Kpi label="People in view now" value={num(data?.people_now)} hint={data ? `${data.cameras_online}/${data.cameras_total} cameras online` : ""} />
          <Kpi label="Entries today" value={num(data?.entries_today)} hint="line crossings" />
          <Kpi label="Exits today" value={num(data?.exits_today)} hint="line crossings" />
          <Kpi label="Open alerts" value={num(data?.open_alerts)} tone={data?.open_alerts ? "kpi-alert" : ""} />
        </section>

        <section className="cameras" aria-label="Cameras">
          {live.loading && !data && <p className="muted">Loading…</p>}
          {data && data.cameras.length === 0 && (
            <div className="card empty">
              <h3>No cameras configured</h3>
              <p className="muted">Add cameras through the API, or run <code>scripts/seed_demo.py</code> for a recorded-video demo.</p>
            </div>
          )}
          {data?.cameras.map((c) => <CameraCard key={c.camera_id} camera={c} now={now} />)}
        </section>

        <aside className="side">
          <AlertsPanel alerts={alerts.data?.items} error={alerts.error} onChanged={alerts.refresh} now={now} timeZone={tz} />
          <SystemHealth live={data} db={db} health={health} />
        </aside>

        <section className="card trend" aria-labelledby="trend-title">
          <div className="trend-head">
            <h2 id="trend-title">People over time (all cameras)</h2>
            <div className="seg" role="group" aria-label="Time range">
              {[[60, "1 h"], [180, "3 h"], [720, "12 h"], [1440, "24 h"]].map(([m, label]) => (
                <button key={m} type="button" className={range === m ? "on" : ""} onClick={() => setRange(m)}>
                  {label}
                </button>
              ))}
            </div>
          </div>
          <TrendChart points={history.data?.points} timeZone={tz} />
        </section>
      </main>

      <footer className="pagefoot muted small">
        Updated {live.updatedAt ? clock(live.updatedAt, tz) : "—"} · times in {tz || "local time"} ·
        counts are estimates from computer vision and can miss people hidden by others.
      </footer>
    </div>
  );
}
