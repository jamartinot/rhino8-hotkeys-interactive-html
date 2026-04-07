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
    nextRefreshSeconds: FALLBACK_REFRESH_SECONDS,
  },
};

let tick = FALLBACK_REFRESH_SECONDS;
let eventSource = null;

const dom = {};
const lastRender = {};

function byId(id) {
  if (!(id in dom)) {
    dom[id] = document.getElementById(id);
  }
  return dom[id];
}

function setUrlFilters() {
  const url = new URL(window.location.href);
  const pairs = [
    ["ip", state.filters.ip],
    ["site", state.filters.site],
    ["status", state.filters.status],
  ];

  for (const [key, value] of pairs) {
    if (value) {
      url.searchParams.set(key, value);
    } else {
      url.searchParams.delete(key);
    }
  }

  window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
}

function readUrlFilters() {
  const params = new URLSearchParams(window.location.search);
  state.filters.ip = params.get("ip") || "";
  state.filters.site = params.get("site") || "";
  state.filters.status = params.get("status") || "";
}

function syncSelectValue(selectId, value) {
  const select = byId(selectId);
  if (select) {
    select.value = value || "";
  }
}

function formatFilters() {
  const parts = [];
  if (state.filters.ip) parts.push(`IP: ${state.filters.ip}`);
  if (state.filters.site) parts.push(`Site: ${state.filters.site}`);
  if (state.filters.status) parts.push(`Status: ${state.filters.status}`);
  return parts.length ? parts.join(" | ") : "No active filters";
}

function applyFilterChange(nextFilters) {
  state.filters = { ...state.filters, ...nextFilters };
  setUrlFilters();
  render();
  refreshAll();
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
}

function setBusy(value) {
  state.ui.actionBusy = value;
}

function renderSection(key, signature, fn) {
  if (lastRender[key] === signature) {
    return;
  }
  lastRender[key] = signature;
  fn();
}

function renderConnectionBadge() {
  const badge = byId("conn-status");
  if (!badge) return;

  badge.classList.remove("connected", "reconnecting", "offline");
  badge.classList.add(state.connection);

  if (state.connection === "connected") {
    badge.textContent = "● Live";
  } else if (state.connection === "offline") {
    badge.textContent = "● Offline";
  } else {
    badge.textContent = "● Reconnecting";
  }
}

function renderStatusCards() {
  const wrap = byId("status-cards");
  if (!wrap) return;

  const fragment = document.createDocumentFragment();
  const tasks = state.data.tasks || [];

  if (!tasks.length) {
    const empty = document.createElement("article");
    empty.className = "status-card";
    empty.textContent = "No task data.";
    fragment.appendChild(empty);
    wrap.replaceChildren(fragment);
    return;
  }

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
        ["Next Run", task.NextRunTime || "-"] ,
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

  const previousScrollTop = target.scrollTop;
  const lines = String(rawText || "").split(/\r?\n/);
  const limited = lines.slice(-MAX_LOG_LINES);
  target.textContent = limited.join("\n") || "(empty)";

  if (state.ui.logsPaused) {
    target.scrollTop = previousScrollTop;
  } else {
    target.scrollTop = target.scrollHeight;
  }
}

