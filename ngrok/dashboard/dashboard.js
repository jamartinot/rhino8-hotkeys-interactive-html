const FALLBACK_REFRESH_SECONDS = 60;
const MAX_LOG_LINES = 500;
const FILTER_DEBOUNCE_MS = 300;

const state = {
  stats: null,
  dimensions: null,
  alerts: [],
  rules: [],
  attackReport: null,
  attackScan: null,
  attackDefaultTarget: "",
  attackScanForm: {
    target: "",
    profile: "standard",
    burstRequests: 80,
    burstConcurrency: 16,
    timeout: 8,
    allowPublicTarget: true,
  },
  logs: {
    local: "",
    ngrok: "",
    rows: [],
  },
  filters: {
    ip: "",
    site: "",
    status: "",
    methods: [],
    statusFamily: "",
    q: "",
  },
  logsView: {
    source: "local",
    sortBy: "dt",
    order: "desc",
    q: "",
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
    activeTab: "overview",
  },
  requests: {
    controller: null,
    debounceTimer: null,
    refreshInFlight: false,
    lastRefreshAt: null,
  },
  charts: {
    hourly: null,
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
  const banner = byId("global-banner");
  if (!banner) return;
  banner.textContent = text;
  banner.dataset.type = type;
}

function showError(message) {
  setBanner(message, "error");
}

function showInfo(message) {
  setBanner(message, "info");
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

function setTab(tabName) {
  state.ui.activeTab = tabName;
  for (const tab of document.querySelectorAll(".tab")) {
    tab.classList.toggle("is-active", tab.dataset.tab === tabName);
  }
  for (const panel of document.querySelectorAll("[data-panel]")) {
    panel.classList.toggle("hidden", panel.dataset.panel !== tabName);
  }
  if (tabName === "overview" && state.charts.hourly) {
    requestAnimationFrame(() => state.charts.hourly.resize());
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
  setParam("methods", state.filters.methods.join(","));
  setParam("status_family", state.filters.statusFamily);
  setParam("q", state.filters.q);
  setParam("ip_sort", state.sort.ipSort);
  setParam("ip_order", state.sort.ipOrder);
  setParam("tab", state.ui.activeTab);
  setParam("auto_scroll", state.ui.autoScroll ? "1" : "0");
  history.replaceState({}, "", url);
}

function applyStateFromUrl() {
  const params = new URLSearchParams(window.location.search);
  state.filters.ip = params.get("ip") || "";
  state.filters.site = params.get("site") || "";
  state.filters.status = params.get("status") || "";
  state.filters.methods = (params.get("methods") || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  state.filters.statusFamily = params.get("status_family") || "";
  state.filters.q = params.get("q") || "";
  state.sort.ipSort = params.get("ip_sort") || "requests";
  state.sort.ipOrder = params.get("ip_order") || "desc";
  state.ui.activeTab = params.get("tab") || "overview";
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
  if (state.filters.methods.length) entries.push(`Methods: ${escapeHtml(state.filters.methods.join(","))}`);
  if (state.filters.statusFamily) entries.push(`Family: ${escapeHtml(state.filters.statusFamily)}`);
  if (state.filters.q) entries.push(`Text: ${escapeHtml(state.filters.q)}`);
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
  bindValue("filter-family", state.filters.statusFamily);
  bindValue("filter-q", state.filters.q);
  bindValue("ip-sort", state.sort.ipSort);
  bindValue("ip-order", state.sort.ipOrder);
  bindValue("logs-source", state.logsView.source);
  bindValue("logs-sort", state.logsView.sortBy);
  bindValue("logs-order", state.logsView.order);
  bindValue("logs-search", state.logsView.q);

  const methodSelect = byId("filter-method");
  if (methodSelect) {
    for (const option of methodSelect.options) {
      option.selected = state.filters.methods.includes(option.value);
    }
  }
  const autoScroll = byId("auto-scroll");
  if (autoScroll) autoScroll.checked = state.ui.autoScroll;
  updateActiveFilters();
  setTab(state.ui.activeTab);
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
    if (options.clickable) row.type = "button";
    row.className = options.clickable ? "bar-row interactive" : "bar-row";
    row.title = options.clickable ? options.tooltip(item) : item.label;
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

function resolveStatusExplanation(code) {
  const explanations = state.stats?.status_explanations || {};
  return explanations[code] || "Standard HTTP status code.";
}

function buildClickableTree(treeLines, targetUrl) {
  const container = document.createElement("div");
  container.className = "attack-tree-interactive";

  if (!treeLines || treeLines.length === 0) {
    container.textContent = "No tree structure available";
    return container;
  }

  try {
    const normalized = Array.isArray(treeLines) && typeof treeLines[0] === "object"
      ? treeLines
      : treeLines.map((line) => ({ line }));

    for (const item of normalized) {
      const row = document.createElement("div");
      row.className = "tree-line";

      if (!item || item.is_root || item.line === "/") {
        row.textContent = item?.line || "/";
        container.appendChild(row);
        continue;
      }

      const prefixMatch = String(item.line || "").match(/^([├└│─ ]*)/);
      const prefix = prefixMatch ? prefixMatch[1] : "";
      const prefixSpan = document.createElement("span");
      prefixSpan.className = "tree-prefix";
      prefixSpan.textContent = prefix;

      const link = document.createElement(item.url ? "a" : "span");
      link.className = "tree-file-link";
      link.textContent = item.name || String(item.line || "").replace(/^[├└│─\s]+/, "").trim();
      if (item.url) {
        link.href = item.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.title = `Open: ${item.url}`;
      }

      row.append(prefixSpan, link);
      container.appendChild(row);
    }
  } catch (error) {
    console.error("Error building clickable tree:", error);
    for (const line of treeLines) {
      const row = document.createElement("div");
      row.className = "tree-line";
      row.textContent = line;
      container.appendChild(row);
    }
  }

  return container;
}

function getStatusCodeClass(status) {
  const code = Number(status);
  if (code >= 200 && code < 300) return "status-2xx";
  if (code >= 300 && code < 400) return "status-3xx";
  if (code >= 400 && code < 500) return "status-4xx";
  if (code >= 500) return "status-5xx";
  return "status-unknown";
}

function renderProbeIpLogs(targetId, probeIps, allLogs) {
  const target = byId(targetId);
  if (!target) return;

  if (!state.attackReport?.available) {
    target.textContent = "Run an attack simulation to reveal probe IP logs.";
    return;
  }

  if (!probeIps || probeIps.length === 0) {
    target.textContent = "No probe IPs captured yet.";
    return;
  }

  const probeIpSet = new Set(probeIps.map((item) => item.label));
  const probeLogs = (allLogs || []).filter((log) => probeIpSet.has(log.ip));

  if (probeLogs.length === 0) {
    target.textContent = "No probe logs captured in the latest attack simulation.";
    return;
  }

  const table = document.createElement("table");
  table.className = "probe-logs-table";
  table.innerHTML = `
    <thead>
      <tr>
        <th>Time</th>
        <th>IP</th>
        <th>Method</th>
        <th>Status</th>
        <th>Path</th>
      </tr>
    </thead>
    <tbody></tbody>
  `;
  const body = table.querySelector("tbody");

  const sortedLogs = probeLogs.sort((a, b) => {
    const timeA = new Date(a.dt || 0).getTime();
    const timeB = new Date(b.dt || 0).getTime();
    return timeB - timeA;
  }).slice(0, 10);

  for (const log of sortedLogs) {
    const row = document.createElement("tr");
    row.className = `log-row ${getStatusCodeClass(log.status)}`;
    row.innerHTML = `
      <td class="log-time">${escapeHtml(log.dt || "-")}</td>
      <td class="log-ip">${escapeHtml(log.ip || "-")}</td>
      <td class="log-method">${escapeHtml(log.method || "-")}</td>
      <td class="log-status" title="${escapeHtml(resolveStatusExplanation(log.status))}">${escapeHtml(log.status || "-")}</td>
      <td class="log-path" title="${escapeHtml(log.path || "")}">${escapeHtml(log.path || "-")}</td>
    `;
    body.appendChild(row);
  }

  target.replaceChildren(table);
}

async function refreshConnectionHealth() {
  try {
    const target = state.attackDefaultTarget || state.attackReport?.target || "";
    const url = target ? `/api/connection-test?target=${encodeURIComponent(target)}` : "/api/connection-test";
    const payload = await fetchJson(url);
    const checked = Number(payload.checked || 0);
    const required = Number(payload.required || 3);
    const status = payload.connected ? "connected" : "degraded";
    setConnectionStatus(status, `${status} (${checked}/${required})`);
    return payload;
  } catch (error) {
    // Older backend versions may not expose /api/connection-test yet.
    setConnectionStatus("offline", "site check unavailable");
    showError(`Site check unavailable: ${error.message}`);
    return null;
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
  table.innerHTML = "<thead><tr><th>Time</th><th>Method</th><th>Status</th><th>Path</th><th>IP</th><th>Geo</th></tr></thead><tbody></tbody>";
  const body = table.querySelector("tbody");
  for (const item of items.slice(0, 25)) {
    const geo = item.geo ? `${item.geo.country} ${item.geo.city}` : "--";
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${escapeHtml(item.dt || "")}</td>
      <td>${escapeHtml(item.method || "")}</td>
      <td title="${escapeHtml(resolveStatusExplanation(item.status))}">${escapeHtml(item.status || "")}</td>
      <td title="${escapeHtml(item.path || "")}">${escapeHtml(item.path || "")}</td>
      <td>${escapeHtml(item.ip || "")}</td>
      <td>${escapeHtml(geo)}</td>
    `;
    body.appendChild(row);
  }
  target.appendChild(table);
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

function renderSecuritySignals(security) {
  const target = byId("security-signals");
  if (!target) return;
  target.replaceChildren();

  const denied = Number(security?.denied_requests || 0);
  const forbidden = Number(security?.forbidden_requests || 0);
  const unauthorized = Number(security?.unauthorized_requests || 0);
  const notFound = Number(security?.not_found_requests || 0);
  const rateLimited = Number(security?.rate_limited_requests || 0);
  const serverErrors = Number(security?.server_error_requests || 0);
  const probes = Number(security?.bot_probe_requests || 0);
  const apiProbes = Number(security?.api_probe_requests || 0);
  const suspicious = Number(security?.suspicious_requests || 0);
  const uniqueProbeIps = Number(security?.unique_probe_ips || 0);
  const total = Number(state.stats?.total_requests || 0);
  const probeRate = total > 0 ? Math.round((suspicious / total) * 100) : 0;

  const wrap = document.createElement("div");
  wrap.className = "metric-grid";
  wrap.innerHTML = `
    <div class="metric"><div class="metric-label">Denied (401/403)</div><div class="metric-value">${escapeHtml(denied)}</div></div>
    <div class="metric"><div class="metric-label">Rate Limited (429)</div><div class="metric-value">${escapeHtml(rateLimited)}</div></div>
    <div class="metric"><div class="metric-label">Bot Probe Requests</div><div class="metric-value">${escapeHtml(probes)}</div></div>
    <div class="metric"><div class="metric-label">API Probe Requests</div><div class="metric-value">${escapeHtml(apiProbes)}</div></div>
    <div class="metric"><div class="metric-label">Suspicious Requests</div><div class="metric-value">${escapeHtml(suspicious)}</div></div>
    <div class="metric"><div class="metric-label">Probe Rate</div><div class="metric-value">${escapeHtml(probeRate)}%</div></div>
    <div class="metric"><div class="metric-label">Unique Probe IPs</div><div class="metric-value">${escapeHtml(uniqueProbeIps)}</div></div>
    <div class="metric"><div class="metric-label">404 Not Found</div><div class="metric-value">${escapeHtml(notFound)}</div></div>
    <div class="metric"><div class="metric-label">401 Unauthorized</div><div class="metric-value">${escapeHtml(unauthorized)}</div></div>
    <div class="metric"><div class="metric-label">403 Forbidden</div><div class="metric-value">${escapeHtml(forbidden)}</div></div>
    <div class="metric"><div class="metric-label">5xx Errors</div><div class="metric-value">${escapeHtml(serverErrors)}</div></div>
    <div class="metric"><div class="metric-label">Total Requests</div><div class="metric-value">${escapeHtml(total)}</div></div>
  `;
  target.appendChild(wrap);
}

function renderAlerts(alerts) {
  const target = byId("alerts-list");
  target.replaceChildren();
  if (!alerts || alerts.length === 0) {
    target.textContent = "No active alerts.";
    return;
  }
  const wrap = document.createElement("div");
  wrap.className = "alerts-wrap";
  for (const alert of alerts) {
    const row = document.createElement("div");
    row.className = `alert-row ${alert.level || "warning"}`;
    row.innerHTML = `<strong>${escapeHtml(alert.code || "alert")}</strong> ${escapeHtml(alert.message || "")}`;
    wrap.appendChild(row);
  }
  target.appendChild(wrap);
}

function renderSecurityActions(security) {
  const target = byId("security-actions");
  target.replaceChildren();
  const ips = [
    ...(security?.top_probe_ips || []),
    ...(security?.top_api_probe_ips || []),
  ];
  const seen = new Set();
  const uniqueIps = ips
    .filter((item) => item?.label)
    .filter((item) => {
      if (seen.has(item.label)) return false;
      seen.add(item.label);
      return true;
    })
    .slice(0, 15);

  if (uniqueIps.length === 0) {
    target.textContent = "No flagged IPs in current selection.";
    return;
  }

  const table = document.createElement("table");
  table.className = "recent-table";
  table.innerHTML = "<thead><tr><th>IP</th><th>Hits</th><th>Action</th></tr></thead><tbody></tbody>";
  const body = table.querySelector("tbody");

  for (const item of uniqueIps) {
    const row = document.createElement("tr");
    const actions = document.createElement("td");

    const blockBtn = document.createElement("button");
    blockBtn.type = "button";
    blockBtn.className = "btn warn";
    blockBtn.textContent = "Block IP";
    blockBtn.addEventListener("click", () => applySecurityAction("block", item.label));

    const limitBtn = document.createElement("button");
    limitBtn.type = "button";
    limitBtn.className = "btn";
    limitBtn.textContent = "Rate Limit";
    limitBtn.addEventListener("click", () => applySecurityAction("rate-limit", item.label));

    actions.append(blockBtn, limitBtn);
    row.innerHTML = `<td>${escapeHtml(item.label)}</td><td>${escapeHtml(item.value)}</td>`;
    row.appendChild(actions);
    body.appendChild(row);
  }

  target.appendChild(table);
}

function renderRules(rules) {
  const target = byId("rules-list");
  target.replaceChildren();
  if (!rules || rules.length === 0) {
    target.textContent = "No manual rules configured.";
    return;
  }

  const table = document.createElement("table");
  table.className = "recent-table";
  table.innerHTML = "<thead><tr><th>IP</th><th>Action</th><th>Seconds Left</th><th>Config</th><th>Manage</th></tr></thead><tbody></tbody>";
  const body = table.querySelector("tbody");
  for (const rule of rules) {
    const row = document.createElement("tr");
    const cfg = [rule.requests ? `req=${rule.requests}` : "", rule.window ? `win=${rule.window}` : "", rule.ban ? `ban=${rule.ban}` : ""]
      .filter(Boolean)
      .join(" ");
    row.innerHTML = `<td>${escapeHtml(rule.ip || "")}</td><td>${escapeHtml(rule.action || "")}</td><td>${escapeHtml(rule.seconds_left || 0)}</td><td>${escapeHtml(cfg || "-")}</td>`;
    const manage = document.createElement("td");
    const clearBtn = document.createElement("button");
    clearBtn.type = "button";
    clearBtn.className = "btn";
    clearBtn.textContent = "Remove";
    clearBtn.addEventListener("click", () => removeSecurityRule(rule.ip));
    manage.appendChild(clearBtn);
    row.appendChild(manage);
    body.appendChild(row);
  }
  target.appendChild(table);
}

function renderAttackReport(report) {
  const target = byId("attack-report-summary");
  if (!target) return;
  target.replaceChildren();

  if (!report || !report.available || !report.summary) {
    target.textContent = "No attack simulation report detected yet.";
    return;
  }

  const summary = report.summary || {};
  const discovery = report.discovery || {};
  const severity = summary.severity || {};
  const findings = Array.isArray(summary.findings) ? summary.findings : [];
  const wrap = document.createElement("div");
  wrap.className = "attack-summary";
  wrap.innerHTML = `
    <div class="metric-grid">
      <div class="metric"><div class="metric-label">Target</div><div class="metric-value metric-text">${escapeHtml(report.target || "-")}</div></div>
      <div class="metric"><div class="metric-label">Profile</div><div class="metric-value">${escapeHtml(report.profile || "-")}</div></div>
      <div class="metric"><div class="metric-label">Findings</div><div class="metric-value">${escapeHtml(summary.total_findings || 0)}</div></div>
      <div class="metric"><div class="metric-label">High</div><div class="metric-value">${escapeHtml(severity.high || 0)}</div></div>
      <div class="metric"><div class="metric-label">Medium</div><div class="metric-value">${escapeHtml(severity.medium || 0)}</div></div>
      <div class="metric"><div class="metric-label">Low</div><div class="metric-value">${escapeHtml(severity.low || 0)}</div></div>
    </div>
  `;
  target.appendChild(wrap);

  if (findings.length > 0) {
    const table = document.createElement("table");
    table.className = "recent-table";
    table.innerHTML = "<thead><tr><th>Severity</th><th>Category</th><th>Method</th><th>Path</th><th>Message</th></tr></thead><tbody></tbody>";
    const body = table.querySelector("tbody");
    for (const finding of findings.slice(0, 12)) {
      const row = document.createElement("tr");
      row.innerHTML = `
        <td>${escapeHtml(finding.severity || "-")}</td>
        <td>${escapeHtml(finding.category || "-")}</td>
        <td>${escapeHtml(finding.method || "-")}</td>
        <td>${escapeHtml(finding.path || "-")}</td>
        <td>${escapeHtml(finding.message || "-")}</td>
      `;
      body.appendChild(row);
    }
    target.appendChild(table);
  }

  const addresses = Array.isArray(discovery.addresses) ? discovery.addresses : [];
  const ports = Array.isArray(discovery.ports) ? discovery.ports : [];
  const files = Array.isArray(discovery.available_files) ? discovery.available_files : [];
  const treeNodes = Array.isArray(discovery.tree_nodes) && discovery.tree_nodes.length > 0
    ? discovery.tree_nodes
    : (Array.isArray(discovery.file_tree_lines) ? discovery.file_tree_lines : []);
  const networkNotesRaw = Array.isArray(discovery.network_notes) ? discovery.network_notes : [];
  const networkNotes = Array.from(new Set(networkNotesRaw.map((item) => String(item || "").trim()).filter(Boolean)));
  const riskSummary = Array.isArray(discovery.risk_summary) ? discovery.risk_summary : [];

  const discoveryWrap = document.createElement("div");
  discoveryWrap.className = "attack-discovery";

  const addrBox = document.createElement("div");
  addrBox.className = "metric";
  addrBox.innerHTML = `<div class="metric-label">Resolved Addresses</div><div class="metric-value">${escapeHtml(addresses.length || 0)}</div>`;
  discoveryWrap.appendChild(addrBox);

  const portsBox = document.createElement("div");
  portsBox.className = "metric";
  const openPorts = ports.filter((item) => item && item.state === "open").map((item) => item.port);
  portsBox.innerHTML = `<div class="metric-label">Open Ports</div><div class="metric-value">${escapeHtml(openPorts.length || 0)}</div>`;
  discoveryWrap.appendChild(portsBox);

  const filesBox = document.createElement("div");
  filesBox.className = "metric";
  filesBox.innerHTML = `<div class="metric-label">Available Paths</div><div class="metric-value">${escapeHtml(files.length || 0)}</div>`;
  discoveryWrap.appendChild(filesBox);

  if (treeNodes.length > 0) {
    const treeBox = document.createElement("div");
    treeBox.className = "metric attack-tree-wrap";
    treeBox.innerHTML = `<div class="metric-label">Folder/File Tree</div>`;
    const treeContent = buildClickableTree(treeNodes, report.target);
    treeBox.appendChild(treeContent);
    discoveryWrap.appendChild(treeBox);
  }

  if (addresses.length > 0) {
    const ipBox = document.createElement("div");
    ipBox.className = "metric";
    ipBox.innerHTML = `<div class="metric-label">Edge IPs</div>`;
    const ipList = document.createElement("div");
    ipList.className = "pill-row";
    for (const address of addresses) {
      const pill = document.createElement("span");
      pill.className = "chip";
      pill.textContent = address;
      ipList.appendChild(pill);
    }
    ipBox.appendChild(ipList);
    discoveryWrap.appendChild(ipBox);
  }

  if (ports.length > 0) {
    const portsCard = document.createElement("div");
    portsCard.className = "metric";
    portsCard.innerHTML = `<div class="metric-label">Port Map</div>`;
    const portGrid = document.createElement("div");
    portGrid.className = "port-grid";
    for (const item of ports) {
      const stateValue = String(item?.state || "unknown").toLowerCase();
      const row = document.createElement("div");
      row.className = `port-row state-${stateValue.replace(/[^a-z0-9-]/g, "")}`;
      const note = item?.port === 443
        ? "HTTPS (encrypted web traffic)"
        : item?.port === 80
          ? "HTTP (unencrypted web traffic)"
          : "Service probe result";
      row.innerHTML = `<strong>${escapeHtml(item?.port ?? "-")}</strong><span>${escapeHtml(stateValue)}</span><span>${escapeHtml(note)}</span>`;
      portGrid.appendChild(row);
    }
    portsCard.appendChild(portGrid);
    discoveryWrap.appendChild(portsCard);
  }

  if (networkNotes.length > 0) {
    const notesBox = document.createElement("div");
    notesBox.className = "metric";
    notesBox.innerHTML = `<div class="metric-label">Network Explanation</div>`;
    const list = document.createElement("ul");
    list.className = "attack-notes";
    for (const note of networkNotes) {
      const li = document.createElement("li");
      li.textContent = note;
      list.appendChild(li);
    }
    notesBox.appendChild(list);
    discoveryWrap.appendChild(notesBox);
  }

  if (riskSummary.length > 0) {
    const riskTable = document.createElement("table");
    riskTable.className = "recent-table";
    riskTable.innerHTML = "<thead><tr><th>Type</th><th>Value</th><th>Risk</th><th>Note</th></tr></thead><tbody></tbody>";
    const body = riskTable.querySelector("tbody");
    for (const row of riskSummary.slice(0, 20)) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${escapeHtml(row.type || "-")}</td>
        <td>${escapeHtml(row.value || "-")}</td>
        <td>${escapeHtml(row.risk || "-")}</td>
        <td>${escapeHtml(row.note || "-")}</td>
      `;
      body.appendChild(tr);
    }
    target.appendChild(riskTable);
  }

  target.appendChild(discoveryWrap);
}

function renderAttackScanControls(scanState, report, defaultTarget) {
  const target = byId("attack-scan-controls");
  if (!target) return;
  target.replaceChildren();

  if (!state.attackScanForm.target) {
    // Prefer report target (public server), then default, then fallback
    state.attackScanForm.target = report?.target || defaultTarget || "https://extraterritorial-carlota-ironfisted.ngrok-free.dev/Rhino8_cheat_sheet_timestamps_interactive.html";
    // Ensure we always allow public targets
    state.attackScanForm.allowPublicTarget = true;
  }

  const form = document.createElement("div");
  form.className = "attack-controls";
  form.innerHTML = `
    <div class="sort-controls">
      <label for="scan-target">Target URL</label>
      <input id="scan-target" type="url" value="${escapeHtml(state.attackScanForm.target)}" placeholder="https://..." />
      <label for="scan-profile">Profile</label>
      <select id="scan-profile">
        <option value="quick" ${state.attackScanForm.profile === "quick" ? "selected" : ""}>quick</option>
        <option value="standard" ${state.attackScanForm.profile === "standard" ? "selected" : ""}>standard</option>
        <option value="aggressive" ${state.attackScanForm.profile === "aggressive" ? "selected" : ""}>aggressive</option>
      </select>
      <label for="scan-requests">Burst Requests</label>
      <input id="scan-requests" type="number" min="1" max="5000" value="${escapeHtml(state.attackScanForm.burstRequests)}" />
      <label for="scan-concurrency">Burst Concurrency</label>
      <input id="scan-concurrency" type="number" min="1" max="200" value="${escapeHtml(state.attackScanForm.burstConcurrency)}" />
      <label for="scan-timeout">Timeout (s)</label>
      <input id="scan-timeout" type="number" min="1" max="60" value="${escapeHtml(state.attackScanForm.timeout)}" />
      <label class="toggle">
        <input id="scan-allow-public" type="checkbox" ${state.attackScanForm.allowPublicTarget ? "checked" : ""} />
        Allow public target
      </label>
      <button id="run-attack-scan" class="btn warn" type="button" ${scanState?.running ? "disabled" : ""}>${scanState?.running ? "Running..." : "Run Attack Scan"}</button>
    </div>
    <div class="attack-run-status"></div>
  `;
  target.appendChild(form);

  const status = form.querySelector(".attack-run-status");
  const finished = scanState?.last_finished ? `last finished: ${scanState.last_finished}` : "not run yet";
  const running = scanState?.running ? "scan is running" : "idle";
  const exitCode = scanState?.last_exit_code == null ? "-" : String(scanState.last_exit_code);
  const error = scanState?.last_error ? ` | error: ${scanState.last_error}` : "";
  status.textContent = `Status: ${running} | exit: ${exitCode} | ${finished}${error}`;

  form.querySelector("#scan-target")?.addEventListener("input", (event) => {
    state.attackScanForm.target = event.target.value || "";
  });
  form.querySelector("#scan-profile")?.addEventListener("change", (event) => {
    state.attackScanForm.profile = event.target.value || "standard";
  });
  form.querySelector("#scan-requests")?.addEventListener("input", (event) => {
    state.attackScanForm.burstRequests = Number(event.target.value || 80);
  });
  form.querySelector("#scan-concurrency")?.addEventListener("input", (event) => {
    state.attackScanForm.burstConcurrency = Number(event.target.value || 16);
  });
  form.querySelector("#scan-timeout")?.addEventListener("input", (event) => {
    state.attackScanForm.timeout = Number(event.target.value || 8);
  });
  form.querySelector("#scan-allow-public")?.addEventListener("change", (event) => {
    state.attackScanForm.allowPublicTarget = !!event.target.checked;
  });
  form.querySelector("#run-attack-scan")?.addEventListener("click", runAttackScan);
}

async function runAttackScan() {
  try {
    const payload = await fetchJson("/api/attack/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target: state.attackScanForm.target,
        profile: state.attackScanForm.profile,
        burst_requests: state.attackScanForm.burstRequests,
        burst_concurrency: state.attackScanForm.burstConcurrency,
        timeout: state.attackScanForm.timeout,
        allow_public_target: state.attackScanForm.allowPublicTarget,
      }),
    });
    if (!payload.ok) {
      throw new Error(payload.error || "Failed to start attack scan");
    }
    showInfo("Attack scan started.");
    scheduleRefresh(400);
  } catch (error) {
    showError(error.message);
  }
}

function renderHourlyChart(items) {
  const mount = byId("chart-hourly");
  if (!mount) return;

  mount.replaceChildren();
  if (!items || items.length === 0) {
    mount.textContent = "No hourly data yet.";
    if (state.charts.hourly) {
      state.charts.hourly.destroy();
      state.charts.hourly = null;
    }
    return;
  }

  if (typeof Chart === "undefined") {
    const fallback = document.createElement("div");
    fallback.className = "chart-fallback";
    fallback.textContent = "Interactive chart unavailable. Showing fallback bars.";
    mount.appendChild(fallback);
    renderBars("chart-hourly", items, "hourly");
    if (state.charts.hourly) {
      state.charts.hourly.destroy();
      state.charts.hourly = null;
    }
    return;
  }

  const canvas = document.createElement("canvas");
  canvas.id = "chart-hourly-canvas";
  canvas.height = 130;
  mount.appendChild(canvas);

  const labels = (items || []).map((item) => item.label);
  const data = (items || []).map((item) => item.value);
  if (state.charts.hourly) {
    state.charts.hourly.destroy();
  }
  state.charts.hourly = new Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Requests per hour",
          data,
          borderColor: "#0a6c74",
          backgroundColor: "rgba(10,108,116,0.15)",
          fill: true,
          tension: 0.25,
          pointRadius: 3,
        },
      ],
    },
    options: {
      maintainAspectRatio: false,
      plugins: {
        tooltip: {
          callbacks: {
            label: (ctx) => ` ${ctx.raw} requests`,
          },
        },
      },
      scales: {
        y: {
          beginAtZero: true,
          ticks: { precision: 0 },
        },
      },
    },
  });
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

