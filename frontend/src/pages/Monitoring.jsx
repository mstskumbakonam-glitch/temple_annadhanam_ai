import { useState } from "react";
import "../monitoring.css";
import AlertsPanel from "../components/AlertsPanel.jsx";
import CameraCard from "../components/CameraCard.jsx";
import TrendChart from "../components/TrendChart.jsx";
import { api } from "../lib/api.js";
import { clock, num } from "../lib/format.js";
import { useNow, usePolling } from "../lib/hooks.js";
import { PageHeader } from "../ui.jsx";

// Optional AI / CCTV section (moved here from the former crowd-monitor home page).
// Every number comes from /api/analytics/*; unknown values are shown as a dash.

const MODE_COPY = {
  live: { label: "Live cameras", note: null },
  demo: { label: "Recorded video", note: "Every camera here is playing a recorded video through the real AI. These are not live camera readings." },
  mixed: { label: "Live and recorded", note: "Some cameras are playing recorded demo video. Cards marked RECORDED are not live readings." },
  idle: { label: "AI not running", note: null },
};

function Kpi({ label, value, hint, tone }) {
  return (
    <div className={`kpi ${tone || ""}`}>
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
      {hint && <span className="kpi-hint">{hint}</span>}
    </div>
  );
}

export default function Monitoring() {
  const now = useNow(1000);
  const [range, setRange] = useState(180);
  const live = usePolling(() => api.live(), 2000);
  const alerts = usePolling(() => api.alerts({ pageSize: 15 }), 5000);
  const history = usePolling(
    () => api.history({ minutes: range, bucketMinutes: range > 360 ? 15 : range > 120 ? 5 : 1 }), 30000, [range]);
  const data = live.data;
  const tz = data?.site_timezone;
  const mode = MODE_COPY[data?.mode] || MODE_COPY.idle;

  return (
    <div className={`monitoring mode-${data?.mode || "idle"}`}>
      <PageHeader title="AI / CCTV monitoring"
                  subtitle="Anonymous people counting from cameras. Estimates only: they never change seat status or attendance."
                  actions={<span className={`mode-badge mode-${data?.mode || "idle"}`}>{mode.label}</span>} />
      {mode.note && <div className="demo-banner" role="status">{mode.note}</div>}
      {live.error && (
        <div className="error-banner" role="alert">
          {live.error.status === 403 ? "Your key cannot view monitoring." : `Lost contact with the server (${live.error.message}).`}
          {live.updatedAt ? ` Showing the last values received at ${clock(live.updatedAt, tz)}.` : ""}
        </div>
      )}
      <div className="layout">
        <section className="kpis" aria-label="Headline figures">
          <Kpi label="People in view now" value={num(data?.people_now)} hint={data ? `${data.cameras_online}/${data.cameras_total} cameras online` : ""} />
          <Kpi label="Entries today" value={num(data?.entries_today)} hint="Line crossings" />
          <Kpi label="Exits today" value={num(data?.exits_today)} hint="Line crossings" />
          <Kpi label="Open alerts" value={num(data?.open_alerts)} tone={data?.open_alerts ? "kpi-alert" : ""} />
        </section>
        <section className="cameras" aria-label="Cameras">
          {data && data.cameras.length === 0 && (
            <div className="card empty">
              <h3>No cameras configured</h3>
              <p className="muted">Cameras are added through the API, or with <code>scripts/seed_demo.py</code> for a recorded-video demo.</p>
            </div>
          )}
          {data?.cameras.map((c) => <CameraCard key={c.camera_id} camera={c} now={now} />)}
        </section>
        <aside className="side">
          <AlertsPanel alerts={alerts.data?.items} error={alerts.error} onChanged={alerts.refresh} now={now} timeZone={tz} />
        </aside>
        <section className="card trend" aria-labelledby="trend-title">
          <div className="trend-head">
            <h2 id="trend-title">People over time (all cameras)</h2>
            <div className="seg" role="group" aria-label="Time range">
              {[[60, "1 h"], [180, "3 h"], [720, "12 h"], [1440, "24 h"]].map(([m, label]) => (
                <button key={m} type="button" className={range === m ? "on" : ""} onClick={() => setRange(m)}>{label}</button>
              ))}
            </div>
          </div>
          <TrendChart points={history.data?.points} timeZone={tz} />
        </section>
      </div>
    </div>
  );
}
