param(
  [string]$TaskName = "Ngrok-ControlCenter-8091"
)

$ErrorActionPreference = "Stop"

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  Write-Host "Removed Scheduled Task: $TaskName"
} else {
  Write-Host "Task not found: $TaskName"
}
