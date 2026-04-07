const FALLBACK_REFRESH_SECONDS = 60;
const MAX_LOG_LINES = 400;

const state = {
  connection: "reconnecting",
  filters: {
    ip: "",
    site: "",
    status: "",
  },
  sort: {
    ipSort: "requests",
    ipOrder: "desc",
  },
  ui: {
    actionBusy: false,
    logsPaused: false,
  },
  data: {
    tasks: [],
    localLog: "",
    ngrokLog: "",
    stats: {},
    dimensions: {
      ips: [],
      sites: [],
      statuses: [],
      status_explanations: {},
    },
  },
  meta: {
    lastRefresh: "--",
    actionMessage: "",
    error: "",
  },
};

let tick = FALLBACK_REFRESH_SECONDS;
let eventSource = null;
let refreshTimer = null;
let countdownTimer = null;

const dom = {};
const lastRender = {};

function byId(id) {
  if (!(id in dom)) {
    dom[id] = document.getElementById(id);
  }
  return dom[id];
}

function setBanner(text, isError = false) {
  const banner = byId("error-banner");
  if (!banner) return;
  if (!text) {
    banner.textContent = "";
    banner.hidden = true;
    return;
  }
  banner.textContent = text;
  banner.hidden = false;
  banner.classList.toggle("error-banner--error", isError);
}

function setActionResult(text, isError = false) {
  const el = byId("action-result");
  if (!el) return;
  el.textContent = text;
  el.style.color = isError ? "#8a1f2d" : "#0f5132";
}

function setConnectionStatus(kind) {
  state.connection = kind;
  const badge = byId("conn-status");
  if (!badge) return;
  badge.classList.remove("connected", "reconnecting", "offline");
  badge.classList.add(kind);
  badge.textContent = kind === "connected" ? "● Live" : kind === "offline" ? "● Offline" : "● Reconnecting";
}

function setBusy(value) {
  state.ui.actionBusy = value;
  for (const id of ["start-all", "stop-all", "restart-btn"]) {
    const button = byId(id);
    if (button) button.disabled = value;
  }
}

function readUrlFilters() {
  const params = new URLSearchParams(window.location.search);
  state.filters.ip = params.get("ip") || "";
  state.filters.site = params.get("site") || "";
  state.filters.status = params.get("status") || "";
}

function setUrlFilters() {
  const url = new URL(window.location.href);
  const params = url.searchParams;
  const pairs = [
    ["ip", state.filters.ip],
    ["site", state.filters.site],
    ["status", state.filters.status],
  ];

  for (const [key, value] of pairs) {
    if (value) {
      params.set(key, value);
    } else {
      params.delete(key);
    }
  }

  window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
}

function formatFilters() {
  const parts = [];
  if (state.filters.ip) parts.push(`IP: ${state.filters.ip}`);
  if (state.filters.site) parts.push(`Site: ${state.filters.site}`);
  if (state.filters.status) parts.push(`Status: ${state.filters.status}`);
  return parts.length ? parts.join(" | ") : "No active filters";
}

function syncSelectValue(selectId, value) {
  const select = byId(selectId);
  if (select) {
    select.value = value || "";
  }
}

function fillSelectOptions(selectId, values, currentValue) {
  const select = byId(selectId);
  if (!select) return;

  const options = Array.from(new Set(values || []));
  if (currentValue && !options.includes(currentValue)) {
    options.unshift(currentValue);
  }

  const fragment = document.createDocumentFragment();
  const allOption = document.createElement("option");
  allOption.value = "";
  allOption.textContent = "All";
  fragment.appendChild(allOption);

  for (const value of options) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    fragment.appendChild(option);
  }

  select.replaceChildren(fragment);
  select.value = currentValue || "";
}

function renderSection(key, signature, fn) {
  if (lastRender[key] === signature) {
    return;
  }
  lastRender[key] = signature;
  fn();
}

