param(
  [string]$TaskName = "Ngrok-8000",
  [int]$Port = 8000,
  [string]$NgrokExe = "C:\ProgramData\chocolatey\bin\ngrok.exe",
  [string]$PolicyPath = "$PSScriptRoot\traffic-policy-public.yml",
  [string]$LogPath = "C:\ProgramData\ngrok\ngrok-8000.txt"
)

$ErrorActionPreference = "Stop"

# Scheduled task updates require elevated PowerShell.
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
  throw "Run this script in PowerShell as Administrator."
}

if (-not (Test-Path -LiteralPath $NgrokExe)) {
  throw "ngrok executable not found: $NgrokExe"
}
if (-not (Test-Path -LiteralPath $PolicyPath)) {
  throw "traffic policy file not found: $PolicyPath"
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
$arg = "-NoProfile -WindowStyle Hidden -Command `"& '$NgrokExe' http $Port --traffic-policy-file '$PolicyPath' --log stdout *>> '$LogPath'`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $task.Triggers -Principal $task.Principal -Settings $task.Settings -Force | Out-Null
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName $TaskName

Write-Host "Updated and restarted task: $TaskName"
Write-Host "Policy file: $PolicyPath"
