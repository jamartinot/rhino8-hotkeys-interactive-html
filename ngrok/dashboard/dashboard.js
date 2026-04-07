const FALLBACK_REFRESH_SECONDS = 60;
let tick = FALLBACK_REFRESH_SECONDS;
let eventSource = null;
let ipSort = "requests";
let ipOrder = "desc";
let filterIp = "";
let filterSite = "";
let filterStatus = "";

function byId(id) {
  return document.getElementById(id);
}

function setActionResult(text, isError = false) {
  const el = byId("action-result");
  el.textContent = text;
  el.style.color = isError ? "#a4161a" : "#0f5132";
}

function renderStatus(tasks) {
  const wrap = byId("status-cards");
  wrap.innerHTML = "";

  if (!tasks || tasks.length === 0) {
    wrap.innerHTML = "<article class='status-card'>No task data.</article>";
    return;
  }

  for (const t of tasks) {
    const card = document.createElement("article");
    card.className = "status-card";
    if (t.Error) {
      card.innerHTML = `
        <h3>${t.TaskName}</h3>
        <div class="status-kv">
          <span class="k">Error</span><span class="v">${t.Error}</span>
        </div>
      `;
    } else {
      card.innerHTML = `
        <h3>${t.TaskName}</h3>
        <div class="status-kv">
          <span class="k">State</span><span class="v">${t.State}</span>
          <span class="k">Last Result</span><span class="v">${t.LastTaskResult}</span>
          <span class="k">Last Run</span><span class="v">${t.LastRunTime || "-"}</span>
          <span class="k">Next Run</span><span class="v">${t.NextRunTime || "-"}</span>
        </div>
      `;
    }
    wrap.appendChild(card);
  }
}

function renderBars(targetId, items, cls) {
  const target = byId(targetId);
  target.innerHTML = "";

  if (!items || items.length === 0) {
    target.textContent = "No data yet.";
    return;
  }

  const max = Math.max(1, ...items.map((x) => x.value));
  for (const item of items) {
    const row = document.createElement("div");
    row.className = "bar-row";
    const width = (item.value / max) * 100;
    row.innerHTML = `
      <div class="label" title="${item.label}">${item.label}</div>
      <div class="bar-wrap"><div class="bar ${cls}" style="width:${width}%"></div></div>
      <div class="val">${item.value}</div>
    `;
    target.appendChild(row);
  }
}