function logRowClass(row) {
  if (!row || !row.status) return "row-plain";
  const code = Number(row.status);
  if (code >= 500) return "row-5xx";
  if (code >= 400) return "row-4xx";
  if (code >= 300) return "row-3xx";
  if (code >= 200) return "row-2xx";
  return "row-plain";
}

function renderLogTable(rows) {
  const wrap = byId("log-table-wrap");
  wrap.replaceChildren();
  if (!rows || rows.length === 0) {
    wrap.textContent = "No rows to display.";
    return;
  }

  const sorted = [...rows].sort((a, b) => {
    const key = state.logsView.sortBy;
    const av = String(a[key] ?? "");
    const bv = String(b[key] ?? "");
    const cmp = av.localeCompare(bv, undefined, { numeric: true, sensitivity: "base" });
    return state.logsView.order === "asc" ? cmp : -cmp;
  });

  const table = document.createElement("table");
  table.className = "recent-table structured-log-table";
  table.innerHTML = "<thead><tr><th>Time</th><th>IP</th><th>Geo</th><th>Method</th><th>Status</th><th>Path</th><th>Raw</th></tr></thead><tbody></tbody>";
  const body = table.querySelector("tbody");
  for (const row of sorted) {
    const tr = document.createElement("tr");
    tr.className = logRowClass(row);
    const geo = row.geo ? `${row.geo.country} ${row.geo.city}` : "--";
    tr.innerHTML = `
      <td>${escapeHtml(row.dt || "")}</td>
      <td>${escapeHtml(row.ip || "")}</td>
      <td>${escapeHtml(geo)}</td>
      <td>${escapeHtml(row.method || "")}</td>
      <td>${escapeHtml(row.status || "")}</td>
      <td title="${escapeHtml(row.path || "")}">${escapeHtml(row.path || "")}</td>
      <td title="${escapeHtml(row.raw || "")}">${escapeHtml(row.raw || "")}</td>
    `;
    body.appendChild(tr);
  }
  wrap.appendChild(table);
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
    if (value === currentValue) option.selected = true;
    select.appendChild(option);
  }
}

