const FALLBACK_REFRESH_SECONDS = 60;
const MAX_LOG_LINES = 500;
const FILTER_DEBOUNCE_MS = 300;

const state = {
  stats: null,
  dimensions: null,
  logs: {
    local: "",
    ngrok: "",
  },
  filters: {
    ip: "",
    site: "",
    status: "",
  },
  sort: {
    ipSort: "requests",
    ipOrder: "desc",
  },
  connection: {
    status: "offline",
    retryDelayMs: 1000,
    retryTimer: null,
    eventSource: null,
  },
  ui: {
    autoScroll: true,
    bannerType: "info",
    bannerText: "",
  },
  requests: {
    controller: null,
    debounceTimer: null,
    refreshInFlight: false,
    lastRefreshAt: null,
  },
};

function byId(id) {
  return document.getElementById(id);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function setBanner(text, type = "info") {
  state.ui.bannerText = text;
  state.ui.bannerType = type;
  const banner = byId("global-banner");
  if (!banner) {
    return;
  }
  banner.textContent = text;
  banner.dataset.type = type;
}

function setActionResult(text, isError = false) {
  const el = byId("action-result");
  el.textContent = text;
  el.dataset.type = isError ? "error" : "ok";
}

function setConnectionStatus(status, detail = "") {
  state.connection.status = status;
  const el = byId("connection-status");
  if (!el) return;
  el.dataset.status = status;
  el.textContent = detail || status;
}

function setLoading(isLoading) {
  document.body.dataset.loading = isLoading ? "true" : "false";
  for (const id of ["start-all", "stop-all", "restart-all"]) {
    const button = byId(id);
    if (button) button.disabled = isLoading;
  }
}

function setUrlFromState() {
  const url = new URL(window.location.href);
  const params = url.searchParams;
  const setParam = (key, value) => {
    if (value) params.set(key, value);
    else params.delete(key);
  };

  setParam("ip", state.filters.ip);
  setParam("site", state.filters.site);
  setParam("status", state.filters.status);
  setParam("ip_sort", state.sort.ipSort);
  setParam("ip_order", state.sort.ipOrder);
  setParam("auto_scroll", state.ui.autoScroll ? "1" : "0");
  history.replaceState({}, "", url);
}

function applyStateFromUrl() {
  const params = new URLSearchParams(window.location.search);
  state.filters.ip = params.get("ip") || "";
  state.filters.site = params.get("site") || "";
  state.filters.status = params.get("status") || "";
  state.sort.ipSort = params.get("ip_sort") || "requests";
  state.sort.ipOrder = params.get("ip_order") || "desc";
  const autoScroll = params.get("auto_scroll");
  state.ui.autoScroll = autoScroll == null ? true : autoScroll !== "0";
}

function updateActiveFilters() {
  const target = byId("active-filters");
  if (!target) return;
  const entries = [];
  if (state.filters.ip) entries.push(`IP: ${escapeHtml(state.filters.ip)}`);
  if (state.filters.site) entries.push(`Site: ${escapeHtml(state.filters.site)}`);
  if (state.filters.status) entries.push(`Status: ${escapeHtml(state.filters.status)}`);
  target.innerHTML = entries.length ? entries.join(" <span class='sep'>•</span> ") : "No active filters";
}

function updateControlsFromState() {
  const bindValue = (id, value) => {
    const el = byId(id);
    if (el) el.value = value;
  };
  bindValue("filter-ip", state.filters.ip);
  bindValue("filter-site", state.filters.site);
  bindValue("filter-status", state.filters.status);
  bindValue("ip-sort", state.sort.ipSort);
  bindValue("ip-order", state.sort.ipOrder);
  const autoScroll = byId("auto-scroll");
  if (autoScroll) autoScroll.checked = state.ui.autoScroll;
  updateActiveFilters();
}

function showError(message) {
  setBanner(message, "error");
}

function showInfo(message) {
  setBanner(message, "info");
}

async function readErrorMessage(response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    try {
      const payload = await response.json();
      return payload.error || `Request failed (${response.status})`;
    } catch {
      return `Request failed (${response.status})`;
    }
  }
  try {
    const text = await response.text();
    return text || `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return response.json();
}

async function fetchText(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return response.text();
}

function scheduleRefresh(delay = FILTER_DEBOUNCE_MS) {
  if (state.requests.debounceTimer) {
    clearTimeout(state.requests.debounceTimer);
  }
  state.requests.debounceTimer = setTimeout(() => {
    state.requests.debounceTimer = null;
    refreshAll();
  }, delay);
}

function abortCurrentRefresh() {
  if (state.requests.controller) {
    state.requests.controller.abort();
  }
}

function renderStatus(tasks) {
  const wrap = byId("status-cards");
  wrap.replaceChildren();

  if (!tasks || tasks.length === 0) {
    const empty = document.createElement("article");
    empty.className = "status-card empty-state";
    empty.textContent = "No task data.";
    wrap.appendChild(empty);
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
      kv.append(makeKeyValue("Error", task.Error, true));
    } else {
      kv.append(
        makeKeyValue("State", task.State || "-"),
        makeKeyValue("Last Result", task.LastTaskResult ?? "-"),
        makeKeyValue("Last Run", task.LastRunTime || "-"),
        makeKeyValue("Next Run", task.NextRunTime || "-")
      );
    }
    card.appendChild(kv);
    wrap.appendChild(card);
  }
}

function makeKeyValue(label, value, isError = false) {
  const key = document.createElement("span");
  key.className = "k";
  key.textContent = label;

  const val = document.createElement("span");
  val.className = isError ? "v error" : "v";
  val.textContent = value;

  return [key, val].reduce((fragment, node) => {
    fragment.appendChild(node);
    return fragment;
  }, document.createDocumentFragment());
}

function renderBars(targetId, items, cls, options = {}) {
  const target = byId(targetId);
  target.replaceChildren();

  if (!items || items.length === 0) {
    target.textContent = "No data yet.";
    return;
  }

  const max = Math.max(1, ...items.map((item) => item.value));
  for (const item of items) {
    const width = (item.value / max) * 100;
    const row = document.createElement(options.clickable ? "button" : "div");
    if (options.clickable) {
      row.type = "button";
    }
    row.className = options.clickable ? "bar-row interactive" : "bar-row";
    row.title = options.clickable ? options.tooltip(item) : item.label;
    row.dataset.label = item.label;
    row.dataset.value = String(item.value);
    row.innerHTML = `
      <div class="label">${escapeHtml(item.label)}</div>
      <div class="bar-wrap"><div class="bar ${cls}" style="width:${width}%"></div></div>
      <div class="val">${escapeHtml(item.value)}</div>
    `;
    if (options.clickable) {
      row.addEventListener("click", () => options.onClick(item));
    }
    target.appendChild(row);
  }
}

function renderRecentRequests(targetId, items) {
  const target = byId(targetId);
  target.replaceChildren();

  if (!items || items.length === 0) {
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
  for (const item of items.slice(0, 25)) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${escapeHtml(item.dt)}</td>
      <td>${escapeHtml(item.method)}</td>
      <td title="${escapeHtml(resolveStatusExplanation(item.status))}">${escapeHtml(item.status)}</td>
      <td title="${escapeHtml(item.path)}">${escapeHtml(item.path)}</td>
      <td>${escapeHtml(item.ip)}</td>
    `;
    body.appendChild(row);
  }

  target.appendChild(table);
}

