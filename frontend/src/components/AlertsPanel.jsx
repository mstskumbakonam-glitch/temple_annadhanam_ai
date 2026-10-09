import { useState } from "react";
import { api } from "../services/api.js";
import { ago, clock, dash, num } from "../format.js";

const TYPE_LABEL = { CROWD_DENSITY: "Crowd density", QUEUE_CONGESTION: "Queue congestion" };

export default function AlertsPanel({ alerts, error, onChanged, now, timeZone }) {
  const [message, setMessage] = useState(null);

  const acknowledge = async (id) => {
    try {
      await api.acknowledge(id);
      setMessage(null);
      onChanged();
    } catch (err) {
      setMessage(err.status === 403 ? "Acknowledging needs an admin key." : err.message);
    }
  };

  return (
    <section className="card alerts" aria-labelledby="alerts-title">
      <h2 id="alerts-title">Alerts</h2>
      {error && <p className="inline-warning">Could not load alerts: {error.message}</p>}
      {message && <p className="inline-warning">{message}</p>}
      {alerts && alerts.length === 0 && <p className="muted">No alerts recorded.</p>}
      <ol className="timeline">
        {(alerts || []).map((a) => (
          <li key={a.alert_id} className={`tl-item sev-${a.severity} ${a.active ? "is-active" : ""}`}>
            <div className="tl-head">
              <strong>{TYPE_LABEL[a.alert_type] || a.alert_type}</strong>
              <span className="muted">{a.camera_id}{a.zone_id ? ` · ${a.zone_id}` : ""}</span>
            </div>
            <p className="tl-msg">{a.message || dash}</p>
            <p className="tl-meta">
              {a.active ? <span className="pill pill-active">ACTIVE</span> : <span className="pill">cleared</span>}
              <span>since {clock(a.started_at, timeZone)} ({ago(a.started_at, now)})</span>
              {a.ended_at && <span>ended {clock(a.ended_at, timeZone)}</span>}
              <span>peak {num(a.peak_value, 1)} / limit {num(a.threshold, 0)}</span>
              {a.acknowledged_at ? (
                <span className="muted">acknowledged</span>
              ) : (
                <button type="button" className="link" onClick={() => acknowledge(a.alert_id)}>
                  Acknowledge
                </button>
              )}
            </p>
          </li>
        ))}
      </ol>
    </section>
  );
}
