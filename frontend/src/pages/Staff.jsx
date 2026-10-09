import { useEffect, useState } from "react";
import { api } from "../lib/api.js";
import { navigate, useSession } from "../lib/app.js";
import { isoDay, longDate, num, titleCase } from "../lib/format.js";
import { useApi, useDebounced } from "../lib/hooks.js";
import {
  ActiveBadge, Badge, DataTable, DemoBadge, Empty, ErrorState, Field, Loading, Modal, PageHeader,
  Pagination, Panel, SearchBox, Select, Stat, useAction,
} from "../ui.jsx";

const PAGE = 20;
const SHIFTS = ["MORNING", "AFTERNOON", "EVENING", "NIGHT", "FULL_DAY"];
const ATT = {
  PRESENT: { label: "Present", tone: "ok" },
  HALF_DAY: { label: "Half day", tone: "info" },
  ABSENT: { label: "Absent", tone: "bad" },
  LEAVE: { label: "On leave", tone: "neutral" },
};

function StaffForm({ open, member, onClose, onSaved }) {
  const editing = Boolean(member);
  const temples = useApi(() => (open ? api.temples({ page_size: 200 }) : Promise.resolve(null)), [open]);
  const [form, setForm] = useState(null);
  const values = form ?? {
    staff_name: member?.staff_name ?? "", employee_code: member?.employee_code ?? "",
    designation: member?.designation ?? "", department: member?.department ?? "",
    shift: member?.shift ?? "", phone: member?.phone ?? "", temple_code: member?.temple_code ?? "",
    hall_code: member?.hall_code ?? "", active: member?.active ?? true,
  };
  const halls = useApi(() => (open && values.temple_code ? api.halls({ temple_code: values.temple_code, page_size: 200 })
                                                         : Promise.resolve(null)), [open, values.temple_code]);
  const [errors, setErrors] = useState({});
  const [run, busy] = useAction();
  const set = (k) => (e) => {
    const v = e.target.type === "checkbox" ? e.target.checked : e.target.value;
    setForm({ ...values, [k]: v, ...(k === "temple_code" ? { hall_code: "" } : {}) });
  };
  const close = () => { setForm(null); setErrors({}); onClose(); };

  const submit = async (e) => {
    e.preventDefault();
    const errs = {};
    if (!values.staff_name.trim()) errs.staff_name = "Enter the name.";
    if (values.phone && !/^\+?[0-9][0-9 -]{5,19}$/.test(values.phone.trim())) errs.phone = "Use digits, spaces or dashes, 6–20 characters.";
    setErrors(errs);
    if (Object.keys(errs).length) return;
    const body = Object.fromEntries(Object.entries(values).map(([k, v]) => [k, v === "" ? null : v]));
    let saved;
    const ok = await run(async () => {
      saved = editing ? await api.updateStaff(member.staff_code, body) : await api.createStaff(body);
    }, editing ? "Staff details saved" : "Staff member registered");
    if (ok) { setForm(null); onSaved(saved); }
  };

  return (
    <Modal open={open} title={editing ? `Edit ${member.staff_name}` : "Register staff member"} onClose={close}
           footer={<>
             <button type="button" className="btn btn-quiet" onClick={close}>Cancel</button>
             <button type="submit" form="staff-form" className="btn btn-primary" disabled={busy}>
               {busy ? "Saving…" : editing ? "Save changes" : "Register"}
             </button>
           </>}>
      <form id="staff-form" className="form-grid" onSubmit={submit} noValidate>
        <Field label="Full name" error={errors.staff_name}>{(id) => <input id={id} value={values.staff_name} onChange={set("staff_name")} />}</Field>
        <Field label="Role / designation" hint="For example Cook, Server, Supervisor.">{(id) => <input id={id} value={values.designation} onChange={set("designation")} />}</Field>
        <Field label="Contact number" error={errors.phone} hint="Only administrators can see this.">
          {(id) => <input id={id} type="tel" value={values.phone} onChange={set("phone")} />}
        </Field>
        <Field label="Employee code" hint="Optional payroll reference.">{(id) => <input id={id} value={values.employee_code} onChange={set("employee_code")} />}</Field>
        <Field label="Assigned temple">
          {(id) => (
            <select id={id} value={values.temple_code} onChange={set("temple_code")}>
              <option value="">Not assigned</option>
              {(temples.data?.items || []).map((t) => <option key={t.temple_code} value={t.temple_code}>{t.name}</option>)}
            </select>
          )}
        </Field>
        <Field label="Assigned hall" hint={values.temple_code ? undefined : "Choose a temple first."}>
          {(id) => (
            <select id={id} value={values.hall_code} onChange={set("hall_code")} disabled={!values.temple_code}>
              <option value="">Any hall</option>
              {(halls.data?.items || []).map((h) => <option key={h.hall_code} value={h.hall_code}>{h.name}</option>)}
            </select>
          )}
        </Field>
        <Field label="Shift">
          {(id) => (
            <select id={id} value={values.shift} onChange={set("shift")}>
              <option value="">Not set</option>
              {SHIFTS.map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
            </select>
          )}
        </Field>
        <Field label="Department">{(id) => <input id={id} value={values.department} onChange={set("department")} />}</Field>
        <label className="check"><input type="checkbox" checked={values.active} onChange={set("active")} /> Currently employed</label>
      </form>
    </Modal>
  );
}

function StaffList() {
  const { can } = useSession();
  const [search, setSearch] = useState("");
  const [temple, setTemple] = useState("");
  const [shift, setShift] = useState("");
  const [status, setStatus] = useState("true");
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState(null);
  const q = useDebounced(search);
  const temples = useApi(() => api.temples({ page_size: 200 }), []);
  const list = useApi(() => api.staff({ search: q, temple_code: temple, shift, active: status || undefined, page, page_size: PAGE }),
                      [q, temple, shift, status, page]);
  const reset = (fn) => (v) => { fn(v); setPage(1); };

  return (
    <Panel title="Staff" actions={can("admin") && <button type="button" className="btn btn-primary" onClick={() => setEditing("new")}>Register staff</button>}>
      <div className="toolbar">
        <SearchBox value={search} onChange={reset(setSearch)} placeholder="Search by name, ID or role" />
        <Select label="Temple" value={temple} onChange={reset(setTemple)} includeAll="All temples"
                options={(temples.data?.items || []).map((t) => ({ value: t.temple_code, label: t.name }))} />
        <Select label="Shift" value={shift} onChange={reset(setShift)} includeAll="Any shift"
                options={SHIFTS.map((s) => ({ value: s, label: titleCase(s) }))} />
        <Select label="Employment" value={status} onChange={reset(setStatus)} includeAll="Everyone"
                options={[{ value: "true", label: "Active" }, { value: "false", label: "Inactive" }]} />
      </div>
      <ErrorState error={list.error} onRetry={list.reload} />
      {list.loading && !list.data && <Loading />}
      {list.data && (
        <>
          <DataTable caption="Staff" rows={list.data.items} rowKey={(s) => s.staff_code}
                     onRowClick={can("admin") ? (s) => setEditing(s) : undefined}
                     empty={<Empty title={q || temple || shift ? "Nobody matches these filters" : "No staff registered yet"} />}
                     columns={[
                       { key: "staff_code", label: "Staff ID" },
                       { key: "staff_name", label: "Name", render: (s) => <>{s.staff_name} <DemoBadge show={s.is_demo} /></> },
                       { key: "phone", label: "Contact", render: (s) => (can("admin") ? s.phone || "—" : <span className="muted">Admins only</span>) },
                       { key: "temple", label: "Temple", render: (s) => s.temple_code || "—" },
                       { key: "hall", label: "Hall", render: (s) => s.hall_code || "—" },
                       { key: "designation", label: "Role", render: (s) => s.designation || "—" },
                       { key: "shift", label: "Shift", render: (s) => titleCase(s.shift) },
                       { key: "active", label: "Employment", render: (s) => <ActiveBadge active={s.active} /> },
                       { key: "today", label: "Today", render: (s) => (s.today_attendance
                         ? <Badge tone={ATT[s.today_attendance].tone}>{ATT[s.today_attendance].label}</Badge>
                         : <span className="muted">Not marked</span>) },
                     ]} />
          <Pagination page={page} pageSize={PAGE} total={list.data.total} onPage={setPage} />
        </>
      )}
      <StaffForm open={editing !== null} member={editing === "new" ? null : editing}
                 onClose={() => setEditing(null)} onSaved={() => { setEditing(null); list.reload(); }} />
    </Panel>
  );
}

function AttendanceRegister() {
  const { can, tz } = useSession();
  const today = isoDay(new Date(), tz);
  const [day, setDay] = useState(today);
  const [temple, setTemple] = useState("");
  const temples = useApi(() => api.temples({ page_size: 200 }), []);
  const reg = useApi(() => api.attendance({ date: day, temple_code: temple }), [day, temple]);
  const [draft, setDraft] = useState({});
  const [run, busy] = useAction();
  useEffect(() => setDraft({}), [reg.data]);

  const changed = Object.keys(draft).length;
  const save = async () => {
    const entries = Object.entries(draft).map(([staff_code, status]) => ({ staff_code, attendance_date: day, status }));
    if (await run(() => api.markAttendance(entries), `Attendance saved for ${entries.length} ${entries.length === 1 ? "person" : "people"}`)) reg.reload();
  };
  const markAll = (status) => {
    const next = {};
    for (const r of reg.data?.rows || []) if (!r.status) next[r.staff_code] = status;
    setDraft({ ...draft, ...next });
  };
  const s = reg.data?.summary;

  if (!can("operator")) {
    return <Panel title="Attendance register"><p className="muted">The attendance register is available to operators and administrators.</p></Panel>;
  }
  return (
    <Panel title={`Attendance register, ${longDate(day)}`}
           actions={<button type="button" className="btn btn-primary" disabled={!changed || busy} onClick={save}>
             {busy ? "Saving…" : changed ? `Save ${changed} ${changed === 1 ? "change" : "changes"}` : "Save"}
           </button>}>
      <div className="toolbar">
        <div className="filter">
          <label htmlFor="att-day">Date</label>
          <input id="att-day" type="date" max={today} value={day} onChange={(e) => e.target.value && setDay(e.target.value)} />
        </div>
        <Select label="Temple" value={temple} onChange={setTemple} includeAll="All temples"
                options={(temples.data?.items || []).map((t) => ({ value: t.temple_code, label: t.name }))} />
        <button type="button" className="btn btn-quiet" onClick={() => markAll("PRESENT")}>Mark everyone not marked as present</button>
      </div>
      <ErrorState error={reg.error} onRetry={reg.reload} />
      {s && (
        <div className="attendance-strip">
          <Stat label="Present" value={num(s.present)} tone="ok" />
          <Stat label="Half day" value={num(s.half_day)} />
          <Stat label="Absent" value={num(s.absent)} />
          <Stat label="On leave" value={num(s.on_leave)} />
          <Stat label="Not marked" value={num(s.not_marked)} note="Not counted as absent" />
        </div>
      )}
      {reg.data && (
        <DataTable caption="Attendance register" rows={reg.data.rows} rowKey={(r) => r.staff_code}
                   empty={<Empty title="No active staff for this selection" />}
                   columns={[
                     { key: "staff_name", label: "Name", render: (r) => <>{r.staff_name} <span className="muted">{r.staff_code}</span></> },
                     { key: "designation", label: "Role", render: (r) => r.designation || "—" },
                     { key: "shift", label: "Shift", render: (r) => titleCase(r.shift) },
                     { key: "hall", label: "Hall", render: (r) => r.hall_code || "—" },
                     { key: "status", label: "Attendance", render: (r) => {
                       const value = draft[r.staff_code] ?? r.status ?? "";
                       return (
                         <select aria-label={`Attendance for ${r.staff_name}`} value={value}
                                 className={draft[r.staff_code] ? "changed" : ""}
                                 onChange={(e) => setDraft({ ...draft, [r.staff_code]: e.target.value })}>
                           <option value="" disabled>Not marked</option>
                           {Object.entries(ATT).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
                         </select>
                       );
                     } },
                   ]} />
      )}
    </Panel>
  );
}

export default function Staff({ params }) {
  const tab = params.get("tab") === "attendance" ? "attendance" : "list";
  return (
    <>
      <PageHeader title="Staff management" subtitle="Registration, assignments and the daily attendance register." />
      <div className="tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === "list"} className={tab === "list" ? "on" : ""}
                onClick={() => navigate("#/staff")}>Staff list</button>
        <button type="button" role="tab" aria-selected={tab === "attendance"} className={tab === "attendance" ? "on" : ""}
                onClick={() => navigate("#/staff?tab=attendance")}>Attendance register</button>
      </div>
      {tab === "list" ? <StaffList /> : <AttendanceRegister />}
    </>
  );
}
