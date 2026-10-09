import { useEffect, useState } from "react";
import SeatMap from "../components/SeatMap.jsx";
import { api } from "../lib/api.js";
import { href, useSession } from "../lib/app.js";
import { dateTime, num, pct } from "../lib/format.js";
import { useApi, usePolling } from "../lib/hooks.js";
import { DataTable, Empty, ErrorState, Loading, PageHeader, Panel, SEAT_TONES, SeatBar, SeatLegend, Select } from "../ui.jsx";

export default function Seats({ params }) {
  const { tz } = useSession();
  const [temple, setTemple] = useState("");
  const [hall, setHall] = useState(params.get("hall") || "");
  const [view, setView] = useState("map");
  const [status, setStatus] = useState("");
  const temples = useApi(() => api.temples({ page_size: 200, active: true }), []);
  const halls = useApi(() => api.halls({ temple_code: temple, active: true, page_size: 200 }), [temple]);

  useEffect(() => {
    // Pick the first hall automatically so the page is never empty without reason.
    const items = halls.data?.items || [];
    if (items.length && !items.some((h) => h.hall_code === hall)) setHall(items[0].hall_code);
  }, [halls.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const seats = usePolling(() => (hall ? api.hallSeats(hall) : Promise.resolve(null)), 8000, [hall]);
  const summary = usePolling(() => (hall ? api.hall(hall) : Promise.resolve(null)), 8000, [hall]);
  const s = summary.data;
  const reload = () => { seats.refresh(); summary.refresh(); };
  const rows = (seats.data || []).filter((x) => !status || x.status === status);

  return (
    <>
      <PageHeader title="Seat management" subtitle="Mark seats occupied, available or reserved as devotees are seated."
                  actions={
                    <>
                      <Select label="Temple" value={temple} onChange={(v) => { setTemple(v); setHall(""); }} includeAll="All temples"
                              options={(temples.data?.items || []).map((t) => ({ value: t.temple_code, label: t.name }))} />
                      <Select label="Hall" value={hall} onChange={setHall} includeAll={null}
                              options={(halls.data?.items || []).map((h) => ({ value: h.hall_code, label: `${h.name} (${h.temple_name})` }))} />
                    </>
                  } />
      {halls.data && halls.data.items.length === 0 && (
        <Empty title="No active halls" action={<a className="btn btn-quiet" href="#/halls">Go to halls</a>}>
          Seats belong to a hall. Add or activate a hall first.
        </Empty>
      )}
      {hall && s && (
        <Panel className="capacity">
          <div className="capacity-head">
            <div>
              <h2><a href={href("halls", hall)}>{s.name}</a></h2>
              <p className="muted">{s.temple_name}</p>
            </div>
            <p className="capacity-figure">
              <strong>{num(s.seats.available)}</strong>
              <span>seats free of {num(s.seats.capacity)}</span>
              <span className="capacity-pct">{s.seats.capacity ? `${pct(s.seats.occupancy_percentage)} occupied` : ""}</span>
            </p>
          </div>
          <SeatBar counts={s.seats} size="lg" label={s.name} />
          <SeatLegend counts={s.seats} />
        </Panel>
      )}
      {hall && (
        <Panel title={view === "map" ? "Seat map" : "Seat list"}
               actions={
                 <div className="seg" role="group" aria-label="View">
                   <button type="button" className={view === "map" ? "on" : ""} aria-pressed={view === "map"} onClick={() => setView("map")}>Map</button>
                   <button type="button" className={view === "list" ? "on" : ""} aria-pressed={view === "list"} onClick={() => setView("list")}>List</button>
                 </div>
               }>
          <ErrorState error={seats.error} onRetry={seats.refresh} />
          {!seats.data && <Loading />}
          {seats.data && view === "map" && <SeatMap hallCode={hall} seats={seats.data} onChanged={reload} />}
          {seats.data && view === "list" && (
            <>
              <div className="toolbar">
                <Select label="Status" value={status} onChange={setStatus} includeAll="Any status"
                        options={Object.entries(SEAT_TONES).map(([k, v]) => ({ value: k, label: v.label }))} />
              </div>
              <DataTable caption="Seats" rows={rows} rowKey={(x) => x.seat_id}
                         empty={<Empty title="No seats with this status" />}
                         columns={[
                           { key: "seat_id", label: "Seat" },
                           { key: "seat_label", label: "Label", render: (x) => x.seat_label || "—" },
                           { key: "status", label: "Status", render: (x) => (
                             <span className="status-cell"><i className={`swatch ${SEAT_TONES[x.status].cls}`} aria-hidden="true" />{SEAT_TONES[x.status].label}</span>) },
                           { key: "since", label: "Occupied since", render: (x) => (x.occupied_since ? dateTime(x.occupied_since, tz) : "—") },
                           { key: "ref", label: "Reservation", render: (x) => x.reservation_ref || (x.reserved_session_id ? `Session ${x.reserved_session_id}` : "—") },
                           { key: "src", label: "Source", render: (x) => (x.status_source === "AI_CONFIRMED" ? "AI suggestion, confirmed" : "Staff") },
                           { key: "upd", label: "Last updated", render: (x) => dateTime(x.updated_at, tz) },
                         ]} />
              <p className="muted small">Select a seat in the map view to change it.</p>
            </>
          )}
        </Panel>
      )}
    </>
  );
}
