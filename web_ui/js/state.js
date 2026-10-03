export const store = {
  route: "recognition",
  runtime: null,
  historyVersion: -1,
  employees: { query: "", page: 1, pageSize: 10, data: null, stale: true },
  history: { query: "", status: "", page: 1, pageSize: 10, data: null, stale: true },
  dashboard: { data: null, stale: true },
  recognitionTestVersion: 0,
  scroll: new Map(),
};

export function markDataStale(historyVersion) {
  if (historyVersion === store.historyVersion) return;
  store.historyVersion = historyVersion;
  store.history.stale = true;
  store.dashboard.stale = true;
}
