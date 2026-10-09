import { useEffect, useState } from "react";
import { api } from "../services/api.js";
import { ago, dash, duration, LEVEL_CLASS, num } from "../format.js";

const STATE_LABEL = {
  online: "Online",
  connecting: "Connecting",
  offline: "Offline",
  error: "Error",
  stopped: "Stopped",
  not_running: "AI not running",
};

function Preview({ camera }) {
  const [url, setUrl] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!camera.preview_available) {
      setUrl(null);
      return undefined;
    }
    let alive = true;
    let current = null;
    let timer = null;
    const load = async () => {
      try {
        const blob = await api.preview(camera.camera_id);
        if (!alive) return;
        const next = URL.createObjectURL(blob);
        setUrl(next);
        setFailed(false);
        if (current) URL.revokeObjectURL(current);
        current = next;
      } catch {
        if (alive) setFailed(true);
      }
      if (alive) timer = setTimeout(load, 1500);
    };
    load();
    return () => {
      alive = false;
      clearTimeout(timer);
      if (current) URL.revokeObjectURL(current);
    };
  }, [camera.camera_id, camera.preview_available]);

  if (!camera.preview_available) {
    return (
      <div className="preview preview-empty">
        <span>{camera.ai_running ? "Preview disabled (PREVIEW_ENABLED=false)" : "No video"}</span>
      </div>
    );
  }
  return (
    <div className="preview">
      {url && <img src={url} alt={`Processed frame from ${camera.camera_name}`} />}
      {failed && <span className="preview-note">Preview unavailable</span>}
    </div>
  );
}

function ZoneRow({ zone }) {
  const thresholdHint =
    zone.kind === "queue"
      ? `Queue ${num(zone.queue_length)} · wait ≈ ${duration(zone.estimated_wait_seconds)}`
      : `${num(zone.density, zone.density_unit === "people" ? 0 : 2)} ${
          zone.density_unit === "people" ? "people" : "per m²"
        }`;
  return (
    <li className="zone">
      <div className="zone-head">
        <span className="zone-name">{zone.name || zone.zone_id}</span>
        <span className={`level ${LEVEL_CLASS[zone.level]}`}>{zone.level}</span>
      </div>
      <div className="zone-meta">
        <span>{thresholdHint}</span>
        {zone.kind === "queue" && zone.throughput_per_minute !== null && (
          <span>{num(zone.throughput_per_minute, 1)} served/min</span>
        )}
      </div>
    </li>
  );
}

export default function CameraCard({ camera, now }) {
  const recorded = camera.source_kind === "recorded";
  const state = camera.stale ? "stale" : camera.connection_state;
  return (
    <article className={`card camera ${recorded ? "is-recorded" : ""}`}>
      <header className="camera-head">
        <div>
          <h3>{camera.camera_name}</h3>
          <p className="muted">
            {camera.camera_id}
            {camera.location ? ` · ${camera.location}` : ""}
          </p>
        </div>
        <div className="badges">
          <span className={`badge ${recorded ? "badge-recorded" : "badge-live"}`}>
            {recorded ? "RECORDED" : "LIVE"}
          </span>
          <span className={`status status-${state}`}>
            <i aria-hidden="true" />
            {camera.stale ? "Stale feed" : STATE_LABEL[camera.connection_state] || camera.connection_state}
          </span>
        </div>
      </header>

      <Preview camera={camera} />

      <div className="camera-stats">
        <div>
          <span className="stat-label">People now</span>
          <span className="stat-value">{num(camera.confirmed_count)}</span>
        </div>
        <div>
          <span className="stat-label">Entries today</span>
          <span className="stat-value">{num(camera.entries_today)}</span>
        </div>
        <div>
          <span className="stat-label">Exits today</span>
          <span className="stat-value">{num(camera.exits_today)}</span>
        </div>
      </div>

      {camera.zones.length > 0 && <ul className="zones">{camera.zones.map((z) => <ZoneRow key={z.zone_id} zone={z} />)}</ul>}
      {camera.ai_running && camera.has_analytics && camera.zones.length === 0 && (
        <p className="muted small">Zones resume when video is flowing.</p>
      )}
      {!camera.has_analytics && (
        <p className="muted small">No counting lines or zones configured for this camera.</p>
      )}

      {camera.alerts.map((a) => (
        <p key={a.alert_id} className={`inline-alert sev-${a.severity}`}>
          {a.message}
        </p>
      ))}
      {camera.warnings.map((w) => (
        <p key={w} className="inline-warning">{w}</p>
      ))}

      <footer className="camera-foot">
        <span title="AI frames processed per second">AI {num(camera.processing_fps, 1)} fps</span>
        <span title="Model inference time for the last frame">{camera.inference_ms === null ? dash : `${num(camera.inference_ms)} ms`}</span>
        <span title="Last frame received">{ago(camera.last_frame_timestamp, now)}</span>
        {camera.reconnect_count > 0 && <span>{camera.reconnect_count} reconnects</span>}
        {camera.last_error && <span className="err" title={camera.last_error}>{camera.last_error}</span>}
      </footer>
    </article>
  );
}