function resolveStatusExplanation(code) {
  const explanations = state.stats?.status_explanations || {};
  return explanations[code] || "Standard HTTP status code.";
}

function renderStatusExplanations(targetId, explanations) {
  const target = byId(targetId);
  target.replaceChildren();
  const entries = Object.entries(explanations || {});
  if (entries.length === 0) {
    target.textContent = "No status codes in current selection.";
    return;
  }

  const table = document.createElement("table");
  table.className = "recent-table";
  table.innerHTML = "<thead><tr><th>Status</th><th>Explanation</th></tr></thead><tbody></tbody>";
  const body = table.querySelector("tbody");
  for (const [code, explanation] of entries) {
    const row = document.createElement("tr");
    row.innerHTML = `<td title="${escapeHtml(explanation)}">${escapeHtml(code)}</td><td>${escapeHtml(explanation)}</td>`;
    body.appendChild(row);
  }
  target.appendChild(table);
}

function renderLog(targetId, text) {
  const target = byId(targetId);
  target.replaceChildren();
  const lines = String(text || "")
    .split(/\r?\n/)
    .filter(Boolean)
    .slice(-MAX_LOG_LINES);

  if (lines.length === 0) {
    target.textContent = "(empty)";
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const line of lines) {
    const row = document.createElement("div");
    row.className = `log-line ${statusClassForLine(line)}`;
    row.innerHTML = escapeHtml(line);
    fragment.appendChild(row);
  }
  target.appendChild(fragment);

  if (state.ui.autoScroll) {
    target.scrollTop = target.scrollHeight;
  }
}

