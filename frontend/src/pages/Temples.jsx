import { useState } from "react";
import { api } from "../lib/api.js";
import { href, navigate, useSession } from "../lib/app.js";
import { num } from "../lib/format.js";
import { useApi, useDebounced } from "../lib/hooks.js";
import {
  ActiveBadge, ConfirmDialog, DataTable, DemoBadge, Empty, ErrorState, Field, Loading, Modal,
  Occupancy, PageHeader, Pagination, Panel, SearchBox, Select, Stat, useAction,
} from "../ui.jsx";
import { HallForm } from "./Halls.jsx";

const PAGE = 20;

export function TempleForm({ open, temple, onClose, onSaved }) {
  const editing = Boolean(temple);
  const [form, setForm] = useState(null);
  const [errors, setErrors] = useState({});
  const [run, busy] = useAction();
  const values = form ?? {
    temple_code: temple?.temple_code ?? "", name: temple?.name ?? "", address: temple?.address ?? "",
    district: temple?.district ?? "", contact_phone: temple?.contact_phone ?? "", active: temple?.active ?? true,
  };
  const set = (k) => (e) => setForm({ ...values, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  const close = () => { setForm(null); setErrors({}); onClose(); };

  const submit = async (e) => {
    e.preventDefault();
    const errs = {};
    if (!values.name.trim()) errs.name = "Enter the temple name.";
    if (!editing && !/^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(values.temple_code)) errs.temple_code = "Use letters, numbers, - or _.";
    setErrors(errs);
    if (Object.keys(errs).length) return;
    const body = { ...values, address: values.address || null, district: values.district || null,
                   contact_phone: values.contact_phone || null };
    let saved;
    const ok = await run(async () => {
      if (editing) {
        delete body.temple_code;
        saved = await api.updateTemple(temple.temple_code, body);
      } else {
        saved = await api.createTemple(body);
      }
    }, editing ? "Temple updated" : "Temple added");
    if (ok) { setForm(null); onSaved(saved); }
  };

  return (
    <Modal open={open} title={editing ? `Edit ${temple.name}` : "Add temple"} onClose={close}
           footer={<>
             <button type="button" className="btn btn-quiet" onClick={close}>Cancel</button>
             <button type="submit" form="temple-form" className="btn btn-primary" disabled={busy}>
               {busy ? "Saving…" : editing ? "Save changes" : "Add temple"}
             </button>
           </>}>
      <form id="temple-form" className="form-grid" onSubmit={submit} noValidate>
        <Field label="Temple ID" hint={editing ? "The ID cannot be changed." : "Short unique code, e.g. KMB-01."} error={errors.temple_code}>
          {(id) => <input id={id} value={values.temple_code} onChange={set("temple_code")} disabled={editing} required />}
        </Field>
        <Field label="Temple name" error={errors.name}>
          {(id) => <input id={id} value={values.name} onChange={set("name")} required />}
        </Field>
        <Field label="Address" wide>
          {(id) => <textarea id={id} rows={2} value={values.address} onChange={set("address")} />}
        </Field>
        <Field label="District">
          {(id) => <input id={id} value={values.district} onChange={set("district")} />}
        </Field>
        <Field label="Contact number" hint="Digits, spaces, dashes and a leading +.">
          {(id) => <input id={id} type="tel" value={values.contact_phone} onChange={set("contact_phone")} />}
        </Field>
        <label className="check">
          <input type="checkbox" checked={values.active} onChange={set("active")} /> Active
        </label>
      </form>
    </Modal>
  );
}

export default function Temples() {
  const { can } = useSession();
  const [search, setSearch] = useState("");
  const [district, setDistrict] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState(null);       // null | "new" | temple
  const [addHallFor, setAddHallFor] = useState(null);
  const q = useDebounced(search);
  const districts = useApi(() => api.districts(), []);
  const list = useApi(() => api.temples({ search: q, district, active: status || undefined, page, page_size: PAGE }),
                      [q, district, status, page]);

  const columns = [
    { key: "temple_code", label: "Temple ID" },
    { key: "name", label: "Temple name", render: (t) => <><a href={href("temples", t.temple_code)} onClick={(e) => e.stopPropagation()}>{t.name}</a> <DemoBadge show={t.is_demo} /></> },
    { key: "address", label: "Address", render: (t) => t.address || "—" },
    { key: "district", label: "District", render: (t) => t.district || "—" },
    { key: "contact_phone", label: "Contact", render: (t) => t.contact_phone || "—" },
    { key: "halls", label: "Halls", align: "right", render: (t) => num(t.hall_count) },
    { key: "capacity", label: "Seat capacity", align: "right", render: (t) => num(t.seats.capacity) },
    { key: "active", label: "Status", render: (t) => <ActiveBadge active={t.active} /> },
    { key: "actions", label: "Actions", render: (t) => (
      <span className="row-actions" onClick={(e) => e.stopPropagation()}>
        <a href={href("temples", t.temple_code)}>View</a>
        {can("admin") && <button type="button" className="link" onClick={() => setEditing(t)}>Edit</button>}
        {can("admin") && <button type="button" className="link" onClick={() => setAddHallFor(t.temple_code)}>Add hall</button>}
      </span>
    ) },
  ];

  return (
    <>
      <PageHeader title="Temples" subtitle="Each temple can run one or more annadhanam halls."
                  actions={can("admin") && <button type="button" className="btn btn-primary" onClick={() => setEditing("new")}>Add temple</button>} />
      <Panel>
        <div className="toolbar">
          <SearchBox value={search} onChange={(v) => { setSearch(v); setPage(1); }} placeholder="Search by temple name or ID" />
          <Select label="District" value={district} onChange={(v) => { setDistrict(v); setPage(1); }} includeAll="All districts"
                  options={(districts.data || []).map((d) => ({ value: d, label: d }))} />
          <Select label="Status" value={status} onChange={(v) => { setStatus(v); setPage(1); }} includeAll="Any status"
                  options={[{ value: "true", label: "Active" }, { value: "false", label: "Inactive" }]} />
        </div>
        <ErrorState error={list.error} onRetry={list.reload} />
        {list.loading && !list.data && <Loading />}
        {list.data && (
          <>
            <DataTable caption="Temples" columns={columns} rows={list.data.items} rowKey={(t) => t.temple_code}
                       onRowClick={(t) => navigate(href("temples", t.temple_code))}
                       empty={<Empty title={q || district || status ? "No temples match these filters" : "No temples yet"}
                                     action={can("admin") && !q && <button type="button" className="btn btn-primary" onClick={() => setEditing("new")}>Add the first temple</button>}>
                         {q || district || status ? "Clear the search or filters to see all temples." : null}
                       </Empty>} />
            <Pagination page={page} pageSize={PAGE} total={list.data.total} onPage={setPage} />
          </>
        )}
      </Panel>
      <TempleForm open={editing !== null} temple={editing === "new" ? null : editing}
                  onClose={() => setEditing(null)} onSaved={() => { setEditing(null); list.reload(); districts.reload(); }} />
      <HallForm open={addHallFor !== null} templeCode={addHallFor} onClose={() => setAddHallFor(null)}
                onSaved={(h) => { setAddHallFor(null); navigate(href("halls", h.hall_code)); }} />
    </>
  );
}

export function TempleDetail({ code }) {
  const { can } = useSession();
  const temple = useApi(() => api.temple(code), [code]);
  const halls = useApi(() => api.halls({ temple_code: code, page_size: 200 }), [code]);
  const [editing, setEditing] = useState(false);
  const [addingHall, setAddingHall] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [run, busy] = useAction();
  const t = temple.data;

  if (temple.error) return <ErrorState error={temple.error} onRetry={temple.reload} />;
  if (!t) return <Loading />;

  return (
    <>
      <PageHeader
        back={{ href: "#/temples", label: "Temples" }}
        title={<>{t.name} <DemoBadge show={t.is_demo} /></>}
        subtitle={[t.temple_code, t.district].filter(Boolean).join(", ")}
        actions={can("admin") && (
          <>
            <button type="button" className="btn btn-quiet" onClick={() => setConfirmDelete(true)}>Delete</button>
            <button type="button" className="btn btn-quiet" onClick={() => setEditing(true)}>Edit temple</button>
            <button type="button" className="btn btn-primary" onClick={() => setAddingHall(true)}>Add hall</button>
          </>
        )}
      />
      <div className="stats-row">
        <Stat label="Status" value={<ActiveBadge active={t.active} />} />
        <Stat label="Halls" value={num(t.hall_count)} />
        <Stat label="Seat capacity" value={num(t.seats.capacity)} note="Active halls only" />
        <Stat label="Occupied now" value={num(t.seats.occupied)} note={`${num(t.seats.available)} available`} />
      </div>
      <div className="grid-2 grid-wide-left">
        <Panel title="Annadhanam halls">
          {halls.data && (
            <DataTable caption="Halls" rows={halls.data.items} rowKey={(h) => h.hall_code}
                       onRowClick={(h) => navigate(href("halls", h.hall_code))}
                       empty={<Empty title="This temple has no halls yet"
                                     action={can("admin") && <button type="button" className="btn btn-primary" onClick={() => setAddingHall(true)}>Add hall</button>} />}
                       columns={[
                         { key: "name", label: "Hall", render: (h) => <a href={href("halls", h.hall_code)}>{h.name}</a> },
                         { key: "floor", label: "Location", render: (h) => [h.building, h.floor].filter(Boolean).join(", ") || "—" },
                         { key: "cap", label: "Capacity", align: "right", render: (h) => num(h.seats.capacity) },
                         { key: "occ", label: "Occupancy", render: (h) => <Occupancy counts={h.seats} /> },
                         { key: "active", label: "Status", render: (h) => <ActiveBadge active={h.active} /> },
                       ]} />
          )}
        </Panel>
        <Panel title="Details">
          <dl className="details">
            <dt>Temple ID</dt><dd>{t.temple_code}</dd>
            <dt>Address</dt><dd>{t.address || "—"}</dd>
            <dt>District</dt><dd>{t.district || "—"}</dd>
            <dt>Contact</dt><dd>{t.contact_phone || "—"}</dd>
          </dl>
        </Panel>
      </div>
      <TempleForm open={editing} temple={t} onClose={() => setEditing(false)}
                  onSaved={() => { setEditing(false); temple.reload(); }} />
      <HallForm open={addingHall} templeCode={code} onClose={() => setAddingHall(false)}
                onSaved={() => { setAddingHall(false); halls.reload(); temple.reload(); }} />
      <ConfirmDialog open={confirmDelete} danger busy={busy} title={`Delete ${t.name}?`}
                     message={t.hall_count ? "A temple with halls cannot be deleted. Mark it inactive instead, or remove its halls first."
                                           : "This removes the temple permanently. Staff assigned to it become unassigned."}
                     confirmLabel="Delete temple" onClose={() => setConfirmDelete(false)}
                     onConfirm={async () => {
                       if (await run(() => api.deleteTemple(code), "Temple deleted")) navigate("#/temples");
                       else setConfirmDelete(false);
                     }} />
    </>
  );
}
