import { useState } from "react";
import { api } from "../lib/api.js";
import { useSession } from "../lib/app.js";
import { addDays, isoDay, num } from "../lib/format.js";
import { useApi } from "../lib/hooks.js";
import { DataTable, Empty, ErrorState, Loading, PageHeader, Panel, Select, useAction } from "../ui.jsx";

const REPORTS = [
  { id: "temple-capacity", label: "Temple seat capacity", dated: false,
    about: "Seats per temple and hall. Capacity excludes seats that are out of service." },
  { id: "hall-occupancy", label: "Hall occupancy now", dated: false,
    about: "Current confirmed seat status per hall." },
  { id: "sessions", label: "Session schedule", dated: true,
    about: "Sessions in the period, with expected and actual devotees where actual numbers were recorded." },
  { id: "staff-attendance", label: "Staff attendance", dated: true, role: "operator",
    about: "Days present, absent, on leave and not marked per active staff member." },
  { id: "seat-occupancy", label: "Seat occupancy history", dated: true,
    about: "Per day and hall: seats taken, seats freed and total occupied seat-minutes, from the seat change history." },
];

const LABELS = {
  temple_code: "Temple", temple_name: "Temple name", district: "District", temple_active: "Temple active",
  hall_code: "Hall", hall_name: "Hall name", hall_active: "Hall active", installed_seats: "Installed",
  seat_capacity: "Capacity", out_of_service: "Out of service", capacity: "Capacity", occupied: "Occupied",
  reserved: "Reserved", available: "Available", occupancy_percentage: "Occupancy %", as_of: "As of",
  session_id: "Session", date: "Date", start_time: "Start", end_time: "End", session_name: "Name",
  meal_type: "Meal", status: "Status", expected_devotees: "Expected", actual_devotees: "Actual",
  difference: "Difference", hall_capacity: "Hall seats", responsible_staff: "Responsible",
  staff_code: "Staff ID", staff_name: "Name", designation: "Role", present: "Present", half_day: "Half day",
  absent: "Absent", leave: "Leave", not_marked: "Not marked", days_in_range: "Days",
  occupations_started: "Seats taken", occupations_ended: "Seats freed",
  occupied_seat_minutes: "Occupied seat-minutes", average_minutes_per_occupation: "Avg minutes per seating",
};

function cell(v) {
  if (v === null || v === undefined) return <span className="muted">Not recorded</span>;
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "number") return num(v, Number.isInteger(v) ? 0 : 1);
  return String(v);
}

export default function Reports() {
  const { can, tz } = useSession();
  const today = isoDay(new Date(), tz);
  const available = REPORTS.filter((r) => !r.role || can(r.role));
  const [id, setId] = useState(available[0].id);
  const [start, setStart] = useState(addDays(today, -6));
  const [end, setEnd] = useState(today);
  const [temple, setTemple] = useState("");
  const [actualOnly, setActualOnly] = useState(false);
  const [run, busy] = useAction();
  const report = REPORTS.find((r) => r.id === id);
  const temples = useApi(() => api.temples({ page_size: 200 }), []);
  const params = {
    ...(report.dated ? { start_date: start, end_date: end } : {}),
    ...(id !== "seat-occupancy" && temple ? { temple_code: temple } : {}),
    ...(id === "sessions" && actualOnly ? { actual_recorded_only: true } : {}),
  };
  const data = useApi(() => api.report(id, params), [id, start, end, temple, actualOnly]);

  const download = () => run(async () => {
    const blob = await api.reportCsv(id, params);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${id}${report.dated ? `_${start}_${end}` : `_${today}`}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }, "CSV downloaded");

  const totals = id === "sessions" && data.data
    ? data.data.rows.reduce((t, r) => ({
        expected: t.expected + r.expected_devotees,
        actual: t.actual + (r.actual_devotees ?? 0),
        recorded: t.recorded + (r.actual_devotees !== null ? 1 : 0),
      }), { expected: 0, actual: 0, recorded: 0 })
    : null;

  return (
    <>
      <PageHeader title="Reports" subtitle="Built from stored records only. Download any report as CSV." />
      <div className="tabs" role="tablist">
        {available.map((r) => (
          <button key={r.id} type="button" role="tab" aria-selected={id === r.id} className={id === r.id ? "on" : ""}
                  onClick={() => setId(r.id)}>{r.label}</button>
        ))}
      </div>
      <Panel title={report.label}
             actions={<button type="button" className="btn btn-quiet" onClick={download} disabled={busy || !data.data?.rows.length}>Download CSV</button>}>
        <p className="muted">{report.about}</p>
        <div className="toolbar">
          {report.dated && (
            <>
              <div className="filter"><label htmlFor="r-start">From</label>
                <input id="r-start" type="date" value={start} max={end} onChange={(e) => e.target.value && setStart(e.target.value)} /></div>
              <div className="filter"><label htmlFor="r-end">To</label>
                <input id="r-end" type="date" value={end} min={start} onChange={(e) => e.target.value && setEnd(e.target.value)} /></div>
              <div className="quick" role="group" aria-label="Quick ranges">
                <button type="button" className="btn btn-quiet" onClick={() => { setStart(today); setEnd(today); }}>Today</button>
                <button type="button" className="btn btn-quiet" onClick={() => { setStart(addDays(today, -6)); setEnd(today); }}>Last 7 days</button>
                <button type="button" className="btn btn-quiet" onClick={() => { setStart(addDays(today, -29)); setEnd(today); }}>Last 30 days</button>
              </div>
            </>
          )}
          {id !== "seat-occupancy" && (
            <Select label="Temple" value={temple} onChange={setTemple} includeAll="All temples"
                    options={(temples.data?.items || []).map((t) => ({ value: t.temple_code, label: t.name }))} />
          )}
          {id === "sessions" && (
            <label className="check"><input type="checkbox" checked={actualOnly} onChange={(e) => setActualOnly(e.target.checked)} /> Only sessions with actual numbers</label>
          )}
        </div>
        <ErrorState error={data.error} onRetry={data.reload} />
        {data.loading && !data.data && <Loading />}
        {totals && totals.recorded > 0 && (
          <p className="notice">
            {totals.recorded} of {data.data.rows.length} sessions have actual numbers recorded.
          </p>
        )}
        {data.data && (
          <DataTable caption={report.label} rows={data.data.rows} rowKey={(r) => JSON.stringify(r)}
                     empty={<Empty title="No records for this selection">Try a wider date range or another temple.</Empty>}
                     columns={data.data.columns.map((c) => ({
                       key: c, label: LABELS[c] || c,
                       align: typeof data.data.rows[0]?.[c] === "number" ? "right" : undefined,
                       render: (r) => cell(r[c]),
                     }))} />
        )}
      </Panel>
    </>
  );
}