function fillMultiOptions(selectId, values, currentValues) {
  const select = byId(selectId);
  if (!select) return;
  select.replaceChildren();
  for (const value of values || []) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    option.selected = currentValues.includes(value);
    select.appendChild(option);
  }
}

async function loadDimensions() {
  const payload = await fetchJson("/api/dimensions");
  state.dimensions = payload.dimensions || {};
  fillSelectOptions("filter-ip", state.dimensions.ips || [], state.filters.ip);
  fillSelectOptions("filter-site", state.dimensions.sites || [], state.filters.site);
  fillSelectOptions("filter-status", state.dimensions.statuses || [], state.filters.status);
  fillSelectOptions("filter-family", state.dimensions.status_families || [], state.filters.statusFamily);
  fillMultiOptions("filter-method", state.dimensions.methods || [], state.filters.methods);
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
  if (state.filters.methods.length) params.set("methods", state.filters.methods.join(","));
  if (state.filters.statusFamily) params.set("status_family", state.filters.statusFamily);
  if (state.filters.q) params.set("q", state.filters.q);
  return `/api/stats?${params.toString()}`;
}

function buildLogRowsUrl() {
  const source = state.logsView.source === "ngrok" ? "ngrok" : "local";
  const params = new URLSearchParams({ tail: "180" });
  if (state.logsView.q) params.set("q", state.logsView.q);
  return `/api/log/${source}/rows?${params.toString()}`;
}