function statusClassForLine(line) {
  const match = line.match(/\s(\d{3})\s/);
  if (!match) return "status-plain";
  const code = Number(match[1]);
  if (code >= 500) return "status-5xx";
  if (code >= 400) return "status-4xx";
  if (code >= 200) return "status-2xx";
  return "status-plain";
}

function fillSelectOptions(selectId, values, currentValue) {
  const select = byId(selectId);
  if (!select) return;
  select.replaceChildren();
  const allOption = document.createElement("option");
  allOption.value = "";
  allOption.textContent = "All";
  select.appendChild(allOption);

  for (const value of values || []) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    if (value === currentValue) {
      option.selected = true;
    }
    select.appendChild(option);
  }
}

async function loadDimensions() {
  const payload = await fetchJson("/api/dimensions");
  state.dimensions = payload.dimensions || {};
  fillSelectOptions("filter-ip", state.dimensions.ips || [], state.filters.ip);
  fillSelectOptions("filter-site", state.dimensions.sites || [], state.filters.site);
  fillSelectOptions("filter-status", state.dimensions.statuses || [], state.filters.status);
}

function buildStatsUrl() {
  const params = new URLSearchParams({
    top: "20",
    ip_sort: state.sort.ipSort,
    ip_order: state.sort.ipOrder,
  });
  if (state.filters.ip) params.set("ip", state.filters.ip);
  if (state.filters.site) params.set("site", state.filters.site);
  if (state.filters.status) params.set("status", state.filters.status);
  return `/api/stats?${params.toString()}`;
}

async function refreshAll() {
  abortCurrentRefresh();
  const controller = new AbortController();
  state.requests.controller = controller;
  state.requests.refreshInFlight = true;
  setLoading(true);

  try {
    const [status, localLog, ngrokLog, statsResp] = await Promise.all([
      fetchJson("/api/status", { signal: controller.signal }),
      fetchText("/api/log/local?tail=120", { signal: controller.signal }),
      fetchText("/api/log/ngrok?tail=120", { signal: controller.signal }),
      fetchJson(buildStatsUrl(), { signal: controller.signal }),
    ]);

    if (controller.signal.aborted) {
      return;
    }

    renderStatus(status.tasks || []);
    state.logs.local = localLog;
    state.logs.ngrok = ngrokLog;
    renderLog("local-log", state.logs.local);
    renderLog("ngrok-log", state.logs.ngrok);

    state.stats = statsResp.stats || {};
    renderBars("chart-files", state.stats.top_files || [], "files", {
      clickable: true,
      tooltip: (item) => `Filter to ${item.label}`,
      onClick: (item) => setFilter("site", item.label),
    });
    renderBars("chart-methods", state.stats.methods || [], "methods");
    renderBars("chart-status", state.stats.status_codes || [], "status", {
      clickable: true,
      tooltip: (item) => resolveStatusExplanation(item.label),
      onClick: (item) => setFilter("status", item.label),
    });
    renderBars("chart-families", state.stats.status_families || [], "families");
    renderBars("chart-ips", state.stats.top_ips || [], "ips", {
      clickable: true,
      tooltip: (item) => `Filter to ${item.label}`,
      onClick: (item) => setFilter("ip", item.label),
    });
    renderBars("chart-sites-per-ip", state.stats.sites_per_ip || [], "methods");
    renderBars("chart-hourly", state.stats.hourly || [], "hourly");
    renderRecentRequests("recent-requests", state.stats.recent_requests || []);
    renderStatusExplanations("status-explanations", state.stats.status_explanations || {});

    state.requests.lastRefreshAt = new Date();
    byId("last-refresh").textContent = `Last refresh: ${state.requests.lastRefreshAt.toLocaleTimeString()}`;
  } catch (error) {
    if (error.name !== "AbortError") {
      showError(`Refresh failed: ${error.message}`);
    }
  } finally {
    if (state.requests.controller === controller) {
      state.requests.controller = null;
      state.requests.refreshInFlight = false;
      setLoading(false);
    }
  }
}

function setFilter(kind, value) {
  state.filters[kind] = value || "";
  updateControlsFromState();
  setUrlFromState();
  scheduleRefresh();
}

function bindFilterControl(selectId, kind) {
  const select = byId(selectId);
  if (!select) return;
  select.addEventListener("change", () => setFilter(kind, select.value || ""));
}