function renderStatusCards() {
  const wrap = byId("status-cards");
  if (!wrap) return;

  const tasks = state.data.tasks || [];
  if (!tasks.length) {
    wrap.replaceChildren();
    const empty = document.createElement("article");
    empty.className = "status-card";
    empty.textContent = "No task data.";
    wrap.appendChild(empty);
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const task of tasks) {
    const card = document.createElement("article");
    card.className = "status-card";

    const title = document.createElement("h3");
    title.textContent = task.TaskName || "Task";
    card.appendChild(title);

    const kv = document.createElement("div");
    kv.className = "status-kv";

    if (task.Error) {
      const key = document.createElement("span");
      key.className = "k";
      key.textContent = "Error";
      const value = document.createElement("span");
      value.className = "v";
      value.textContent = task.Error;
      kv.append(key, value);
    } else {
      const entries = [
        ["State", task.State || "-"],
        ["Last Result", task.LastTaskResult ?? "-"],
        ["Last Run", task.LastRunTime || "-"],
        ["Next Run", task.NextRunTime || "-"],
      ];

      for (const [label, valueText] of entries) {
        const key = document.createElement("span");
        key.className = "k";
        key.textContent = label;
        const value = document.createElement("span");
        value.className = "v";
        value.textContent = String(valueText);
        kv.append(key, value);
      }
    }

    card.appendChild(kv);
    fragment.appendChild(card);
  }

  wrap.replaceChildren(fragment);
}

function renderLogContainer(targetId, rawText) {
  const target = byId(targetId);
  if (!target) return;

  const lines = String(rawText || "").split(/\r?\n/);
  const limited = lines.slice(-MAX_LOG_LINES);
  target.textContent = limited.join("\n") || "(empty)";
  if (!state.ui.logsPaused) {
    target.scrollTop = target.scrollHeight;
  }
}

function renderBars(targetId, items, cls) {
  const target = byId(targetId);
  if (!target) return;

  const entries = items || [];
  if (!entries.length) {
    target.textContent = "No data yet.";
    return;
  }

  const max = Math.max(1, ...entries.map((item) => Number(item.value) || 0));
  const fragment = document.createDocumentFragment();
  for (const item of entries) {
    const row = document.createElement("div");
    row.className = "bar-row";
    const width = ((Number(item.value) || 0) / max) * 100;

    const label = document.createElement("div");
    label.className = "label";
    label.textContent = item.label;

    const barWrap = document.createElement("div");
    barWrap.className = "bar-wrap";
    const bar = document.createElement("div");
    bar.className = `bar ${cls}`;
    bar.style.width = `${width}%`;
    barWrap.appendChild(bar);

    const value = document.createElement("div");
    value.className = "val";
    value.textContent = String(item.value);

    row.append(label, barWrap, value);
    fragment.appendChild(row);
  }

  target.replaceChildren(fragment);
}

function renderStatsTable() {
  const target = byId("stats-table");
  if (!target) return;

  const rows = state.data.stats.ip_stats || [];
  if (!rows.length) {
    target.textContent = "No IP stats yet.";
    return;
  }

  const table = document.createElement("table");
  table.className = "stats-table recent-table";
  table.innerHTML = `
    <thead>
      <tr>
        <th data-sort="ip" class="sortable">IP</th>
        <th data-sort="requests" class="sortable">Requests</th>
        <th data-sort="sites" class="sortable">Sites</th>
      </tr>
    </thead>
    <tbody></tbody>
  `;

  const body = table.querySelector("tbody");
  for (const item of rows) {
    const row = document.createElement("tr");
    row.dataset.ip = item.label;
    row.innerHTML = `
      <td>${item.label}</td>
      <td>${item.requests}</td>
      <td>${item.sites}</td>
    `;
    body.appendChild(row);
  }

  target.replaceChildren(table);
}