async function refreshAll() {
  abortCurrentRefresh();
  const controller = new AbortController();
  state.requests.controller = controller;
  state.requests.refreshInFlight = true;
  setLoading(true);

  try {
    const [status, localLog, ngrokLog, statsResp, logsRowsResp] = await Promise.all([
      fetchJson("/api/status", { signal: controller.signal }),
      fetchText("/api/log/local?tail=120", { signal: controller.signal }),
      fetchText("/api/log/ngrok?tail=120", { signal: controller.signal }),
      fetchJson(buildStatsUrl(), { signal: controller.signal }),
      fetchJson(buildLogRowsUrl(), { signal: controller.signal }),
    ]);

    if (controller.signal.aborted) return;

    renderStatus(status.tasks || []);
    state.logs.local = localLog;
    state.logs.ngrok = ngrokLog;
    state.logs.rows = logsRowsResp.rows || [];
    renderLog("local-log", state.logs.local);
    renderLog("ngrok-log", state.logs.ngrok);
    renderLogTable(state.logs.rows);

    state.stats = statsResp.stats || {};
    state.alerts = statsResp.alerts || [];
    state.rules = statsResp.rules || [];
    state.attackReport = statsResp.attack_report || null;
    state.attackScan = statsResp.attack_scan || null;
    state.attackDefaultTarget = statsResp.attack_default_target || "";

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
    renderBars("chart-probe-ips", state.stats.security?.top_probe_ips || [], "status", {
      clickable: true,
      tooltip: (item) => `Filter to ${item.label}`,
      onClick: (item) => setFilter("ip", item.label),
    });
    renderProbeIpLogs("probe-ips-logs", state.stats.security?.top_probe_ips || [], state.logs.rows);
    renderBars("chart-api-probe-ips", state.stats.security?.top_api_probe_ips || [], "status", {
      clickable: true,
      tooltip: (item) => `Filter to ${item.label}`,
      onClick: (item) => setFilter("ip", item.label),
    });

    renderHourlyChart(state.stats.hourly || []);
    renderSecuritySignals(state.stats.security || {});
    renderSecurityActions(state.stats.security || {});
    renderAlerts(state.alerts);
    renderRules(state.rules);
    renderAttackScanControls(state.attackScan, state.attackReport, state.attackDefaultTarget);
    renderAttackReport(state.attackReport);
    renderRecentRequests("recent-requests", state.stats.recent_requests || []);
    renderStatusExplanations("status-explanations", state.stats.status_explanations || {});

    state.requests.lastRefreshAt = new Date();
    byId("last-refresh").textContent = `Last refresh: ${state.requests.lastRefreshAt.toLocaleTimeString()}`;
    if (state.alerts.length > 0) {
      showInfo(`${state.alerts.length} active alert(s)`);
    }
    if (state.attackScan?.running) {
      scheduleRefresh(1500);
    }
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

function setMethodsFromControl() {
  const select = byId("filter-method");
  if (!select) return;
  const selected = Array.from(select.selectedOptions).map((item) => item.value);
  state.filters.methods = selected;
  updateControlsFromState();
  setUrlFromState();
  scheduleRefresh();
}

function scheduleReconnect() {
  if (state.connection.retryTimer) return;
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
    if (state.connection.status !== "connected") {
      setConnectionStatus("checking", "testing site");
    }
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

async function applySecurityAction(kind, ip) {
  try {
    if (!ip) return;
    const route = kind === "block" ? "/api/security/block-ip" : "/api/security/rate-limit-ip";
    const body =
      kind === "block"
        ? { ip, seconds: 3600 }
        : { ip, seconds: 3600, requests: 15, window: 60, ban: 600 };
    const payload = await fetchJson(route, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!payload.ok) {
      throw new Error(payload.error || "Security action failed");
    }
    showInfo(`${kind} applied to ${ip}`);
    await refreshAll();
  } catch (error) {
    showError(error.message);
  }
}

async function removeSecurityRule(ip) {
  try {
    const payload = await fetchJson("/api/security/unblock-ip", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ip }),
    });
    if (!payload.ok) throw new Error(payload.error || "Failed to remove rule");
    showInfo(`Removed rule for ${ip}`);
    await refreshAll();
  } catch (error) {
    showError(error.message);
  }
}

