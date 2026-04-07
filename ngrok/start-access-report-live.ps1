param(
  [string]$LogPath = "C:\ProgramData\localserver\server-8000.txt",
  [string]$OutPath = "C:\Users\gkayt\OneDrive\Documents\vscode\html\ngrok\access-report.html",
  [int]$IntervalSeconds = 15,
  [int]$Top = 50,
  [switch]$Open
)

$ErrorActionPreference = "Stop"

if ($IntervalSeconds -lt 1) {
  throw "IntervalSeconds must be 1 or greater."
}

$generator = Join-Path $PSScriptRoot "generate-access-report.ps1"
if (-not (Test-Path -LiteralPath $generator)) {
  throw "Generator script not found: $generator"
}

$opened = $false

while ($true) {
  & $generator -LogPath $LogPath -OutPath $OutPath -Top $Top

  if ($Open -and -not $opened) {
    Start-Process $OutPath
    $opened = $true
  }

  Write-Host "Next refresh in $IntervalSeconds seconds..."
  Start-Sleep -Seconds $IntervalSeconds
}