function renderRecentRequests(targetId, items) {
  const target = byId(targetId);
  if (!target) return;

  const entries = (items || []).slice(0, 25);
  if (!entries.length) {
    target.textContent = "No request history yet.";
    return;
  }

  const table = document.createElement("table");
  table.className = "recent-table";
  table.innerHTML = `
    <thead>
      <tr>
        <th>Time</th>
        <th>Method</th>
        <th>Status</th>
        <th>Path</th>
        <th>IP</th>
      </tr>
    </thead>
    <tbody></tbody>
  `;

  const body = table.querySelector("tbody");
  for (const item of entries) {
    const row = document.createElement("tr");
    const explanation = state.data.stats.status_explanations?.[item.status] || "Standard HTTP status code.";
    row.innerHTML = `
      <td>${item.dt}</td>
      <td>${item.method}</td>
      <td title="${explanation}"><span class="status-pill">${item.status}</span></td>
      <td title="${item.path}">${item.path}</td>
      <td>${item.ip}</td>
    `;
    body.appendChild(row);
  }

  target.replaceChildren(table);
}

function renderStatusExplanations(targetId, explanations) {
  const target = byId(targetId);
  if (!target) return;

  const entries = Object.entries(explanations || {});
  if (!entries.length) {
    target.textContent = "No status codes in current selection.";
    return;
  }

  const table = document.createElement("table");
  table.className = "recent-table";
  table.innerHTML = `
    <thead><tr><th>Status</th><th>Explanation</th></tr></thead>
    <tbody></tbody>
  `;

  const body = table.querySelector("tbody");
  for (const [code, explanation] of entries) {
    const row = document.createElement("tr");
    row.innerHTML = `<td>${code}</td><td>${explanation}</td>`;
    body.appendChild(row);
  }

  target.replaceChildren(table);
}

function renderFilters() {
  const active = byId("active-filters");
  if (active) {
    active.textContent = formatFilters();
  }

  syncSelectValue("filter-ip", state.filters.ip);
  syncSelectValue("filter-site", state.filters.site);
  syncSelectValue("filter-status", state.filters.status);

  const toggleScroll = byId("toggle-scroll");
  if (toggleScroll) toggleScroll.textContent = state.ui.logsPaused ? "Resume" : "Pause";

  for (const id of ["start-all", "stop-all", "restart-btn"]) {
    const button = byId(id);
    if (button) button.disabled = state.ui.actionBusy;
  }
}

function renderHeader() {
  const lastRefresh = byId("last-refresh");
  if (lastRefresh) lastRefresh.textContent = `Last refresh: ${state.meta.lastRefresh}`;

  const nextRefresh = byId("next-refresh");
  if (nextRefresh) {
    nextRefresh.textContent = state.connection === "connected"
      ? "Live updates connected"
      : `Fallback refresh in ${tick}s`;
  }

  setBanner(state.meta.error, Boolean(state.meta.error));
  const actionResult = byId("action-result");
  if (actionResult) {
    actionResult.textContent = state.meta.actionMessage;
    actionResult.style.color = state.meta.actionMessage.startsWith("Error") ? "#8a1f2d" : "#0f5132";
  }

  renderStatusCards();
  renderFilters();
}

function renderLogs() {
  renderLogContainer("logs-local", state.data.localLog);
  renderLogContainer("logs-ngrok", state.data.ngrokLog);
}

function renderStats() {
  const stats = state.data.stats || {};
  renderBars("chart-files", stats.top_files || [], "files");
  renderBars("chart-methods", stats.methods || [], "methods");
  renderBars("chart-status", stats.status_codes || [], "status");
  renderBars("chart-families", stats.status_families || [], "families");
  renderStatsTable();
  renderBars("chart-sites-per-ip", stats.sites_per_ip || [], "methods");
  renderBars("chart-hourly", stats.hourly || [], "hourly");
  renderRecentRequests("recent-requests", stats.recent_requests || []);
  renderStatusExplanations("status-explanations", stats.status_explanations || {});
}

