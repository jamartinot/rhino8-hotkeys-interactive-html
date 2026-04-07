param(
  [int]$Port = 8091,
  [string]$LogPath = "C:\ProgramData\localserver\dashboard-8091.txt"
)

$ErrorActionPreference = "Stop"

$serverScript = Join-Path $PSScriptRoot "dashboard_server.py"
if (-not (Test-Path -LiteralPath $serverScript)) {
  throw "dashboard_server.py not found: $serverScript"
}

$logDir = Split-Path -Parent $LogPath
if (-not (Test-Path -LiteralPath $logDir)) {
  New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

"[$(Get-Date -Format s)] Starting dashboard on port $Port" | Out-File -FilePath $LogPath -Append -Encoding UTF8

& "C:\Windows\py.exe" -3 "$serverScript" --port $Port *>> "$LogPath"
