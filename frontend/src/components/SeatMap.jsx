import { useMemo, useState } from "react";
import { api } from "../lib/api.js";
import { useSession } from "../lib/app.js";
import { dateTime, duration } from "../lib/format.js";
import { Badge, Empty, Field, Modal, SEAT_TONES, useAction } from "../ui.jsx";

// Allowed next statuses, mirroring the backend's TRANSITIONS table.
const ACTIONS = {
  AVAILABLE: [["OCCUPIED", "Mark occupied"], ["RESERVED", "Reserve"], ["OUT_OF_SERVICE", "Take out of service"]],
  RESERVED: [["OCCUPIED", "Seat the reserved guest"], ["AVAILABLE", "Cancel reservation"], ["OUT_OF_SERVICE", "Take out of service"]],
  OCCUPIED: [["AVAILABLE", "Mark available"]],
  OUT_OF_SERVICE: [["AVAILABLE", "Return to service"]],
};

function SeatDialog({ hallCode, seat, onClose, onChanged }) {
  const { can, tz } = useSession();
  const [ref, setRef] = useState("");
  const [note, setNote] = useState("");
  const [run, busy] = useAction();
  if (!seat) return <Modal open={false} onClose={onClose} />;
  const tone = SEAT_TONES[seat.status];

  const apply = async (status) => {
    const body = { status, expected_version: seat.status_version };
    if (status === "RESERVED" && ref.trim()) body.reservation_ref = ref.trim();
    if (note.trim()) body.note = note.trim();
    const ok = await run(() => api.setSeatStatus(hallCode, seat.seat_id, body),
                         `${seat.seat_id}: ${SEAT_TONES[status].label.toLowerCase()}`);
    onChanged();                                       // refresh either way: someone may have changed it
    if (ok) onClose();
  };

  return (
    <Modal open title={`Seat ${seat.seat_id}`} onClose={onClose} size="sm">
      <p className="seat-dialog-status">
        <i className={`swatch ${tone.cls}`} aria-hidden="true" /> {tone.label}
        {seat.status_source === "AI_CONFIRMED" && <Badge tone="info">Confirmed from AI suggestion</Badge>}
      </p>
      <dl className="details">
        <dt>Label</dt><dd>{seat.seat_label || "—"}</dd>
        <dt>{seat.status === "OCCUPIED" ? "Occupied since" : "Since"}</dt>
        <dd>{dateTime(seat.status_since, tz)} ({duration((Date.now() - new Date(seat.status_since)) / 1000)})</dd>
        {seat.reservation_ref && (<><dt>Reservation</dt><dd>{seat.reservation_ref}</dd></>)}
        <dt>Last updated</dt><dd>{dateTime(seat.updated_at, tz)}</dd>
      </dl>
      {can("operator") ? (
        <>
          {seat.status === "AVAILABLE" && (
            <Field label="Reservation reference" hint="Optional, used only when reserving, e.g. a name or token number.">
              {(id) => <input id={id} value={ref} maxLength={64} onChange={(e) => setRef(e.target.value)} />}
            </Field>
          )}
          <Field label="Note" hint="Optional, kept in the seat history.">
            {(id) => <input id={id} value={note} maxLength={255} onChange={(e) => setNote(e.target.value)} />}
          </Field>
          <div className="seat-actions">
            {ACTIONS[seat.status].map(([status, label]) => (
              <button key={status} type="button" disabled={busy}
                      className={`btn ${status === "OCCUPIED" ? "btn-primary" : "btn-quiet"}`}
                      onClick={() => apply(status)}>{label}</button>
            ))}
          </div>
        </>
      ) : (
        <p className="muted">Your access key can view seats but not change them.</p>
      )}
    </Modal>
  );
}

/** The hall's seats laid out by row and column, coloured by their confirmed status. */
export default function SeatMap({ hallCode, seats, onChanged }) {
  const [selected, setSelected] = useState(null);
  const [filter, setFilter] = useState("");

  const rows = useMemo(() => {
    const byRow = new Map();
    for (const s of seats) {
      const r = s.row_number ?? 0;
      if (!byRow.has(r)) byRow.set(r, []);
      byRow.get(r).push(s);
    }
    return [...byRow.entries()].sort((a, b) => a[0] - b[0]);
  }, [seats]);

  if (seats.length === 0) {
    return <Empty title="No seats in this hall yet">An administrator can create a seat layout from the hall page.</Empty>;
  }
  const current = selected ? seats.find((s) => s.seat_id === selected) : null;

  return (
    <div className="seatmap-wrap">
      <div className="seatmap-filter" role="group" aria-label="Highlight seats">
        {["", "AVAILABLE", "OCCUPIED", "RESERVED", "OUT_OF_SERVICE"].map((k) => (
          <button key={k || "all"} type="button" aria-pressed={filter === k} className={filter === k ? "on" : ""}
                  onClick={() => setFilter(k)}>
            {k ? SEAT_TONES[k].label : "All seats"}
          </button>
        ))}
      </div>
      <div className="stage-line" aria-hidden="true">Serving side</div>
      <div className="seatmap" role="grid" aria-label="Seat map">
        {rows.map(([r, list]) => (
          <div className="seat-row" role="row" key={r}>
            <span className="row-label" aria-hidden="true">{r || "–"}</span>
            {list.map((s) => (
              <button key={s.seat_id} type="button" role="gridcell"
                      className={`seat ${SEAT_TONES[s.status].cls} ${filter && filter !== s.status ? "dim" : ""}`}
                      aria-label={`Seat ${s.seat_id}, ${SEAT_TONES[s.status].label}`}
                      title={`${s.seat_id}: ${SEAT_TONES[s.status].label}`}
                      onClick={() => setSelected(s.seat_id)}>
                {s.seat_id.replace(/^[A-Z]+0*/, "")}
              </button>
            ))}
          </div>
        ))}
      </div>
      <SeatDialog hallCode={hallCode} seat={current} onClose={() => setSelected(null)} onChanged={onChanged} />
    </div>
  );
}
