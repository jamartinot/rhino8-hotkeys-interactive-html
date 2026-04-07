param(
  [string]$LogPath = "C:\ProgramData\localserver\server-8000.txt",
  [string]$OutPath = "C:\Users\gkayt\OneDrive\Documents\vscode\html\ngrok\access-report.html",
  [int]$Top = 50
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $LogPath)) {
  throw "Log file not found: $LogPath"
}

$accessPattern = '^(?<ip>\S+)\s+-\s+-\s+\[(?<dt>[^\]]+)\]\s+"(?<method>[A-Z]+)\s+(?<path>\S+)\s+HTTP\/\d(?:\.\d)?"\s+(?<status>\d{3})\s+'

$pathCounts = @{}
$statusCounts = @{}
$methodCounts = @{}
$familyCounts = @{}
$ipCounts = @{}
$hourCounts = @{}
$recentRequests = New-Object System.Collections.Generic.List[object]

$totalRequests = 0

Get-Content -LiteralPath $LogPath | ForEach-Object {
  $line = $_
  if ($line -match $accessPattern) {
    $totalRequests++

    $rawPath = $Matches.path
    $status = $Matches.status
    $method = $Matches.method
    $ip = $Matches.ip
    $dtText = $Matches.dt

    $pathOnly = ($rawPath -split '\?')[0]
    if ([string]::IsNullOrWhiteSpace($pathOnly)) {
      $pathOnly = "/"
    }

    if ($pathCounts.ContainsKey($pathOnly)) {
      $pathCounts[$pathOnly]++
    } else {
      $pathCounts[$pathOnly] = 1
    }

    if ($statusCounts.ContainsKey($status)) {
      $statusCounts[$status]++
    } else {
      $statusCounts[$status] = 1
    }

    if ($methodCounts.ContainsKey($method)) {
      $methodCounts[$method]++
    } else {
      $methodCounts[$method] = 1
    }

    $familyKey = "$($status.Substring(0, 1))xx"
    if ($familyCounts.ContainsKey($familyKey)) {
      $familyCounts[$familyKey]++
    } else {
      $familyCounts[$familyKey] = 1
    }

    if ($ipCounts.ContainsKey($ip)) {
      $ipCounts[$ip]++
    } else {
      $ipCounts[$ip] = 1
    }

    $recentRequests.Insert(0, [pscustomobject]@{
      ip = $ip
      dt = $dtText
      method = $method
      path = $pathOnly
      status = $status
    })

    if ($dtText -match '^(?<day>\d{2})\/(?<mon>[A-Za-z]{3})\/(?<year>\d{4})\s+(?<hour>\d{2})') {
      $hourKey = "$($Matches.year)-$($Matches.mon)-$($Matches.day) $($Matches.hour):00"
      if ($hourCounts.ContainsKey($hourKey)) {
        $hourCounts[$hourKey]++
      } else {
        $hourCounts[$hourKey] = 1
      }
    }
  }
}

$topFiles = $pathCounts.GetEnumerator() |
  Sort-Object -Property Value -Descending |
  Select-Object -First $Top

$topStatuses = $statusCounts.GetEnumerator() |
  Sort-Object -Property Name

$topMethods = $methodCounts.GetEnumerator() |
  Sort-Object -Property Value -Descending

$topFamilies = $familyCounts.GetEnumerator() |
  Sort-Object -Property Name

$topIps = $ipCounts.GetEnumerator() |
  Sort-Object -Property Value -Descending |
  Select-Object -First 15

$hourly = $hourCounts.GetEnumerator() |
  Sort-Object -Property Name

$maxFile = [Math]::Max(1, (($topFiles | Measure-Object -Property Value -Maximum).Maximum))
$maxStatus = [Math]::Max(1, (($topStatuses | Measure-Object -Property Value -Maximum).Maximum))
$maxMethod = [Math]::Max(1, (($topMethods | Measure-Object -Property Value -Maximum).Maximum))
$maxFamily = [Math]::Max(1, (($topFamilies | Measure-Object -Property Value -Maximum).Maximum))
$maxIp = [Math]::Max(1, (($topIps | Measure-Object -Property Value -Maximum).Maximum))
$maxHour = [Math]::Max(1, (($hourly | Measure-Object -Property Value -Maximum).Maximum))

