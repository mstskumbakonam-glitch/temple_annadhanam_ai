// Fetch wrapper for the FastAPI backend. Paths are relative; Vite proxies /api in development.
//
// The API key is typed in by the user at sign-in and kept in sessionStorage
// (cleared when the tab closes). It is never compiled into the bundle.

const KEY_STORAGE = "annadhanam.apiKey";

export function getApiKey() {
  try {
    return sessionStorage.getItem(KEY_STORAGE) || "";
  } catch {
    return "";
  }
}

export function setApiKey(key) {
  try {
    if (key) sessionStorage.setItem(KEY_STORAGE, key);
    else sessionStorage.removeItem(KEY_STORAGE);
  } catch {
    /* storage unavailable: the key lives only for this page load */
  }
}

function authHeaders() {
  const key = getApiKey();
  return key ? { Authorization: `Bearer ${key}` } : {};
}

function query(params = {}) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") q.set(k, v);
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

export class ApiError extends Error {
  constructor(message, status, body) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function request(path, { method = "GET", body, params } = {}) {
  const response = await fetch(path + query(params), {
    method,
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 204) return null;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const message =
      data?.detail && typeof data.detail === "string"
        ? data.detail
        : response.status === 429
          ? "Too many requests. Wait a moment and try again."
          : `Request failed (${response.status}).`;
    throw new ApiError(message, response.status, data);
  }
  return data;
}

async function requestBlob(path, params) {
  const response = await fetch(path + query(params), { headers: authHeaders() });
  if (!response.ok) throw new ApiError(`Request failed (${response.status}).`, response.status);
  return response.blob();
}

export const api = {
  // system
  health: () => request("/api/health"),
  databaseHealth: () => request("/api/health/db"),
  me: () => request("/api/auth/me"),

  // dashboard
  overview: (temple_code) => request("/api/dashboard/overview", { params: { temple_code } }),

  // temples
  temples: (params) => request("/api/temples", { params }),
  districts: () => request("/api/temples/districts"),
  temple: (code) => request(`/api/temples/${encodeURIComponent(code)}`),
  createTemple: (body) => request("/api/temples", { method: "POST", body }),
  updateTemple: (code, body) => request(`/api/temples/${encodeURIComponent(code)}`, { method: "PUT", body }),
  deleteTemple: (code) => request(`/api/temples/${encodeURIComponent(code)}`, { method: "DELETE" }),

  // halls and seats
  halls: (params) => request("/api/halls", { params }),
  hall: (code) => request(`/api/halls/${encodeURIComponent(code)}`),
  createHall: (body) => request("/api/halls", { method: "POST", body }),
  updateHall: (code, body) => request(`/api/halls/${encodeURIComponent(code)}`, { method: "PUT", body }),
  deleteHall: (code) => request(`/api/halls/${encodeURIComponent(code)}`, { method: "DELETE" }),
  generateSeats: (code, body) =>
    request(`/api/halls/${encodeURIComponent(code)}/seats/generate`, { method: "POST", body }),
  hallSeats: (code, params) => request(`/api/halls/${encodeURIComponent(code)}/seats`, { params }),
  seatHistory: (code, params) => request(`/api/halls/${encodeURIComponent(code)}/seats/history`, { params }),
  setSeatStatus: (hall, seat, body) =>
    request(`/api/halls/${encodeURIComponent(hall)}/seats/${encodeURIComponent(seat)}/status`, {
      method: "POST",
      body,
    }),

  // sessions
  sessions: (params) => request("/api/sessions", { params }),
  createSession: (body) => request("/api/sessions", { method: "POST", body }),
  updateSession: (id, body) => request(`/api/sessions/${id}`, { method: "PUT", body }),
  cancelSession: (id, reason) => request(`/api/sessions/${id}/cancel`, { method: "POST", body: { reason } }),

  // staff and attendance
  staff: (params) => request("/api/staff", { params }),
  createStaff: (body) => request("/api/staff", { method: "POST", body }),
  updateStaff: (code, body) => request(`/api/staff/${encodeURIComponent(code)}`, { method: "PUT", body }),
  attendance: (params) => request("/api/attendance", { params }),
  markAttendance: (entries) => request("/api/attendance", { method: "PUT", body: { entries } }),

  // reports
  report: (name, params) => request(`/api/reports/${name}`, { params }),
  reportCsv: (name, params) => requestBlob(`/api/reports/${name}`, { ...params, format: "csv" }),

  // AI / CCTV monitoring
  live: () => request("/api/analytics/live"),
  history: ({ cameraId, zoneId = "*", minutes = 180, bucketMinutes = 5 } = {}) =>
    request("/api/analytics/history", {
      params: { camera_id: cameraId, zone_id: zoneId, minutes, bucket_minutes: bucketMinutes },
    }),
  alerts: ({ active, pageSize = 20 } = {}) => request("/api/alerts", { params: { active, page_size: pageSize } }),
  acknowledge: (alertId) => request(`/api/alerts/${alertId}/acknowledge`, { method: "POST" }),
  preview: (cameraId) => requestBlob(`/api/cameras/${encodeURIComponent(cameraId)}/preview.jpg`),
};
