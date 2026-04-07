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
$ipCounts = @{}
$hourCounts = @{}

$totalRequests = 0

Get-Content -LiteralPath $LogPath | ForEach-Object {
  $line = $_
  if ($line -match $accessPattern) {
    $totalRequests++

    $rawPath = $Matches.path
    $status = $Matches.status
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

    if ($ipCounts.ContainsKey($ip)) {
      $ipCounts[$ip]++
    } else {
      $ipCounts[$ip] = 1
    }

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

$topIps = $ipCounts.GetEnumerator() |
  Sort-Object -Property Value -Descending |
  Select-Object -First 15

$hourly = $hourCounts.GetEnumerator() |
  Sort-Object -Property Name

$maxFile = [Math]::Max(1, (($topFiles | Measure-Object -Property Value -Maximum).Maximum))
$maxStatus = [Math]::Max(1, (($topStatuses | Measure-Object -Property Value -Maximum).Maximum))
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
$statusRows = New-BarRows -Items $topStatuses -MaxValue $maxStatus -LabelClass "label short" -BarClass "bar status" -ValueClass "value"
$ipRows = New-BarRows -Items $topIps -MaxValue $maxIp -LabelClass "label" -BarClass "bar ip" -ValueClass "value"
$hourRows = New-BarRows -Items $hourly -MaxValue $maxHour -LabelClass "label" -BarClass "bar hour" -ValueClass "value"

$generatedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$logPathEncoded = [System.Net.WebUtility]::HtmlEncode($LogPath)

$html = @"
<!doctype html>
<html lang='en'>
<head>
  <meta charset='utf-8' />
  <meta name='viewport' content='width=device-width, initial-scale=1' />
  <title>Access Statistics Report</title>
  <style>
    :root {
      --bg: #f4f1ea;
      --panel: #fffdf9;
      --ink: #1f2a30;
      --muted: #5d676e;
      --file: #005f73;
      --status: #ae2012;
      --ip: #0a9396;
      --hour: #ca6702;
      --border: #ded5c7;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Segoe UI, Tahoma, sans-serif;
      color: var(--ink);
      background: radial-gradient(circle at 10% 10%, #fff6df, var(--bg));
    }
    .wrap {
      max-width: 1200px;
      margin: 24px auto;
      padding: 0 16px 32px;
    }
    .hero {
      background: linear-gradient(120deg, #e5f4f6, #fff4e9);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 18px;
      margin-bottom: 16px;
    }
    h1 { margin: 0 0 8px; font-size: 28px; }
    .meta { color: var(--muted); font-size: 14px; }
    .cards {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 10px;
      margin-top: 12px;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 12px;
    }
    .card .k { font-size: 13px; color: var(--muted); }
    .card .v { font-size: 26px; font-weight: 700; margin-top: 4px; }
    .grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 14px;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 12px;
    }
    .panel h2 { margin: 4px 0 12px; font-size: 18px; }
    .row {
      display: grid;
      grid-template-columns: minmax(180px, 34%) 1fr 64px;
      gap: 10px;
      align-items: center;
      margin: 6px 0;
    }
    .label {
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      font-size: 13px;
    }
    .label.short { max-width: 120px; }
    .bar-wrap {
      width: 100%;
      height: 12px;
      background: #e9e3d8;
      border-radius: 999px;
      overflow: hidden;
    }
    .bar { height: 100%; border-radius: 999px; }
    .bar.file { background: var(--file); }
    .bar.status { background: var(--status); }
    .bar.ip { background: var(--ip); }
    .bar.hour { background: var(--hour); }
    .value {
      text-align: right;
      font-variant-numeric: tabular-nums;
      font-size: 13px;
      color: #1f2a30;
    }
    @media (max-width: 720px) {
      .row {
        grid-template-columns: 1fr;
        gap: 6px;
      }
      .value { text-align: left; }
    }
  </style>
</head>
<body>
  <div class='wrap'>
    <section class='hero'>
      <h1>Access Statistics Report</h1>
      <div class='meta'>Generated: $generatedAt</div>
      <div class='meta'>Source log: $logPathEncoded</div>
      <div class='cards'>
        <div class='card'><div class='k'>Total Requests</div><div class='v'>$totalRequests</div></div>
        <div class='card'><div class='k'>Unique Files</div><div class='v'>$($pathCounts.Count)</div></div>
        <div class='card'><div class='k'>Status Codes Seen</div><div class='v'>$($statusCounts.Count)</div></div>
        <div class='card'><div class='k'>Client IPs Seen</div><div class='v'>$($ipCounts.Count)</div></div>
      </div>
    </section>

    <section class='grid'>
      <section class='panel'>
        <h2>Top Accessed Files</h2>
        $fileRows
      </section>

      <section class='panel'>
        <h2>Status Code Breakdown</h2>
        $statusRows
      </section>

      <section class='panel'>
        <h2>Top Client IPs</h2>
        $ipRows
      </section>

      <section class='panel'>
        <h2>Requests Per Hour</h2>
        $hourRows
      </section>
    </section>
  </div>
</body>
</html>
"@

$outDir = Split-Path -Parent $OutPath
if (-not (Test-Path -LiteralPath $outDir)) {
  New-Item -Path $outDir -ItemType Directory | Out-Null
}

Set-Content -LiteralPath $OutPath -Value $html -Encoding UTF8

Write-Host "Report generated: $OutPath"
Write-Host "Total requests parsed: $totalRequests"