function New-BarRows {
  param(
    [Parameter(Mandatory = $true)]$Items,
    [Parameter(Mandatory = $true)][int]$MaxValue,
    [Parameter(Mandatory = $true)][string]$LabelClass,
    [Parameter(Mandatory = $true)][string]$BarClass,
    [Parameter(Mandatory = $true)][string]$ValueClass
  )

  $rows = foreach ($item in $Items) {
    $label = [System.Net.WebUtility]::HtmlEncode([string]$item.Key)
    $count = [int]$item.Value
    $pct = [Math]::Round(($count / $MaxValue) * 100, 2)
    @"
    <div class='row'>
      <div class='$LabelClass' title='$label'>$label</div>
      <div class='bar-wrap'><div class='$BarClass' style='width: $pct%'></div></div>
      <div class='$ValueClass'>$count</div>
    </div>
"@
  }

  return ($rows -join "`n")
}

$fileRows = New-BarRows -Items $topFiles -MaxValue $maxFile -LabelClass "label" -BarClass "bar file" -ValueClass "value"
$methodRows = New-BarRows -Items $topMethods -MaxValue $maxMethod -LabelClass "label short" -BarClass "bar methods" -ValueClass "value"
$statusRows = New-BarRows -Items $topStatuses -MaxValue $maxStatus -LabelClass "label short" -BarClass "bar status" -ValueClass "value"
$familyRows = New-BarRows -Items $topFamilies -MaxValue $maxFamily -LabelClass "label short" -BarClass "bar families" -ValueClass "value"
$ipRows = New-BarRows -Items $topIps -MaxValue $maxIp -LabelClass "label" -BarClass "bar ip" -ValueClass "value"
$hourRows = New-BarRows -Items $hourly -MaxValue $maxHour -LabelClass "label" -BarClass "bar hour" -ValueClass "value"

$recentRows = foreach ($item in ($recentRequests | Select-Object -First 25)) {
  $ip = [System.Net.WebUtility]::HtmlEncode([string]$item.ip)
  $dt = [System.Net.WebUtility]::HtmlEncode([string]$item.dt)
  $method = [System.Net.WebUtility]::HtmlEncode([string]$item.method)
  $path = [System.Net.WebUtility]::HtmlEncode([string]$item.path)
  $status = [System.Net.WebUtility]::HtmlEncode([string]$item.status)
  @"
    <tr>
      <td>$dt</td>
      <td>$method</td>
      <td>$status</td>
      <td>$path</td>
      <td>$ip</td>
    </tr>
"@
}

$generatedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$bootstrap = [ordered]@{
  generatedAt = $generatedAt
  logPath = $LogPath
  stats = [ordered]@{
    totalRequests = $totalRequests
    uniqueFiles = $pathCounts.Count
    topFiles = @($topFiles | ForEach-Object { [ordered]@{ label = [string]$_.Key; value = [int]$_.Value } })
    methods = @($topMethods | ForEach-Object { [ordered]@{ label = [string]$_.Key; value = [int]$_.Value } })
    statusCodes = @($topStatuses | ForEach-Object { [ordered]@{ label = [string]$_.Key; value = [int]$_.Value } })
    statusFamilies = @($topFamilies | ForEach-Object { [ordered]@{ label = [string]$_.Key; value = [int]$_.Value } })
    topIps = @($topIps | ForEach-Object { [ordered]@{ label = [string]$_.Key; value = [int]$_.Value } })
    hourly = @($hourly | ForEach-Object { [ordered]@{ label = [string]$_.Key; value = [int]$_.Value } })
    recentRequests = @($recentRequests | Select-Object -First 25 | ForEach-Object {
      [ordered]@{
        dt = [string]$_.dt
        method = [string]$_.method
        status = [string]$_.status
        path = [string]$_.path
        ip = [string]$_.ip
      }
    })
  }
}