function render() {
  const headerSignature = JSON.stringify({
    connection: state.connection,
    lastRefresh: state.meta.lastRefresh,
    error: state.meta.error,
    actionMessage: state.meta.actionMessage,
    busy: state.ui.actionBusy,
    filters: state.filters,
    tasks: state.data.tasks,
  });
  const logsSignature = JSON.stringify({
    local: state.data.localLog,
    ngrok: state.data.ngrokLog,
    paused: state.ui.logsPaused,
  });
  const filtersSignature = JSON.stringify({
    filters: state.filters,
    dimensions: state.data.dimensions,
  });
  const statsSignature = JSON.stringify({
    stats: state.data.stats,
    sort: state.sort,
  });

  renderSection("header", headerSignature, renderHeader);
  renderSection("logs", logsSignature, renderLogs);
  renderSection("filters", filtersSignature, renderFilters);
  renderSection("stats", statsSignature, renderStats);
}

function fetchJson(url) {
  return fetch(url).then(async (response) => {
    if (!response.ok) {
      throw new Error(`${url} failed: ${response.status}`);
    }
    return response.json();
  });
}

function fetchText(url) {
  return fetch(url).then(async (response) => {
    if (!response.ok) {
      throw new Error(`${url} failed: ${response.status}`);
    }
    return response.text();
  });
}

function updateDimensionsControls() {
  const dimensions = state.data.dimensions || {};
  fillSelectOptions("filter-ip", dimensions.ips || [], state.filters.ip);
  fillSelectOptions("filter-site", dimensions.sites || [], state.filters.site);
  fillSelectOptions("filter-status", dimensions.statuses || [], state.filters.status);
}

async function loadDimensions() {
  const payload = await fetchJson("/api/dimensions");
  state.data.dimensions = payload.dimensions || {};
  updateDimensionsControls();
}

async function refreshAll() {
  try {
    const [status, localLog, ngrokLog, statsResp] = await Promise.all([
      fetchJson("/api/status"),
      fetchText("/api/log/local?tail=400"),
      fetchText("/api/log/ngrok?tail=400"),
      fetchJson(`/api/stats?top=20&ip_sort=${encodeURIComponent(state.sort.ipSort)}&ip_order=${encodeURIComponent(state.sort.ipOrder)}&ip=${encodeURIComponent(state.filters.ip)}&site=${encodeURIComponent(state.filters.site)}&status=${encodeURIComponent(state.filters.status)}`),
    ]);

    state.data.tasks = status.tasks || [];
    state.data.localLog = localLog || "";
    state.data.ngrokLog = ngrokLog || "";
    state.data.stats = statsResp.stats || {};
    state.meta.lastRefresh = new Date().toLocaleTimeString();
    state.meta.error = "";
    tick = FALLBACK_REFRESH_SECONDS;
    render();
  } catch (err) {
    state.meta.error = `Refresh error: ${err.message}`;
    render();
  }
}

function connectLiveEvents() {
  if (eventSource) {
    eventSource.close();
  }

  eventSource = new EventSource("/api/events");
  eventSource.onopen = () => setConnectionStatus("connected");
  eventSource.addEventListener("update", async () => {
    await refreshAll();
  });
  eventSource.onerror = () => {
    setConnectionStatus("reconnecting");
  };
}

async function taskAction(action) {
  if (action === "restart-all" && !window.confirm("Restart services?")) {
    return;
  }

  try {
    setBusy(true);
    setActionResult("Running action...");
    const response = await fetch(`/api/tasks/${action}`, { method: "POST" });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      throw new Error(data.error || `Action failed (${response.status})`);
    }
    state.meta.actionMessage = `${action} completed.`;
    state.meta.error = "";
    await refreshAll();
  } catch (err) {
    state.meta.error = err.message;
    state.meta.actionMessage = `Error: ${err.message}`;
    render();
  } finally {
    setBusy(false);
    render();
  }
}

function scrollLogsToLatest() {
  for (const targetId of ["logs-local", "logs-ngrok"]) {
    const target = byId(targetId);
    if (target) {
      target.scrollTop = target.scrollHeight;
    }
  }
}