function renderBars(targetId, items, cls, titleMap = {}) {
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

    const label = document.createElement("div");
    label.className = "label";
    label.textContent = item.label;
    if (titleMap[item.label]) {
      label.title = titleMap[item.label];
    }

    const barWrap = document.createElement("div");
    barWrap.className = "bar-wrap";
    const bar = document.createElement("div");
    bar.className = `bar ${cls}`;
    bar.style.width = `${((Number(item.value) || 0) / max) * 100}%`;
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

  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  const headers = [
    ["ip", "IP"],
    ["requests", "Requests"],
    ["sites", "Sites"],
  ];

  for (const [sortKey, label] of headers) {
    const th = document.createElement("th");
    th.textContent = label;
    th.dataset.sort = sortKey;
    th.className = "sortable";
    th.dataset.active = String(state.sort.ipSort === sortKey);
    if (state.sort.ipSort === sortKey) {
      th.textContent = `${label} ${state.sort.ipOrder === "asc" ? "▲" : "▼"}`;
    }
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);

  const tbody = document.createElement("tbody");
  for (const item of rows) {
    const tr = document.createElement("tr");
    tr.dataset.ip = item.label;

    const cells = [item.label, item.requests, item.sites];
    for (const cellValue of cells) {
      const td = document.createElement("td");
      td.textContent = String(cellValue);
      tr.appendChild(td);
    }

    tbody.appendChild(tr);
  }

  table.append(thead, tbody);
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

  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  ["Time", "Method", "Status", "Path", "IP"].forEach((label) => {
    const th = document.createElement("th");
    th.textContent = label;
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);

  const tbody = document.createElement("tbody");
  for (const item of entries) {
    const row = document.createElement("tr");

    const timeCell = document.createElement("td");
    timeCell.textContent = item.dt || "";

    const methodCell = document.createElement("td");
    methodCell.textContent = item.method || "";

    const statusCell = document.createElement("td");
    const code = String(item.status || "");
    const explanation = state.data.stats.status_explanations?.[code] || state.data.dimensions.status_explanations?.[code] || "Standard HTTP status code.";
    const statusSpan = document.createElement("span");
    statusSpan.className = "status-pill";
    statusSpan.title = explanation;
    statusSpan.textContent = code;
    statusCell.appendChild(statusSpan);

    const pathCell = document.createElement("td");
    pathCell.textContent = item.path || "";
    if (item.path) {
      pathCell.title = item.path;
    }

    const ipCell = document.createElement("td");
    ipCell.textContent = item.ip || "";

    row.append(timeCell, methodCell, statusCell, pathCell, ipCell);
    tbody.appendChild(row);
  }

  table.append(thead, tbody);
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

  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  ["Status", "Explanation"].forEach((label) => {
    const th = document.createElement("th");
    th.textContent = label;
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);

  const tbody = document.createElement("tbody");
  for (const [code, explanation] of entries) {
    const row = document.createElement("tr");
    const status = document.createElement("td");
    status.textContent = code;
    const text = document.createElement("td");
    text.textContent = explanation;
    row.append(status, text);
    tbody.appendChild(row);
  }

  table.append(thead, tbody);
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
  if (toggleScroll) {
    toggleScroll.textContent = state.ui.logsPaused ? "Resume" : "Pause";
  }

  const restartBtn = byId("restart-btn");
  if (restartBtn) {
    restartBtn.disabled = state.ui.actionBusy;
  }

  const startBtn = byId("start-all");
  const stopBtn = byId("stop-all");
  if (startBtn) startBtn.disabled = state.ui.actionBusy;
  if (stopBtn) stopBtn.disabled = state.ui.actionBusy;
}

function renderHeader() {
  renderConnectionBadge();

  const lastRefresh = byId("last-refresh");
  if (lastRefresh) {
    lastRefresh.textContent = `Last refresh: ${state.meta.lastRefresh}`;
  }

  const nextRefresh = byId("next-refresh");
  if (nextRefresh) {
    if (state.connection === "connected") {
      nextRefresh.textContent = "Live updates connected";
    } else if (state.connection === "offline") {
      nextRefresh.textContent = `Live updates offline; fallback refresh in ${tick}s`;
    } else {
      nextRefresh.textContent = `Live updates reconnecting; fallback refresh in ${tick}s`;
    }
  }

  setBanner(state.meta.error, Boolean(state.meta.error));

  const actionResult = byId("action-result");
  if (actionResult) {
    actionResult.textContent = state.meta.actionMessage;
    actionResult.style.color = state.meta.actionMessage && state.meta.actionMessage.startsWith("Error") ? "#8a1f2d" : "#0f5132";
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
  const explanations = stats.status_explanations || {};

  renderBars("chart-files", stats.top_files || [], "files");
  renderBars("chart-methods", stats.methods || [], "methods");
  renderBars("chart-status", stats.status_codes || [], "status", explanations);
  renderBars("chart-families", stats.status_families || [], "families");
  renderStatsTable();
  renderBars("chart-sites-per-ip", stats.sites_per_ip || [], "methods");
  renderBars("chart-hourly", stats.hourly || [], "hourly");
  renderRecentRequests("recent-requests", stats.recent_requests || []);
  renderStatusExplanations("status-explanations", explanations);
}

function render() {
  const headerSignature = JSON.stringify({
    connection: state.connection,
    lastRefresh: state.meta.lastRefresh,
    tick,
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

function fillSelectOptions(selectId, values, currentValue) {
  const select = byId(selectId);
  if (!select) return;

  const items = Array.from(new Set(values || []));
  if (currentValue && !items.includes(currentValue)) {
    items.unshift(currentValue);
  }

  const fragment = document.createDocumentFragment();
  const allOption = document.createElement("option");
  allOption.value = "";
  allOption.textContent = "All";
  fragment.appendChild(allOption);

  for (const value of items) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    fragment.appendChild(option);
  }

  select.replaceChildren(fragment);
  select.value = currentValue || "";
}

function updateDimensionsControls() {
  const dimensions = state.data.dimensions || {};
  fillSelectOptions("filter-ip", dimensions.ips || [], state.filters.ip);
  fillSelectOptions("filter-site", dimensions.sites || [], state.filters.site);
  fillSelectOptions("filter-status", dimensions.statuses || [], state.filters.status);
}

async function loadDimensions() {
  try {
    const payload = await fetchJson("/api/dimensions");
    state.data.dimensions = payload.dimensions || {};
    updateDimensionsControls();
    render();
  } catch (err) {
    state.meta.error = `Dimensions error: ${err.message}`;
    render();
  }
}

async function fetchText(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${url} failed: ${response.status}`);
  }
  return response.text();
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${url} failed: ${response.status}`);
  }
  return response.json();
}

async function refreshAll() {
  try {
    const [status, localLog, ngrokLog, statsResp] = await Promise.all([
      fetchJson("/api/status"),
      fetchText("/api/log/local?tail=400"),
      fetchText("/api/log/ngrok?tail=400"),
      fetchJson(
        `/api/stats?top=20&ip_sort=${encodeURIComponent(state.sort.ipSort)}&ip_order=${encodeURIComponent(state.sort.ipOrder)}&ip=${encodeURIComponent(state.filters.ip)}&site=${encodeURIComponent(state.filters.site)}&status=${encodeURIComponent(state.filters.status)}`
      ),
    ]);

    state.data.tasks = status.tasks || [];
    state.data.localLog = localLog || "";
    state.data.ngrokLog = ngrokLog || "";
    state.data.stats = statsResp.stats || {};
    state.meta.lastRefresh = new Date().toLocaleTimeString();
    state.meta.error = "";
    tick = FALLBACK_REFRESH_SECONDS;
    state.meta.nextRefreshSeconds = tick;
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

  setConnectionStatus("reconnecting");
  render();

  eventSource = new EventSource("/api/events");
  eventSource.onopen = () => {
    setConnectionStatus("connected");
    render();
  };
  eventSource.addEventListener("update", async () => {
    await refreshAll();
  });
  eventSource.onerror = () => {
    setConnectionStatus(eventSource.readyState === EventSource.CLOSED ? "offline" : "reconnecting");
    render();
  };
}

async function taskAction(action) {
  if (action === "restart-all" && !window.confirm("Restart services?")) {
    return;
  }

  try {
    setBusy(true);
    setActionResult("Running action...");
    render();

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
    filterIpEl.addEventListener("change", () => {
      applyFilterChange({ ip: filterIpEl.value || "" });
    });
  }

  if (filterSiteEl) {
    filterSiteEl.addEventListener("change", () => {
      applyFilterChange({ site: filterSiteEl.value || "" });
    });
  }

  if (filterStatusEl) {
    filterStatusEl.addEventListener("change", () => {
      applyFilterChange({ status: filterStatusEl.value || "" });
    });
  }

  if (clearFiltersBtn) {
    clearFiltersBtn.addEventListener("click", () => {
      state.filters = { ip: "", site: "", status: "" };
      setUrlFilters();
      render();
      refreshAll();
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
        applyFilterChange({ ip: row.dataset.ip });
      }
    });
  }
}

function startCountdown() {
  setInterval(() => {
    tick = Math.max(0, tick - 1);
    state.meta.nextRefreshSeconds = tick;
    render();
  }, 1000);
}

async function boot() {
  readUrlFilters();
  updateDimensionsControls();
  bindActions();
  render();
  await loadDimensions();
  connectLiveEvents();
  await refreshAll();
  startCountdown();
  setInterval(refreshAll, FALLBACK_REFRESH_SECONDS * 1000);
}

boot();