function scheduleReconnect() {
  if (state.connection.retryTimer) {
    return;
  }
  const delay = state.connection.retryDelayMs;
  setConnectionStatus("reconnecting", `reconnecting in ${Math.round(delay / 1000)}s`);
  state.connection.retryTimer = setTimeout(() => {
    state.connection.retryTimer = null;
    connectLiveEvents();
  }, delay);
  state.connection.retryDelayMs = Math.min(state.connection.retryDelayMs * 2, 30000);
}

function connectLiveEvents() {
  if (state.connection.eventSource) {
    state.connection.eventSource.close();
  }

  const source = new EventSource("/api/events");
  state.connection.eventSource = source;
  setConnectionStatus("connecting", "connecting");

  source.onopen = () => {
    state.connection.retryDelayMs = 1000;
    setConnectionStatus("connected", "connected");
    if (state.connection.retryTimer) {
      clearTimeout(state.connection.retryTimer);
      state.connection.retryTimer = null;
    }
  };

  source.addEventListener("update", () => {
    scheduleRefresh(0);
  });

  source.onerror = () => {
    setConnectionStatus("offline", "connection lost");
    source.close();
    state.connection.eventSource = null;
    scheduleReconnect();
  };
}

function updateCountdown() {
  const nextRefresh = byId("next-refresh");
  if (!nextRefresh) return;
  if (state.connection.status === "connected") {
    nextRefresh.textContent = "Live updates connected";
    return;
  }

  const remaining = Math.max(0, FALLBACK_REFRESH_SECONDS - Math.floor((Date.now() - (state.requests.lastRefreshAt?.getTime() || 0)) / 1000));
  nextRefresh.textContent = `Fallback refresh in ${remaining}s`;
}

async function taskAction(action) {
  const riskyActions = new Set(["stop-all", "restart-all"]);
  if (riskyActions.has(action) && !window.confirm(`Confirm ${action.replace("-", " ")} ?`)) {
    return;
  }

  try {
    setActionResult("Running action...");
    setLoading(true);
    const response = await fetch(`/api/tasks/${action}`, { method: "POST" });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      throw new Error(data.error || `Action failed (${response.status})`);
    }
    setActionResult(`${action} completed.`);
    showInfo(`${action} completed.`);
    await refreshAll();
  } catch (error) {
    setActionResult(error.message, true);
    showError(error.message);
  } finally {
    setLoading(false);
  }
}

function bindActions() {
  byId("start-all").addEventListener("click", () => taskAction("start-all"));
  byId("stop-all").addEventListener("click", () => taskAction("stop-all"));
  byId("restart-all").addEventListener("click", () => taskAction("restart-all"));

  const ipSortEl = byId("ip-sort");
  const ipOrderEl = byId("ip-order");
  if (ipSortEl) {
    ipSortEl.addEventListener("change", () => {
      state.sort.ipSort = ipSortEl.value || "requests";
      updateActiveFilters();
      setUrlFromState();
      scheduleRefresh();
    });
  }
  if (ipOrderEl) {
    ipOrderEl.addEventListener("change", () => {
      state.sort.ipOrder = ipOrderEl.value || "desc";
      updateActiveFilters();
      setUrlFromState();
      scheduleRefresh();
    });
  }

  bindFilterControl("filter-ip", "ip");
  bindFilterControl("filter-site", "site");
  bindFilterControl("filter-status", "status");

  const clearFiltersEl = byId("clear-filters");
  if (clearFiltersEl) {
    clearFiltersEl.addEventListener("click", () => {
      state.filters.ip = "";
      state.filters.site = "";
      state.filters.status = "";
      updateControlsFromState();
      setUrlFromState();
      scheduleRefresh(0);
    });
  }

  const autoScrollEl = byId("auto-scroll");
  if (autoScrollEl) {
    autoScrollEl.addEventListener("change", () => {
      state.ui.autoScroll = autoScrollEl.checked;
      setUrlFromState();
      renderLog("local-log", state.logs.local);
      renderLog("ngrok-log", state.logs.ngrok);
    });
  }
}

async function boot() {
  applyStateFromUrl();
  updateControlsFromState();
  bindActions();
  connectLiveEvents();

  try {
    await loadDimensions();
  } catch (error) {
    showError(`Failed to load filters: ${error.message}`);
  }

  await refreshAll();
  window.addEventListener("popstate", () => {
    applyStateFromUrl();
    updateControlsFromState();
    refreshAll();
  });

  setInterval(updateCountdown, 1000);
  setInterval(() => {
    if (state.connection.status !== "connected") {
      scheduleRefresh(0);
    }
  }, FALLBACK_REFRESH_SECONDS * 1000);
}

boot();