function renderRecentRequests(targetId, items) {
  const target = byId(targetId);
  target.innerHTML = "";

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
      <td>${item.dt}</td>
      <td>${item.method}</td>
      <td>${item.status}</td>
      <td title="${item.path}">${item.path}</td>
      <td>${item.ip}</td>
    `;
    body.appendChild(row);
  }

  target.appendChild(table);
}

function renderStatusExplanations(targetId, explanations) {
  const target = byId(targetId);
  target.innerHTML = "";
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
    row.innerHTML = `<td>${code}</td><td>${explanation}</td>`;
    body.appendChild(row);
  }
  target.appendChild(table);
}

function fillSelectOptions(selectId, values, currentValue) {
  const select = byId(selectId);
  if (!select) return;
  select.innerHTML = '<option value="">All</option>';
  for (const v of values || []) {
    const option = document.createElement("option");
    option.value = v;
    option.textContent = v;
    if (v === currentValue) {
      option.selected = true;
    }
    select.appendChild(option);
  }
}

async function loadDimensions() {
  const payload = await fetchJson("/api/dimensions");
  const dims = payload.dimensions || {};
  fillSelectOptions("filter-ip", dims.ips || [], filterIp);
  fillSelectOptions("filter-site", dims.sites || [], filterSite);
  fillSelectOptions("filter-status", dims.statuses || [], filterStatus);
}

async function fetchText(url) {
  const r = await fetch(url);
  if (!r.ok) {
    throw new Error(`${url} failed: ${r.status}`);
  }
  return r.text();
}

async function fetchJson(url) {
  const r = await fetch(url);
  if (!r.ok) {
    throw new Error(`${url} failed: ${r.status}`);
  }
  return r.json();
}

async function refreshAll() {
  try {
    const [status, localLog, ngrokLog, statsResp] = await Promise.all([
      fetchJson("/api/status"),
      fetchText("/api/log/local?tail=80"),
      fetchText("/api/log/ngrok?tail=80"),
      fetchJson(`/api/stats?top=20&ip_sort=${encodeURIComponent(ipSort)}&ip_order=${encodeURIComponent(ipOrder)}&ip=${encodeURIComponent(filterIp)}&site=${encodeURIComponent(filterSite)}&status=${encodeURIComponent(filterStatus)}`),
    ]);

    renderStatus(status.tasks || []);
    byId("local-log").textContent = localLog || "(empty)";
    byId("ngrok-log").textContent = ngrokLog || "(empty)";

    const stats = statsResp.stats || {};
    renderBars("chart-files", stats.top_files || [], "files");
    renderBars("chart-methods", stats.methods || [], "methods");
    renderBars("chart-status", stats.status_codes || [], "status");
    renderBars("chart-families", stats.status_families || [], "families");
    renderBars("chart-ips", stats.top_ips || [], "ips");
    renderBars("chart-sites-per-ip", stats.sites_per_ip || [], "methods");
    renderBars("chart-hourly", stats.hourly || [], "hourly");
    renderRecentRequests("recent-requests", stats.recent_requests || []);
    renderStatusExplanations("status-explanations", stats.status_explanations || {});

    byId("last-refresh").textContent = `Last refresh: ${new Date().toLocaleTimeString()}`;
    tick = FALLBACK_REFRESH_SECONDS;
  } catch (err) {
    setActionResult(`Refresh error: ${err.message}`, true);
  }
}

function connectLiveEvents() {
  if (eventSource) {
    eventSource.close();
  }

  eventSource = new EventSource("/api/events");
  eventSource.onopen = () => {
    byId("next-refresh").textContent = "Live updates connected";
  };
  eventSource.addEventListener("update", async () => {
    await refreshAll();
  });
  eventSource.onerror = () => {
    byId("next-refresh").textContent = `Live updates reconnecting; fallback refresh in ${tick}s`;
  };
}

async function taskAction(action) {
  try {
    setActionResult("Running action...");
    const r = await fetch(`/api/tasks/${action}`, { method: "POST" });
    const data = await r.json();
    if (!r.ok || !data.ok) {
      throw new Error(data.error || `Action failed (${r.status})`);
    }
    setActionResult(`${action} completed.`);
    await refreshAll();
  } catch (err) {
    setActionResult(err.message, true);
  }
}

function bindActions() {
  byId("start-all").addEventListener("click", () => taskAction("start-all"));
  byId("stop-all").addEventListener("click", () => taskAction("stop-all"));
  byId("restart-all").addEventListener("click", () => taskAction("restart-all"));

  const ipSortEl = byId("ip-sort");
  const ipOrderEl = byId("ip-order");
  if (ipSortEl) {
    ipSortEl.addEventListener("change", async () => {
      ipSort = ipSortEl.value || "requests";
      await refreshAll();
    });
  }
  if (ipOrderEl) {
    ipOrderEl.addEventListener("change", async () => {
      ipOrder = ipOrderEl.value || "desc";
      await refreshAll();
    });
  }

  const filterIpEl = byId("filter-ip");
  const filterSiteEl = byId("filter-site");
  const filterStatusEl = byId("filter-status");
  const clearFiltersEl = byId("clear-filters");

  if (filterIpEl) {
    filterIpEl.addEventListener("change", async () => {
      filterIp = filterIpEl.value || "";
      await refreshAll();
    });
  }
  if (filterSiteEl) {
    filterSiteEl.addEventListener("change", async () => {
      filterSite = filterSiteEl.value || "";
      await refreshAll();
    });
  }
  if (filterStatusEl) {
    filterStatusEl.addEventListener("change", async () => {
      filterStatus = filterStatusEl.value || "";
      await refreshAll();
    });
  }
  if (clearFiltersEl) {
    clearFiltersEl.addEventListener("click", async () => {
      filterIp = "";
      filterSite = "";
      filterStatus = "";
      if (filterIpEl) filterIpEl.value = "";
      if (filterSiteEl) filterSiteEl.value = "";
      if (filterStatusEl) filterStatusEl.value = "";
      await refreshAll();
    });
  }
}

function startCountdown() {
  setInterval(() => {
    tick = Math.max(0, tick - 1);
    if (eventSource && eventSource.readyState === 1) {
      byId("next-refresh").textContent = "Live updates connected";
    } else {
      byId("next-refresh").textContent = `Fallback refresh in ${tick}s`;
    }
  }, 1000);
}

async function boot() {
  await loadDimensions();
  bindActions();
  connectLiveEvents();
  await refreshAll();
  startCountdown();
  setInterval(refreshAll, FALLBACK_REFRESH_SECONDS * 1000);
}

boot();
