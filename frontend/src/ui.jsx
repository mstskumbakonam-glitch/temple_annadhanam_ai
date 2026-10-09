import { createContext, useCallback, useContext, useEffect, useId, useRef, useState } from "react";
import { num, pct } from "./lib/format.js";

// ------------------------------------------------------------------ layout
export function PageHeader({ title, subtitle, actions, back }) {
  return (
    <header className="page-head">
      <div>
        {back && <a className="back" href={back.href}>{back.label}</a>}
        <h1>{title}</h1>
        {subtitle && <p className="page-sub">{subtitle}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

export function Panel({ title, actions, children, className = "", id }) {
  const headingId = useId();
  return (
    <section className={`panel ${className}`} aria-labelledby={title ? headingId : undefined} id={id}>
      {(title || actions) && (
        <div className="panel-head">
          {title && <h2 id={headingId}>{title}</h2>}
          {actions && <div className="panel-actions">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, note, tone }) {
  return (
    <div className={`stat ${tone ? `stat-${tone}` : ""}`}>
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      {note && <span className="stat-note">{note}</span>}
    </div>
  );
}

export function Badge({ children, tone = "neutral", title }) {
  return <span className={`badge badge-${tone}`} title={title}>{children}</span>;
}

const SESSION_TONES = { SCHEDULED: "info", IN_PROGRESS: "ok", COMPLETED: "neutral", CANCELLED: "bad" };

/** Session status; a scheduled session whose end time has passed is flagged for closing. */
export function SessionBadge({ s }) {
  if (s.status === "SCHEDULED" && s.timing === "past") {
    return <Badge tone="warn" title="The session has ended but was not marked completed">Not closed</Badge>;
  }
  const label = s.status.charAt(0) + s.status.slice(1).toLowerCase().replace("_", " ");
  return <Badge tone={SESSION_TONES[s.status]}>{label}</Badge>;
}

export function DemoBadge({ show }) {
  return show ? <Badge tone="demo" title="Sample record created by the demo seed">Sample data</Badge> : null;
}

export function ActiveBadge({ active }) {
  return active ? <Badge tone="ok">Active</Badge> : <Badge>Inactive</Badge>;
}

// ------------------------------------------------------------------ states
export function Loading({ label = "Loading" }) {
  return <p className="state state-loading" role="status">{label}…</p>;
}

export function ErrorState({ error, onRetry }) {
  if (!error) return null;
  return (
    <div className="state state-error" role="alert">
      <p>{error.status === 403 ? "Your key does not allow this page." : error.message}</p>
      {onRetry && <button type="button" className="btn btn-quiet" onClick={onRetry}>Try again</button>}
    </div>
  );
}

export function Empty({ title, children, action }) {
  return (
    <div className="state state-empty">
      <p className="state-title">{title}</p>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

// ------------------------------------------------------------------ seats
export const SEAT_TONES = {
  AVAILABLE: { label: "Available", cls: "seat-available" },
  OCCUPIED: { label: "Occupied", cls: "seat-occupied" },
  RESERVED: { label: "Reserved", cls: "seat-reserved" },
  OUT_OF_SERVICE: { label: "Out of service", cls: "seat-oos" },
};

export function SeatLegend({ counts }) {
  const keys = [["AVAILABLE", "available"], ["OCCUPIED", "occupied"], ["RESERVED", "reserved"], ["OUT_OF_SERVICE", "out_of_service"]];
  return (
    <ul className="seat-legend">
      {keys.map(([k, field]) => (
        <li key={k}>
          <i className={`swatch ${SEAT_TONES[k].cls}`} aria-hidden="true" />
          {SEAT_TONES[k].label}
          {counts && <strong>{num(counts[field])}</strong>}
        </li>
      ))}
    </ul>
  );
}

/** Seat capacity as one segmented bar: occupied | reserved | available, then out of service. */
export function SeatBar({ counts, size = "md", label }) {
  const total = counts ? counts.installed : 0;
  if (!counts || total === 0) {
    return <div className={`seatbar seatbar-${size} seatbar-empty`} role="img" aria-label="No seats configured" />;
  }
  const parts = [
    ["occupied", "seat-occupied"],
    ["reserved", "seat-reserved"],
    ["available", "seat-available"],
    ["out_of_service", "seat-oos"],
  ];
  const text = `${label ? `${label}: ` : ""}${counts.occupied} occupied, ${counts.reserved} reserved, ${counts.available} available, ${counts.out_of_service} out of service`;
  return (
    <div className={`seatbar seatbar-${size}`} role="img" aria-label={text} title={text}>
      {parts.map(([field, cls]) =>
        counts[field] > 0 ? (
          <span key={field} className={cls} style={{ flexGrow: counts[field] }} />
        ) : null,
      )}
    </div>
  );
}

export function Occupancy({ counts }) {
  return (
    <div className="occ">
      <SeatBar counts={counts} size="sm" />
      <span className="occ-text">{counts?.capacity ? pct(counts.occupancy_percentage) : "No seats"}</span>
    </div>
  );
}

// ------------------------------------------------------------------ tables
export function DataTable({ columns, rows, rowKey, onRowClick, empty, caption }) {
  if (!rows) return null;
  if (rows.length === 0) return empty;
  return (
    <div className="table-wrap">
      <table className="table">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} scope="col" className={c.align === "right" ? "num" : ""}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={rowKey(row)}
              className={onRowClick ? "clickable" : ""}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              onKeyDown={onRowClick ? (e) => { if (e.key === "Enter") onRowClick(row); } : undefined}
              tabIndex={onRowClick ? 0 : undefined}
            >
              {columns.map((c) => (
                <td key={c.key} className={c.align === "right" ? "num" : ""} data-label={c.label}>
                  {c.render ? c.render(row) : row[c.key] ?? "—"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Pagination({ page, pageSize, total, onPage }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (total <= pageSize) return <p className="pager-info">{total} {total === 1 ? "record" : "records"}</p>;
  return (
    <nav className="pager" aria-label="Pagination">
      <span className="pager-info">
        {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} of {total}
      </span>
      <button type="button" className="btn btn-quiet" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
      <span className="pager-info">Page {page} of {pages}</span>
      <button type="button" className="btn btn-quiet" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
    </nav>
  );
}

// ------------------------------------------------------------------ forms
export function Field({ label, hint, error, children, wide }) {
  const id = useId();
  const child = typeof children === "function" ? children(id) : children;
  return (
    <div className={`field ${wide ? "field-wide" : ""} ${error ? "has-error" : ""}`}>
      <label htmlFor={id}>{label}</label>
      {child}
      {hint && !error && <p className="hint">{hint}</p>}
      {error && <p className="field-error">{error}</p>}
    </div>
  );
}

export function SearchBox({ value, onChange, placeholder, label = "Search" }) {
  const id = useId();
  return (
    <div className="search">
      <label htmlFor={id} className="sr-only">{label}</label>
      <input id={id} type="search" value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

export function Select({ label, value, onChange, options, includeAll = "All", hideLabel }) {
  const id = useId();
  return (
    <div className="filter">
      <label htmlFor={id} className={hideLabel ? "sr-only" : ""}>{label}</label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        {includeAll !== null && <option value="">{includeAll}</option>}
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </div>
  );
}

// ------------------------------------------------------------------ dialogs
/** Native <dialog>: focus is trapped and Escape closes it, for free. */
export function Modal({ open, title, onClose, children, footer, size = "md" }) {
  const ref = useRef(null);
  const titleId = useId();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} className={`modal modal-${size}`} aria-labelledby={titleId} onClose={onClose}
            onCancel={(e) => { e.preventDefault(); onClose(); }}>
      {open && (
        <>
          <div className="modal-head">
            <h2 id={titleId}>{title}</h2>
            <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">×</button>
          </div>
          <div className="modal-body">{children}</div>
          {footer && <div className="modal-foot">{footer}</div>}
        </>
      )}
    </dialog>
  );
}

export function ConfirmDialog({ open, title, message, confirmLabel, danger, onConfirm, onClose, busy }) {
  return (
    <Modal open={open} title={title} onClose={onClose} size="sm"
           footer={
             <>
               <button type="button" className="btn btn-quiet" onClick={onClose}>Keep it</button>
               <button type="button" className={`btn ${danger ? "btn-danger" : "btn-primary"}`} onClick={onConfirm} disabled={busy}>
                 {busy ? "Working…" : confirmLabel}
               </button>
             </>
           }>
      <p>{message}</p>
    </Modal>
  );
}

// ------------------------------------------------------------------ toasts
const ToastContext = createContext(() => {});

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const push = useCallback((text, tone = "ok") => {
    const id = Math.random().toString(36).slice(2);
    setToasts((t) => [...t, { id, text, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), tone === "error" ? 7000 : 3500);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.tone}`}>{t.text}</div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}

/** Run an async action, toast the outcome, return true on success. */
export function useAction() {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const run = useCallback(async (fn, success) => {
    setBusy(true);
    try {
      await fn();
      if (success) toast(success);
      return true;
    } catch (err) {
      toast(err.message, "error");
      return false;
    } finally {
      setBusy(false);
    }
  }, [toast]);
  return [run, busy];
}
