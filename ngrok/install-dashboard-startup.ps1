param(
  [string]$TaskName = "Ngrok-ControlCenter-8091",
  [int]$Port = 8091,
  [switch]$StartNow
)

$ErrorActionPreference = "Stop"

$runnerScript = Join-Path $PSScriptRoot "run-dashboard-server.ps1"
if (-not (Test-Path -LiteralPath $runnerScript)) {
  throw "run-dashboard-server.ps1 not found: $runnerScript"
}

$arg = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$runnerScript`" -Port $Port"

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Force -ErrorAction Stop | Out-Null
Write-Host "Installed Scheduled Task: $TaskName"
Write-Host "Dashboard URL: http://127.0.0.1:$Port"

if ($StartNow) {
  Start-ScheduledTask -TaskName $TaskName
  Start-Sleep -Seconds 2
  Start-Process "http://127.0.0.1:$Port"
}