import { useState } from "react";
import SeatMap from "../components/SeatMap.jsx";
import { api } from "../lib/api.js";
import { href, navigate, useSession } from "../lib/app.js";
import { dateTime, duration, hhmm, isoDay, num, pct, shortDate, titleCase } from "../lib/format.js";
import { useApi, useDebounced, usePolling } from "../lib/hooks.js";
import {
  ActiveBadge, ConfirmDialog, DataTable, DemoBadge, Empty, ErrorState, Field, Loading, Modal,
  Occupancy, PageHeader, Pagination, Panel, SearchBox, SeatLegend, Select, SessionBadge, Stat, useAction,
} from "../ui.jsx";

const PAGE = 20;

export function HallForm({ open, hall, templeCode, onClose, onSaved }) {
  const editing = Boolean(hall);
  const temples = useApi(() => (open ? api.temples({ page_size: 200 }) : Promise.resolve(null)), [open]);
  const [form, setForm] = useState(null);
  const [errors, setErrors] = useState({});
  const [run, busy] = useAction();
  const values = form ?? {
    hall_code: hall?.hall_code ?? "", name: hall?.name ?? "", temple_code: hall?.temple_code ?? templeCode ?? "",
    building: hall?.building ?? "", floor: hall?.floor ?? "", location_note: hall?.location_note ?? "",
    active: hall?.active ?? true,
  };
  const set = (k) => (e) => setForm({ ...values, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  const close = () => { setForm(null); setErrors({}); onClose(); };

  const submit = async (e) => {
    e.preventDefault();
    const errs = {};
    if (!values.name.trim()) errs.name = "Enter the hall name.";
    if (!values.temple_code) errs.temple_code = "Choose the temple this hall belongs to.";
    if (!editing && !/^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(values.hall_code)) errs.hall_code = "Use letters, numbers, - or _.";
    setErrors(errs);
    if (Object.keys(errs).length) return;
    const body = { ...values, building: values.building || null, floor: values.floor || null,
                   location_note: values.location_note || null };
    let saved;
    const ok = await run(async () => {
      if (editing) {
        delete body.hall_code;
        saved = await api.updateHall(hall.hall_code, body);
      } else {
        saved = await api.createHall(body);
      }
    }, editing ? "Hall updated" : "Hall added");
    if (ok) { setForm(null); onSaved(saved); }
  };

  return (
    <Modal open={open} title={editing ? `Edit ${hall.name}` : "Add annadhanam hall"} onClose={close}
           footer={<>
             <button type="button" className="btn btn-quiet" onClick={close}>Cancel</button>
             <button type="submit" form="hall-form" className="btn btn-primary" disabled={busy}>
               {busy ? "Saving…" : editing ? "Save changes" : "Add hall"}
             </button>
           </>}>
      <form id="hall-form" className="form-grid" onSubmit={submit} noValidate>
        <Field label="Hall ID" hint={editing ? "The ID cannot be changed." : "Unique code, e.g. KMB-01-H1."} error={errors.hall_code}>
          {(id) => <input id={id} value={values.hall_code} onChange={set("hall_code")} disabled={editing} />}
        </Field>
        <Field label="Hall name" error={errors.name}>
          {(id) => <input id={id} value={values.name} onChange={set("name")} />}
        </Field>
        <Field label="Temple" error={errors.temple_code}>
          {(id) => (
            <select id={id} value={values.temple_code} onChange={set("temple_code")}>
              <option value="">Choose a temple</option>
              {(temples.data?.items || []).map((t) => <option key={t.temple_code} value={t.temple_code}>{t.name}</option>)}
            </select>
          )}
        </Field>
        <Field label="Building">{(id) => <input id={id} value={values.building} onChange={set("building")} />}</Field>
        <Field label="Floor">{(id) => <input id={id} value={values.floor} onChange={set("floor")} />}</Field>
        <Field label="Location note" wide>{(id) => <input id={id} value={values.location_note} onChange={set("location_note")} />}</Field>
        <label className="check"><input type="checkbox" checked={values.active} onChange={set("active")} /> Active</label>
      </form>
    </Modal>
  );
}

export default function Halls({ params }) {
  const { can } = useSession();
  const [temple, setTemple] = useState(params.get("temple") || "");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [adding, setAdding] = useState(false);
  const q = useDebounced(search);
  const temples = useApi(() => api.temples({ page_size: 200 }), []);
  const list = useApi(() => api.halls({ temple_code: temple, search: q, active: status || undefined, page, page_size: PAGE }),
                      [temple, q, status, page]);

  const columns = [
    { key: "hall_code", label: "Hall ID" },
    { key: "name", label: "Hall name", render: (h) => <><a href={href("halls", h.hall_code)} onClick={(e) => e.stopPropagation()}>{h.name}</a> <DemoBadge show={h.is_demo} /></> },
    { key: "temple", label: "Temple", render: (h) => h.temple_name },
    { key: "loc", label: "Location", render: (h) => [h.building, h.floor, h.location_note].filter(Boolean).join(", ") || "—" },
    { key: "cap", label: "Capacity", align: "right", render: (h) => num(h.seats.capacity) },
    { key: "occ", label: "Occupied", align: "right", render: (h) => num(h.seats.occupied) },
    { key: "avail", label: "Available", align: "right", render: (h) => num(h.seats.available) },
    { key: "res", label: "Reserved", align: "right", render: (h) => num(h.seats.reserved) },
    { key: "oos", label: "Out of service", align: "right", render: (h) => num(h.seats.out_of_service) },
    { key: "pct", label: "Occupancy", render: (h) => <Occupancy counts={h.seats} /> },
    { key: "active", label: "Status", render: (h) => <ActiveBadge active={h.active} /> },
  ];

  return (
    <>
      <PageHeader title="Annadhanam halls" subtitle="Seat figures come from each hall's seat list."
                  actions={can("admin") && <button type="button" className="btn btn-primary" onClick={() => setAdding(true)}>Add hall</button>} />
      <Panel>
        <div className="toolbar">
          <SearchBox value={search} onChange={(v) => { setSearch(v); setPage(1); }} placeholder="Search by hall name or ID" />
          <Select label="Temple" value={temple} onChange={(v) => { setTemple(v); setPage(1); }} includeAll="All temples"
                  options={(temples.data?.items || []).map((t) => ({ value: t.temple_code, label: t.name }))} />
          <Select label="Status" value={status} onChange={(v) => { setStatus(v); setPage(1); }} includeAll="Any status"
                  options={[{ value: "true", label: "Active" }, { value: "false", label: "Inactive" }]} />
        </div>
        <SeatLegend />
        <ErrorState error={list.error} onRetry={list.reload} />
        {list.loading && !list.data && <Loading />}
        {list.data && (
          <>
            <DataTable caption="Halls" columns={columns} rows={list.data.items} rowKey={(h) => h.hall_code}
                       onRowClick={(h) => navigate(href("halls", h.hall_code))}
                       empty={<Empty title={q || temple || status ? "No halls match these filters" : "No halls yet"}>
                         {q || temple || status ? "Clear the search or filters to see all halls." : "Halls are added to a temple. Add a temple first if there is none."}
                       </Empty>} />
            <Pagination page={page} pageSize={PAGE} total={list.data.total} onPage={setPage} />
          </>
        )}
      </Panel>
      <HallForm open={adding} templeCode={temple} onClose={() => setAdding(false)}
                onSaved={(h) => { setAdding(false); navigate(href("halls", h.hall_code)); }} />
    </>
  );
}

function LayoutForm({ open, hallCode, onClose, onDone }) {
  const [rows, setRows] = useState(5);
  const [cols, setCols] = useState(10);
  const [run, busy] = useAction();
  const total = Number(rows) * Number(cols);
  return (
    <Modal open={open} title="Add seats in a grid" onClose={onClose} size="sm"
           footer={<>
             <button type="button" className="btn btn-quiet" onClick={onClose}>Cancel</button>
             <button type="button" className="btn btn-primary" disabled={busy || !(total > 0 && total <= 2000)}
                     onClick={async () => {
                       let r;
                       const ok = await run(async () => { r = await api.generateSeats(hallCode, { rows: Number(rows), columns: Number(cols) }); });
                       if (ok) onDone(r);
                     }}>
               {busy ? "Adding…" : `Add ${total || 0} seats`}
             </button>
           </>}>
      <p className="muted">Seats are numbered S001, S002… row by row. Seat numbers that already exist are kept as they are.</p>
      <div className="form-grid">
        <Field label="Rows">{(id) => <input id={id} type="number" min={1} max={100} value={rows} onChange={(e) => setRows(e.target.value)} />}</Field>
        <Field label="Seats per row">{(id) => <input id={id} type="number" min={1} max={100} value={cols} onChange={(e) => setCols(e.target.value)} />}</Field>
      </div>
    </Modal>
  );
}

export function HallDetail({ code }) {
  const { can, tz } = useSession();
  const hall = usePolling(() => api.hall(code), 10000, [code]);
  const seats = usePolling(() => api.hallSeats(code), 10000, [code]);
  const history = useApi(() => api.seatHistory(code, { page_size: 10 }), [code]);
  const today = isoDay(new Date(), tz);
  const sessions = useApi(() => api.sessions({ hall_code: code, start_date: today, page_size: 6 }), [code]);
  const [editing, setEditing] = useState(false);
  const [layout, setLayout] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [run, busy] = useAction();
  const h = hall.data;

  const refresh = () => { hall.refresh(); seats.refresh(); history.reload(); };
  if (hall.error && !h) return <ErrorState error={hall.error} onRetry={hall.refresh} />;
  if (!h) return <Loading />;

  return (
    <>
      <PageHeader
        back={{ href: href("temples", h.temple_code), label: h.temple_name }}
        title={<>{h.name} <DemoBadge show={h.is_demo} /></>}
        subtitle={[h.hall_code, h.building, h.floor].filter(Boolean).join(", ")}
        actions={can("admin") && (
          <>
            <button type="button" className="btn btn-quiet" onClick={() => setConfirmDelete(true)}>Delete</button>
            <button type="button" className="btn btn-quiet" onClick={() => setEditing(true)}>Edit hall</button>
            <button type="button" className="btn btn-primary" onClick={() => setLayout(true)}>Add seats</button>
          </>
        )}
      />
      {!h.active && <p className="notice">This hall is inactive. Seat status cannot be changed and it is left out of temple totals.</p>}
      <div className="stats-row">
        <Stat label="Seat capacity" value={num(h.seats.capacity)} note={`${num(h.seats.installed)} installed`} />
        <Stat label="Occupied" value={num(h.seats.occupied)} tone="occupied" />
        <Stat label="Reserved" value={num(h.seats.reserved)} tone="reserved" />
        <Stat label="Available" value={num(h.seats.available)} tone="available" />
        <Stat label="Out of service" value={num(h.seats.out_of_service)} />
        <Stat label="Occupancy" value={h.seats.capacity ? pct(h.seats.occupancy_percentage) : "—"} />
      </div>
      <div className="grid-2 grid-wide-left">
        <Panel title="Seat map" actions={<SeatLegend />}>
          <ErrorState error={seats.error} onRetry={seats.refresh} />
          {seats.data ? <SeatMap hallCode={code} seats={seats.data} onChanged={refresh} /> : <Loading />}
          <p className="muted small">Colours show the status confirmed by hall staff. AI camera estimates never change a seat by themselves.</p>
        </Panel>
        <div className="stack">
          <Panel title="Sessions from today" actions={<a className="link" href={`#/calendar?hall=${encodeURIComponent(code)}`}>Calendar</a>}>
            {sessions.data?.items.length === 0 && <Empty title="Nothing scheduled from today" />}
            <ul className="mini-list">
              {(sessions.data?.items || []).map((s) => (
                <li key={s.id}>
                  <span>{shortDate(s.session_date)} {hhmm(s.start_time)}</span>
                  <span>{s.name}</span>
                  <SessionBadge s={s} />
                </li>
              ))}
            </ul>
          </Panel>
          <Panel title="Recent seat changes">
            {history.data?.items.length === 0 && <Empty title="No seat changes recorded yet" />}
            <ul className="mini-list">
              {(history.data?.items || []).map((e, i) => (
                <li key={i}>
                  <span>{dateTime(e.changed_at, tz)}</span>
                  <span>{e.seat_id}: {titleCase(e.from_status)} to {titleCase(e.to_status)}</span>
                  <span className="muted">
                    {e.from_status === "OCCUPIED" && e.previous_duration_seconds !== null ? duration(e.previous_duration_seconds) : e.actor_role || ""}
                  </span>
                </li>
              ))}
            </ul>
          </Panel>
        </div>
      </div>
      <HallForm open={editing} hall={h} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); refresh(); }} />
      <LayoutForm open={layout} hallCode={code} onClose={() => setLayout(false)}
                  onDone={() => { setLayout(false); refresh(); }} />
      <ConfirmDialog open={confirmDelete} danger busy={busy} title={`Delete ${h.name}?`}
                     message={h.seats.installed ? "A hall with seats or sessions cannot be deleted. Mark it inactive instead."
                                                : "This removes the hall permanently."}
                     confirmLabel="Delete hall" onClose={() => setConfirmDelete(false)}
                     onConfirm={async () => {
                       if (await run(() => api.deleteHall(code), "Hall deleted")) navigate(href("temples", h.temple_code));
                       else setConfirmDelete(false);
                     }} />
    </>
  );
}