function bindActions() {
  const startBtn = byId("start-all");
  const stopBtn = byId("stop-all");
  const restartBtn = byId("restart-btn");
  const toggleScrollBtn = byId("toggle-scroll");
  const jumpLatestBtn = byId("jump-latest");
  const clearFiltersBtn = byId("clear-filters");
  const ipSortEl = byId("ip-sort");
  const ipOrderEl = byId("ip-order");
  const filterIpEl = byId("filter-ip");
  const filterSiteEl = byId("filter-site");
  const filterStatusEl = byId("filter-status");
  const statsTable = byId("stats-table");

  if (startBtn) startBtn.addEventListener("click", () => taskAction("start-all"));
  if (stopBtn) stopBtn.addEventListener("click", () => taskAction("stop-all"));
  if (restartBtn) restartBtn.addEventListener("click", () => taskAction("restart-all"));

  if (toggleScrollBtn) {
    toggleScrollBtn.addEventListener("click", () => {
      state.ui.logsPaused = !state.ui.logsPaused;
      render();
      if (!state.ui.logsPaused) {
        scrollLogsToLatest();
      }
    });
  }

  if (jumpLatestBtn) {
    jumpLatestBtn.addEventListener("click", () => {
      state.ui.logsPaused = false;
      render();
      scrollLogsToLatest();
    });
  }

  if (ipSortEl) {
    ipSortEl.addEventListener("change", async () => {
      state.sort.ipSort = ipSortEl.value || "requests";
      render();
      await refreshAll();
    });
  }

  if (ipOrderEl) {
    ipOrderEl.addEventListener("change", async () => {
      state.sort.ipOrder = ipOrderEl.value || "desc";
      render();
      await refreshAll();
    });
  }

  if (filterIpEl) {
    filterIpEl.addEventListener("change", async () => {
      state.filters.ip = filterIpEl.value || "";
      setUrlFilters();
      render();
      await refreshAll();
    });
  }

  if (filterSiteEl) {
    filterSiteEl.addEventListener("change", async () => {
      state.filters.site = filterSiteEl.value || "";
      setUrlFilters();
      render();
      await refreshAll();
    });
  }

  if (filterStatusEl) {
    filterStatusEl.addEventListener("change", async () => {
      state.filters.status = filterStatusEl.value || "";
      setUrlFilters();
      render();
      await refreshAll();
    });
  }

  if (clearFiltersBtn) {
    clearFiltersBtn.addEventListener("click", async () => {
      state.filters = { ip: "", site: "", status: "" };
      setUrlFilters();
      render();
      await refreshAll();
    });
  }

  if (statsTable) {
    statsTable.addEventListener("click", (event) => {
      const sortHeader = event.target.closest("th[data-sort]");
      if (sortHeader) {
        const nextSort = sortHeader.dataset.sort || "requests";
        if (state.sort.ipSort === nextSort) {
          state.sort.ipOrder = state.sort.ipOrder === "asc" ? "desc" : "asc";
        } else {
          state.sort.ipSort = nextSort;
        }
        render();
        refreshAll();
        return;
      }

      const row = event.target.closest("tr[data-ip]");
      if (row && row.dataset.ip) {
        state.filters.ip = row.dataset.ip;
        setUrlFilters();
        render();
        refreshAll();
      }
    });
  }
}

function startCountdown() {
  if (countdownTimer) {
    clearInterval(countdownTimer);
  }
  countdownTimer = setInterval(() => {
    tick = Math.max(0, tick - 1);
    const nextRefresh = byId("next-refresh");
    if (nextRefresh) {
      nextRefresh.textContent = state.connection === "connected"
        ? "Live updates connected"
        : `Fallback refresh in ${tick}s`;
    }
  }, 1000);
}

async function boot() {
  readUrlFilters();
  bindActions();
  render();
  await loadDimensions();
  connectLiveEvents();
  await refreshAll();
  startCountdown();
  if (refreshTimer) {
    clearInterval(refreshTimer);
  }
  refreshTimer = setInterval(refreshAll, FALLBACK_REFRESH_SECONDS * 1000);
}

boot();