$bootstrapJson = $bootstrap | ConvertTo-Json -Depth 8

$html = @'
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Access Statistics Report</title>
  <style>
    :root {
      --bg: #f5efdf;
      --panel: #fffdf6;
      --ink: #1d2429;
      --muted: #59656e;
      --line: #d8ccbb;
      --accent: #005f73;
      --accent-2: #bc3908;
      --accent-3: #2a9d8f;
      --accent-4: #7f5539;
      --accent-5: #6d597a;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at 80% 0%, #ffe9cb 0, transparent 30%),
        radial-gradient(circle at 0% 100%, #daf0f2 0, transparent 40%),
        var(--bg);
      font-family: Segoe UI, Tahoma, sans-serif;
    }
    .wrap { max-width: 1320px; margin: 0 auto; padding: 20px; }
    .hero {
      background: linear-gradient(125deg, #f8fff5, #fff4ec);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 18px;
    }
    .hero h1 { margin: 0; font-size: clamp(28px, 4vw, 44px); }
    .hero p { margin: 8px 0 0; color: var(--muted); }
    .hero-meta {
      display: flex; gap: 16px; flex-wrap: wrap;
      margin-top: 12px; color: var(--muted);
      font-family: Consolas, monospace; font-size: 13px;
    }
    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin-top: 12px; }
    .card { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 12px; }
    .card .k { font-size: 13px; color: var(--muted); }
    .card .v { font-size: 26px; font-weight: 700; margin-top: 4px; }
    .grid { display: grid; gap: 14px; margin-top: 14px; }
    .two { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 12px; }
    .panel h2 { margin: 4px 0 12px; font-size: 18px; }
    .bar-row { display: grid; grid-template-columns: minmax(120px, 35%) 1fr 56px; gap: 8px; align-items: center; margin: 8px 0; }
    .label { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: 13px; }
    .label.short { max-width: 120px; }
    .bar-wrap { background: #ece2d5; border-radius: 999px; height: 11px; overflow: hidden; }
    .bar { height: 100%; border-radius: 999px; }
    .bar.files { background: var(--accent); }
    .bar.methods { background: #8e6c8a; }
    .bar.status { background: var(--accent-2); }
    .bar.ips { background: var(--accent-3); }
    .bar.hourly { background: var(--accent-4); }
    .val { text-align: right; font-family: Consolas, monospace; font-size: 12px; }
    .sort-controls { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 10px; font-size: 13px; }
    .sort-controls select { border: 1px solid var(--line); border-radius: 8px; background: #fff; padding: 4px 8px; font: inherit; }
    .donut-wrap { display: grid; grid-template-columns: auto 1fr; gap: 14px; align-items: center; }
    .donut {
      width: 220px; height: 220px; border-radius: 50%;
      position: relative; background: conic-gradient(#ddd 0 100%);
    }
    .donut::after {
      content: ''; position: absolute; inset: 42px; border-radius: 50%;
      background: var(--panel); border: 1px solid var(--line);
    }
    .legend { display: grid; gap: 8px; }
    .legend-item { display: flex; gap: 8px; align-items: center; font-size: 13px; }
    .swatch { width: 12px; height: 12px; border-radius: 3px; }
    .line-wrap { overflow-x: auto; }
    .line-chart { width: 100%; min-width: 520px; height: 240px; display: block; }
    .line-axis { fill: none; stroke: #c8b9a4; stroke-width: 1; }
    .line-series { fill: none; stroke: var(--accent); stroke-width: 3; }
    .line-points { fill: var(--accent); }
    .table-wrap { overflow: auto; border: 1px solid var(--line); border-radius: 10px; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
    th { position: sticky; top: 0; background: #fffdf8; }
    td { word-break: break-word; }
    @media (max-width: 900px) { .two { grid-template-columns: 1fr; } .donut-wrap { grid-template-columns: 1fr; justify-items: center; } }
    @media (max-width: 720px) { .bar-row { grid-template-columns: 1fr; gap: 6px; } .val { text-align: left; } }
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <h1>Access Statistics Report</h1>
      <p>Self-updating snapshot of file access activity from the live dashboard API.</p>
      <div class="hero-meta">
        <span id="generated-at">Generated: --</span>
        <span id="source-log">Source log: --</span>
        <span id="refresh-state">Waiting for live data...</span>
      </div>
      <div class="cards">
        <div class="card"><div class="k">Total Requests</div><div class="v" id="total-requests">0</div></div>
        <div class="card"><div class="k">Unique Files</div><div class="v" id="unique-files">0</div></div>
        <div class="card"><div class="k">Status Codes Seen</div><div class="v" id="status-count">0</div></div>
        <div class="card"><div class="k">Client IPs Seen</div><div class="v" id="ip-count">0</div></div>
      </div>
    </section>

    <section class="grid two">
      <article class="panel"><h2>Top Accessed Files</h2><div id="chart-files"></div></article>
      <article class="panel"><h2>HTTP Methods</h2><div id="chart-methods"></div></article>
    </section>

    <section class="grid">
      <article class="panel">
        <h2>Drill-Down Filters</h2>
        <div class="sort-controls">
          <label for="filter-ip">IP</label>
          <select id="filter-ip"><option value="">All</option></select>
          <label for="filter-site">Site</label>
          <select id="filter-site"><option value="">All</option></select>
          <label for="filter-status">Status</label>
          <select id="filter-status"><option value="">All</option></select>
          <button id="clear-filters" type="button">Clear Filters</button>
        </div>
      </article>
    </section>

    <section class="grid two">
      <article class="panel"><h2>Status Families</h2><div id="chart-families" class="donut-wrap"></div></article>
      <article class="panel"><h2>Status Code Breakdown</h2><div id="chart-status"></div></article>
    </section>

    <section class="grid two">
      <article class="panel">
        <h2>Top Client IPs</h2>
        <div class="sort-controls">
          <label for="ip-sort">Sort by</label>
          <select id="ip-sort">
            <option value="requests" selected>Requests</option>
            <option value="sites">Unique Sites</option>
            <option value="ip">IP Address</option>
          </select>
          <label for="ip-order">Order</label>
          <select id="ip-order">
            <option value="desc" selected>Desc</option>
            <option value="asc">Asc</option>
          </select>
        </div>
        <div id="chart-ips"></div>
      </article>
      <article class="panel"><h2>Requests Per Hour</h2><div id="chart-hourly" class="line-wrap"></div></article>
    </section>

    <section class="grid">
      <article class="panel"><h2>Sites Per IP</h2><div id="chart-sites-per-ip"></div></article>
    </section>

    <section class="grid">
      <article class="panel"><h2>Recent Requests</h2><div id="recent-requests"></div></article>
    </section>

    <section class="grid">
      <article class="panel"><h2>Status Code Explanations</h2><div id="status-explanations"></div></article>
    </section>

    <section class="grid">
      <article class="panel"><h2>All Status Code Explanations</h2><div id="status-explanations-all"></div></article>
    </section>
  </main>

  <script>
    const TOP = __TOP__;
    const REFRESH_MS = 15000;
    window.__REPORT_BOOTSTRAP__ = __BOOTSTRAP__;
    let eventSource = null;
    let ipSort = 'requests';
    let ipOrder = 'desc';
    let filterIp = '';
    let filterSite = '';
    let filterStatus = '';
    let allStatusExplanations = {};

    function byId(id) { return document.getElementById(id); }

    function renderBars(targetId, items, cls) {
      const target = byId(targetId);
      target.innerHTML = '';
      if (!items || items.length === 0) { target.textContent = 'No data yet.'; return; }
      const max = Math.max(1, ...items.map(x => x.value));
      for (const item of items) {
        const row = document.createElement('div');
        row.className = 'bar-row';
        const width = (item.value / max) * 100;
        row.innerHTML = `<div class="label" title="${item.label}">${item.label}</div><div class="bar-wrap"><div class="bar ${cls}" style="width:${width}%"></div></div><div class="val">${item.value}</div>`;
        target.appendChild(row);
      }
    }

    function renderDonut(targetId, items) {
      const target = byId(targetId);
      target.innerHTML = '';
      if (!items || items.length === 0) { target.textContent = 'No data yet.'; return; }
      const colors = ['#ae2012', '#ca6702', '#0a9396', '#6d597a', '#005f73'];
      const total = items.reduce((sum, item) => sum + item.value, 0) || 1;
      let angle = 0;
      const stops = [];
      items.forEach((item, index) => {
        const pct = (item.value / total) * 100;
        const next = angle + pct;
        stops.push(`${colors[index % colors.length]} ${angle}% ${next}%`);
        angle = next;
      });
      const donut = document.createElement('div');
      donut.className = 'donut';
      donut.style.background = `conic-gradient(${stops.join(', ')})`;
      const legend = document.createElement('div');
      legend.className = 'legend';
      items.forEach((item, index) => {
        const row = document.createElement('div');
        row.className = 'legend-item';
        row.innerHTML = `<span class="swatch" style="background:${colors[index % colors.length]}"></span><span>${item.label}</span><span style="margin-left:auto;font-family:Consolas,monospace;">${item.value}</span>`;
        legend.appendChild(row);
      });
      target.appendChild(donut);
      target.appendChild(legend);
    }

    function renderLine(targetId, items) {
      const target = byId(targetId);
      target.innerHTML = '';
      if (!items || items.length === 0) { target.textContent = 'No data yet.'; return; }
      const width = Math.max(720, items.length * 90);
      const height = 240;
      const pad = 24;
      const max = Math.max(1, ...items.map(x => x.value));
      const points = items.map((item, index) => {
        const x = pad + (index * (width - pad * 2)) / Math.max(1, items.length - 1);
        const y = height - pad - ((item.value / max) * (height - pad * 2));
        return { x, y, label: item.label, value: item.value };
      });
      const polylinePoints = points.map(p => `${p.x},${p.y}`).join(' ');
      const circles = points.map(p => `<circle class="line-points" cx="${p.x}" cy="${p.y}" r="4"><title>${p.label}: ${p.value}</title></circle>`).join('');
      const labels = points.map(p => `<text x="${p.x}" y="${height - 6}" text-anchor="middle" font-size="10" fill="#59656e">${p.label.split(' ')[1] || p.label}</text>`).join('');
      target.innerHTML = `
        <svg class="line-chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
          <line class="line-axis" x1="${pad}" y1="${height - pad}" x2="${width - pad}" y2="${height - pad}" />
          <line class="line-axis" x1="${pad}" y1="${pad}" x2="${pad}" y2="${height - pad}" />
          <polyline class="line-series" points="${polylinePoints}" />
          ${circles}
          ${labels}
        </svg>`;
    }

    function renderRecent(items) {
      const target = byId('recent-requests');
      target.innerHTML = '';
      if (!items || items.length === 0) { target.textContent = 'No request history yet.'; return; }
      const table = document.createElement('table');
      table.innerHTML = `
        <thead>
          <tr><th>Time</th><th>Method</th><th>Status</th><th>Path</th><th>IP</th></tr>
        </thead>
        <tbody></tbody>`;
      const body = table.querySelector('tbody');
      items.slice(0, 25).forEach(item => {
        const row = document.createElement('tr');
        row.innerHTML = `<td>${item.dt}</td><td>${item.method}</td><td>${item.status}</td><td title="${item.path}">${item.path}</td><td>${item.ip}</td>`;
        body.appendChild(row);
      });
      target.appendChild(table);
    }

    function renderStatusExplanations(explanations) {
      const target = byId('status-explanations');
      target.innerHTML = '';
      const entries = Object.entries(explanations || {});
      if (entries.length === 0) { target.textContent = 'No status codes in current selection.'; return; }
      const table = document.createElement('table');
      table.innerHTML = '<thead><tr><th>Status</th><th>Explanation</th></tr></thead><tbody></tbody>';
      const body = table.querySelector('tbody');
      entries.forEach(([code, explanation]) => {
        const row = document.createElement('tr');
        row.innerHTML = `<td>${code}</td><td>${explanation}</td>`;
        body.appendChild(row);
      });
      target.appendChild(table);
    }

    function renderAllStatusExplanations(explanations) {
      const target = byId('status-explanations-all');
      target.innerHTML = '';
      const entries = Object.entries(explanations || {}).sort((a, b) => Number(a[0]) - Number(b[0]));
      if (entries.length === 0) { target.textContent = 'No status code reference loaded.'; return; }
      const table = document.createElement('table');
      table.innerHTML = '<thead><tr><th>Status</th><th>Explanation</th></tr></thead><tbody></tbody>';
      const body = table.querySelector('tbody');
      entries.forEach(([code, explanation]) => {
        const row = document.createElement('tr');
        row.innerHTML = `<td>${code}</td><td>${explanation}</td>`;
        body.appendChild(row);
      });
      target.appendChild(table);
    }

    function fillSelectOptions(selectId, values, currentValue) {
      const select = byId(selectId);
      if (!select) return;
      select.innerHTML = '<option value="">All</option>';
      (values || []).forEach((value) => {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = value;
        if (value === currentValue) option.selected = true;
        select.appendChild(option);
      });
    }

    async function loadDimensions() {
      const payload = await fetch('/api/dimensions', { cache: 'no-store' });
      const data = await payload.json();
      const dims = data.dimensions || {};
      fillSelectOptions('filter-ip', dims.ips || [], filterIp);
      fillSelectOptions('filter-site', dims.sites || [], filterSite);
      fillSelectOptions('filter-status', dims.statuses || [], filterStatus);
      allStatusExplanations = dims.status_explanations_all || {};
      renderAllStatusExplanations(allStatusExplanations);
    }

    function renderSummary(stats) {
      byId('generated-at').textContent = `Generated: ${window.__REPORT_BOOTSTRAP__.generatedAt}`;
      byId('source-log').textContent = `Source log: ${window.__REPORT_BOOTSTRAP__.logPath}`;
      byId('total-requests').textContent = stats.totalRequests || 0;
      byId('unique-files').textContent = stats.uniqueFiles || 0;
      byId('status-count').textContent = (stats.statusCodes || []).length;
      byId('ip-count').textContent = (stats.topIps || []).length;
    }

    function normalizeStats(stats) {
      if (!stats) return {};
      return {
        totalRequests: stats.totalRequests ?? stats.total_requests ?? 0,
        uniqueFiles: stats.uniqueFiles ?? stats.unique_files ?? 0,
        topFiles: stats.topFiles ?? stats.top_files ?? [],
        methods: stats.methods ?? [],
        statusCodes: stats.statusCodes ?? stats.status_codes ?? [],
        statusFamilies: stats.statusFamilies ?? stats.status_families ?? [],
        topIps: stats.topIps ?? stats.top_ips ?? [],
        sitesPerIp: stats.sitesPerIp ?? stats.sites_per_ip ?? [],
        hourly: stats.hourly ?? [],
        recentRequests: stats.recentRequests ?? stats.recent_requests ?? [],
        statusExplanations: stats.statusExplanations ?? stats.status_explanations ?? {},
      };
    }

    function renderAll(stats) {
      const s = normalizeStats(stats);
      renderSummary(s);
      renderBars('chart-files', s.topFiles || [], 'files');
      renderBars('chart-methods', s.methods || [], 'methods');
      renderDonut('chart-families', s.statusFamilies || []);
      renderBars('chart-status', s.statusCodes || [], 'status');
      renderBars('chart-ips', s.topIps || [], 'ips');
      renderLine('chart-hourly', s.hourly || []);
      renderBars('chart-sites-per-ip', s.sitesPerIp || [], 'methods');
      renderRecent(s.recentRequests || []);
      renderStatusExplanations(s.statusExplanations || {});
    }

    async function refresh() {
      try {
        const response = await fetch(`/api/stats?top=${TOP}&ip_sort=${encodeURIComponent(ipSort)}&ip_order=${encodeURIComponent(ipOrder)}&ip=${encodeURIComponent(filterIp)}&site=${encodeURIComponent(filterSite)}&status=${encodeURIComponent(filterStatus)}`, { cache: 'no-store' });
        const payload = await response.json();
        renderAll(payload.stats || {});
        byId('refresh-state').textContent = `Last refreshed: ${new Date().toLocaleTimeString()}`;
      } catch (error) {
        byId('refresh-state').textContent = `Refresh error: ${error.message}`;
      }
    }

    function bindSortControls() {
      const ipSortEl = byId('ip-sort');
      const ipOrderEl = byId('ip-order');
      if (ipSortEl) {
        ipSortEl.addEventListener('change', () => {
          ipSort = ipSortEl.value || 'requests';
          refresh();
        });
      }
      if (ipOrderEl) {
        ipOrderEl.addEventListener('change', () => {
          ipOrder = ipOrderEl.value || 'desc';
          refresh();
        });
      }
    }

    function connectLiveUpdates() {
      if (eventSource) {
        eventSource.close();
      }

      eventSource = new EventSource('/api/events');
      eventSource.onopen = () => {
        byId('refresh-state').textContent = 'Live updates connected';
      };
      eventSource.addEventListener('update', () => {
        refresh();
      });
      eventSource.onerror = () => {
        byId('refresh-state').textContent = 'Live updates reconnecting...';
      };
    }

    function bindFilterControls() {
      const filterIpEl = byId('filter-ip');
      const filterSiteEl = byId('filter-site');
      const filterStatusEl = byId('filter-status');
      const clearFiltersEl = byId('clear-filters');

      if (filterIpEl) {
        filterIpEl.addEventListener('change', () => {
          filterIp = filterIpEl.value || '';
          refresh();
        });
      }
      if (filterSiteEl) {
        filterSiteEl.addEventListener('change', () => {
          filterSite = filterSiteEl.value || '';
          refresh();
        });
      }
      if (filterStatusEl) {
        filterStatusEl.addEventListener('change', () => {
          filterStatus = filterStatusEl.value || '';
          refresh();
        });
      }
      if (clearFiltersEl) {
        clearFiltersEl.addEventListener('click', () => {
          filterIp = '';
          filterSite = '';
          filterStatus = '';
          if (filterIpEl) filterIpEl.value = '';
          if (filterSiteEl) filterSiteEl.value = '';
          if (filterStatusEl) filterStatusEl.value = '';
          refresh();
        });
      }
    }

    renderAll(window.__REPORT_BOOTSTRAP__.stats);
    loadDimensions();
    bindFilterControls();
    bindSortControls();
    connectLiveUpdates();
    refresh();
    setInterval(refresh, REFRESH_MS);
  </script>
</body>
</html>
'@

$html = $html.Replace('__BOOTSTRAP__', $bootstrapJson).Replace('__TOP__', [string]$Top)

$outDir = Split-Path -Parent $OutPath
if (-not (Test-Path -LiteralPath $outDir)) {
  New-Item -Path $outDir -ItemType Directory | Out-Null
}

Set-Content -LiteralPath $OutPath -Value $html -Encoding UTF8

Write-Host "Report generated: $OutPath"
Write-Host "Total requests parsed: $totalRequests"