function bindActions() {
  byId("start-all").addEventListener("click", () => taskAction("start-all"));
  byId("stop-all").addEventListener("click", () => taskAction("stop-all"));
  byId("restart-all").addEventListener("click", () => taskAction("restart-all"));
  byId("test-connection").addEventListener("click", () => refreshConnectionHealth());

  for (const tab of document.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => {
      setTab(tab.dataset.tab || "overview");
      setUrlFromState();
    });
  }

  byId("ip-sort")?.addEventListener("change", (event) => {
    state.sort.ipSort = event.target.value || "requests";
    setUrlFromState();
    scheduleRefresh();
  });

  byId("ip-order")?.addEventListener("change", (event) => {
    state.sort.ipOrder = event.target.value || "desc";
    setUrlFromState();
    scheduleRefresh();
  });

  byId("filter-ip")?.addEventListener("change", (event) => setFilter("ip", event.target.value || ""));
  byId("filter-site")?.addEventListener("change", (event) => setFilter("site", event.target.value || ""));
  byId("filter-status")?.addEventListener("change", (event) => setFilter("status", event.target.value || ""));
  byId("filter-family")?.addEventListener("change", (event) => setFilter("statusFamily", event.target.value || ""));
  byId("filter-method")?.addEventListener("change", setMethodsFromControl);
  byId("filter-q")?.addEventListener("input", (event) => {
    state.filters.q = event.target.value || "";
    setUrlFromState();
    scheduleRefresh();
  });

  byId("logs-source")?.addEventListener("change", (event) => {
    state.logsView.source = event.target.value || "local";
    scheduleRefresh();
  });
  byId("logs-sort")?.addEventListener("change", (event) => {
    state.logsView.sortBy = event.target.value || "dt";
    renderLogTable(state.logs.rows);
  });
  byId("logs-order")?.addEventListener("change", (event) => {
    state.logsView.order = event.target.value || "desc";
    renderLogTable(state.logs.rows);
  });
  byId("logs-search")?.addEventListener("input", (event) => {
    state.logsView.q = event.target.value || "";
    scheduleRefresh();
  });

  byId("clear-filters")?.addEventListener("click", () => {
    state.filters.ip = "";
    state.filters.site = "";
    state.filters.status = "";
    state.filters.methods = [];
    state.filters.statusFamily = "";
    state.filters.q = "";
    updateControlsFromState();
    setUrlFromState();
    scheduleRefresh(0);
  });

  byId("auto-scroll")?.addEventListener("change", (event) => {
    state.ui.autoScroll = !!event.target.checked;
    setUrlFromState();
    renderLog("local-log", state.logs.local);
    renderLog("ngrok-log", state.logs.ngrok);
  });
}

async function boot() {
  applyStateFromUrl();
  bindActions();
  connectLiveEvents();
  try {
    await loadDimensions();
  } catch (error) {
    showError(`Failed to load filters: ${error.message}`);
  }

  updateControlsFromState();
  await refreshAll();
  await refreshConnectionHealth();

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
