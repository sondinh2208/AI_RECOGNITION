const jsonHeaders = { "Content-Type": "application/json" };

async function request(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

export const api = {
  state: () => request("/api/state"),
  setMode: (mode) => request("/api/mode", { method: "POST", headers: jsonHeaders, body: JSON.stringify({ mode }) }),
  retry: () => request("/api/recognition/retry", { method: "POST" }),
  testImage: async (file) => {
    const response = await fetch("/api/recognition/test-image", {
      method: "POST",
      headers: { "X-Filename": encodeURIComponent(file.name) },
      body: file,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  },
  dashboard: () => request("/api/dashboard"),
  employees: ({ query = "", page = 1, pageSize = 10 } = {}) => request(`/api/employees?query=${encodeURIComponent(query)}&page=${page}&page_size=${pageSize}`),
  updateEmployee: (id, payload) => request(`/api/employees/${encodeURIComponent(id)}`, { method: "PUT", headers: jsonHeaders, body: JSON.stringify(payload) }),
  toggleEmployee: (id) => request(`/api/employees/${encodeURIComponent(id)}/toggle`, { method: "POST" }),
  deleteEmployee: (id) => request(`/api/employees/${encodeURIComponent(id)}`, { method: "DELETE" }),
  history: ({ query = "", status = "", page = 1, pageSize = 10 } = {}) => request(`/api/attendance?query=${encodeURIComponent(query)}&status=${encodeURIComponent(status)}&page=${page}&page_size=${pageSize}`),
  saveEnrollment: (payload) => request("/api/enrollment/save", { method: "POST", headers: jsonHeaders, body: JSON.stringify(payload) }),
  resetEnrollment: () => request("/api/enrollment/reset", { method: "POST" }),
};

export function connectStateSocket(onState, onConnection) {
  let socket;
  let retryTimer;
  let retryDelay = 500;
  let stopped = false;

  const connect = () => {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${scheme}://${location.host}/ws/state`);
    socket.addEventListener("open", () => { retryDelay = 500; onConnection(true); });
    socket.addEventListener("message", event => {
      try { onState(JSON.parse(event.data)); } catch (_) { /* ignore malformed event */ }
    });
    socket.addEventListener("close", () => {
      onConnection(false);
      if (!stopped) {
        retryTimer = setTimeout(connect, retryDelay);
        retryDelay = Math.min(retryDelay * 1.7, 5000);
      }
    });
    socket.addEventListener("error", () => socket.close());
  };
  connect();
  return () => { stopped = true; clearTimeout(retryTimer); socket?.close(); };
}
