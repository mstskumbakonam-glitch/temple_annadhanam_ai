import { api } from "../lib/api.js";
import { useSession } from "../lib/app.js";
import { useApi } from "../lib/hooks.js";
import { PageHeader, Panel, SeatLegend } from "../ui.jsx";

const ROLE_HELP = {
  viewer: "You can see dashboards, temples, halls, seats, the calendar and capacity reports.",
  operator: "You can also change seat status, schedule and edit sessions, and mark staff attendance.",
  admin: "You can also add and edit temples, halls, seat layouts, staff and cameras.",
};

export default function Settings({ onSignOut }) {
  const s = useSession();
  const db = useApi(() => api.databaseHealth(), []);
  return (
    <>
      <PageHeader title="Settings" subtitle="Your access, site settings and how figures are counted." />
      <div className="grid-2">
        <Panel title="Your access">
          <dl className="details">
            <dt>Role</dt><dd>{s.role[0].toUpperCase() + s.role.slice(1)}</dd>
            <dt>What you can do</dt><dd>{ROLE_HELP[s.role]}</dd>
          </dl>
          {s.authRequired ? (
            <button type="button" className="btn btn-quiet" onClick={onSignOut}>Sign out</button>
          ) : (
            <p className="notice notice-warn">No access keys are configured on this server, so anyone who can reach it has full access. Set API keys before using it on a shared network.</p>
          )}
        </Panel>
        <Panel title="Site">
          <dl className="details">
            <dt>Time zone</dt><dd>{s.tz} (all dates and times on these pages)</dd>
            <dt>Environment</dt><dd>{s.environment}</dd>
            <dt>Version</dt><dd>{s.version}</dd>
            <dt>Database</dt><dd>{db.data ? `Connected, PostgreSQL ${db.data.server_version}` : db.error ? "Not reachable" : "Checking…"}</dd>
            <dt>Demo mode</dt><dd>{s.demoMode ? "On: sample records and recorded videos may appear, always labelled." : "Off"}</dd>
          </dl>
          <p className="muted small">Server settings are changed in <code>backend/.env</code> by the system administrator.</p>
        </Panel>
      </div>
      <Panel title="How seats are counted">
        <SeatLegend />
        <ul className="rules">
          <li>Every seat has exactly one status, so a seat is never counted twice.</li>
          <li>Seat capacity is every enabled seat that is not out of service.</li>
          <li>Available seats = capacity − occupied − reserved.</li>
          <li>Temple and dashboard totals include only active halls of active temples.</li>
          <li>Only hall staff confirm a seat as occupied. AI camera detections are estimates and never change a seat on their own.</li>
          <li>Staff without an attendance entry for the day are shown as “not marked”, not as absent.</li>
        </ul>
      </Panel>
    </>
  );
}
