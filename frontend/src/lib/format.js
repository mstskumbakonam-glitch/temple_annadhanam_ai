// Display helpers. Unknown values render as an em dash, never as a fake zero.

export const dash = "—";

export function num(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(value)) return dash;
  return Number(value).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function duration(seconds) {
  if (seconds === null || seconds === undefined) return dash;
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

export function clock(value, timeZone) {
  if (!value) return dash;
  return new Date(value).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZone,
  });
}

export function ago(value, now = new Date()) {
  if (!value) return dash;
  const s = Math.max(0, (now - new Date(value)) / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${Math.round(s)} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}

export const LEVEL_CLASS = {
  LOW: "lvl-low",
  MEDIUM: "lvl-medium",
  HIGH: "lvl-high",
  CRITICAL: "lvl-critical",
};

// ---------------------------------------------------------------- dates (site time zone)
export const SITE_TZ_FALLBACK = "Asia/Kolkata";

/** YYYY-MM-DD of `date` as seen in the temple's time zone. */
export function isoDay(date = new Date(), timeZone = SITE_TZ_FALLBACK) {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" })
    .formatToParts(date);
  const get = (t) => parts.find((p) => p.type === t).value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

/** Calendar arithmetic on YYYY-MM-DD strings (time-zone free). */
export function addDays(iso, n) {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export function weekday(iso) {
  return new Date(`${iso}T12:00:00Z`).getUTCDay(); // 0 = Sunday
}

export function startOfWeek(iso) {
  return addDays(iso, -((weekday(iso) + 6) % 7)); // weeks start on Monday
}

export function startOfMonth(iso) {
  return `${iso.slice(0, 7)}-01`;
}

export function endOfMonth(iso) {
  const d = new Date(`${startOfMonth(iso)}T12:00:00Z`);
  d.setUTCMonth(d.getUTCMonth() + 1, 0);
  return d.toISOString().slice(0, 10);
}

export function addMonths(iso, n) {
  const d = new Date(`${startOfMonth(iso)}T12:00:00Z`);
  d.setUTCMonth(d.getUTCMonth() + n);
  return d.toISOString().slice(0, 10);
}

export function longDate(iso) {
  return new Date(`${iso}T12:00:00Z`).toLocaleDateString("en-IN", {
    weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC",
  });
}

export function shortDate(iso) {
  return new Date(`${iso}T12:00:00Z`).toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "UTC" });
}

export function monthLabel(iso) {
  return new Date(`${iso}T12:00:00Z`).toLocaleDateString("en-IN", { month: "long", year: "numeric", timeZone: "UTC" });
}

/** "11:30" from "11:30:00". */
export function hhmm(time) {
  return time ? time.slice(0, 5) : dash;
}

export function dateTime(value, timeZone = SITE_TZ_FALLBACK) {
  if (!value) return dash;
  return new Date(value).toLocaleString("en-IN", {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone,
  });
}

export function pct(value) {
  return value === null || value === undefined ? dash : `${Number(value).toFixed(1)}%`;
}

export function titleCase(value) {
  if (!value) return dash;
  return value.toLowerCase().replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}
