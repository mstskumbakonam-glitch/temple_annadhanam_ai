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
