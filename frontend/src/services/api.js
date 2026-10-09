// Thin fetch wrapper. Paths are relative; Vite proxies /api to the backend in development.
//
// The API key is entered by the operator at runtime and kept in sessionStorage
// (cleared when the browser tab closes). It is NEVER built into the bundle.

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
    /* storage unavailable: key lives only for this page load */
  }
}

function authHeaders() {
  const key = getApiKey();
  return key ? { Authorization: `Bearer ${key}` } : {};
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...authHeaders(), ...(options.headers || {}) },
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    // 503 from /api/health/db carries a useful payload, so return it rather than throwing it away.
    const error = new Error(body?.detail || `${response.status} ${response.statusText}`);
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return body;
}

async function requestBlob(path) {
  const response = await fetch(path, { headers: authHeaders() });
  if (!response.ok) {
    const error = new Error(`${response.status}`);
    error.status = response.status;
    throw error;
  }
  return response.blob();
}

export const api = {
  health: () => request("/api/health"),
  databaseHealth: () => request("/api/health/db"),
  live: () => request("/api/analytics/live"),
  history: ({ cameraId, zoneId = "*", minutes = 180, bucketMinutes = 5 } = {}) => {
    const params = new URLSearchParams({ zone_id: zoneId, minutes, bucket_minutes: bucketMinutes });
    if (cameraId) params.set("camera_id", cameraId);
    return request(`/api/analytics/history?${params}`);
  },
  alerts: ({ active, pageSize = 20 } = {}) => {
    const params = new URLSearchParams({ page_size: pageSize });
    if (active !== undefined) params.set("active", active);
    return request(`/api/alerts?${params}`);
  },
  acknowledge: (alertId) => request(`/api/alerts/${alertId}/acknowledge`, { method: "POST" }),
  preview: (cameraId) => requestBlob(`/api/cameras/${encodeURIComponent(cameraId)}/preview.jpg`),
};
