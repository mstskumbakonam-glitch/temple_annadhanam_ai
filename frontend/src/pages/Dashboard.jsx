import { useState } from "react";
import { api } from "../lib/api.js";
import { href, useSession } from "../lib/app.js";
import { hhmm, longDate, num, pct, titleCase } from "../lib/format.js";
import { useApi, usePolling } from "../lib/hooks.js";
import { SessionBadge, Empty, ErrorState, Loading, PageHeader, Panel, SeatBar, SeatLegend, Select, Stat } from "../ui.jsx";


export default function Dashboard() {
  const { tz } = useSession();
  const [temple, setTemple] = useState("");
  const temples = useApi(() => api.temples({ page_size: 200 }), []);
  const overview = usePolling(() => api.overview(temple || undefined), 15000, [temple]);
  const halls = usePolling(() => api.halls({ temple_code: temple || undefined, active: true, page_size: 200 }), 15000, [temple]);
  const o = overview.data;

  return (
    <>
      <PageHeader
        title="Today at a glance"
        subtitle={o ? longDate(o.local_date) : " "}
        actions={
          <Select label="Temple" value={temple} onChange={setTemple} includeAll="All temples"
                  options={(temples.data?.items || []).map((t) => ({ value: t.temple_code, label: t.name }))} />
        }
      />
      <ErrorState error={overview.error} onRetry={overview.refresh} />
      {!o && overview.loading && <Loading />}
      {o && (
        <>
          {o.demo_records_present && (
            <p className="notice notice-demo">Sample records from the demo seed are included in these figures. They are marked “Sample data” wherever they appear.</p>
          )}

          <Panel className="capacity">
            <div className="capacity-head">
              <div>
                <h2>Seats in service</h2>
                <p className="muted">Active halls of active temples. Every seat has exactly one status.</p>
              </div>
              <p className="capacity-figure">
                <strong>{num(o.seats.occupied)}</strong>
                <span>of {num(o.seats.capacity)} seats occupied</span>
                <span className="capacity-pct">{pct(o.seats.occupancy_percentage)}</span>
              </p>
            </div>
            <SeatBar counts={o.seats} size="lg" label="All halls" />
            <SeatLegend counts={o.seats} />
          </Panel>

          <div className="stats-row">
            <Stat label="Temples" value={num(o.temples_total)} note={`${num(o.temples_active)} active`} />
            <Stat label="Annadhanam halls" value={num(o.halls_total)} note={`${num(o.halls_active)} active`} />
            <Stat label="Available seats" value={num(o.seats.available)} note={`${num(o.seats.reserved)} reserved`} />
            <Stat label="Staff present today"
                  value={o.attendance.in_use ? num(o.attendance.present + o.attendance.half_day) : "—"}
                  note={o.attendance.in_use ? `of ${num(o.staff_total_active)} active staff` : "Attendance not recorded yet"} />
            <Stat label="Sessions today" value={num(o.sessions.today_total)}
                  note={`${num(o.sessions.upcoming)} upcoming in total`} />
            {o.active_alerts !== null && (
              <Stat label="Active AI alerts" value={num(o.active_alerts)} tone={o.active_alerts ? "bad" : undefined}
                    note={<a href="#/monitoring">Open monitoring</a>} />
            )}
          </div>

          <div className="grid-2">
            <Panel title="Hall occupancy" actions={<a href="#/halls" className="link">All halls</a>}>
              {halls.data && halls.data.items.length === 0 && (
                <Empty title="No active halls yet">Add a temple, then add its annadhanam halls.</Empty>
              )}
              <ul className="hall-bars">
                {(halls.data?.items || []).map((h) => (
                  <li key={h.hall_code}>
                    <a href={href("halls", h.hall_code)}>
                      <span className="hb-name">{h.name}</span>
                      <span className="hb-temple">{h.temple_name}</span>
                    </a>
                    <SeatBar counts={h.seats} label={h.name} />
                    <span className="hb-figure">
                      {h.seats.capacity ? `${num(h.seats.occupied)} / ${num(h.seats.capacity)}` : "No seats"}
                    </span>
                  </li>
                ))}
              </ul>
            </Panel>

            <Panel title="Today's sessions" actions={<a href="#/calendar" className="link">Open calendar</a>}>
              {o.todays_sessions.length === 0 ? (
                <Empty title="No sessions scheduled today" action={<a className="btn btn-quiet" href="#/calendar">Schedule a session</a>} />
              ) : (
                <ol className="agenda">
                  {o.todays_sessions.map((s) => (
                    <li key={s.id} className={s.status === "CANCELLED" ? "is-cancelled" : ""}>
                      <span className="agenda-time">{hhmm(s.start_time)}<small>{hhmm(s.end_time)}</small></span>
                      <span className="agenda-body">
                        <strong>{s.name}</strong>
                        <span className="muted">{s.hall_name}, {s.temple_name}</span>
                        <span className="muted">
                          {num(s.expected_devotees)} expected
                          {s.actual_devotees !== null && `, ${num(s.actual_devotees)} served`}
                        </span>
                        {s.capacity_warning && <span className="warn-text">{s.capacity_warning}</span>}
                      </span>
                      <SessionBadge s={s} />
                    </li>
                  ))}
                </ol>
              )}
              <p className="muted small">
                {num(o.sessions.today_completed)} completed, {num(o.sessions.today_in_progress)} in progress,{" "}
                {num(o.sessions.today_cancelled)} cancelled today.
              </p>
            </Panel>
          </div>

          <Panel title="Staff attendance today" actions={<a href="#/staff?tab=attendance" className="link">Mark attendance</a>}>
            {!o.attendance.in_use ? (
              <Empty title="Attendance is not being recorded yet">
                Once supervisors mark the daily register, present, absent and leave counts appear here.
              </Empty>
            ) : (
              <div className="attendance-strip">
                <Stat label="Present" value={num(o.attendance.present)} tone="ok" />
                <Stat label="Half day" value={num(o.attendance.half_day)} />
                <Stat label="Absent" value={num(o.attendance.absent)} tone={o.attendance.absent ? "bad" : undefined} />
                <Stat label="On leave" value={num(o.attendance.on_leave)} />
                <Stat label="Not marked" value={num(o.attendance.not_marked)} note="Not counted as absent" />
              </div>
            )}
          </Panel>
          <p className="muted small foot-note">Figures are counted from the database and refresh every 15 seconds. Times are in {tz}.</p>
        </>
      )}
    </>
  );
}
