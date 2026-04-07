const REFRESH_SECONDS = 15;
let tick = REFRESH_SECONDS;

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
      fetchJson("/api/stats?top=20"),
    ]);

    renderStatus(status.tasks || []);
    byId("local-log").textContent = localLog || "(empty)";
    byId("ngrok-log").textContent = ngrokLog || "(empty)";

    const stats = statsResp.stats || {};
    renderBars("chart-files", stats.top_files || [], "files");
    renderBars("chart-status", stats.status_codes || [], "status");
    renderBars("chart-ips", stats.top_ips || [], "ips");
    renderBars("chart-hourly", stats.hourly || [], "hourly");

    byId("last-refresh").textContent = `Last refresh: ${new Date().toLocaleTimeString()}`;
    tick = REFRESH_SECONDS;
  } catch (err) {
    setActionResult(`Refresh error: ${err.message}`, true);
  }
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
}

function startCountdown() {
  setInterval(() => {
    tick = Math.max(0, tick - 1);
    byId("next-refresh").textContent = `Next refresh in ${tick}s`;
  }, 1000);
}

async function boot() {
  bindActions();
  await refreshAll();
  startCountdown();
  setInterval(refreshAll, REFRESH_SECONDS * 1000);
}

boot();
