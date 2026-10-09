import { useMemo, useState } from "react";
import { api } from "../lib/api.js";
import { useSession } from "../lib/app.js";
import {
  addDays, addMonths, hhmm, isoDay, longDate, monthLabel, num, shortDate, startOfMonth,
  startOfWeek, titleCase,
} from "../lib/format.js";
import { useApi } from "../lib/hooks.js";
import { Badge, SessionBadge, Empty, ErrorState, Field, Loading, Modal, PageHeader, Panel, Select, useAction } from "../ui.jsx";

const MEALS = ["BREAKFAST", "LUNCH", "DINNER", "PRASADAM", "SPECIAL"];
const STATUS_TONE = { SCHEDULED: "info", IN_PROGRESS: "ok", COMPLETED: "neutral", CANCELLED: "bad" };
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function SessionForm({ open, session, defaults, halls, onClose, onSaved }) {
  const { can } = useSession();
  const editing = Boolean(session);
  const staff = useApi(() => (open ? api.staff({ active: true, page_size: 200 }).catch(() => null) : Promise.resolve(null)), [open]);
  const [form, setForm] = useState(null);
  const [errors, setErrors] = useState({});
  const [cancelReason, setCancelReason] = useState("");
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [run, busy] = useAction();
  const locked = session && ["CANCELLED", "COMPLETED"].includes(session.status);
  const values = form ?? {
    name: session?.name ?? "Annadhanam", meal_type: session?.meal_type ?? "LUNCH",
    hall_code: session?.hall_code ?? defaults.hall ?? "", session_date: session?.session_date ?? defaults.date,
    start_time: hhmm(session?.start_time ?? "11:30"), end_time: hhmm(session?.end_time ?? "14:00"),
    expected_devotees: session?.expected_devotees ?? 100, actual_devotees: session?.actual_devotees ?? "",
    responsible_staff_code: session?.responsible_staff_code ?? "", notes: session?.notes ?? "",
    status: session?.status ?? "SCHEDULED",
  };
  const set = (k) => (e) => setForm({ ...values, [k]: e.target.value });
  const close = () => { setForm(null); setErrors({}); setCancelReason(""); setConfirmCancel(false); onClose(); };
  const hall = halls.find((h) => h.hall_code === values.hall_code);
  const capacity = hall?.seats.capacity ?? null;
  const over = capacity !== null && Number(values.expected_devotees) > capacity;

  const submit = async (e) => {
    e.preventDefault();
    const errs = {};
    if (!values.name.trim()) errs.name = "Enter a session name.";
    if (!values.hall_code) errs.hall_code = "Choose a hall.";
    if (!values.session_date) errs.session_date = "Choose a date.";
    if (values.end_time <= values.start_time) errs.end_time = "End time must be after the start time.";
    if (values.expected_devotees === "" || Number(values.expected_devotees) < 0) errs.expected_devotees = "Enter 0 or more.";
    setErrors(errs);
    if (Object.keys(errs).length) return;
    const body = {
      name: values.name.trim(), meal_type: values.meal_type, hall_code: values.hall_code,
      session_date: values.session_date, start_time: values.start_time, end_time: values.end_time,
      expected_devotees: Number(values.expected_devotees),
      responsible_staff_code: values.responsible_staff_code || null, notes: values.notes || null,
    };
    let saved;
    const ok = await run(async () => {
      if (!editing) saved = await api.createSession(body);
      else if (session.status === "COMPLETED") {
        saved = await api.updateSession(session.id, {
          actual_devotees: values.actual_devotees === "" ? null : Number(values.actual_devotees), notes: body.notes });
      } else {
        saved = await api.updateSession(session.id, {
          ...body, status: values.status,
          actual_devotees: values.actual_devotees === "" ? null : Number(values.actual_devotees),
        });
      }
    }, editing ? "Session saved" : "Session scheduled");
    if (ok) { setForm(null); onSaved(saved); }
  };

  const cancelSession = async () => {
    const ok = await run(() => api.cancelSession(session.id, cancelReason.trim()), "Session cancelled");
    if (ok) { close(); onSaved(); }
  };

  const readOnly = !can("operator") || session?.status === "CANCELLED";
  const timesLocked = readOnly || session?.status === "COMPLETED";

  return (
    <Modal open={open} size="lg" onClose={close}
           title={editing ? `${session.name}, ${shortDate(session.session_date)}` : "Schedule a session"}
           footer={!readOnly && (
             <>
               {editing && !locked && (
                 <button type="button" className="btn btn-danger-quiet" onClick={() => setConfirmCancel(true)}>Cancel session</button>
               )}
               <span className="spacer" />
               <button type="button" className="btn btn-quiet" onClick={close}>Close</button>
               <button type="submit" form="session-form" className="btn btn-primary" disabled={busy}>
                 {busy ? "Saving…" : editing ? "Save changes" : "Schedule session"}
               </button>
             </>
           )}>
      {session?.status === "CANCELLED" && <p className="notice">Cancelled: {session.cancel_reason}</p>}
      {confirmCancel ? (
        <div className="confirm-inline">
          <Field label="Reason for cancelling" hint="Shown on the calendar and in reports.">
            {(id) => <input id={id} value={cancelReason} onChange={(e) => setCancelReason(e.target.value)} autoFocus />}
          </Field>
          <div className="seat-actions">
            <button type="button" className="btn btn-quiet" onClick={() => setConfirmCancel(false)}>Keep the session</button>
            <button type="button" className="btn btn-danger" disabled={busy || cancelReason.trim().length < 3} onClick={cancelSession}>Cancel session</button>
          </div>
        </div>
      ) : (
        <form id="session-form" className="form-grid" onSubmit={submit} noValidate>
          <Field label="Session name" error={errors.name}>
            {(id) => <input id={id} value={values.name} onChange={set("name")} disabled={timesLocked} />}
          </Field>
          <Field label="Meal type">
            {(id) => (
              <select id={id} value={values.meal_type} onChange={set("meal_type")} disabled={timesLocked}>
                {MEALS.map((m) => <option key={m} value={m}>{titleCase(m)}</option>)}
              </select>
            )}
          </Field>
          <Field label="Hall" error={errors.hall_code} wide>
            {(id) => (
              <select id={id} value={values.hall_code} onChange={set("hall_code")} disabled={timesLocked}>
                <option value="">Choose a hall</option>
                {halls.map((h) => <option key={h.hall_code} value={h.hall_code}>{h.name}, {h.temple_name}</option>)}
              </select>
            )}
          </Field>
          <Field label="Date" error={errors.session_date}>
            {(id) => <input id={id} type="date" value={values.session_date} onChange={set("session_date")} disabled={timesLocked} />}
          </Field>
          <div className="field-pair">
            <Field label="Start (IST)">{(id) => <input id={id} type="time" value={values.start_time} onChange={set("start_time")} disabled={timesLocked} />}</Field>
            <Field label="End (IST)" error={errors.end_time}>{(id) => <input id={id} type="time" value={values.end_time} onChange={set("end_time")} disabled={timesLocked} />}</Field>
          </div>
          <Field label="Expected devotees" error={errors.expected_devotees}
                 hint={capacity !== null ? `Hall seats ${num(capacity)} at a time.` : undefined}>
            {(id) => <input id={id} type="number" min={0} value={values.expected_devotees} onChange={set("expected_devotees")} disabled={timesLocked} />}
          </Field>
          <Field label="Responsible staff">
            {(id) => (
              <select id={id} value={values.responsible_staff_code} onChange={set("responsible_staff_code")} disabled={timesLocked}>
                <option value="">Not assigned</option>
                {(staff.data?.items || []).map((s) => <option key={s.staff_code} value={s.staff_code}>{s.staff_name}{s.designation ? `, ${s.designation}` : ""}</option>)}
              </select>
            )}
          </Field>
          {over && !timesLocked && (
            <p className="notice notice-warn field-wide">
              Expected attendance is more than the hall seats ({num(capacity)}). Plan about {Math.ceil(Number(values.expected_devotees) / capacity)} sittings.
            </p>
          )}
          {editing && !locked && (
            <Field label="Status">
              {(id) => (
                <select id={id} value={values.status} onChange={set("status")}>
                  {["SCHEDULED", "IN_PROGRESS", "COMPLETED"].map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
                </select>
              )}
            </Field>
          )}
          {editing && session.status !== "CANCELLED" && (
            <Field label="Devotees actually served" hint="Leave empty until counted. Empty is shown as not recorded, never as zero.">
              {(id) => <input id={id} type="number" min={0} value={values.actual_devotees} onChange={set("actual_devotees")} disabled={readOnly} />}
            </Field>
          )}
          <Field label="Notes" wide>
            {(id) => <textarea id={id} rows={2} value={values.notes} onChange={set("notes")} disabled={readOnly} />}
          </Field>
        </form>
      )}
    </Modal>
  );
}

function SessionChip({ s, onOpen }) {
  return (
    <button type="button" className={`chip chip-${s.status.toLowerCase()}`} onClick={() => onOpen(s)}
            title={`${s.name}, ${s.hall_name}, ${hhmm(s.start_time)}–${hhmm(s.end_time)}, ${titleCase(s.status)}`}>
      <span className="chip-time">{hhmm(s.start_time)}</span> {s.name}
    </button>
  );
}

export default function Calendar({ params }) {
  const { can, tz } = useSession();
  const today = isoDay(new Date(), tz);
  const [view, setView] = useState("month");
  const [cursor, setCursor] = useState(today);
  const [temple, setTemple] = useState("");
  const [hall, setHall] = useState(params.get("hall") || "");
  const [open, setOpen] = useState(null);           // null | {session} | {new: date}
  const temples = useApi(() => api.temples({ page_size: 200, active: true }), []);
  const halls = useApi(() => api.halls({ temple_code: temple, active: true, page_size: 200 }), [temple]);
  const formHalls = useApi(() => api.halls({ active: true, page_size: 200 }), []);

  const range = useMemo(() => {
    if (view === "day") return [cursor, cursor];
    if (view === "week") { const s = startOfWeek(cursor); return [s, addDays(s, 6)]; }
    const s = startOfWeek(startOfMonth(cursor));
    return [s, addDays(s, 41)];
  }, [view, cursor]);

  const sessions = useApi(() => api.sessions({ start_date: range[0], end_date: range[1], temple_code: temple,
                                               hall_code: hall, page_size: 200 }), [range[0], range[1], temple, hall]);
  const upcoming = useApi(() => api.sessions({ timing: "upcoming", temple_code: temple, hall_code: hall, page_size: 8 }), [temple, hall]);
  const byDay = useMemo(() => {
    const m = new Map();
    for (const s of sessions.data?.items || []) {
      if (!m.has(s.session_date)) m.set(s.session_date, []);
      m.get(s.session_date).push(s);
    }
    return m;
  }, [sessions.data]);

  const step = (n) => setCursor(view === "month" ? addMonths(cursor, n) : addDays(cursor, view === "week" ? 7 * n : n));
  const title = view === "month" ? monthLabel(cursor)
    : view === "week" ? `${shortDate(range[0])} – ${shortDate(range[1])}` : longDate(cursor);
  const reload = () => { sessions.reload(); upcoming.reload(); };
  const allHalls = halls.data?.items || [];

  return (
    <>
      <PageHeader title="Calendar & sessions" subtitle="Annadhanam sessions per hall. Times are India Standard Time."
                  actions={can("operator") && <button type="button" className="btn btn-primary" onClick={() => setOpen({ new: cursor })}>Schedule session</button>} />
      <Panel>
        <div className="toolbar cal-toolbar">
          <div className="cal-nav">
            <button type="button" className="btn btn-quiet" onClick={() => step(-1)} aria-label="Previous">‹</button>
            <button type="button" className="btn btn-quiet" onClick={() => setCursor(today)}>Today</button>
            <button type="button" className="btn btn-quiet" onClick={() => step(1)} aria-label="Next">›</button>
            <h2 className="cal-title" aria-live="polite">{title}</h2>
          </div>
          <div className="seg" role="group" aria-label="Calendar view">
            {["month", "week", "day"].map((v) => (
              <button key={v} type="button" className={view === v ? "on" : ""} aria-pressed={view === v} onClick={() => setView(v)}>{titleCase(v)}</button>
            ))}
          </div>
          <Select label="Temple" value={temple} onChange={(v) => { setTemple(v); setHall(""); }} includeAll="All temples"
                  options={(temples.data?.items || []).map((t) => ({ value: t.temple_code, label: t.name }))} />
          <Select label="Hall" value={hall} onChange={setHall} includeAll="All halls"
                  options={allHalls.map((h) => ({ value: h.hall_code, label: h.name }))} />
          <div className="filter">
            <label htmlFor="cal-date">Date</label>
            <input id="cal-date" type="date" value={cursor} onChange={(e) => e.target.value && setCursor(e.target.value)} />
          </div>
        </div>
        <ErrorState error={sessions.error} onRetry={sessions.reload} />
        {sessions.loading && !sessions.data && <Loading />}

        {view === "month" && (
          <div className="month" role="grid" aria-label={title}>
            {WEEKDAYS.map((d) => <div key={d} className="month-head" role="columnheader">{d}</div>)}
            {Array.from({ length: 42 }, (_, i) => addDays(range[0], i)).map((day) => {
              const list = byDay.get(day) || [];
              const other = day.slice(0, 7) !== cursor.slice(0, 7);
              return (
                <div key={day} role="gridcell" className={`month-cell ${other ? "other" : ""} ${day === today ? "today" : ""}`}>
                  <button type="button" className="day-num" onClick={() => { setCursor(day); setView("day"); }}
                          aria-label={longDate(day)}>{Number(day.slice(8))}</button>
                  {list.slice(0, 3).map((s) => <SessionChip key={s.id} s={s} onOpen={(x) => setOpen({ session: x })} />)}
                  {list.length > 3 && (
                    <button type="button" className="more" onClick={() => { setCursor(day); setView("day"); }}>+{list.length - 3} more</button>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {view === "week" && (
          <div className="week">
            {Array.from({ length: 7 }, (_, i) => addDays(range[0], i)).map((day) => (
              <div key={day} className={`week-col ${day === today ? "today" : ""}`}>
                <button type="button" className="week-head" onClick={() => { setCursor(day); setView("day"); }}>
                  {WEEKDAYS[i(day, range[0])]} <strong>{Number(day.slice(8))}</strong>
                </button>
                {(byDay.get(day) || []).map((s) => <SessionChip key={s.id} s={s} onOpen={(x) => setOpen({ session: x })} />)}
                {can("operator") && <button type="button" className="add-slot" onClick={() => setOpen({ new: day })}>Add</button>}
              </div>
            ))}
          </div>
        )}

        {view === "day" && (
          (byDay.get(cursor) || []).length === 0 ? (
            <Empty title={`Nothing scheduled on ${shortDate(cursor)}`}
                   action={can("operator") && <button type="button" className="btn btn-primary" onClick={() => setOpen({ new: cursor })}>Schedule session</button>} />
          ) : (
            <ol className="agenda">
              {(byDay.get(cursor) || []).map((s) => (
                <li key={s.id} className={s.status === "CANCELLED" ? "is-cancelled" : ""}>
                  <span className="agenda-time">{hhmm(s.start_time)}<small>{hhmm(s.end_time)}</small></span>
                  <span className="agenda-body">
                    <button type="button" className="link strong" onClick={() => setOpen({ session: s })}>{s.name}</button>
                    <span className="muted">{titleCase(s.meal_type)} in {s.hall_name}, {s.temple_name}</span>
                    <span className="muted">
                      {num(s.expected_devotees)} expected, hall seats {num(s.hall_capacity)}
                      {s.actual_devotees !== null ? `, ${num(s.actual_devotees)} served` : ""}
                      {s.responsible_staff_name ? `, led by ${s.responsible_staff_name}` : ""}
                    </span>
                    {s.capacity_warning && <span className="warn-text">{s.capacity_warning}</span>}
                    {s.cancel_reason && <span className="warn-text">Cancelled: {s.cancel_reason}</span>}
                  </span>
                  <SessionBadge s={s} />
                </li>
              ))}
            </ol>
          )
        )}
        <ul className="cal-legend" aria-label="Status colours">
          {Object.keys(STATUS_TONE).map((k) => <li key={k}><i className={`chip-dot chip-${k.toLowerCase()}`} /> {titleCase(k)}</li>)}
        </ul>
      </Panel>

      <Panel title="Upcoming sessions">
        {upcoming.data?.items.length === 0 && <Empty title="No upcoming sessions" />}
        <ul className="mini-list">
          {(upcoming.data?.items || []).map((s) => (
            <li key={s.id}>
              <span>{shortDate(s.session_date)} {hhmm(s.start_time)}</span>
              <button type="button" className="link" onClick={() => setOpen({ session: s })}>{s.name}</button>
              <span className="muted">{s.hall_name}</span>
            </li>
          ))}
        </ul>
      </Panel>

      <SessionForm open={open !== null} session={open?.session || null}
                   defaults={{ date: open?.new || cursor, hall }} halls={formHalls.data?.items || []}
                   onClose={() => setOpen(null)} onSaved={() => { setOpen(null); reload(); }} />
    </>
  );
}

function i(day, start) {
  return Math.round((new Date(`${day}T12:00:00Z`) - new Date(`${start}T12:00:00Z`)) / 86400000);
}

