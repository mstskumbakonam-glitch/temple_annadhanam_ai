// Thin fetch wrapper. Paths are relative; Vite proxies /api to the backend in development.

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
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

export const api = {
  health: () => request("/api/health"),
  databaseHealth: () => request("/api/health/db"),
};
